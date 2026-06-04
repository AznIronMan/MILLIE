#!/usr/bin/env python3
"""Materialize MILLIE's primary internal folder taxonomy."""

from __future__ import annotations

import argparse
import re
import sys
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from psycopg.types.json import Jsonb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from millie.importing.models import stable_id  # noqa: E402
from millie.service.auth import default_service_login  # noqa: E402
from millie.settings_loader import load_local_settings  # noqa: E402
from millie.storage.postgres_store import PostgresMailStore, normalize_mailbox_path  # noqa: E402
from tools.millie_classification_review_buckets import APPROVE_LIKELY, bucket_item  # noqa: E402


PRIMARY_ROOTS = (
    "Archive",
    "CNB",
    "Personal",
    "Important",
    "Receipts",
    "Trash_Hold",
)

ARCHIVE_SUBROOTS = (
    "Archive/Personal",
    "Archive/Work",
    "Archive/Education",
    "Archive/Misc",
)

LEGACY_CATEGORY_PREFIXES = (
    "Archive/Receipts",
    "Archive/Taxes",
    "Archive/Travel",
    "Hold/Reevaluate",
    "trash-hold",
)

TRASH_SOURCE_TERMS = ("trash", "deleted")
SPAM_SOURCE_TERMS = ("spam", "junk")
EDUCATION_SOURCE_TERMS = ("education", "school", "ecpi", "blackboard", "canvas", "math ")
TAX_SOURCE_TERMS = ("tax", "irs", "turbotax")
RECEIPT_SOURCE_TERMS = ("receipt", "receipts", "invoice", "invoices", "order")
TRAVEL_SOURCE_TERMS = ("travel", "trip", "flight", "hotel", "airline", "rental car")
WORK_SOURCE_TERMS = ("work", "job", "employer", "former_employers", "lintech")


@dataclass(frozen=True, slots=True)
class TaxonomyMapping:
    message_id: str
    folder_path: str
    reason: str


