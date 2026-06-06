#!/usr/bin/env python3
"""Find and quarantine unreadable raw MIME rows in the recovered MILLIE archive."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from millie.settings_loader import load_local_settings  # noqa: E402
from millie.storage.postgres_safety import validate_postgres_settings  # noqa: E402
from millie.storage.postgres_store import PostgresMailStore  # noqa: E402


DEFAULT_LIMIT = 1000
DEFAULT_REPORT_DIR = PROJECT_ROOT / ".private" / "local"
DEFAULT_MAX_DELETE_BYTES = 1_000_000


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Probe raw MIME rows for Postgres decompression corruption. Dry-run by "
            "default; --apply marks damaged messages as quarantined. Raw-row deletion "
            "requires --delete-raw-row and is size-guarded."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write quarantine metadata for damaged rows.",
    )
    parser.add_argument(
        "--delete-raw-row",
        action="store_true",
        help="Delete damaged mail_raw_mime rows after quarantine. Requires --apply.",
    )
    parser.add_argument(
        "--force-large-delete",
        action="store_true",
        help="Allow --delete-raw-row above --max-delete-bytes.",
    )
    parser.add_argument(
        "--max-delete-bytes",
        type=int,
        default=DEFAULT_MAX_DELETE_BYTES,
        help="Largest declared raw MIME size eligible for deletion without --force-large-delete.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Maximum rows to probe. Default {DEFAULT_LIMIT}; use --all for every candidate.",
    )
    parser.add_argument("--all", action="store_true", help="Probe every non-quarantined raw MIME row.")
    parser.add_argument("--message-id", action="append", default=[], help="Probe one exact message id. Repeatable.")
    parser.add_argument("--after-id", default="", help="Resume probing after this mail_messages.id value.")
    parser.add_argument("--report", type=Path, default=None, help="JSONL report path.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.delete_raw_row and not args.apply:
        raise SystemExit("--delete-raw-row requires --apply.")

    settings = load_local_settings()["settings"]
    if (settings.get("database_mode") or "").lower() != "postgres":
        raise SystemExit("millie_quarantine_corrupt_raw_mime requires database_mode=postgres.")
    host, port, dbname = validate_postgres_settings(settings)
    report_path = args.report or default_report_path()
    report_path.parent.mkdir(parents=True, exist_ok=True)

    with PostgresMailStore.connect(settings) as store:
        print(f"Endpoint: {host}:{port}/{dbname}")
        print(f"Mode: {'apply' if args.apply else 'dry-run'}")
        print(f"Report: {report_path}")

        probed = 0
        damaged = 0
        quarantined = 0
        deleted = 0
        skipped_large_delete = 0
        expected_total = None if args.message_id else count_candidates(store, after_id=args.after_id)
        limit = None if args.all else max(0, args.limit)
        if expected_total is not None:
            requested = expected_total if limit is None else min(expected_total, limit)
            print(f"Candidate rows: {expected_total}")
            print(f"Requested probe: {requested}")

        with report_path.open("a", encoding="utf-8") as report:
            for row in candidate_rows(store, message_ids=args.message_id, after_id=args.after_id, limit=limit):
                probed += 1
                message_id = str(row["message_id"])
                declared_size = int(row["raw_mime_size_bytes"] or 0)
                try:
                    stored_size = probe_raw_size(store, message_id)
                except psycopg.errors.DataCorrupted as exc:
                    store.connection.rollback()
                    damaged += 1
                    event = {
                        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "message_id": message_id,
                        "subject": row.get("subject") or "",
                        "declared_size": declared_size,
                        "error": str(exc),
                        "applied": bool(args.apply),
                        "deleted_raw_row": False,
                        "delete_skipped_large": False,
                    }
                    if args.apply:
                        store.mark_raw_mime_quarantined(
                            message_id,
                            reason="postgres_data_corrupted",
                            source="raw_mime_quarantine_tool",
                            details={
                                "declared_size": declared_size,
                                "error": str(exc),
                                "delete_raw_row_requested": bool(args.delete_raw_row),
                            },
                        )
                        quarantined += 1
                        if args.delete_raw_row:
                            allowed_delete = args.force_large_delete or declared_size <= max(0, args.max_delete_bytes)
                            if allowed_delete:
                                delete_raw_row(store, message_id)
                                event["deleted_raw_row"] = True
                                deleted += 1
                            else:
                                event["delete_skipped_large"] = True
                                skipped_large_delete += 1
                        store.connection.commit()
                    report.write(json.dumps(event, sort_keys=True) + "\n")
                    report.flush()
                    print(
                        "damaged "
                        f"message_id={message_id} declared_size={declared_size} "
                        f"quarantined={args.apply} deleted={event['deleted_raw_row']}",
                        flush=True,
                    )
                    continue
                if stored_size != declared_size:
                    report.write(
                        json.dumps(
                            {
                                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                "message_id": message_id,
                                "declared_size": declared_size,
                                "stored_size": stored_size,
                                "warning": "raw_mime_size_mismatch",
                            },
                            sort_keys=True,
                        )
                        + "\n"
                    )
                if probed % 500 == 0:
                    store.connection.rollback()
                    print(f"probed={probed} damaged={damaged}", flush=True)

    print(
        "millie_quarantine_corrupt_raw_mime=done "
        f"probed={probed} damaged={damaged} quarantined={quarantined} "
        f"deleted={deleted} skipped_large_delete={skipped_large_delete}"
    )
    return 0


def default_report_path() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return DEFAULT_REPORT_DIR / f"millie_corrupt_raw_mime_{stamp}.jsonl"


def count_candidates(store: PostgresMailStore, *, after_id: str) -> int:
    row = store.connection.execute(
        """
        SELECT count(*)
        FROM mail_messages m
        JOIN mail_raw_mime r ON r.message_id = m.id
        WHERE (%s = '' OR m.id > %s)
          AND NOT EXISTS (
              SELECT 1
              FROM mail_message_metadata q
              WHERE q.message_id = m.id
                AND q.metadata_key = 'raw_mime_quarantined'
                AND lower(q.value_text) IN ('true', '1', 'yes')
          )
        """,
        (after_id, after_id),
    ).fetchone()
    return int(row[0] or 0)


def candidate_rows(
    store: PostgresMailStore,
    *,
    message_ids: list[str],
    after_id: str,
    limit: int | None,
) -> list[dict[str, object]]:
    if message_ids:
        rows = store.connection.execute(
            """
            SELECT m.id, m.subject, m.raw_mime_size_bytes
            FROM mail_messages m
            JOIN mail_raw_mime r ON r.message_id = m.id
            WHERE m.id = ANY(%s)
            ORDER BY m.id
            """,
            (message_ids,),
        ).fetchall()
    else:
        limit_clause = "" if limit is None else "LIMIT %s"
        params: tuple[object, ...] = (after_id, after_id) if limit is None else (after_id, after_id, limit)
        rows = store.connection.execute(
            f"""
            SELECT m.id, m.subject, m.raw_mime_size_bytes
            FROM mail_messages m
            JOIN mail_raw_mime r ON r.message_id = m.id
            WHERE (%s = '' OR m.id > %s)
              AND NOT EXISTS (
                  SELECT 1
                  FROM mail_message_metadata q
                  WHERE q.message_id = m.id
                    AND q.metadata_key = 'raw_mime_quarantined'
                    AND lower(q.value_text) IN ('true', '1', 'yes')
              )
            ORDER BY m.id
            {limit_clause}
            """,
            params,
        ).fetchall()
    return [
        {
            "message_id": row[0],
            "subject": row[1],
            "raw_mime_size_bytes": row[2],
        }
        for row in rows
    ]


def probe_raw_size(store: PostgresMailStore, message_id: str) -> int:
    row = store.connection.execute(
        """
        SELECT octet_length(content_blob)
        FROM mail_raw_mime
        WHERE message_id = %s
        """,
        (message_id,),
    ).fetchone()
    return int(row[0] or 0) if row else 0


def delete_raw_row(store: PostgresMailStore, message_id: str) -> None:
    store.connection.execute("DELETE FROM mail_raw_mime WHERE message_id = %s", (message_id,))
    store.connection.execute(
        """
        UPDATE mail_messages
        SET metadata_json = metadata_json || %s,
            updated_at = now()
        WHERE id = %s
        """,
        (Jsonb({"raw_mime_deleted_after_quarantine": True}), message_id),
    )


if __name__ == "__main__":
    raise SystemExit(main())
