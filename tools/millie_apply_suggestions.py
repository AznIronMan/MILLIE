#!/usr/bin/env python3
"""Apply approved MILLIE suggestions inside the MILLIE mailbox facade."""

from __future__ import annotations

import argparse
import sys
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from millie.brain.apply import ClassificationAction, plan_classification_action  # noqa: E402
from millie.brain.automation import automation_level_allows  # noqa: E402
from millie.service.auth import default_service_login  # noqa: E402
from millie.settings_loader import load_local_settings  # noqa: E402
from millie.storage.postgres_store import PostgresMailStore  # noqa: E402


@dataclass(slots=True)
class ApplySummary:
    mode: str
    scanned: int = 0
    planned: int = 0
    blocked: int = 0
    applied: int = 0
    skipped: int = 0
    failed: int = 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Apply approved MILLIE brain classifications to the internal mailbox facade. "
            "Default mode is dry-run. Execution only maps messages into MILLIE folders; "
            "it does not write to source providers."
        )
    )
    parser.add_argument("--execute", action="store_true", help="Apply eligible internal mappings.")
    parser.add_argument("--limit", type=int, default=100, help="Maximum approved suggestions to inspect. Use 0 for all.")
    parser.add_argument("--classification-id", action="append", default=[], help="Apply exact classification id(s).")
    parser.add_argument("--kind", action="append", default=[], help="Filter by classification kind.")
    parser.add_argument("--target-folder", action="append", default=[], help="Filter by target folder path.")
    parser.add_argument("--mailbox", default="", help="MILLIE mailbox address. Defaults to geon@<service_mail_domain>.")
    parser.add_argument(
        "--record-blocked",
        action="store_true",
        help="In execute mode, write blocked audit rows when automation level prevents applying.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_local_settings()
    settings = config["settings"]
    if (settings.get("database_mode") or "").lower() != "postgres":
        raise SystemExit("millie_apply_suggestions currently requires database_mode=postgres.")

    allowed = automation_level_allows(settings, "auto_internal")
    mode = "execute" if args.execute else "dry-run"
    with PostgresMailStore.connect(settings) as store:
        store.initialize()
        mailbox_address = args.mailbox or default_service_login(settings, "geon")
        mailbox = store.mailbox_by_address(mailbox_address)
        if mailbox is None:
            raise SystemExit(f"Mailbox not found: {mailbox_address}")
        rows = load_approved_classifications(store, args)
        actions = [action for row in rows if (action := plan_classification_action(row))]
        summary = ApplySummary(mode=mode, scanned=len(rows), planned=len(actions))

        if args.execute and not allowed and actions:
            summary.blocked = len(actions)
            if args.record_blocked:
                for action in actions:
                    write_blocked_audit(store, action, reason="automation_level below auto_internal")
                store.connection.commit()
            print_summary(summary, actions)
            return 2

        if args.execute:
            apply_actions(store, mailbox_id=str(mailbox["id"]), actions=actions, summary=summary)
            store.connection.commit()

    print_summary(summary, actions)
    return 0


def load_approved_classifications(
    store: PostgresMailStore,
    args: argparse.Namespace,
) -> list[dict[str, object]]:
    where = ["c.status = 'approved'"]
    params: list[Any] = []

    if args.classification_id:
        where.append(f"c.id IN ({placeholders(args.classification_id)})")
        params.extend(args.classification_id)

    if args.kind:
        where.append(f"c.classification_kind IN ({placeholders(args.kind)})")
        params.extend(args.kind)

    if args.target_folder:
        where.append(f"c.target_folder_path IN ({placeholders(args.target_folder)})")
        params.extend(args.target_folder)

    where.append(
        """
        NOT EXISTS (
            SELECT 1
            FROM millie_automation_audit_log applied
            WHERE applied.classification_id = c.id
              AND applied.action_type = 'apply_internal_tag'
              AND applied.status = 'applied'
        )
        """
    )

    limit_sql = ""
    if args.limit and args.limit > 0:
        limit_sql = "LIMIT %s"
        params.append(args.limit)

    rows = store.connection.execute(
        f"""
        SELECT
            c.id,
            c.message_id,
            c.classification_kind,
            c.classification_value,
            c.target_folder_path,
            c.confidence,
            c.reason_text
        FROM millie_message_classifications c
        WHERE {" AND ".join(where)}
        ORDER BY c.confidence DESC, c.reviewed_at NULLS LAST, c.created_at
        {limit_sql}
        """,
        tuple(params),
    ).fetchall()
    return [
        {
            "classification_id": row[0],
            "message_id": row[1],
            "kind": row[2],
            "value": row[3],
            "target_folder_path": row[4],
            "confidence": float(row[5] or 0),
            "reason": row[6] or "",
        }
        for row in rows
    ]


def apply_actions(
    store: PostgresMailStore,
    *,
    mailbox_id: str,
    actions: list[ClassificationAction],
    summary: ApplySummary,
) -> None:
    for action in actions:
        try:
            store.ensure_mailbox_folder(mailbox_id, action.target_folder_path)
            target_uid = store.map_message_to_mailbox(
                mailbox_id=mailbox_id,
                folder_path=action.target_folder_path,
                message_id=action.message_id,
            )
            store.connection.execute(
                """
                UPDATE millie_message_classifications
                SET status = 'applied',
                    applied_at = coalesce(applied_at, now()),
                    updated_at = now()
                WHERE id = %s
                """,
                (action.classification_id,),
            )
            write_applied_audit(store, action, target_uid=target_uid)
            summary.applied += 1
        except Exception as exc:  # noqa: BLE001 - record per-action failure.
            write_failed_audit(store, action, error=str(exc))
            summary.failed += 1


def write_applied_audit(
    store: PostgresMailStore,
    action: ClassificationAction,
    *,
    target_uid: int,
) -> None:
    store.connection.execute(
        """
        INSERT INTO millie_automation_audit_log (
            id, message_id, classification_id, action_type, automation_level,
            status, after_json
        )
        VALUES (%s, %s, %s, 'apply_internal_tag', 'auto_internal', 'applied', %s)
        """,
        (
            str(uuid.uuid4()),
            action.message_id,
            action.classification_id,
            Jsonb(
                {
                    "target_folder_path": action.target_folder_path,
                    "target_uid": target_uid,
                    "kind": action.kind,
                    "value": action.value,
                }
            ),
        ),
    )


def write_blocked_audit(
    store: PostgresMailStore,
    action: ClassificationAction,
    *,
    reason: str,
) -> None:
    store.connection.execute(
        """
        INSERT INTO millie_automation_audit_log (
            id, message_id, classification_id, action_type, automation_level,
            status, after_json, error_message
        )
        VALUES (%s, %s, %s, 'apply_internal_tag', 'auto_internal', 'blocked', %s, %s)
        """,
        (
            str(uuid.uuid4()),
            action.message_id,
            action.classification_id,
            Jsonb({"target_folder_path": action.target_folder_path}),
            reason,
        ),
    )


def write_failed_audit(
    store: PostgresMailStore,
    action: ClassificationAction,
    *,
    error: str,
) -> None:
    store.connection.execute(
        """
        INSERT INTO millie_automation_audit_log (
            id, message_id, classification_id, action_type, automation_level,
            status, after_json, error_message
        )
        VALUES (%s, %s, %s, 'apply_internal_tag', 'auto_internal', 'failed', %s, %s)
        """,
        (
            str(uuid.uuid4()),
            action.message_id,
            action.classification_id,
            Jsonb({"target_folder_path": action.target_folder_path}),
            error,
        ),
    )


def print_summary(summary: ApplySummary, actions: list[ClassificationAction]) -> None:
    counts = Counter(action.target_folder_path for action in actions)
    print("MILLIE apply suggestions")
    print(f"Mode: {summary.mode}")
    print(f"Scanned approved suggestions: {summary.scanned}")
    print(f"Planned internal actions: {summary.planned}")
    print(f"Applied: {summary.applied}")
    print(f"Blocked: {summary.blocked}")
    print(f"Failed: {summary.failed}")
    if counts:
        print("Targets:")
        for target, count in counts.most_common(20):
            print(f"  {target}: {count}")


def placeholders(values: list[object]) -> str:
    if not values:
        raise ValueError("values must not be empty")
    return ", ".join(["%s"] * len(values))


if __name__ == "__main__":
    raise SystemExit(main())
