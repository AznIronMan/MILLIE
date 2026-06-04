#!/usr/bin/env python3
"""Manage MILLIE retention policies with dry-run-first writes."""

from __future__ import annotations

import argparse
import re
import sys
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from millie.brain.observe import (  # noqa: E402
    BULK_REEVALUATION_FOLDER,
    SPAM_REEVALUATION_FOLDER,
    TRASH_REEVALUATION_FOLDER,
)
from millie.importing.models import stable_id  # noqa: E402
from millie.service.auth import default_service_login  # noqa: E402
from millie.settings_loader import load_local_settings  # noqa: E402
from millie.storage.postgres_store import PostgresMailStore  # noqa: E402


POLICY_STATUSES = {"proposed", "active", "disabled", "retired"}
POLICY_ACTIONS = {
    "no_action",
    "hide_from_default_views",
    "expire_internal_copy",
    "delete_internal_copy",
}
DEFAULT_HOLD_FOLDERS = (
    TRASH_REEVALUATION_FOLDER,
    SPAM_REEVALUATION_FOLDER,
    BULK_REEVALUATION_FOLDER,
)


@dataclass(frozen=True, slots=True)
class PolicyChange:
    policy_id: str
    action: str
    before: dict[str, object]
    after: dict[str, object]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="List and edit MILLIE retention policies.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List retention policies.")
    list_parser.add_argument("--status", action="append", default=[], choices=sorted(POLICY_STATUSES))
    list_parser.add_argument("--folder", action="append", default=[], help="Filter by target folder.")
    list_parser.add_argument("--policy-id", action="append", default=[], help="Filter by policy id.")

    create_parser = subparsers.add_parser("create", help="Create or update a folder retention policy.")
    create_parser.add_argument("--name", required=True)
    create_parser.add_argument("--folder", required=True)
    create_parser.add_argument("--duration", required=True, help="Hold duration such as 12h, 14d, 2w, or 30 days.")
    create_parser.add_argument("--action", required=True, choices=sorted(POLICY_ACTIONS))
    create_parser.add_argument("--status", default="proposed", choices=sorted(POLICY_STATUSES))
    add_review_flags(create_parser)
    create_parser.add_argument("--execute", action="store_true", help="Write the policy.")

    activate_parser = subparsers.add_parser("activate", help="Activate proposed or disabled policies.")
    activate_parser.add_argument("--policy-id", action="append", default=[])
    activate_parser.add_argument("--folder", action="append", default=[])
    activate_parser.add_argument(
        "--default-holds",
        action="store_true",
        help="Activate default reevaluation hold policies.",
    )
    activate_parser.add_argument("--execute", action="store_true", help="Write the status change.")

    disable_parser = subparsers.add_parser("disable", help="Disable active or proposed policies.")
    disable_parser.add_argument("--policy-id", action="append", default=[])
    disable_parser.add_argument("--folder", action="append", default=[])
    disable_parser.add_argument("--execute", action="store_true", help="Write the status change.")

    update_parser = subparsers.add_parser("update", help="Edit duration, action, status, or review requirement.")
    update_parser.add_argument("--policy-id", required=True)
    update_parser.add_argument("--duration", default="", help="Hold duration such as 12h, 14d, 2w, or 30 days.")
    update_parser.add_argument("--action", choices=sorted(POLICY_ACTIONS))
    update_parser.add_argument("--status", choices=sorted(POLICY_STATUSES))
    add_review_flags(update_parser)
    update_parser.add_argument("--execute", action="store_true", help="Write the policy edit.")

    return parser


