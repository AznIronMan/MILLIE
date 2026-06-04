#!/usr/bin/env python3
"""Rebuild derived Postgres search documents from recovered MILLIE mail rows."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from millie.settings_loader import load_local_settings  # noqa: E402
from millie.storage.postgres_store import PostgresMailStore  # noqa: E402


DEFAULT_BATCH_SIZE = 500
MAX_SEARCH_BYTES = 800_000
INSERT_SEARCH_DOCUMENT_SQL = """
    INSERT INTO mail_search_documents (
        message_id, subject, body_text, from_text, to_text, cc_text,
        bcc_text, metadata_text, search_text
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT(message_id) DO UPDATE SET
        subject = excluded.subject,
        body_text = excluded.body_text,
        from_text = excluded.from_text,
        to_text = excluded.to_text,
        cc_text = excluded.cc_text,
        bcc_text = excluded.bcc_text,
        metadata_text = excluded.metadata_text,
        search_text = excluded.search_text,
        updated_at = now()
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Rebuild missing rows in mail_search_documents from mail_messages, "
            "mail_message_addresses, and mail_message_metadata. Dry-run by default."
        )
    )
    parser.add_argument("--apply", action="store_true", help="Write rebuilt search rows.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum messages to rebuild. Use 0 for all missing rows.")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument(
        "--include-existing",
        action="store_true",
        help="Rebuild existing search rows too. Default only fills missing rows.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    batch_size = max(1, min(args.batch_size, 5000))
    settings = load_local_settings()["settings"]
    if (settings.get("database_mode") or "").lower() != "postgres":
        raise SystemExit("millie_rebuild_search_documents requires database_mode=postgres.")

    with PostgresMailStore.connect(settings) as store:
        total = count_candidates(store, include_existing=args.include_existing)
        requested = total if args.limit <= 0 else min(total, args.limit)
        print(f"Endpoint: {settings['postgres_host_ip']}:{settings['postgres_port']}/{settings['postgres_database']}")
        print(f"Mode: {'apply' if args.apply else 'dry-run'}")
        print(f"Candidate messages: {total}")
        print(f"Requested rebuild: {requested}")
        if not args.apply or requested == 0:
            return 0

        rebuilt = 0
        skipped_ids: set[str] = set()
        while rebuilt < requested:
            rows = load_batch(
                store,
                limit=min(batch_size, requested - rebuilt),
                include_existing=args.include_existing,
                skip_ids=skipped_ids,
            )
            if not rows:
                break
            written, skipped = write_search_rows(store, rows)
            store.connection.commit()
            skipped_ids.update(skipped)
            rebuilt += len(rows)
            skipped_text = f", skipped {len(skipped_ids)} damaged rows" if skipped_ids else ""
            print(f"Rebuilt {rebuilt}/{requested}{skipped_text}", flush=True)
        print(f"Search rows rebuilt: {rebuilt}")
        if skipped_ids:
            print("Skipped message ids:")
            for message_id in sorted(skipped_ids):
                print(f"- {message_id}")
    return 0


def count_candidates(store: PostgresMailStore, *, include_existing: bool) -> int:
    where = "" if include_existing else """
        WHERE NOT EXISTS (
            SELECT 1
            FROM mail_search_documents sd
            WHERE sd.message_id = m.id
        )
    """
    row = store.connection.execute(
        f"""
        SELECT count(*)
        FROM mail_messages m
        {where}
        """
    ).fetchone()
    return int(row[0] or 0)


def load_batch(
    store: PostgresMailStore,
    *,
    limit: int,
    include_existing: bool,
    skip_ids: set[str],
) -> list[dict[str, str | None]]:
    missing_where = "" if include_existing else """
        AND NOT EXISTS (
            SELECT 1
            FROM mail_search_documents sd
            WHERE sd.message_id = m.id
        )
    """
    skip_where = ""
    params: list[object] = []
    if skip_ids:
        skip_where = "AND m.id <> ALL(%s)"
        params.append(list(skip_ids))
    rows = store.connection.execute(
        f"""
        WITH candidates AS (
            SELECT m.id
            FROM mail_messages m
            WHERE TRUE
              {missing_where}
              {skip_where}
            ORDER BY m.id
            LIMIT %s
        ),
        address_text AS (
            SELECT
                a.message_id,
                string_agg(
                    CASE
                        WHEN coalesce(a.display_name, '') <> '' OR coalesce(a.email_address, '') <> ''
                            THEN trim(coalesce(a.display_name, '') || ' ' || coalesce(a.email_address, ''))
                        ELSE coalesce(a.raw_value, '')
                    END,
                    ' ' ORDER BY a.ordinal
                )
                    FILTER (WHERE a.role IN ('from', 'sender')) AS from_text,
                string_agg(
                    CASE
                        WHEN coalesce(a.display_name, '') <> '' OR coalesce(a.email_address, '') <> ''
                            THEN trim(coalesce(a.display_name, '') || ' ' || coalesce(a.email_address, ''))
                        ELSE coalesce(a.raw_value, '')
                    END,
                    ' ' ORDER BY a.ordinal
                )
                    FILTER (WHERE a.role = 'to') AS to_text,
                string_agg(
                    CASE
                        WHEN coalesce(a.display_name, '') <> '' OR coalesce(a.email_address, '') <> ''
                            THEN trim(coalesce(a.display_name, '') || ' ' || coalesce(a.email_address, ''))
                        ELSE coalesce(a.raw_value, '')
                    END,
                    ' ' ORDER BY a.ordinal
                )
                    FILTER (WHERE a.role = 'cc') AS cc_text,
                string_agg(
                    CASE
                        WHEN coalesce(a.display_name, '') <> '' OR coalesce(a.email_address, '') <> ''
                            THEN trim(coalesce(a.display_name, '') || ' ' || coalesce(a.email_address, ''))
                        ELSE coalesce(a.raw_value, '')
                    END,
                    ' ' ORDER BY a.ordinal
                )
                    FILTER (WHERE a.role = 'bcc') AS bcc_text
            FROM mail_message_addresses a
            JOIN candidates c ON c.id = a.message_id
            GROUP BY a.message_id
        ),
        metadata_text AS (
            SELECT
                mm.message_id,
                string_agg(mm.value_text, ' ' ORDER BY mm.metadata_key) AS value_text
            FROM mail_message_metadata mm
            JOIN candidates c ON c.id = mm.message_id
            GROUP BY mm.message_id
        )
        SELECT
            m.id,
            m.subject,
            m.body_text,
            m.body_html,
            address_text.from_text,
            address_text.to_text,
            address_text.cc_text,
            address_text.bcc_text,
            nullif(
                concat_ws(
                    ' ',
                    metadata_text.value_text,
                    nullif(m.metadata_json::text, '{{}}')
                ),
                ''
            ) AS metadata_text
        FROM candidates c
        JOIN mail_messages m ON m.id = c.id
        LEFT JOIN address_text ON address_text.message_id = m.id
        LEFT JOIN metadata_text ON metadata_text.message_id = m.id
        ORDER BY m.id
        """,
        tuple([*params, limit]),
    ).fetchall()
    return [
        {
            "message_id": row[0],
            "subject": row[1],
            "body_text": row[2],
            "body_html": row[3],
            "from_text": row[4],
            "to_text": row[5],
            "cc_text": row[6],
            "bcc_text": row[7],
            "metadata_text": row[8],
        }
        for row in rows
    ]


def address_display(display_name: str | None, email_address: str | None, raw_value: str | None) -> str:
    values = [value for value in [display_name, email_address] if value]
    if values:
        return " ".join(values)
    return raw_value or ""


def search_text(parts: Iterable[str | None]) -> str:
    return truncate_search_text(" ".join(value for value in parts if value))


def truncate_search_text(value: str, *, max_bytes: int = MAX_SEARCH_BYTES) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def write_search_rows(store: PostgresMailStore, rows: list[dict[str, str | None]]) -> tuple[int, list[str]]:
    payload = [
        (
            row["message_id"],
            row["subject"],
            row["body_text"],
            row["from_text"],
            row["to_text"],
            row["cc_text"],
            row["bcc_text"],
            row["metadata_text"],
            search_text(
                [
                    row["subject"],
                    row["body_text"],
                    row["body_html"],
                    row["from_text"],
                    row["to_text"],
                    row["cc_text"],
                    row["bcc_text"],
                    row["metadata_text"],
                ]
            ),
        )
        for row in rows
    ]
    try:
        with store.connection.cursor() as cursor:
            cursor.executemany(INSERT_SEARCH_DOCUMENT_SQL, payload)
        return len(payload), []
    except Exception:
        store.connection.rollback()
        return write_search_rows_safely(store, payload)


def write_search_rows_safely(
    store: PostgresMailStore,
    payload: list[tuple[str | None, ...]],
) -> tuple[int, list[str]]:
    written = 0
    skipped: list[str] = []
    for row in payload:
        message_id = str(row[0] or "")
        try:
            store.connection.execute(INSERT_SEARCH_DOCUMENT_SQL, row)
            store.connection.commit()
            written += 1
        except Exception as exc:  # noqa: BLE001 - recovered archive can contain damaged rows.
            store.connection.rollback()
            skipped.append(message_id)
            print(f"Skipped damaged search row {message_id}: {type(exc).__name__}", flush=True)
    return written, skipped


if __name__ == "__main__":
    raise SystemExit(main())
