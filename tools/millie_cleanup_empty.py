#!/usr/bin/env python3
"""Report and clean empty MILLIE internal metadata."""

from __future__ import annotations

import argparse
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from psycopg.types.json import Jsonb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from millie.brain.automation import automation_level, automation_level_allows  # noqa: E402
from millie.service.auth import default_service_login  # noqa: E402
from millie.settings_loader import load_local_settings  # noqa: E402
from millie.storage.postgres_store import PostgresMailStore  # noqa: E402


@dataclass(frozen=True, slots=True)
class CleanupItem:
    id: str
    label: str
    details: dict[str, object]


@dataclass(slots=True)
class CleanupSummary:
    mode: str
    mailbox_leaf_folders: int = 0
    source_leaf_folders: int = 0
    blank_addresses: int = 0
    empty_import_jobs: int = 0
    empty_sources: int = 0
    empty_part_containers: int = 0
    deleted_mailbox_leaf_folders: int = 0
    deleted_source_leaf_folders: int = 0
    deleted_blank_addresses: int = 0
    deleted_empty_import_jobs: int = 0
    deleted_empty_sources: int = 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Report empty internal MILLIE metadata and optionally remove safe empty rows. "
            "This never contacts providers, deletes provider mail, or removes canonical messages."
        )
    )
    parser.add_argument("--mailbox", default="", help="Mailbox address. Defaults to geon@<service_mail_domain>.")
    parser.add_argument("--limit", type=int, default=50, help="Maximum sample rows to print per category.")
    parser.add_argument("--max-passes", type=int, default=20, help="Maximum iterative folder cleanup passes.")
    parser.add_argument(
        "--execute-mailbox-folders",
        action="store_true",
        help="Delete empty custom MILLIE mailbox leaf folders.",
    )
    parser.add_argument(
        "--execute-source-folders",
        action="store_true",
        help="Delete empty canonical source-folder metadata leaves.",
    )
    parser.add_argument(
        "--execute-blank-addresses",
        action="store_true",
        help="Delete address rows with no display name, email address, or raw value.",
    )
    parser.add_argument(
        "--execute-empty-import-jobs",
        action="store_true",
        help="Delete non-running import jobs that have no messages attached.",
    )
    parser.add_argument(
        "--execute-empty-sources",
        action="store_true",
        help="Delete source definitions with no messages, folders, aliases, cursors, jobs, or bindings.",
    )
    parser.add_argument(
        "--include-derived-parts-report",
        action="store_true",
        help="Also report empty derived MIME part containers. They are report-only.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_local_settings()
    settings = config["settings"]
    if (settings.get("database_mode") or "").lower() != "postgres":
        raise SystemExit("millie_cleanup_empty currently requires database_mode=postgres.")

    execute_requested = any(
        [
            args.execute_mailbox_folders,
            args.execute_source_folders,
            args.execute_blank_addresses,
            args.execute_empty_import_jobs,
            args.execute_empty_sources,
        ]
    )
    allowed = automation_level_allows(settings, "auto_internal")
    if execute_requested and not allowed:
        raise SystemExit(
            "Execution is blocked because automation_level is below auto_internal. "
            "Run without execute flags for a report."
        )

    mode = "execute" if execute_requested else "dry-run"
    with PostgresMailStore.connect(settings) as store:
        store.initialize()
        mailbox_address = args.mailbox or default_service_login(settings, "geon")
        mailbox = store.mailbox_by_address(mailbox_address)
        if mailbox is None:
            raise SystemExit(f"Mailbox not found: {mailbox_address}")
        mailbox_id = str(mailbox["id"])

        summary = CleanupSummary(mode=mode)
        samples = collect_samples(
            store,
            mailbox_id=mailbox_id,
            limit=args.limit,
            include_derived_parts_report=args.include_derived_parts_report,
        )
        apply_sample_counts(summary, samples)

        run_id = ""
        if execute_requested:
            run_id = create_cleanup_run(store, settings=settings, args=args)
            if args.execute_mailbox_folders:
                summary.deleted_mailbox_leaf_folders = delete_mailbox_leaf_folders(
                    store,
                    mailbox_id=mailbox_id,
                    max_passes=args.max_passes,
                )
            if args.execute_source_folders:
                summary.deleted_source_leaf_folders = delete_source_leaf_folders(
                    store,
                    max_passes=args.max_passes,
                )
            if args.execute_blank_addresses:
                summary.deleted_blank_addresses = delete_blank_addresses(store)
            if args.execute_empty_import_jobs:
                summary.deleted_empty_import_jobs = delete_empty_import_jobs(store)
            if args.execute_empty_sources:
                summary.deleted_empty_sources = delete_empty_sources(store)
            complete_cleanup_run(store, run_id=run_id, summary=summary)
            store.connection.commit()

    print_summary(summary, samples, execute_allowed=allowed, run_id=run_id)
    return 0


def collect_samples(
    store: PostgresMailStore,
    *,
    mailbox_id: str,
    limit: int,
    include_derived_parts_report: bool,
) -> dict[str, list[CleanupItem]]:
    samples = {
        "mailbox_leaf_folders": empty_mailbox_leaf_folders(store, mailbox_id=mailbox_id, limit=limit),
        "source_leaf_folders": empty_source_leaf_folders(store, limit=limit),
        "blank_addresses": blank_address_rows(store, limit=limit),
        "empty_import_jobs": empty_import_jobs(store, limit=limit),
        "empty_sources": empty_sources(store, limit=limit),
        "empty_part_containers": [],
    }
    if include_derived_parts_report:
        samples["empty_part_containers"] = empty_derived_part_containers(store, limit=limit)
    return samples


def apply_sample_counts(summary: CleanupSummary, samples: dict[str, list[CleanupItem]]) -> None:
    summary.mailbox_leaf_folders = len(samples["mailbox_leaf_folders"])
    summary.source_leaf_folders = len(samples["source_leaf_folders"])
    summary.blank_addresses = len(samples["blank_addresses"])
    summary.empty_import_jobs = len(samples["empty_import_jobs"])
    summary.empty_sources = len(samples["empty_sources"])
    summary.empty_part_containers = len(samples["empty_part_containers"])


def empty_mailbox_leaf_folders(
    store: PostgresMailStore,
    *,
    mailbox_id: str,
    limit: int,
) -> list[CleanupItem]:
    rows = store.connection.execute(
        """
        SELECT id, folder_path, folder_role, created_at
        FROM millie_mailbox_folders mf
        WHERE mailbox_id = %s
          AND folder_role = 'custom'
          AND NOT EXISTS (
              SELECT 1
              FROM millie_mailbox_messages mm
              WHERE mm.folder_id = mf.id
          )
          AND NOT EXISTS (
              SELECT 1
              FROM millie_mailbox_folders child
              WHERE child.mailbox_id = mf.mailbox_id
                AND child.folder_path LIKE mf.folder_path || '/%%'
          )
          AND NOT EXISTS (
              SELECT 1
              FROM millie_source_mailbox_bindings b
              WHERE b.target_folder_id = mf.id
          )
        ORDER BY length(folder_path) DESC, folder_path
        LIMIT %s
        """,
        (mailbox_id, bounded_limit(limit)),
    ).fetchall()
    return [
        CleanupItem(str(row[0]), str(row[1]), {"folder_role": row[2], "created_at": row[3]})
        for row in rows
    ]


def empty_source_leaf_folders(store: PostgresMailStore, *, limit: int) -> list[CleanupItem]:
    rows = store.connection.execute(
        """
        SELECT f.id, f.folder_path, s.display_name, s.source_uri
        FROM mail_folders f
        JOIN mail_sources s ON s.id = f.source_id
        WHERE NOT EXISTS (
              SELECT 1
              FROM mail_message_folders mmf
              WHERE mmf.folder_id = f.id
          )
          AND NOT EXISTS (
              SELECT 1
              FROM mail_folders child
              WHERE child.source_id = f.source_id
                AND child.folder_path LIKE f.folder_path || '/%%'
          )
          AND NOT EXISTS (
              SELECT 1
              FROM millie_source_mailbox_bindings b
              WHERE b.source_folder_id = f.id
          )
        ORDER BY s.display_name NULLS LAST, length(f.folder_path) DESC, f.folder_path
        LIMIT %s
        """,
        (bounded_limit(limit),),
    ).fetchall()
    return [
        CleanupItem(
            str(row[0]),
            str(row[1]),
            {"source_display_name": row[2], "source_uri": row[3]},
        )
        for row in rows
    ]


def blank_address_rows(store: PostgresMailStore, *, limit: int) -> list[CleanupItem]:
    rows = store.connection.execute(
        """
        SELECT id, message_id, role, ordinal
        FROM mail_message_addresses
        WHERE btrim(coalesce(display_name, '')) = ''
          AND btrim(coalesce(email_address, '')) = ''
          AND btrim(coalesce(raw_value, '')) = ''
        ORDER BY message_id, role, ordinal
        LIMIT %s
        """,
        (bounded_limit(limit),),
    ).fetchall()
    return [
        CleanupItem(
            str(row[0]),
            f"{row[2]}[{row[3]}]",
            {"message_id": row[1], "role": row[2], "ordinal": row[3]},
        )
        for row in rows
    ]


def empty_import_jobs(store: PostgresMailStore, *, limit: int) -> list[CleanupItem]:
    rows = store.connection.execute(
        """
        SELECT j.id, j.mode, j.status, s.display_name, s.source_uri
        FROM mail_import_jobs j
        LEFT JOIN mail_sources s ON s.id = j.source_id
        WHERE j.status <> 'running'
          AND NOT EXISTS (
              SELECT 1
              FROM mail_messages m
              WHERE m.import_job_id = j.id
          )
        ORDER BY j.started_at NULLS LAST, j.id
        LIMIT %s
        """,
        (bounded_limit(limit),),
    ).fetchall()
    return [
        CleanupItem(
            str(row[0]),
            f"{row[1]}:{row[2]}",
            {"source_display_name": row[3], "source_uri": row[4]},
        )
        for row in rows
    ]


def empty_sources(store: PostgresMailStore, *, limit: int) -> list[CleanupItem]:
    rows = store.connection.execute(
        """
        SELECT s.id, s.display_name, s.source_type, s.source_uri
        FROM mail_sources s
        WHERE NOT EXISTS (SELECT 1 FROM mail_messages m WHERE m.source_id = s.id)
          AND NOT EXISTS (SELECT 1 FROM mail_folders f WHERE f.source_id = s.id)
          AND NOT EXISTS (SELECT 1 FROM mail_source_message_aliases a WHERE a.source_id = s.id)
          AND NOT EXISTS (SELECT 1 FROM mail_source_cursors c WHERE c.source_id = s.id)
          AND NOT EXISTS (SELECT 1 FROM mail_import_jobs j WHERE j.source_id = s.id)
          AND NOT EXISTS (SELECT 1 FROM millie_sync_health h WHERE h.source_id = s.id)
          AND NOT EXISTS (SELECT 1 FROM millie_source_mailbox_bindings b WHERE b.source_id = s.id)
        ORDER BY s.display_name NULLS LAST, s.source_uri
        LIMIT %s
        """,
        (bounded_limit(limit),),
    ).fetchall()
    return [
        CleanupItem(
            str(row[0]),
            str(row[1] or row[3]),
            {"source_type": row[2], "source_uri": row[3]},
        )
        for row in rows
    ]


def empty_derived_part_containers(store: PostgresMailStore, *, limit: int) -> list[CleanupItem]:
    rows = store.connection.execute(
        """
        SELECT p.id, p.message_id, p.part_path, p.content_type
        FROM mail_message_parts p
        WHERE p.is_container = TRUE
          AND p.is_body = FALSE
          AND p.is_attachment = FALSE
          AND p.is_inline = FALSE
          AND p.is_embedded_message = FALSE
          AND p.text_content IS NULL
          AND p.binary_content IS NULL
          AND NOT EXISTS (
              SELECT 1
              FROM mail_message_parts child
              WHERE child.parent_part_id = p.id
          )
        ORDER BY p.message_id, p.part_path
        LIMIT %s
        """,
        (bounded_limit(limit),),
    ).fetchall()
    return [
        CleanupItem(
            str(row[0]),
            str(row[2]),
            {"message_id": row[1], "content_type": row[3]},
        )
        for row in rows
    ]


def delete_mailbox_leaf_folders(
    store: PostgresMailStore,
    *,
    mailbox_id: str,
    max_passes: int,
) -> int:
    deleted = 0
    for _ in range(max(max_passes, 1)):
        rows = store.connection.execute(
            """
            DELETE FROM millie_mailbox_folders mf
            WHERE mailbox_id = %s
              AND folder_role = 'custom'
              AND NOT EXISTS (
                  SELECT 1
                  FROM millie_mailbox_messages mm
                  WHERE mm.folder_id = mf.id
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM millie_mailbox_folders child
                  WHERE child.mailbox_id = mf.mailbox_id
                    AND child.folder_path LIKE mf.folder_path || '/%%'
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM millie_source_mailbox_bindings b
                  WHERE b.target_folder_id = mf.id
              )
            RETURNING id, folder_path
            """,
            (mailbox_id,),
        ).fetchall()
        deleted += len(rows)
        if not rows:
            break
    return deleted


def delete_source_leaf_folders(store: PostgresMailStore, *, max_passes: int) -> int:
    deleted = 0
    for _ in range(max(max_passes, 1)):
        rows = store.connection.execute(
            """
            DELETE FROM mail_folders f
            WHERE NOT EXISTS (
                  SELECT 1
                  FROM mail_message_folders mmf
                  WHERE mmf.folder_id = f.id
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM mail_folders child
                  WHERE child.source_id = f.source_id
                    AND child.folder_path LIKE f.folder_path || '/%%'
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM millie_source_mailbox_bindings b
                  WHERE b.source_folder_id = f.id
              )
            RETURNING id, folder_path
            """
        ).fetchall()
        deleted += len(rows)
        if not rows:
            break
    return deleted


def delete_blank_addresses(store: PostgresMailStore) -> int:
    return int(
        store.connection.execute(
            """
            DELETE FROM mail_message_addresses
            WHERE btrim(coalesce(display_name, '')) = ''
              AND btrim(coalesce(email_address, '')) = ''
              AND btrim(coalesce(raw_value, '')) = ''
            """
        ).rowcount
        or 0
    )


def delete_empty_import_jobs(store: PostgresMailStore) -> int:
    return int(
        store.connection.execute(
            """
            DELETE FROM mail_import_jobs j
            WHERE j.status <> 'running'
              AND NOT EXISTS (
                  SELECT 1
                  FROM mail_messages m
                  WHERE m.import_job_id = j.id
              )
            """
        ).rowcount
        or 0
    )


def delete_empty_sources(store: PostgresMailStore) -> int:
    return int(
        store.connection.execute(
            """
            DELETE FROM mail_sources s
            WHERE NOT EXISTS (SELECT 1 FROM mail_messages m WHERE m.source_id = s.id)
              AND NOT EXISTS (SELECT 1 FROM mail_folders f WHERE f.source_id = s.id)
              AND NOT EXISTS (SELECT 1 FROM mail_source_message_aliases a WHERE a.source_id = s.id)
              AND NOT EXISTS (SELECT 1 FROM mail_source_cursors c WHERE c.source_id = s.id)
              AND NOT EXISTS (SELECT 1 FROM mail_import_jobs j WHERE j.source_id = s.id)
              AND NOT EXISTS (SELECT 1 FROM millie_sync_health h WHERE h.source_id = s.id)
              AND NOT EXISTS (SELECT 1 FROM millie_source_mailbox_bindings b WHERE b.source_id = s.id)
            """
        ).rowcount
        or 0
    )


def create_cleanup_run(
    store: PostgresMailStore,
    *,
    settings: dict[str, str],
    args: argparse.Namespace,
) -> str:
    run_id = str(uuid.uuid4())
    store.connection.execute(
        """
        INSERT INTO millie_automation_runs (
            id, run_type, automation_level, status, trigger_source,
            started_at, metadata_json
        )
        VALUES (%s, 'system', %s, 'running', 'cli', now(), %s)
        """,
        (
            run_id,
            automation_level(settings),
            Jsonb(
                {
                    "tool": "millie_cleanup_empty",
                    "execute_mailbox_folders": bool(args.execute_mailbox_folders),
                    "execute_source_folders": bool(args.execute_source_folders),
                    "execute_blank_addresses": bool(args.execute_blank_addresses),
                    "execute_empty_import_jobs": bool(args.execute_empty_import_jobs),
                    "execute_empty_sources": bool(args.execute_empty_sources),
                }
            ),
        ),
    )
    return run_id


def complete_cleanup_run(
    store: PostgresMailStore,
    *,
    run_id: str,
    summary: CleanupSummary,
) -> None:
    actions_applied = (
        summary.deleted_mailbox_leaf_folders
        + summary.deleted_source_leaf_folders
        + summary.deleted_blank_addresses
        + summary.deleted_empty_import_jobs
        + summary.deleted_empty_sources
    )
    store.connection.execute(
        """
        UPDATE millie_automation_runs
        SET status = 'completed',
            completed_at = now(),
            actions_applied = %s,
            metadata_json = metadata_json || %s
        WHERE id = %s
        """,
        (
            actions_applied,
            Jsonb({"cleanup_summary": summary_dict(summary)}),
            run_id,
        ),
    )
    store.connection.execute(
        """
        INSERT INTO millie_automation_audit_log (
            id, run_id, action_type, automation_level, status, after_json
        )
        VALUES (%s, %s, 'custom', 'auto_internal', 'applied', %s)
        """,
        (str(uuid.uuid4()), run_id, Jsonb({"cleanup_summary": summary_dict(summary)})),
    )


def summary_dict(summary: CleanupSummary) -> dict[str, object]:
    return {
        "mode": summary.mode,
        "deleted_mailbox_leaf_folders": summary.deleted_mailbox_leaf_folders,
        "deleted_source_leaf_folders": summary.deleted_source_leaf_folders,
        "deleted_blank_addresses": summary.deleted_blank_addresses,
        "deleted_empty_import_jobs": summary.deleted_empty_import_jobs,
        "deleted_empty_sources": summary.deleted_empty_sources,
    }


def bounded_limit(limit: int) -> int:
    return max(int(limit), 1)


def print_summary(
    summary: CleanupSummary,
    samples: dict[str, list[CleanupItem]],
    *,
    execute_allowed: bool,
    run_id: str,
) -> None:
    print("MILLIE empty cleanup")
    print(f"Mode: {summary.mode}")
    print(f"Internal execution allowed: {execute_allowed}")
    if run_id:
        print(f"Run id: {run_id}")
    print(f"Empty mailbox leaf folders sampled: {summary.mailbox_leaf_folders}")
    print(f"Empty source leaf folders sampled: {summary.source_leaf_folders}")
    print(f"Blank address rows sampled: {summary.blank_addresses}")
    print(f"Empty import jobs sampled: {summary.empty_import_jobs}")
    print(f"Empty source definitions sampled: {summary.empty_sources}")
    print(f"Empty derived MIME containers sampled: {summary.empty_part_containers}")
    if summary.mode == "execute":
        print(f"Deleted mailbox leaf folders: {summary.deleted_mailbox_leaf_folders}")
        print(f"Deleted source leaf folders: {summary.deleted_source_leaf_folders}")
        print(f"Deleted blank address rows: {summary.deleted_blank_addresses}")
        print(f"Deleted empty import jobs: {summary.deleted_empty_import_jobs}")
        print(f"Deleted empty source definitions: {summary.deleted_empty_sources}")
    for name, rows in samples.items():
        if rows:
            print(f"Sample {name}:")
            for item in rows[:5]:
                detail = ", ".join(f"{key}={value}" for key, value in item.details.items() if value is not None)
                print(f"  {item.label} ({detail})")


if __name__ == "__main__":
    raise SystemExit(main())
