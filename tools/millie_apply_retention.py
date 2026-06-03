#!/usr/bin/env python3
"""Apply reviewed retention actions inside the MILLIE mailbox facade."""

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

from millie.brain.automation import automation_level_allows  # noqa: E402
from millie.service.auth import default_service_login  # noqa: E402
from millie.settings_loader import load_local_settings  # noqa: E402
from millie.storage.postgres_store import PostgresMailStore  # noqa: E402


SUPPORTED_ACTIONS = {"no_action", "hide_from_default_views"}


@dataclass(slots=True)
class ApplySummary:
    mode: str
    scanned: int = 0
    planned: int = 0
    applied: int = 0
    hidden_rows: int = 0
    blocked: int = 0
    unsupported: int = 0
    failed: int = 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Apply acknowledged MILLIE retention decisions internally. Default mode is "
            "dry-run. Execution never writes to source providers."
        )
    )
    parser.add_argument("--execute", action="store_true", help="Apply eligible internal retention actions.")
    parser.add_argument("--limit", type=int, default=100, help="Maximum acknowledged decisions to inspect. Use 0 for all.")
    parser.add_argument("--policy-id", action="append", default=[], help="Filter by retention policy id.")
    parser.add_argument("--folder", action="append", default=[], help="Filter by retention target folder.")
    parser.add_argument("--action", action="append", default=[], help="Filter by policy action.")
    parser.add_argument("--mailbox", default="", help="MILLIE mailbox address. Defaults to geon@<service_mail_domain>.")
    parser.add_argument(
        "--include-proposed",
        action="store_true",
        help="Dry-run proposed policies too. Execution still requires active policies.",
    )
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
        raise SystemExit("millie_apply_retention currently requires database_mode=postgres.")

    if args.execute and args.include_proposed:
        raise SystemExit("--include-proposed is dry-run only; activate policies before execution.")

    allowed = automation_level_allows(settings, "auto_internal")
    mode = "execute" if args.execute else "dry-run"
    with PostgresMailStore.connect(settings) as store:
        store.initialize()
        mailbox_address = args.mailbox or default_service_login(settings, "geon")
        mailbox = store.mailbox_by_address(mailbox_address)
        if mailbox is None:
            raise SystemExit(f"Mailbox not found: {mailbox_address}")
        rows = load_acknowledged_retention(store, mailbox_id=str(mailbox["id"]), args=args)
        summary = ApplySummary(mode=mode, scanned=len(rows))
        actions = [row for row in rows if row["policy_action"] in SUPPORTED_ACTIONS]
        summary.planned = len(actions)
        summary.unsupported = len(rows) - len(actions)

        if args.execute and not allowed and actions:
            summary.blocked = len(actions)
            if args.record_blocked:
                for action in actions:
                    write_retention_audit(
                        store,
                        action,
                        status="blocked",
                        error="automation_level below auto_internal",
                    )
                store.connection.commit()
            print_summary(summary, rows)
            return 2

        if args.execute:
            apply_actions(store, actions=actions, summary=summary)
            store.connection.commit()

    print_summary(summary, rows)
    return 0


def load_acknowledged_retention(
    store: PostgresMailStore,
    *,
    mailbox_id: str,
    args: argparse.Namespace,
) -> list[dict[str, object]]:
    where = [
        "mm.mailbox_id = %s",
        "latest_feedback.new_value_json->>'action' = 'acknowledge'",
        "mm.is_expunged = FALSE",
        "p.hold_duration IS NOT NULL",
        "mm.copied_at + p.hold_duration <= now()",
    ]
    params: list[Any] = [mailbox_id]

    if args.include_proposed:
        where.append("p.status IN ('proposed', 'active')")
    else:
        where.append("p.status = 'active'")

    if args.policy_id:
        where.append(f"p.id IN ({placeholders(args.policy_id)})")
        params.extend(args.policy_id)
    if args.folder:
        where.append(f"p.target_value IN ({placeholders(args.folder)})")
        params.extend(args.folder)
    if args.action:
        where.append(f"p.action IN ({placeholders(args.action)})")
        params.extend(args.action)

    where.append(
        """
        NOT EXISTS (
            SELECT 1
            FROM millie_automation_audit_log applied
            WHERE applied.message_id = mm.message_id
              AND applied.retention_policy_id = p.id
              AND applied.action_type = 'retention_apply'
              AND applied.status = 'applied'
              AND applied.after_json->>'mailbox_message_id' = mm.id
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
            p.id,
            p.policy_name,
            p.status,
            p.target_value,
            p.hold_duration,
            p.action,
            p.requires_review,
            mm.mailbox_id,
            mm.id,
            mm.message_id,
            mf.folder_path,
            mm.imap_uid,
            mm.copied_at,
            mm.copied_at + p.hold_duration AS eligible_at,
            coalesce(m.subject, '(no subject)') AS subject,
            latest_feedback.id AS feedback_id,
            latest_feedback.created_at AS feedback_at
        FROM millie_retention_policies p
        JOIN millie_mailbox_folders mf
          ON p.target_kind = 'folder'
         AND p.target_value = mf.folder_path
        JOIN millie_mailbox_messages mm ON mm.folder_id = mf.id
        JOIN mail_messages m ON m.id = mm.message_id
        JOIN LATERAL (
            SELECT e.id, e.new_value_json, e.created_at
            FROM millie_user_feedback_events e
            WHERE e.message_id = mm.message_id
              AND e.feedback_type = 'retention_override'
              AND e.metadata_json->>'retention_policy_id' = p.id
              AND e.metadata_json->>'mailbox_message_id' = mm.id
            ORDER BY e.created_at DESC
            LIMIT 1
        ) latest_feedback ON TRUE
        WHERE {" AND ".join(where)}
        ORDER BY latest_feedback.created_at, mm.copied_at, mf.folder_path, mm.imap_uid
        {limit_sql}
        """,
        tuple(params),
    ).fetchall()
    return [
        {
            "policy_id": row[0],
            "policy_name": row[1],
            "policy_status": row[2],
            "target_value": row[3],
            "hold_duration": row[4],
            "policy_action": row[5],
            "requires_review": bool(row[6]),
            "mailbox_id": row[7],
            "mailbox_message_id": row[8],
            "message_id": row[9],
            "folder_path": row[10],
            "uid": int(row[11]),
            "copied_at": row[12],
            "eligible_at": row[13],
            "subject": row[14],
            "feedback_id": row[15],
            "feedback_at": row[16],
        }
        for row in rows
    ]