@dataclass(frozen=True, slots=True)
class ProposedClassification:
    classification_id: str
    message_id: str
    kind: str
    value: str
    target_folder_path: str
    confidence: float
    subject: str
    from_text: str
    sender_domain: str
    source_folders: str
    reason: str
    evidence: dict[str, object]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create MILLIE's primary internal folder taxonomy and map existing messages "
            "into roll-up folders. This does not touch source providers."
        )
    )
    parser.add_argument("--apply", action="store_true", help="Write internal folder mappings.")
    parser.add_argument(
        "--clear-existing",
        action="store_true",
        help="Clear existing mappings under managed taxonomy roots before remapping.",
    )
    parser.add_argument(
        "--retire-legacy",
        action="store_true",
        help="Remove old internal facade mappings such as Archive/Receipts/* and trash-hold.",
    )
    parser.add_argument(
        "--include-proposed-approve-likely",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Map proposed classifications only when the review-bucket triage says Approve Likely.",
    )
    parser.add_argument("--mailbox", default="", help="MILLIE mailbox address. Defaults to geon@<service domain>.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_local_settings()
    settings = config["settings"]
    if (settings.get("database_mode") or "").lower() != "postgres":
        raise SystemExit("millie_taxonomy_folders currently requires database_mode=postgres.")

    with PostgresMailStore.connect(settings) as store:
        store.initialize()
        mailbox_address = args.mailbox or default_service_login(settings, "geon")
        mailbox = store.mailbox_by_address(mailbox_address)
        if mailbox is None:
            raise SystemExit(f"Mailbox not found: {mailbox_address}")
        mailbox_id = str(mailbox["id"])
        mappings = build_mappings(store, include_proposed_approve_likely=args.include_proposed_approve_likely)
        folder_counts = Counter(mapping.folder_path for mapping in mappings)
        reason_counts = Counter(mapping.reason for mapping in mappings)

        print("MILLIE taxonomy folders")
        print(f"Mode: {'apply' if args.apply else 'dry-run'}")
        print(f"Mailbox: {mailbox_address}")
        print(f"Unique message/folder mappings planned: {len(mappings)}")
        print("Top folders:")
        for folder, count in folder_counts.most_common(30):
            print(f"  {count}: {folder}")
        print("Reasons:")
        for reason, count in reason_counts.most_common():
            print(f"  {count}: {reason}")

        if not args.apply:
            print("Dry run only. Re-run with --apply to write internal folder mappings.")
            return 0

        cleared = 0
        retired = 0
        if args.clear_existing:
            cleared = clear_existing_taxonomy_mappings(store, mailbox_id=mailbox_id)
            print(f"Cleared existing managed taxonomy mappings: {cleared}")
        mapped = write_mappings(store, mailbox_id=mailbox_id, mappings=mappings)
        if args.retire_legacy:
            retired = retire_legacy_mappings(store, mailbox_id=mailbox_id)
            print(f"Retired legacy taxonomy mappings: {retired}")
        write_audit(
            store,
            mapped=mapped,
            cleared=cleared,
            retired=retired,
            folder_counts=folder_counts,
            reason_counts=reason_counts,
        )
        store.connection.commit()
        print(f"Mapped taxonomy rows: {mapped}")
    return 0


def build_mappings(
    store: PostgresMailStore,
    *,
    include_proposed_approve_likely: bool,
) -> list[TaxonomyMapping]:
    result: dict[tuple[str, str], TaxonomyMapping] = {}
    for mapping in classification_mappings(store, include_proposed_approve_likely=include_proposed_approve_likely):
        add_rollup_mapping(result, mapping)
    for mapping in source_folder_mappings(store):
        add_rollup_mapping(result, mapping)
    for root in PRIMARY_ROOTS + ARCHIVE_SUBROOTS:
        result.setdefault(("", root), TaxonomyMapping(message_id="", folder_path=root, reason="ensure_folder"))
    return [mapping for key, mapping in sorted(result.items(), key=lambda item: (item[1].folder_path, item[1].message_id)) if mapping.message_id]


def classification_mappings(
    store: PostgresMailStore,
    *,
    include_proposed_approve_likely: bool,
) -> list[TaxonomyMapping]:
    mappings: list[TaxonomyMapping] = []
    rows = load_classification_rows(store)
    proposed = load_proposed_classifications_for_bucket(store) if include_proposed_approve_likely else {}
    for row in rows:
        status = str(row["status"])
        if status == "proposed":
            proposed_item = proposed.get(str(row["classification_id"]))
            if proposed_item is None or bucket_item(proposed_item).bucket != APPROVE_LIKELY:
                continue
        target = taxonomy_target_for_classification(row)
        if not target:
            continue
        mappings.append(
            TaxonomyMapping(
                message_id=str(row["message_id"]),
                folder_path=target,
                reason=f"classification:{row['kind']}:{row['value']}:{status}",
            )
        )
    return mappings


def load_classification_rows(store: PostgresMailStore) -> list[dict[str, object]]:
    rows = store.connection.execute(
        """
        SELECT
            id,
            message_id,
            status,
            classification_kind,
            classification_value,
            coalesce(target_folder_path, '')
        FROM millie_message_classifications
        WHERE status IN ('applied', 'approved', 'proposed')
        """
    ).fetchall()
    return [
        {
            "classification_id": row[0],
            "message_id": row[1],
            "status": row[2],
            "kind": row[3],
            "value": row[4],
            "target_folder_path": row[5],
        }
        for row in rows
    ]


def load_proposed_classifications_for_bucket(store: PostgresMailStore) -> dict[str, ProposedClassification]:
    rows = store.connection.execute(
        """
        WITH first_from AS (
            SELECT DISTINCT ON (message_id)
                message_id,
                coalesce(email_address, raw_value, '') AS from_text,
                lower(split_part(coalesce(email_address, ''), '@', 2)) AS sender_domain
            FROM mail_message_addresses
            WHERE role = 'from'
            ORDER BY message_id, ordinal
        ),
        source_folders AS (
            SELECT
                mf.message_id,
                string_agg(DISTINCT f.folder_path, ' | ' ORDER BY f.folder_path) AS folders
            FROM mail_message_folders mf
            JOIN mail_folders f ON f.id = mf.folder_id
            GROUP BY mf.message_id
        )
        SELECT
            c.id,
            c.message_id,
            c.classification_kind,
            c.classification_value,
            coalesce(c.target_folder_path, ''),
            c.confidence,
            coalesce(m.subject, ''),
            coalesce(first_from.from_text, ''),
            coalesce(first_from.sender_domain, ''),
            coalesce(source_folders.folders, ''),
            coalesce(c.reason_text, ''),
            c.evidence_json
        FROM millie_message_classifications c
        JOIN mail_messages m ON m.id = c.message_id
        LEFT JOIN first_from ON first_from.message_id = c.message_id
        LEFT JOIN source_folders ON source_folders.message_id = c.message_id
        WHERE c.status = 'proposed'
        """
    ).fetchall()
    return {
        str(row[0]): ProposedClassification(
            classification_id=str(row[0]),
            message_id=str(row[1]),
            kind=str(row[2]),
            value=str(row[3]),
            target_folder_path=str(row[4] or ""),
            confidence=float(row[5] or 0),
            subject=str(row[6] or ""),
            from_text=str(row[7] or ""),
            sender_domain=str(row[8] or ""),
            source_folders=str(row[9] or ""),
            reason=str(row[10] or ""),
            evidence=dict(row[11] or {}),
        )
        for row in rows
    }


def taxonomy_target_for_classification(row: dict[str, object]) -> str:
    kind = str(row["kind"])
    value = str(row["value"])
    target = str(row["target_folder_path"] or "")
    year = year_from_target(target)
    if kind == "folder" and value == "receipts" and year:
        return f"Receipts/{year}"
    if kind == "folder" and value == "taxes" and year:
        return f"Archive/Personal/Taxes/{year}"
    if kind == "folder" and value == "travel" and year:
        return f"Archive/Personal/Travel/{year}"
    if kind == "folder" and value == "work" and year:
        return f"Archive/Work/{year}"
    if kind == "folder" and value == "education" and year:
        return f"Archive/Education/{year}"
    if kind == "spam" and value == "likely_spam":
        return "Trash_Hold/Spam"
    if kind == "spam" and value == "possible_spam":
        return "Trash_Hold/Bulk"
    if kind == "trash" and value == "likely_trash":
        return "Trash_Hold/Trash"
    return ""


def source_folder_mappings(store: PostgresMailStore) -> list[TaxonomyMapping]:
    rows = store.connection.execute(
        """
        SELECT
            mm.message_id,
            mf.folder_path,
            coalesce(m.received_at, m.sent_at, mm.internal_date, now()) AS message_at
        FROM millie_mailbox_messages mm
        JOIN millie_mailbox_folders mf ON mf.id = mm.folder_id
        JOIN mail_messages m ON m.id = mm.message_id
        WHERE mm.is_expunged = false
          AND (
              mf.folder_path LIKE 'Sources/IMAP/%'
              OR mf.folder_path LIKE 'Sources/PST/%'
              OR mf.folder_path IN ('Trash', 'Junk')
          )
        """
    ).fetchall()
    mappings: list[TaxonomyMapping] = []
    for message_id, folder_path, message_at in rows:
        target = taxonomy_target_for_source_folder(str(folder_path), message_at)
        if not target:
            continue
        mappings.append(
            TaxonomyMapping(
                message_id=str(message_id),
                folder_path=target,
                reason=f"source_folder:{source_reason(str(folder_path))}",
            )
        )
    return mappings


def taxonomy_target_for_source_folder(folder_path: str, message_at: object) -> str:
    normalized = normalize_mailbox_path(folder_path)
    lowered = normalized.lower()
    year = year_from_datetime(message_at)
    if not year:
        year = "Undated"

    if lowered == "trash" or lowered == "junk":
        leaf = "Spam" if lowered == "junk" else "Trash"
        return f"Trash_Hold/{leaf}/{year}"

    if normalized.startswith("Sources/IMAP/geoff@cnb.llc/"):
        leaf = normalized.removeprefix("Sources/IMAP/geoff@cnb.llc/")
        leaf_lower = leaf.lower()
        if any(term in leaf_lower for term in TRASH_SOURCE_TERMS):
            return f"Trash_Hold/CNB/Deleted/{year}"
        if any(term in leaf_lower for term in SPAM_SOURCE_TERMS):
            return f"Trash_Hold/CNB/Spam/{year}"
        if leaf_lower.startswith("sent"):
            return f"CNB/Sent/{year}"
        if leaf_lower.startswith("archive"):
            return f"CNB/Archive/{year}"
        if leaf_lower.startswith("inbox"):
            return f"CNB/Inbox/{year}"
        return f"CNB/Misc/{year}"

    if any(term in lowered for term in TRASH_SOURCE_TERMS):
        return f"Trash_Hold/Trash/{year}"

    if any(term in lowered for term in SPAM_SOURCE_TERMS):
        return f"Trash_Hold/Spam/{year}"

    semantic_target = taxonomy_target_for_semantic_source(lowered, year)
    if semantic_target:
        return semantic_target

    if "/[gmail]/important" in lowered:
        return f"Important/{year}"

    if normalized.startswith("Sources/PST/"):
        return taxonomy_target_for_pst_source(normalized, year)

    if normalized.startswith("Sources/IMAP/aznblusuazn@me.com/"):
        return f"Personal/iCloud/{year}"

    if normalized.startswith("Sources/IMAP/gclark82@gmail.com/"):
        return f"Personal/Gmail/{year}"

    if normalized.startswith("Sources/IMAP/geoff@clarktribe.com/Kids"):
        return f"Personal/Family/Kids/{year}"

    if normalized.startswith("Sources/IMAP/geoff@clarktribe.com/Skylar"):
        return f"Personal/Family/Skylar/{year}"

    if normalized.startswith("Sources/IMAP/geoff@clarktribe.com/"):
        leaf = normalized.removeprefix("Sources/IMAP/geoff@clarktribe.com/")
        leaf_lower = leaf.lower()
        if "sent" in leaf_lower:
            return f"Personal/ClarkTribe/Sent/{year}"
        if "archive" in leaf_lower or "[gmail]/all mail" in leaf_lower:
            return f"Archive/Personal/ClarkTribe/{year}"
        if leaf_lower.startswith("inbox"):
            return f"Personal/ClarkTribe/Inbox/{year}"
        return f"Personal/ClarkTribe/Misc/{year}"

    if normalized.startswith("Sources/IMAP/"):
        return f"Personal/Misc/{year}"

    return ""


def taxonomy_target_for_semantic_source(lowered: str, year: str) -> str:
    if any(term in lowered for term in TAX_SOURCE_TERMS):
        return f"Archive/Personal/Taxes/{year}"
    if any(term in lowered for term in RECEIPT_SOURCE_TERMS):
        return f"Receipts/{year}"
    if any(term in lowered for term in TRAVEL_SOURCE_TERMS):
        return f"Archive/Personal/Travel/{year}"
    if any(term in lowered for term in WORK_SOURCE_TERMS):
        return f"Archive/Work/{year}"
    if any(term in lowered for term in EDUCATION_SOURCE_TERMS):
        return f"Archive/Education/{year}"
    return ""


def taxonomy_target_for_pst_source(normalized: str, year: str) -> str:
    source_name = normalized.removeprefix("Sources/PST/").split("/", 1)[0].lower()
    if "csu" in source_name or any(term in source_name for term in EDUCATION_SOURCE_TERMS):
        return f"Archive/Education/{year}"
    if any(term in source_name for term in WORK_SOURCE_TERMS):
        return f"Archive/Work/{year}"
    if "gclark82" in source_name or "gmail" in source_name:
        return f"Archive/Personal/Gmail/{year}"
    return f"Archive/Misc/{year}"


def add_rollup_mapping(result: dict[tuple[str, str], TaxonomyMapping], mapping: TaxonomyMapping) -> None:
    for path in rollup_paths(mapping.folder_path):
        result.setdefault(
            (mapping.message_id, path),
            TaxonomyMapping(message_id=mapping.message_id, folder_path=path, reason=mapping.reason),
        )


def rollup_paths(folder_path: str) -> list[str]:
    normalized = normalize_mailbox_path(folder_path)
    parts = normalized.split("/")
    return ["/".join(parts[: index + 1]) for index in range(len(parts))]


def write_mappings(store: PostgresMailStore, *, mailbox_id: str, mappings: list[TaxonomyMapping]) -> int:
    messages_by_path: dict[str, set[str]] = defaultdict(set)
    for mapping in mappings:
        messages_by_path[normalize_mailbox_path(mapping.folder_path)].add(mapping.message_id)

    folder_ids = ensure_folders(store, mailbox_id=mailbox_id, folder_paths=messages_by_path)
    max_uids = load_max_uids(store, mailbox_id=mailbox_id, folder_ids=folder_ids.values())
    existing = load_existing_mappings(store, mailbox_id=mailbox_id, folder_ids=folder_ids.values())

    rows: list[tuple[str, str, str, int, str]] = []
    for folder_path, message_ids in messages_by_path.items():
        folder_id = folder_ids[folder_path]
        next_uid = max_uids.get(folder_id, 0) + 1
        for message_id in sorted(message_ids):
            if (folder_id, message_id) in existing:
                continue
            rows.append(
                (
                    stable_id("millie_mailbox_message", mailbox_id, folder_id, message_id),
                    mailbox_id,
                    folder_id,
                    next_uid,
                    message_id,
                )
            )
            next_uid += 1
        max_uids[folder_id] = next_uid - 1

    if not rows:
        return 0
    with store.connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO millie_mailbox_messages (
                id, mailbox_id, folder_id, message_id, imap_uid,
                internal_date, flags, is_recent
            )
            SELECT
                %s, %s, %s, m.id, %s,
                coalesce(m.received_at, m.sent_at, now()), ARRAY[]::text[], TRUE
            FROM mail_messages m
            WHERE m.id = %s
            ON CONFLICT(folder_id, message_id) DO NOTHING
            """,
            rows,
        )
    return len(rows)


def ensure_folders(
    store: PostgresMailStore,
    *,
    mailbox_id: str,
    folder_paths: Iterable[str],
) -> dict[str, str]:
    all_paths: set[str] = set()
    for path in folder_paths:
        all_paths.update(rollup_paths(path))
    for root in PRIMARY_ROOTS + ARCHIVE_SUBROOTS:
        all_paths.update(rollup_paths(root))

    rows = []
    for path in sorted(all_paths, key=lambda item: (item.count("/"), item)):
        parent_path = path.rsplit("/", 1)[0] if "/" in path else ""
        rows.append(
            (
                stable_id("millie_folder", mailbox_id, path),
                mailbox_id,
                stable_id("millie_folder", mailbox_id, parent_path) if parent_path else None,
                path,
                path.rsplit("/", 1)[-1],
                True,
                True,
                1000 + path.count("/") * 10,
            )
        )
    with store.connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO millie_mailbox_folders (
                id, mailbox_id, parent_id, folder_path, display_name,
                folder_role, selectable, subscribed, sort_order
            )
            VALUES (%s, %s, %s, %s, %s, 'custom', %s, %s, %s)
            ON CONFLICT (mailbox_id, folder_path) DO UPDATE SET
                parent_id = excluded.parent_id,
                display_name = excluded.display_name,
                selectable = excluded.selectable,
                subscribed = excluded.subscribed,
                updated_at = now()
            """,
            rows,
        )
    return {path: stable_id("millie_folder", mailbox_id, path) for path in all_paths}