def add_review_flags(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--requires-review", dest="requires_review", action="store_true", default=None)
    group.add_argument("--no-requires-review", dest="requires_review", action="store_false")


def main() -> int:
    args = build_parser().parse_args()
    settings = load_local_settings()["settings"]
    if (settings.get("database_mode") or "").lower() != "postgres":
        raise SystemExit("millie_retention_policies currently requires database_mode=postgres.")

    with PostgresMailStore.connect(settings) as store:
        store.initialize()
        mailbox = store.mailbox_by_address(default_service_login(settings, "geon"))
        mailbox_id = str(mailbox["id"]) if mailbox else ""
        if args.command == "list":
            policies = list_policies(store, args)
            print_policies(policies)
            return 0
        if args.command == "create":
            changes = create_policy(store, args, mailbox_id=mailbox_id)
        elif args.command == "activate":
            changes = set_policy_status(store, args, status="active")
        elif args.command == "disable":
            changes = set_policy_status(store, args, status="disabled")
        elif args.command == "update":
            changes = update_policy(store, args)
        else:
            raise SystemExit(f"Unsupported command: {args.command}")

        print_changes(changes, execute=bool(args.execute))
        if args.execute and changes:
            for change in changes:
                write_policy_audit(store, change)
            store.connection.commit()
    return 0


def list_policies(store: PostgresMailStore, args: argparse.Namespace) -> list[dict[str, object]]:
    where = ["target_kind = 'folder'"]
    params: list[object] = []
    if args.status:
        where.append(f"status IN ({placeholders(args.status)})")
        params.extend(args.status)
    if args.folder:
        where.append(f"target_value IN ({placeholders(args.folder)})")
        params.extend(args.folder)
    if args.policy_id:
        where.append(f"id IN ({placeholders(args.policy_id)})")
        params.extend(args.policy_id)
    rows = store.connection.execute(
        f"""
        SELECT id, policy_name, status, target_value, hold_duration, action,
               requires_review, updated_at
        FROM millie_retention_policies
        WHERE {" AND ".join(where)}
        ORDER BY target_value, policy_name
        """,
        tuple(params),
    ).fetchall()
    return [policy_dict(row) for row in rows]


def create_policy(
    store: PostgresMailStore,
    args: argparse.Namespace,
    *,
    mailbox_id: str,
) -> list[PolicyChange]:
    duration = parse_duration(args.duration)
    requires_review = True if args.requires_review is None else bool(args.requires_review)
    policy_id = stable_id("millie_retention_policy", args.folder, duration, args.action)
    before = load_policy(store, policy_id)
    after = {
        "id": policy_id,
        "policy_name": args.name,
        "status": args.status,
        "target_kind": "folder",
        "target_value": args.folder,
        "hold_duration": duration,
        "action": args.action,
        "requires_review": requires_review,
    }
    change = PolicyChange(policy_id=policy_id, action="create_or_update", before=before, after=after)
    if args.execute:
        if mailbox_id:
            store.ensure_mailbox_folder(mailbox_id, args.folder)
        store.connection.execute(
            """
            INSERT INTO millie_retention_policies (
                id, policy_name, status, target_kind, target_value, hold_duration,
                action, requires_review, condition_json, metadata_json
            )
            VALUES (%s, %s, %s, 'folder', %s, %s, %s, %s, %s, %s)
            ON CONFLICT(id) DO UPDATE SET
                policy_name = excluded.policy_name,
                status = excluded.status,
                target_value = excluded.target_value,
                hold_duration = excluded.hold_duration,
                action = excluded.action,
                requires_review = excluded.requires_review,
                condition_json = excluded.condition_json,
                metadata_json = millie_retention_policies.metadata_json || excluded.metadata_json,
                updated_at = now()
            """,
            (
                policy_id,
                args.name,
                args.status,
                args.folder,
                duration,
                args.action,
                requires_review,
                Jsonb({"folder_path": args.folder}),
                Jsonb({"managed_by": "millie_retention_policies"}),
            ),
        )
    return [change]


def set_policy_status(
    store: PostgresMailStore,
    args: argparse.Namespace,
    *,
    status: str,
) -> list[PolicyChange]:
    policies = target_policies(store, args)
    changes: list[PolicyChange] = []
    for policy in policies:
        before = dict(policy)
        after = dict(policy)
        after["status"] = status
        changes.append(PolicyChange(str(policy["id"]), f"set_status:{status}", before, after))
    if args.execute:
        for change in changes:
            store.connection.execute(
                """
                UPDATE millie_retention_policies
                SET status = %s,
                    updated_at = now(),
                    metadata_json = metadata_json || %s
                WHERE id = %s
                """,
                (
                    status,
                    Jsonb({"managed_by": "millie_retention_policies", "status_action": change.action}),
                    change.policy_id,
                ),
            )
    return changes


def update_policy(store: PostgresMailStore, args: argparse.Namespace) -> list[PolicyChange]:
    before = load_policy(store, args.policy_id)
    if not before:
        raise SystemExit(f"Policy not found: {args.policy_id}")
    after = dict(before)
    if args.duration:
        after["hold_duration"] = parse_duration(args.duration)
    if args.action:
        after["action"] = args.action
    if args.status:
        after["status"] = args.status
    if args.requires_review is not None:
        after["requires_review"] = bool(args.requires_review)
    change = PolicyChange(args.policy_id, "update", before, after)
    if args.execute:
        store.connection.execute(
            """
            UPDATE millie_retention_policies
            SET hold_duration = %s,
                action = %s,
                status = %s,
                requires_review = %s,
                updated_at = now(),
                metadata_json = metadata_json || %s
            WHERE id = %s
            """,
            (
                after["hold_duration"],
                after["action"],
                after["status"],
                after["requires_review"],
                Jsonb({"managed_by": "millie_retention_policies", "status_action": "update"}),
                args.policy_id,
            ),
        )
    return [change]


def target_policies(store: PostgresMailStore, args: argparse.Namespace) -> list[dict[str, object]]:
    folders = list(args.folder)
    if getattr(args, "default_holds", False):
        folders.extend(DEFAULT_HOLD_FOLDERS)
    if not args.policy_id and not folders:
        raise SystemExit("Specify --policy-id, --folder, or --default-holds.")
    target_args = argparse.Namespace(
        status=[],
        folder=folders,
        policy_id=args.policy_id,
    )
    policies = list_policies(store, target_args)
    if not policies:
        raise SystemExit("No matching retention policies found.")
    return policies


def load_policy(store: PostgresMailStore, policy_id: str) -> dict[str, object]:
    row = store.connection.execute(
        """
        SELECT id, policy_name, status, target_value, hold_duration, action,
               requires_review, updated_at
        FROM millie_retention_policies
        WHERE id = %s
        """,
        (policy_id,),
    ).fetchone()
    return policy_dict(row) if row else {}


def policy_dict(row: object) -> dict[str, object]:
    return {
        "id": row[0],
        "policy_name": row[1],
        "status": row[2],
        "target_value": row[3],
        "hold_duration": row[4],
        "action": row[5],
        "requires_review": bool(row[6]),
        "updated_at": row[7],
    }


def write_policy_audit(store: PostgresMailStore, change: PolicyChange) -> None:
    store.connection.execute(
        """
        INSERT INTO millie_automation_audit_log (
            id, action_type, automation_level, status, before_json, after_json
        )
        VALUES (%s, 'custom', 'review', 'recorded', %s, %s)
        """,
        (
            str(uuid.uuid4()),
            Jsonb(serialize_policy(change.before)),
            Jsonb(
                {
                    "policy_change": change.action,
                    "policy_id": change.policy_id,
                    "policy": serialize_policy(change.after),
                }
            ),
        ),
    )


def print_policies(policies: list[dict[str, object]]) -> None:
    print(f"MILLIE retention policies: {len(policies)}")
    for policy in policies:
        print(
            f"{policy['status']} {policy['target_value']} "
            f"{duration_text(policy['hold_duration'])} -> {policy['action']} "
            f"review_required={policy['requires_review']} id={policy['id']} "
            f"name={policy['policy_name']!r}"
        )


def print_changes(changes: list[PolicyChange], *, execute: bool) -> None:
    print(f"MILLIE retention policy {'execute' if execute else 'dry-run'}")
    print(f"Changes: {len(changes)}")
    for change in changes:
        before = serialize_policy(change.before)
        after = serialize_policy(change.after)
        print(f"{change.action} id={change.policy_id}")
        print(f"  before: {before}")
        print(f"  after:  {after}")


def serialize_policy(policy: dict[str, object]) -> dict[str, object]:
    serialized = dict(policy)
    if "hold_duration" in serialized:
        serialized["hold_duration"] = duration_text(serialized["hold_duration"])
    if "updated_at" in serialized and serialized["updated_at"] is not None:
        serialized["updated_at"] = serialized["updated_at"].isoformat()
    return serialized


def duration_text(value: object) -> str:
    if value is None:
        return "none"
    if isinstance(value, timedelta):
        total_seconds = int(value.total_seconds())
        if total_seconds % 604800 == 0:
            weeks = total_seconds // 604800
            return f"{weeks}w"
        if total_seconds % 86400 == 0:
            days = total_seconds // 86400
            return f"{days}d"
        if total_seconds % 3600 == 0:
            hours = total_seconds // 3600
            return f"{hours}h"
        return f"{total_seconds}s"
    return str(value)


def parse_duration(value: str) -> timedelta:
    text = value.strip().lower()
    match = re.fullmatch(r"(\d+)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days|w|week|weeks)", text)
    if not match:
        raise argparse.ArgumentTypeError(f"Unsupported duration: {value}")
    amount = int(match.group(1))
    unit = match.group(2)
    if unit.startswith("s"):
        return timedelta(seconds=amount)
    if unit.startswith("m"):
        return timedelta(minutes=amount)
    if unit.startswith("h"):
        return timedelta(hours=amount)
    if unit.startswith("d"):
        return timedelta(days=amount)
    if unit.startswith("w"):
        return timedelta(weeks=amount)
    raise argparse.ArgumentTypeError(f"Unsupported duration: {value}")


def placeholders(values: list[object]) -> str:
    if not values:
        raise ValueError("values must not be empty")
    return ", ".join(["%s"] * len(values))


if __name__ == "__main__":
    raise SystemExit(main())
