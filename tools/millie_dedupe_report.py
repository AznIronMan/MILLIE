#!/usr/bin/env python3
"""Backfill and report MILLIE duplicate fingerprints."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from millie.importing.dedupe import dedupe_fields
from millie.settings_loader import load_local_settings
from millie.storage.postgres_store import PostgresMailStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill normalized duplicate fingerprints and report duplicate candidates."
    )
    parser.add_argument("--backfill", action="store_true", help="Populate missing fingerprint columns.")
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--limit", type=int, default=0, help="Maximum messages to backfill.")
    parser.add_argument("--samples", type=int, default=10, help="Duplicate groups to print per category.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON report.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_local_settings()
    store = PostgresMailStore.connect(config["settings"])
    try:
        store.initialize()
        store.connection.commit()
        backfilled = backfill_fingerprints(store, args) if args.backfill else 0
        report = duplicate_report(store, sample_count=args.samples)
        report["backfilled"] = backfilled
        if args.json:
            print(json.dumps(report, indent=2, default=str))
        else:
            print_report(report)
    finally:
        store.close()
    return 0


def backfill_fingerprints(store: PostgresMailStore, args: argparse.Namespace) -> int:
    processed = 0
    while True:
        remaining_limit = args.limit - processed if args.limit else args.batch_size
        if remaining_limit <= 0:
            break
        batch_size = min(args.batch_size, remaining_limit)
        rows = store.connection.execute(
            """
            SELECT id, internet_message_id, normalized_subject, sent_at::text, body_text, body_html
            FROM mail_messages
            WHERE normalized_message_fingerprint IS NULL
            ORDER BY created_at, id
            LIMIT %s
            """,
            (batch_size,),
        ).fetchall()
        if not rows:
            break

        ids = [str(row[0]) for row in rows]
        addresses = load_addresses(store, ids)
        attachments = load_attachments(store, ids)
        updates = []
        for row in rows:
            message_id = str(row[0])
            fields = dedupe_fields(
                internet_message_id=row[1],
                normalized_subject=row[2],
                sent_at=row[3],
                body_text=row[4],
                body_html=row[5],
                addresses=addresses.get(message_id, []),
                attachments=attachments.get(message_id, []),
            )
            updates.append(
                (
                    fields.normalized_body_sha256,
                    fields.attachment_set_sha256,
                    fields.normalized_message_fingerprint or "",
                    message_id,
                )
            )
        with store.connection.cursor() as cursor:
            cursor.executemany(
                """
                UPDATE mail_messages
                SET normalized_body_sha256 = %s,
                    attachment_set_sha256 = %s,
                    normalized_message_fingerprint = %s,
                    updated_at = now()
                WHERE id = %s
                """,
                updates,
            )
        store.connection.commit()
        processed += len(rows)
        print(f"backfilled={processed}", flush=True)
    return processed


def load_addresses(
    store: PostgresMailStore,
    message_ids: list[str],
) -> dict[str, list[tuple[str, str | None]]]:
    rows = store.connection.execute(
        """
        SELECT message_id, role, email_address
        FROM mail_message_addresses
        WHERE message_id = ANY(%s)
          AND role IN ('from', 'sender', 'to', 'cc', 'bcc')
        """,
        (message_ids,),
    ).fetchall()
    result: dict[str, list[tuple[str, str | None]]] = defaultdict(list)
    for message_id, role, email_address in rows:
        result[str(message_id)].append((str(role), str(email_address) if email_address else None))
    return result


def load_attachments(
    store: PostgresMailStore,
    message_ids: list[str],
) -> dict[str, list[tuple[str | None, int | None, str | None]]]:
    rows = store.connection.execute(
        """
        SELECT message_id, filename, size_bytes, sha256
        FROM mail_message_parts
        WHERE message_id = ANY(%s)
          AND (is_attachment = TRUE OR filename IS NOT NULL)
        """,
        (message_ids,),
    ).fetchall()
    result: dict[str, list[tuple[str | None, int | None, str | None]]] = defaultdict(list)
    for message_id, filename, size_bytes, sha256_value in rows:
        result[str(message_id)].append(
            (
                str(filename) if filename else None,
                int(size_bytes) if size_bytes is not None else None,
                str(sha256_value) if sha256_value else None,
            )
        )
    return result


def duplicate_report(store: PostgresMailStore, *, sample_count: int) -> dict[str, Any]:
    return {
        "counts": {
            "mail_messages": scalar(store, "SELECT count(*) FROM mail_messages"),
            "missing_normalized_fingerprint": scalar(
                store,
                "SELECT count(*) FROM mail_messages WHERE normalized_message_fingerprint IS NULL",
            ),
            "exact_raw_duplicate_groups": duplicate_group_count(store, "raw_mime_sha256"),
            "internet_message_id_duplicate_groups": duplicate_group_count(
                store,
                "internet_message_id",
                condition="internet_message_id IS NOT NULL AND internet_message_id <> ''",
            ),
            "normalized_fingerprint_duplicate_groups": duplicate_group_count(
                store,
                "normalized_message_fingerprint",
                condition=(
                    "normalized_message_fingerprint IS NOT NULL "
                    "AND normalized_message_fingerprint <> ''"
                ),
            ),
        },
        "samples": {
            "exact_raw": duplicate_samples(store, "raw_mime_sha256", sample_count),
            "internet_message_id": duplicate_samples(
                store,
                "internet_message_id",
                sample_count,
                condition="internet_message_id IS NOT NULL AND internet_message_id <> ''",
            ),
            "normalized_fingerprint": duplicate_samples(
                store,
                "normalized_message_fingerprint",
                sample_count,
                condition=(
                    "normalized_message_fingerprint IS NOT NULL "
                    "AND normalized_message_fingerprint <> ''"
                ),
            ),
        },
    }


def scalar(store: PostgresMailStore, sql: str) -> int:
    return int(store.connection.execute(sql).fetchone()[0])


def duplicate_group_count(
    store: PostgresMailStore,
    column: str,
    *,
    condition: str | None = None,
) -> int:
    where_clause = f"WHERE {condition}" if condition else ""
    row = store.connection.execute(
        f"""
        SELECT count(*)
        FROM (
            SELECT {column}
            FROM mail_messages
            {where_clause}
            GROUP BY {column}
            HAVING count(*) > 1
        ) groups
        """
    ).fetchone()
    return int(row[0])


def duplicate_samples(
    store: PostgresMailStore,
    column: str,
    sample_count: int,
    *,
    condition: str | None = None,
) -> list[dict[str, Any]]:
    where_clause = f"WHERE {condition}" if condition else ""
    rows = store.connection.execute(
        f"""
        SELECT {column} AS duplicate_key,
               count(*) AS message_count,
               min(coalesce(sent_at, received_at, created_at)) AS first_seen,
               max(coalesce(sent_at, received_at, created_at)) AS last_seen,
               (array_agg(id ORDER BY created_at, id))[1:5] AS sample_message_ids,
               (array_agg(coalesce(subject, '(no subject)') ORDER BY created_at, id))[1:5]
                   AS sample_subjects
        FROM mail_messages
        {where_clause}
        GROUP BY {column}
        HAVING count(*) > 1
        ORDER BY count(*) DESC, first_seen
        LIMIT %s
        """,
        (sample_count,),
    ).fetchall()
    return [
        {
            "key": row[0],
            "message_count": int(row[1]),
            "first_seen": row[2],
            "last_seen": row[3],
            "sample_message_ids": list(row[4] or []),
            "sample_subjects": list(row[5] or []),
        }
        for row in rows
    ]


def print_report(report: dict[str, Any]) -> None:
    print(f"backfilled={report['backfilled']}")
    for key, value in report["counts"].items():
        print(f"{key}={value}")
    for category, samples in report["samples"].items():
        print(f"{category}_samples={len(samples)}")
        for sample in samples:
            subject = sample["sample_subjects"][0] if sample["sample_subjects"] else ""
            print(
                f"  {category} count={sample['message_count']} "
                f"first_subject={subject!r} key={sample['key']}"
            )


if __name__ == "__main__":
    raise SystemExit(main())