def load_max_uids(store: PostgresMailStore, *, mailbox_id: str, folder_ids: Iterable[str]) -> dict[str, int]:
    folder_ids = list(folder_ids)
    if not folder_ids:
        return {}
    rows = store.connection.execute(
        f"""
        SELECT folder_id, coalesce(max(imap_uid), 0)
        FROM millie_mailbox_messages
        WHERE mailbox_id = %s
          AND folder_id IN ({placeholders(folder_ids)})
        GROUP BY folder_id
        """,
        (mailbox_id, *folder_ids),
    ).fetchall()
    return {str(row[0]): int(row[1] or 0) for row in rows}


def load_existing_mappings(store: PostgresMailStore, *, mailbox_id: str, folder_ids: Iterable[str]) -> set[tuple[str, str]]:
    folder_ids = list(folder_ids)
    if not folder_ids:
        return set()
    rows = store.connection.execute(
        f"""
        SELECT folder_id, message_id
        FROM millie_mailbox_messages
        WHERE mailbox_id = %s
          AND folder_id IN ({placeholders(folder_ids)})
        """,
        (mailbox_id, *folder_ids),
    ).fetchall()
    return {(str(row[0]), str(row[1])) for row in rows}


def clear_existing_taxonomy_mappings(store: PostgresMailStore, *, mailbox_id: str) -> int:
    roots = list(PRIMARY_ROOTS) + list(ARCHIVE_SUBROOTS)
    clauses = ["folder_path = %s OR folder_path LIKE %s" for _ in roots]
    params: list[object] = []
    for root in roots:
        params.extend([root, f"{root}/%"])
    row = store.connection.execute(
        f"""
        WITH managed_folders AS (
            SELECT id
            FROM millie_mailbox_folders
            WHERE mailbox_id = %s
              AND ({" OR ".join(clauses)})
        ),
        deleted AS (
            DELETE FROM millie_mailbox_messages
            WHERE mailbox_id = %s
              AND folder_id IN (SELECT id FROM managed_folders)
            RETURNING id
        )
        SELECT count(*) FROM deleted
        """,
        (mailbox_id, *params, mailbox_id),
    ).fetchone()
    return int(row[0] or 0)


