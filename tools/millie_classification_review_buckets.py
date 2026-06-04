#!/usr/bin/env python3
"""Materialize proposed classification triage buckets as MILLIE review folders."""

from __future__ import annotations

import argparse
import re
import sys
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from millie.importing.models import stable_id  # noqa: E402
from millie.service.auth import default_service_login  # noqa: E402
from millie.settings_loader import load_local_settings  # noqa: E402
from millie.storage.postgres_store import PostgresMailStore, normalize_mailbox_path  # noqa: E402


APPROVE_LIKELY = "Approve Likely"
REJECT_LIKELY = "Reject Likely"
NEEDS_SKIM = "Needs Skim"

SAFE_WORK_DOMAINS = {
    "4s-llc.com",
    "apexsystems.com",
    "barracuda.com",
    "carolinaos.com",
    "charlestonent.com",
    "cisco.com",
    "cnb.llc",
    "commonwealthfg.com",
    "connectsolutions.net",
    "core4ce.com",
    "daarchitects.com",
    "ecstech.com",
    "eclinicalworks.com",
    "egroup-us.com",
    "gomerge.com",
    "idctechnologies.com",
    "implementingtech.com",
    "is-t.net",
    "katalystng.com",
    "securityrs.com",
    "talloak.com",
    "telecochas.com",
    "thecentricsgroup.com",
    "varrow.com",
    "webex.com",
}

LIKELY_NOT_WORK_DOMAINS = {
    "aircanada.ca",
    "bankofamerica.com",
    "campbrain.com",
    "email.openai.com",
    "email.wwe.com",
    "foundrae.com",
    "google.com",
    "kidscommunitypdx.org",
    "linkedin.com",
    "pps.net",
    "read.ai",
    "redtailedhawksflyingclub.org",
    "scouting.org",
    "tldrnewsletter.com",
    "wwe.com",
    "ziprecruiter.com",
}

MARKETING_OR_NEWSLETTER_DOMAINS = {
    "beamtlc.com",
    "celebvm.com",
    "em.target.com",
    "e.shutterfly.com",
    "humblebundle.com",
    "mailer.humblebundle.com",
    "onepeloton.com",
    "shutterfly.com",
    "tldrnewsletter.com",
}

FALSE_POSITIVE_BULK_DOMAINS = {
    "aircanada.ca",
    "bankofamerica.com",
    "cnb.llc",
    "clarktribe.com",
    "gmail.com",
    "porkbun.com",
    "pps.net",
    "scouting.org",
}

FALSE_POSITIVE_BULK_SUBJECT_TERMS = {
    "bank account",
    "booking confirmation",
    "order - thank you",
    "recipient",
    "rsvp",
    "senior night",
}


@dataclass(frozen=True, slots=True)
class ReviewItem:
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
    evidence: dict[str, Any]