def apply_actions(
    store: PostgresMailStore,
    *,
    actions: list[dict[str, object]],
    summary: ApplySummary,
) -> None:
    for action in actions:
        try:
            hidden_rows: list[dict[str, object]] = []
            if action["policy_action"] == "hide_from_default_views":
                hidden_rows = hide_default_view_rows(store, action)
                summary.hidden_rows += len(hidden_rows)
            write_retention_audit(
                store,
                action,
                status="applied",
                hidden_rows=hidden_rows,
            )
            summary.applied += 1
        except Exception as exc:  # noqa: BLE001 - record per-action failure.
            write_retention_audit(store, action, status="failed", error=str(exc))
            summary.failed += 1


def hide_default_view_rows(
    store: PostgresMailStore,
    action: dict[str, object],
) -> list[dict[str, object]]:
    rows = store.connection.execute(
        """
        UPDATE millie_mailbox_messages mm
        SET metadata_json = mm.metadata_json || jsonb_build_object(
                'retention_hidden_from_default_views', true,
                'retention_hidden_at', now(),
                'retention_policy_id', %s::text,
                'retention_source_mailbox_message_id', %s::text
            ),
            updated_at = now()
        FROM millie_mailbox_folders mf
        WHERE mf.id = mm.folder_id
          AND mm.message_id = %s
          AND mm.mailbox_id = %s
          AND mf.folder_path IN ('INBOX', 'All Mail')
          AND mm.is_expunged = FALSE
          AND mm.metadata_json->>'retention_hidden_from_default_views' IS DISTINCT FROM 'true'
        RETURNING mm.id, mf.folder_path, mm.imap_uid
        """,
        (
            action["policy_id"],
            action["mailbox_message_id"],
            action["message_id"],
            action["mailbox_id"],
        ),
    ).fetchall()
    return [
        {
            "mailbox_message_id": row[0],
            "folder_path": row[1],
            "uid": int(row[2]),
        }
        for row in rows
    ]


def write_retention_audit(
    store: PostgresMailStore,
    action: dict[str, object],
    *,
    status: str,
    hidden_rows: list[dict[str, object]] | None = None,
    error: str | None = None,
) -> None:
    store.connection.execute(
        """
        INSERT INTO millie_automation_audit_log (
            id, message_id, retention_policy_id, action_type, automation_level,
            status, after_json, error_message
        )
        VALUES (%s, %s, %s, 'retention_apply', 'auto_internal', %s, %s, %s)
        """,
        (
            str(uuid.uuid4()),
            action["message_id"],
            action["policy_id"],
            status,
            Jsonb(
                {
                    "policy_name": action["policy_name"],
                    "policy_action": action["policy_action"],
                    "policy_status": action["policy_status"],
                    "mailbox_message_id": action["mailbox_message_id"],
                    "folder_path": action["folder_path"],
                    "imap_uid": action["uid"],
                    "feedback_id": action["feedback_id"],
                    "hidden_rows": hidden_rows or [],
                }
            ),
            error,
        ),
    )


def print_summary(summary: ApplySummary, rows: list[dict[str, object]]) -> None:
    counts = Counter(str(row["policy_action"]) for row in rows)
    print("MILLIE apply retention")
    print(f"Mode: {summary.mode}")
    print(f"Scanned acknowledged retention decisions: {summary.scanned}")
    print(f"Planned supported actions: {summary.planned}")
    print(f"Applied: {summary.applied}")
    print(f"Hidden default-view rows: {summary.hidden_rows}")
    print(f"Blocked: {summary.blocked}")
    print(f"Unsupported: {summary.unsupported}")
    print(f"Failed: {summary.failed}")
    if counts:
        print("Actions:")
        for action, count in counts.most_common(20):
            print(f"  {action}: {count}")


def placeholders(values: list[object]) -> str:
    if not values:
        raise ValueError("values must not be empty")
    return ", ".join(["%s"] * len(values))


if __name__ == "__main__":
    raise SystemExit(main())