def retire_legacy_mappings(store: PostgresMailStore, *, mailbox_id: str) -> int:
    clauses = ["folder_path = %s OR folder_path LIKE %s" for _ in LEGACY_CATEGORY_PREFIXES]
    params: list[object] = []
    for prefix in LEGACY_CATEGORY_PREFIXES:
        params.extend([prefix, f"{prefix}/%"])
    row = store.connection.execute(
        f"""
        WITH legacy_folders AS (
            SELECT id
            FROM millie_mailbox_folders
            WHERE mailbox_id = %s
              AND ({" OR ".join(clauses)})
        ),
        deleted AS (
            DELETE FROM millie_mailbox_messages
            WHERE mailbox_id = %s
              AND folder_id IN (SELECT id FROM legacy_folders)
            RETURNING id
        )
        SELECT count(*) FROM deleted
        """,
        (mailbox_id, *params, mailbox_id),
    ).fetchone()
    return int(row[0] or 0)


def write_audit(
    store: PostgresMailStore,
    *,
    mapped: int,
    cleared: int,
    retired: int,
    folder_counts: Counter[str],
    reason_counts: Counter[str],
) -> None:
    store.connection.execute(
        """
        INSERT INTO millie_automation_audit_log (
            id, action_type, automation_level, status, after_json, metadata_json
        )
        VALUES (%s, 'custom', 'auto_internal', 'applied', %s, %s)
        """,
        (
            str(uuid.uuid4()),
            Jsonb(
                {
                    "mapped_rows": mapped,
                    "cleared_rows": cleared,
                    "retired_legacy_rows": retired,
                    "top_folder_counts": dict(folder_counts.most_common(50)),
                    "reason_counts": dict(reason_counts),
                }
            ),
            Jsonb({"managed_by": "millie_taxonomy_folders"}),
        ),
    )


