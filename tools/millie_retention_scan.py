#!/usr/bin/env python3
"""Dry-run retention scanner for MILLIE hold folders."""

from __future__ import annotations

import argparse
import sys
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from millie.brain.retention import (  # noqa: E402
    HeldMessage,
    RetentionCandidate,
    RetentionPolicy,
    human_duration,
    retention_candidate,
)
from millie.importing.models import stable_id  # noqa: E402
from millie.service.auth import default_service_login  # noqa: E402
from millie.settings_loader import load_local_settings  # noqa: E402
from millie.storage.postgres_store import PostgresMailStore  # noqa: E402


DEFAULT_POLICIES = (
    {
        "name": "Default Hold/Trash review",
        "target": "Hold/Trash",
        "duration": timedelta(days=30),
        "action": "no_action",
        "description": "Review messages held in Hold/Trash after 30 days. No deletion by default.",
    },
    {
        "name": "Default Hold/Spam review",
        "target": "Hold/Spam",
        "duration": timedelta(days=14),
        "action": "no_action",
        "description": "Review messages held in Hold/Spam after 14 days. No deletion by default.",
    },
)


@dataclass(slots=True)
class ScanSummary:
    policies: int = 0
    folders: int = 0
    held_messages: int = 0
    eligible: int = 0
    audit_rows: int = 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Scan MILLIE hold folders against retention policies. This is dry-run by "
            "default and never writes to source providers."
        )
    )
    parser.add_argument(
        "--seed-defaults",
        action="store_true",
        help="Create proposed no-action retention policies for Hold/Trash and Hold/Spam.",
    )
    parser.add_argument(
        "--record-scan",
        action="store_true",
        help="Write a retention_scan run plus retention_evaluate audit rows for eligible messages.",
    )
    parser.add_argument("--limit", type=int, default=100, help="Maximum eligible messages to print/audit.")
    parser.add_argument("--policy-id", action="append", default=[], help="Filter by retention policy id.")
    parser.add_argument("--folder", action="append", default=[], help="Filter by target hold folder.")
    parser.add_argument(
        "--active-only",
        action="store_true",
        help="Only scan active policies. By default proposed and active policies are shown.",
    )
    parser.add_argument("--mailbox", default="", help="MILLIE mailbox address. Defaults to geon@<service_mail_domain>.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    settings = load_local_settings()["settings"]
    if (settings.get("database_mode") or "").lower() != "postgres":
        raise SystemExit("millie_retention_scan currently requires database_mode=postgres.")

    with PostgresMailStore.connect(settings) as store:
        store.initialize()
        mailbox_address = args.mailbox or default_service_login(settings, "geon")
        mailbox = store.mailbox_by_address(mailbox_address)
        if mailbox is None:
            raise SystemExit(f"Mailbox not found: {mailbox_address}")
        mailbox_id = str(mailbox["id"])
        if args.seed_defaults:
            seed_default_policies(store, mailbox_id=mailbox_id)
            store.connection.commit()
        policies = load_policies(store, args)
        candidates, summary = scan_policies(
            store,
            mailbox_id=mailbox_id,
            policies=policies,
            limit=args.limit,
        )
        if args.record_scan:
            summary.audit_rows = record_scan(store, candidates, summary)
            store.connection.commit()
    print_summary(summary, policies, candidates)
    return 0


def seed_default_policies(store: PostgresMailStore, *, mailbox_id: str) -> None:
    for policy in DEFAULT_POLICIES:
        target = str(policy["target"])
        store.ensure_mailbox_folder(mailbox_id, target)
        policy_id = stable_id("millie_retention_policy", target, policy["duration"], policy["action"])
        store.connection.execute(
            """
            INSERT INTO millie_retention_policies (
                id, policy_name, status, target_kind, target_value, hold_duration,
                action, requires_review, condition_json, metadata_json
            )
            VALUES (%s, %s, 'proposed', 'folder', %s, %s, %s, TRUE, %s, %s)
            ON CONFLICT(id) DO UPDATE SET
                policy_name = excluded.policy_name,
                target_value = excluded.target_value,
                hold_duration = excluded.hold_duration,
                action = excluded.action,
                requires_review = TRUE,
                condition_json = excluded.condition_json,
                metadata_json = millie_retention_policies.metadata_json || excluded.metadata_json,
                updated_at = now()
            """,
            (
                policy_id,
                policy["name"],
                target,
                policy["duration"],
                policy["action"],
                Jsonb({"folder_path": target}),
                Jsonb({"description": policy["description"], "seeded_by": "millie_retention_scan"}),
            ),
        )


def load_policies(store: PostgresMailStore, args: argparse.Namespace) -> list[RetentionPolicy]:
    where = ["target_kind = 'folder'"]
    params: list[Any] = []
    if args.active_only:
        where.append("status = 'active'")
    else:
        where.append("status IN ('proposed', 'active')")
    if args.policy_id:
        where.append(f"id IN ({placeholders(args.policy_id)})")
        params.extend(args.policy_id)
    if args.folder:
        where.append(f"target_value IN ({placeholders(args.folder)})")
        params.extend(args.folder)
    rows = store.connection.execute(
        f"""
        SELECT id, policy_name, status, target_kind, target_value, hold_duration,
               action, requires_review
        FROM millie_retention_policies
        WHERE {" AND ".join(where)}
        ORDER BY target_value, policy_name
        """,
        tuple(params),
    ).fetchall()
    return [
        RetentionPolicy(
            id=str(row[0]),
            name=str(row[1]),
            status=str(row[2]),
            target_kind=str(row[3]),
            target_value=str(row[4]),
            hold_duration=row[5],
            action=str(row[6]),
            requires_review=bool(row[7]),
        )
        for row in rows
    ]


def scan_policies(
    store: PostgresMailStore,
    *,
    mailbox_id: str,
    policies: list[RetentionPolicy],
    limit: int,
) -> tuple[list[RetentionCandidate], ScanSummary]:
    candidates: list[RetentionCandidate] = []
    summary = ScanSummary(policies=len(policies), folders=len({p.target_value for p in policies}))
    for policy in policies:
        messages = held_messages_for_policy(store, mailbox_id=mailbox_id, policy=policy)
        summary.held_messages += len(messages)
        for message in messages:
            candidate = retention_candidate(policy, message)
            if candidate:
                candidates.append(candidate)
    candidates.sort(key=lambda item: (item.policy.target_value, item.message.copied_at, item.message.message_id))
    if limit > 0:
        candidates = candidates[:limit]
    summary.eligible = len(candidates)
    return candidates, summary


def held_messages_for_policy(
    store: PostgresMailStore,
    *,
    mailbox_id: str,
    policy: RetentionPolicy,
) -> list[HeldMessage]:
    if policy.hold_duration is None:
        return []
    rows = store.connection.execute(
        """
        SELECT
            mm.id,
            mm.message_id,
            mf.folder_path,
            mm.imap_uid,
            mm.copied_at,
            coalesce(m.subject, '(no subject)') AS subject
        FROM millie_mailbox_messages mm
        JOIN millie_mailbox_folders mf ON mf.id = mm.folder_id
        JOIN mail_messages m ON m.id = mm.message_id
        WHERE mm.mailbox_id = %s
          AND mf.folder_path = %s
          AND mm.is_expunged = FALSE
        ORDER BY mm.copied_at, mm.imap_uid
        """,
        (mailbox_id, policy.target_value),
    ).fetchall()
    return [
        HeldMessage(
            mailbox_message_id=str(row[0]),
            message_id=str(row[1]),
            folder_path=str(row[2]),
            imap_uid=int(row[3]),
            copied_at=row[4],
            subject=str(row[5]),
        )
        for row in rows
    ]


def record_scan(
    store: PostgresMailStore,
    candidates: list[RetentionCandidate],
    summary: ScanSummary,
) -> int:
    run_id = str(uuid.uuid4())
    store.connection.execute(
        """
        INSERT INTO millie_automation_runs (
            id, run_type, automation_level, status, trigger_source,
            started_at, completed_at, messages_scanned, suggestions_created,
            metadata_json
        )
        VALUES (%s, 'retention_scan', 'observe', 'completed', 'cli',
                now(), now(), %s, %s, %s)
        """,
        (
            run_id,
            summary.held_messages,
            len(candidates),
            Jsonb({"eligible_messages": len(candidates), "mode": "record_scan"}),
        ),
    )
    for candidate in candidates:
        store.connection.execute(
            """
            INSERT INTO millie_automation_audit_log (
                id, run_id, message_id, retention_policy_id, action_type,
                automation_level, status, after_json
            )
            VALUES (%s, %s, %s, %s, 'retention_evaluate', 'observe', 'recorded', %s)
            """,
            (
                str(uuid.uuid4()),
                run_id,
                candidate.message.message_id,
                candidate.policy.id,
                Jsonb(
                    {
                        "folder_path": candidate.message.folder_path,
                        "policy_action": candidate.policy.action,
                        "requires_review": candidate.policy.requires_review,
                        "eligible_at": candidate.eligible_at.isoformat(),
                        "age_seconds": candidate.age_seconds,
                    }
                ),
            ),
        )
    return len(candidates)


def print_summary(
    summary: ScanSummary,
    policies: list[RetentionPolicy],
    candidates: list[RetentionCandidate],
) -> None:
    print("MILLIE retention scan")
    print(f"Policies scanned: {summary.policies}")
    print(f"Hold folders scanned: {summary.folders}")
    print(f"Held messages scanned: {summary.held_messages}")
    print(f"Eligible messages: {summary.eligible}")
    print(f"Audit rows recorded: {summary.audit_rows}")
    if policies:
        print("Policies:")
        for policy in policies:
            print(
                f"  {policy.status} {policy.target_value}: "
                f"{human_duration(policy.hold_duration)} -> {policy.action} "
                f"review_required={policy.requires_review}"
            )
    if candidates:
        print("Eligible by folder:")
        for folder, count in Counter(item.message.folder_path for item in candidates).items():
            print(f"  {folder}: {count}")
        print("Sample eligible messages:")
        for item in candidates[:20]:
            print(
                "  "
                f"{item.message.folder_path} uid={item.message.imap_uid} "
                f"policy={item.policy.name!r} subject={item.message.subject!r}"
            )


def placeholders(values: list[object]) -> str:
    if not values:
        raise ValueError("values must not be empty")
    return ", ".join(["%s"] * len(values))


if __name__ == "__main__":
    raise SystemExit(main())