@dataclass(frozen=True, slots=True)
class BucketedItem:
    item: ReviewItem
    bucket: str
    why: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create internal MILLIE review folders for proposed classification triage. "
            "This does not approve, reject, apply, delete, or write to source providers."
        )
    )
    parser.add_argument("--apply", action="store_true", help="Map messages into review folders.")
    parser.add_argument(
        "--clear-existing",
        action="store_true",
        help="Before apply, remove existing mailbox message mappings under the review root.",
    )
    parser.add_argument("--root", default="Review/Classification", help="Review folder root.")
    parser.add_argument("--mailbox", default="", help="MILLIE mailbox address. Defaults to geon@<service domain>.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum proposed classifications to inspect. 0 means all.")
    parser.add_argument("--classification-id", action="append", default=[], help="Limit to exact classification id(s).")
    parser.add_argument(
        "--no-domain-folders",
        action="store_true",
        help="Do not create target/domain subfolders. Bucket and target folders are still created.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_local_settings()
    settings = config["settings"]
    if (settings.get("database_mode") or "").lower() != "postgres":
        raise SystemExit("millie_classification_review_buckets currently requires database_mode=postgres.")

    with PostgresMailStore.connect(settings) as store:
        store.initialize()
        mailbox_address = args.mailbox or default_service_login(settings, "geon")
        mailbox = store.mailbox_by_address(mailbox_address)
        if mailbox is None:
            raise SystemExit(f"Mailbox not found: {mailbox_address}")
        mailbox_id = str(mailbox["id"])
        items = [bucket_item(item) for item in load_proposed_items(store, args)]
        summary = Counter(item.bucket for item in items)
        path_counts = Counter()
        for item in items:
            for path in review_paths(item, root=args.root, include_domain=not args.no_domain_folders):
                path_counts[path] += 1

        print("MILLIE classification review buckets")
        print(f"Mode: {'apply' if args.apply else 'dry-run'}")
        print(f"Mailbox: {mailbox_address}")
        print(f"Proposed classifications: {len(items)}")
        for bucket, count in summary.most_common():
            print(f"  {bucket}: {count}")
        print("Top review folders:")
        for path, count in path_counts.most_common(25):
            print(f"  {count}: {path}")

        if not args.apply:
            print("Dry run only. Re-run with --apply to map messages into review folders.")
            return 0

        if args.clear_existing:
            cleared = clear_existing_review_mappings(store, mailbox_id=mailbox_id, root=args.root)
            print(f"Cleared existing review mappings: {cleared}")

        mapped = map_review_folders(
            store,
            mailbox_id=mailbox_id,
            bucketed_items=items,
            root=args.root,
            include_domain=not args.no_domain_folders,
        )
        write_summary_audit(
            store,
            root=args.root,
            counts=summary,
            mapped=mapped,
            clear_existing=args.clear_existing,
            include_domain=not args.no_domain_folders,
        )
        store.connection.commit()
        print(f"Mapped review folder rows: {mapped}")
    return 0


def load_proposed_items(store: PostgresMailStore, args: argparse.Namespace) -> list[ReviewItem]:
    where = ["c.status = 'proposed'"]
    params: list[object] = []
    if args.classification_id:
        where.append(f"c.id IN ({placeholders(args.classification_id)})")
        params.extend(args.classification_id)
    limit_sql = ""
    if args.limit and args.limit > 0:
        limit_sql = "LIMIT %s"
        params.append(args.limit)

    rows = store.connection.execute(
        f"""
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
        WHERE {" AND ".join(where)}
        ORDER BY c.confidence DESC, c.created_at
        {limit_sql}
        """,
        tuple(params),
    ).fetchall()
    return [
        ReviewItem(
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
    ]


def bucket_item(item: ReviewItem) -> BucketedItem:
    subject = item.subject.lower()
    folders = item.source_folders.lower()
    from_text = item.from_text.lower()
    domain = item.sender_domain.lower()
    year = year_from_target(item.target_folder_path)

    if item.kind == "spam" and item.value == "likely_spam":
        return BucketedItem(item, APPROVE_LIKELY, "already in provider spam/junk; non-destructive hold")
    if item.kind == "trash" and item.value == "likely_trash":
        return BucketedItem(item, APPROVE_LIKELY, "trash-like proposal; non-destructive hold")
    if item.kind == "spam" and item.value == "possible_spam":
        if "spam" in folders or "junk" in folders:
            return BucketedItem(item, APPROVE_LIKELY, "spam/bulk hint and already in spam/junk source folder")
        if domain_matches(domain, MARKETING_OR_NEWSLETTER_DOMAINS):
            return BucketedItem(item, APPROVE_LIKELY, "marketing/newsletter bulk sender")
        if domain_matches(domain, FALSE_POSITIVE_BULK_DOMAINS) or any(term in subject for term in FALSE_POSITIVE_BULK_SUBJECT_TERMS):
            return BucketedItem(item, REJECT_LIKELY, "bulk/spam keyword appears false-positive")
        return BucketedItem(item, NEEDS_SKIM, "low-confidence possible spam/bulk")
    if item.kind == "folder" and item.value == "work":
        if domain_matches(domain, SAFE_WORK_DOMAINS):
            return BucketedItem(item, APPROVE_LIKELY, "known work/IT domain")
        if domain_matches(domain, LIKELY_NOT_WORK_DOMAINS):
            return BucketedItem(item, REJECT_LIKELY, "known non-work/newsletter/personal domain")
        if not domain and year <= 2018 and (
            "ppcp" in from_text or "exchange administrative group" in from_text or "/cn=recipients" in from_text
        ):
            return BucketedItem(item, APPROVE_LIKELY, "legacy Exchange/PPCP-style work archive")
        if "inbox/ldcs" in folders or "inbox/daas" in folders:
            return BucketedItem(item, APPROVE_LIKELY, "already sourced from work-labeled archive folder")
        if year >= 2024:
            return BucketedItem(item, NEEDS_SKIM, "recent work proposal from uncategorized domain")
        return BucketedItem(item, NEEDS_SKIM, "older low-confidence work keyword")
    return BucketedItem(item, NEEDS_SKIM, "unrecognized proposal type")


def review_paths(bucketed: BucketedItem, *, root: str, include_domain: bool) -> list[str]:
    target = bucketed.item.target_folder_path or f"{bucketed.item.kind}/{bucketed.item.value}"
    paths = [
        normalize_mailbox_path(root),
        normalize_mailbox_path(f"{root}/{bucketed.bucket}"),
        normalize_mailbox_path(f"{root}/{bucketed.bucket}/{target}"),
    ]
    if include_domain:
        paths.append(normalize_mailbox_path(f"{root}/{bucketed.bucket}/{target}/{domain_segment(bucketed.item.sender_domain)}"))
    return dedupe_preserve_order(paths)


def map_review_folders(
    store: PostgresMailStore,
    *,
    mailbox_id: str,
    bucketed_items: list[BucketedItem],
    root: str,
    include_domain: bool,
) -> int:
    messages_by_path: dict[str, set[str]] = {}
    for bucketed in bucketed_items:
        for path in review_paths(bucketed, root=root, include_domain=include_domain):
            messages_by_path.setdefault(path, set()).add(bucketed.item.message_id)

    folder_ids: dict[str, str] = {}
    for path in sorted(messages_by_path, key=lambda item: (item.count("/"), item)):
        folder_ids[path] = store.ensure_mailbox_folder(mailbox_id, path)

    folder_placeholders = placeholders(list(folder_ids.values()))
    max_uid_rows = store.connection.execute(
        f"""
        SELECT folder_id, coalesce(max(imap_uid), 0)
        FROM millie_mailbox_messages
        WHERE mailbox_id = %s
          AND folder_id IN ({folder_placeholders})
        GROUP BY folder_id
        """,
        (mailbox_id, *folder_ids.values()),
    ).fetchall()
    next_uid = {str(row[0]): int(row[1] or 0) + 1 for row in max_uid_rows}
    for folder_id in folder_ids.values():
        next_uid.setdefault(folder_id, 1)

    existing_rows = store.connection.execute(
        f"""
        SELECT folder_id, message_id
        FROM millie_mailbox_messages
        WHERE mailbox_id = %s
          AND folder_id IN ({folder_placeholders})
        """,
        (mailbox_id, *folder_ids.values()),
    ).fetchall()
    existing = {(str(row[0]), str(row[1])) for row in existing_rows}

    rows: list[tuple[str, str, str, int, str]] = []
    for path, message_ids in messages_by_path.items():
        folder_id = folder_ids[path]
        for message_id in sorted(message_ids):
            if (folder_id, message_id) in existing:
                continue
            uid = next_uid[folder_id]
            next_uid[folder_id] = uid + 1
            rows.append(
                (
                    stable_id("millie_mailbox_message", mailbox_id, folder_id, message_id),
                    mailbox_id,
                    folder_id,
                    uid,
                    message_id,
                )
            )

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


def clear_existing_review_mappings(store: PostgresMailStore, *, mailbox_id: str, root: str) -> int:
    normalized_root = normalize_mailbox_path(root)
    row = store.connection.execute(
        """
        WITH review_folders AS (
            SELECT id
            FROM millie_mailbox_folders
            WHERE mailbox_id = %s
              AND (folder_path = %s OR folder_path LIKE %s)
        ),
        deleted AS (
            DELETE FROM millie_mailbox_messages
            WHERE mailbox_id = %s
              AND folder_id IN (SELECT id FROM review_folders)
            RETURNING id
        )
        SELECT count(*) FROM deleted
        """,
        (mailbox_id, normalized_root, f"{normalized_root}/%", mailbox_id),
    ).fetchone()
    return int(row[0] or 0)


def write_summary_audit(
    store: PostgresMailStore,
    *,
    root: str,
    counts: Counter[str],
    mapped: int,
    clear_existing: bool,
    include_domain: bool,
) -> None:
    store.connection.execute(
        """
        INSERT INTO millie_automation_audit_log (
            id, action_type, automation_level, status, after_json, metadata_json
        )
        VALUES (%s, 'custom', 'review', 'applied', %s, %s)
        """,
        (
            str(uuid.uuid4()),
            Jsonb(
                {
                    "review_folder_root": normalize_mailbox_path(root),
                    "bucket_counts": dict(counts),
                    "mapped_rows": mapped,
                    "clear_existing": clear_existing,
                    "include_domain": include_domain,
                }
            ),
            Jsonb({"managed_by": "millie_classification_review_buckets"}),
        ),
    )


def domain_matches(domain: str, choices: set[str]) -> bool:
    normalized = domain.lower().strip()
    if not normalized:
        return False
    return any(normalized == choice or normalized.endswith(f".{choice}") for choice in choices)


def year_from_target(target: str) -> int:
    try:
        return int(str(target).rsplit("/", 1)[-1])
    except ValueError:
        return 0


def domain_segment(domain: str) -> str:
    value = domain.lower().strip() or "Missing Sender Domain"
    return clean_path_part(value)


def clean_path_part(value: str) -> str:
    cleaned = re.sub(r"[\x00-\x1f/\\]+", " ", str(value)).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or "Unknown"


def dedupe_preserve_order(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def placeholders(values: list[object]) -> str:
    if not values:
        raise ValueError("values must not be empty")
    return ", ".join(["%s"] * len(values))


if __name__ == "__main__":
    raise SystemExit(main())