def source_reason(folder_path: str) -> str:
    if folder_path.startswith("Sources/IMAP/geoff@cnb.llc/"):
        return "cnb"
    if "important" in folder_path.lower():
        return "important"
    if any(term in folder_path.lower() for term in EDUCATION_SOURCE_TERMS):
        return "education"
    if any(term in folder_path.lower() for term in TRASH_SOURCE_TERMS):
        return "trash"
    if any(term in folder_path.lower() for term in SPAM_SOURCE_TERMS):
        return "spam"
    if folder_path.startswith("Sources/PST/"):
        return "pst_archive"
    if folder_path.startswith("Sources/IMAP/geoff@clarktribe.com/"):
        return "clarktribe"
    if folder_path.startswith("Sources/IMAP/aznblusuazn@me.com/"):
        return "icloud"
    if folder_path.startswith("Sources/IMAP/gclark82@gmail.com/"):
        return "gmail"
    if "Kids" in folder_path or "Skylar" in folder_path:
        return "family"
    return "misc"


def year_from_target(target: str) -> str:
    value = str(target or "").rsplit("/", 1)[-1]
    return value if re.fullmatch(r"\d{4}", value) else ""


def year_from_datetime(value: object) -> str:
    if isinstance(value, datetime):
        return str(value.year)
    text = str(value or "")
    return text[:4] if re.fullmatch(r"\d{4}.*", text) else ""


def placeholders(values: Iterable[object]) -> str:
    values = list(values)
    if not values:
        raise ValueError("values must not be empty")
    return ", ".join(["%s"] * len(values))


if __name__ == "__main__":
    raise SystemExit(main())
