#!/usr/bin/env python3
"""Create a remote purge manifest from provider-visible old UIDs."""

from __future__ import annotations

import argparse
import imaplib
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, TypeVar

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from millie.importing.models import stable_id
from millie.importing.sources import ImportSourceError
from millie.settings_loader import load_local_settings
from millie.storage.postgres_store import PostgresMailStore
from tools.millie_imap_bulk_import import (
    account_label,
    connect_account,
    list_account_folders,
    logout,
    quote_imap_astring,
    selected_accounts,
    selected_folders,
    selected_uidvalidity,
    source_uri_for_account_folder,
)
from tools.millie_remote_purge_prep import PurgeCandidate, write_manifest

INTERNALDATE_RE = re.compile(r'\bINTERNALDATE\s+"([^"]+)"', flags=re.IGNORECASE)
UID_RE = re.compile(r"\bUID\s+(\d+)\b", flags=re.IGNORECASE)
IMAP_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class SourceMessageRecord:
    message_id: str
    raw_mime_sha256: str
    source_table: str
    millie_message_at: datetime | None


@dataclass(slots=True)
class VisibleSnapshotStats:
    account: str
    folder: str
    provider_candidates: int = 0
    eligible_by_age: int = 0
    verified_source_uids: int = 0
    skipped_unverified_source_uids: int = 0
    failed: bool = False
    error: str = ""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create a provider-delete manifest from live provider UIDs that are older "
            "than a cutoff and already verified in MILLIE. This command does not "
            "delete or move provider mail."
        )
    )
    parser.add_argument(
        "--account",
        action="append",
        default=[],
        help="Account email/id/display name to include. May be repeated.",
    )
    parser.add_argument(
        "--folder",
        action="append",
        default=[],
        help="Exact provider folder to include. May be repeated. Defaults to all selectable mail folders.",
    )
    parser.add_argument(
        "--cutoff-utc",
        default="",
        help="Only include provider messages with INTERNALDATE at or before this UTC ISO timestamp. Defaults to now.",
    )
    parser.add_argument(
        "--manifest-id",
        default="",
        help="Optional manifest id. Defaults to remote-purge-visible-<UTC timestamp>.",
    )
    parser.add_argument(
        "--action",
        choices=("delete", "hard_delete"),
        default="delete",
        help="Future provider-side action recorded in the manifest.",
    )
    parser.add_argument(
        "--limit-source-uids",
        type=int,
        default=0,
        help="Maximum verified source UIDs to include in this manifest. Default is unlimited.",
    )
    parser.add_argument("--imap-timeout", type=int, default=120)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--include-non-mail-folders", action="store_true")
    parser.add_argument("--stop-on-error", action="store_true")
    parser.add_argument(
        "--initialize-schema",
        action="store_true",
        help="Run schema initialization before snapshotting. Production scheduled jobs should leave this off.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_local_settings()
    settings = config["settings"]
    if (settings.get("database_mode") or "").lower() != "postgres":
        raise SystemExit("Remote purge visible snapshot currently requires database_mode=postgres.")

    accounts = selected_accounts(config["accounts"], args.account)
    if not accounts:
        raise SystemExit("No enabled IMAP accounts matched.")

    cutoff = parse_cutoff(args.cutoff_utc)
    manifest_id = args.manifest_id or default_manifest_id()
    store = PostgresMailStore.connect(settings)
    try:
        if args.initialize_schema:
            store.initialize()
        candidates, stats = snapshot_accounts(
            store,
            accounts=accounts,
            settings=settings,
            cutoff=cutoff,
            args=args,
        )
        summary = summarize_stats(accounts, stats, candidates, cutoff=cutoff)
        write_manifest(
            store,
            manifest_id=manifest_id,
            action=args.action,
            candidates=candidates,
            summary=summary,
        )
        store.connection.commit()
    finally:
        store.close()

    print(
        "remote_purge_visible_snapshot=prepared "
        f"manifest_id={manifest_id} cutoff_utc={cutoff.isoformat()} "
        f"accounts={len(accounts)} source_uids={len(candidates)} "
        f"skipped_unverified_source_uids={summary['metadata']['skipped_unverified_source_uids']} "
        f"failed_folders={summary['failed_folders']} provider_action_not_run=true",
        flush=True,
    )
    return 0


def snapshot_accounts(
    store: PostgresMailStore,
    *,
    accounts: list[dict[str, Any]],
    settings: dict[str, str],
    cutoff: datetime,
    args: argparse.Namespace,
) -> tuple[list[PurgeCandidate], list[VisibleSnapshotStats]]:
    candidates: list[PurgeCandidate] = []
    stats: list[VisibleSnapshotStats] = []
    print("MILLIE provider-visible purge snapshot", flush=True)
    print(f"Cutoff UTC: {cutoff.isoformat()}", flush=True)
    print(f"Accounts: {len(accounts)}", flush=True)
    for account in accounts:
        account_name = account_label(account)
        try:
            folders = selected_folders(
                list_account_folders(account, settings, timeout=args.imap_timeout),
                args.folder,
                include_non_mail=args.include_non_mail_folders,
            )
            print(
                f"- {account_name} {account.get('host')}:{account.get('port') or 993} "
                f"folders={len(folders)}",
                flush=True,
            )
            connection = connect_account(account, settings, timeout=args.imap_timeout)
        except Exception as exc:  # noqa: BLE001 - report account failures explicitly.
            error = f"{type(exc).__name__}: {exc}"
            stats.append(VisibleSnapshotStats(account=account_name, folder="*", failed=True, error=error))
            print(f"- FAILED {account_name}: {error}", flush=True)
            if args.stop_on_error:
                raise
            continue
        try:
            for folder in folders:
                if source_uid_limit_reached(candidates, args.limit_source_uids):
                    break
                remaining = remaining_source_uid_limit(candidates, args.limit_source_uids)
                try:
                    folder_candidates, folder_stats = snapshot_folder(
                        store,
                        connection=connection,
                        account=account,
                        folder_name=folder.name,
                        cutoff=cutoff,
                        batch_size=args.batch_size,
                        limit_source_uids=remaining,
                    )
                    candidates.extend(folder_candidates)
                    stats.append(folder_stats)
                    print(
                        f"  ok folder={folder.name} provider_candidates={folder_stats.provider_candidates} "
                        f"eligible_by_age={folder_stats.eligible_by_age} "
                        f"verified={folder_stats.verified_source_uids} "
                        f"skipped_unverified={folder_stats.skipped_unverified_source_uids}",
                        flush=True,
                    )
                except Exception as exc:  # noqa: BLE001 - keep other folders moving unless requested.
                    error = f"{type(exc).__name__}: {exc}"
                    stats.append(VisibleSnapshotStats(account=account_name, folder=folder.name, failed=True, error=error))
                    print(f"  FAILED folder={folder.name}: {error}", flush=True)
                    if args.stop_on_error:
                        raise
        finally:
            logout(connection)
    return candidates, stats


def snapshot_folder(
    store: PostgresMailStore,
    *,
    connection: imaplib.IMAP4,
    account: dict[str, Any],
    folder_name: str,
    cutoff: datetime,
    batch_size: int,
    limit_source_uids: int | None,
) -> tuple[list[PurgeCandidate], VisibleSnapshotStats]:
    account_name = account_label(account)
    stats = VisibleSnapshotStats(account=account_name, folder=folder_name)
    source_type = "exchange_imap_oauth" if account.get("auth_method") == "oauth" else "imap"
    source_uri = source_uri_for_account_folder(account, folder_name)
    source_id = stable_id("source", source_type, source_uri)

    status, _ = connection.select(quote_imap_astring(folder_name), readonly=True)
    if status != "OK":
        raise RuntimeError(f"Could not select IMAP folder: {folder_name}")
    uidvalidity = selected_uidvalidity(connection)
    provider_uids = search_provider_uids_before_cutoff_date(connection, folder_name, cutoff)
    stats.provider_candidates = len(provider_uids)

    candidates: list[PurgeCandidate] = []
    for uid_batch in batched(provider_uids, max(batch_size, 1)):
        if source_uid_limit_reached(candidates, limit_source_uids):
            break
        internal_dates = fetch_internaldates_for_uids(connection, uid_batch, batch_size=batch_size)
        eligible = {
            uid: internal_date
            for uid, internal_date in internal_dates.items()
            if internal_date <= cutoff
        }
        stats.eligible_by_age += len(eligible)
        source_message_ids = {
            uid: source_message_id_for_uid(uidvalidity, uid)
            for uid in eligible
        }
        records = load_existing_source_message_records(store, source_id, source_message_ids.values())
        for uid in sorted(eligible, key=int):
            if source_uid_limit_reached(candidates, limit_source_uids):
                break
            source_message_id = source_message_ids[uid]
            record = records.get(source_message_id)
            if record is None:
                stats.skipped_unverified_source_uids += 1
                continue
            candidates.append(
                PurgeCandidate(
                    message_id=record.message_id,
                    source_id=source_id,
                    source_message_id=source_message_id,
                    source_type=source_type,
                    source_uri=source_uri,
                    source_account=account_name,
                    source_folder=folder_name,
                    imap_uidvalidity=uidvalidity,
                    imap_uid=uid,
                    raw_mime_sha256=record.raw_mime_sha256,
                    source_table=record.source_table,
                    metadata={
                        "provider_internal_date": eligible[uid].isoformat(),
                        "millie_message_at": datetime_iso(record.millie_message_at),
                    },
                )
            )
            stats.verified_source_uids += 1
    return candidates, stats


def search_provider_uids_before_cutoff_date(
    connection: imaplib.IMAP4,
    folder_name: str,
    cutoff: datetime,
) -> list[str]:
    before_date = imap_search_before_date(cutoff)
    status, data = connection.uid("SEARCH", None, "BEFORE", before_date)
    if status != "OK":
        raise ImportSourceError(f"UID SEARCH BEFORE failed for folder: {folder_name}")
    return [
        uid.decode("ascii", errors="replace")
        for uid in (data[0] or b"").split()
        if uid.decode("ascii", errors="replace").isdigit()
    ]


def fetch_internaldates_for_uids(
    connection: imaplib.IMAP4,
    uids: list[str],
    *,
    batch_size: int,
) -> dict[str, datetime]:
    result: dict[str, datetime] = {}
    if not uids:
        return result
    for batch in batched(uids, max(batch_size, 1)):
        uid_set = ",".join(batch)
        status, data = connection.uid("FETCH", uid_set, "(UID INTERNALDATE)")
        if status != "OK":
            raise RuntimeError(f"UID FETCH INTERNALDATE failed for batch starting {batch[0]}")
        result.update(parse_internaldate_fetch(data or []))
    return result


def parse_internaldate_fetch(data: list[bytes | tuple[bytes, bytes]]) -> dict[str, datetime]:
    result: dict[str, datetime] = {}
    for item in data:
        text = fetch_response_text(item)
        uid_match = UID_RE.search(text)
        date_match = INTERNALDATE_RE.search(text)
        if not uid_match or not date_match:
            continue
        try:
            result[uid_match.group(1)] = parse_internaldate(date_match.group(1))
        except ValueError:
            continue
    return result


def fetch_response_text(item: bytes | tuple[bytes, bytes]) -> str:
    if isinstance(item, tuple):
        chunks = [part for part in item if isinstance(part, bytes)]
        return b" ".join(chunks).decode("ascii", errors="replace")
    if isinstance(item, bytes):
        return item.decode("ascii", errors="replace")
    return ""


def parse_internaldate(value: str) -> datetime:
    parsed = datetime.strptime(value, "%d-%b-%Y %H:%M:%S %z")
    return parsed.astimezone(timezone.utc)


def imap_search_before_date(cutoff: datetime) -> str:
    next_day = cutoff.astimezone(timezone.utc).date() + timedelta(days=1)
    return f"{next_day.day:02d}-{IMAP_MONTHS[next_day.month - 1]}-{next_day.year}"


def load_existing_source_message_records(
    store: PostgresMailStore,
    source_id: str,
    source_message_ids: Iterable[str],
) -> dict[str, SourceMessageRecord]:
    ids = sorted({value for value in source_message_ids if value})
    if not ids:
        return {}
    records: dict[str, SourceMessageRecord] = {}
    for chunk in batched(ids, 1000):
        token_list = placeholders(chunk)
        direct_rows = store.connection.execute(
            f"""
            SELECT source_message_id, id, raw_mime_sha256,
                   coalesce(received_at, sent_at, created_at) AS millie_message_at
            FROM mail_messages
            WHERE source_id = %s
              AND source_message_id IN ({token_list})
            """,
            (source_id, *chunk),
        ).fetchall()
        alias_rows = store.connection.execute(
            f"""
            SELECT a.source_message_id, a.message_id, a.raw_mime_sha256,
                   coalesce(m.received_at, m.sent_at, m.created_at) AS millie_message_at
            FROM mail_source_message_aliases a
            JOIN mail_messages m ON m.id = a.message_id
            WHERE a.source_id = %s
              AND a.source_message_id IN ({token_list})
            """,
            (source_id, *chunk),
        ).fetchall()
        for source_message_id, message_id, raw_hash, message_at in direct_rows:
            records[str(source_message_id)] = SourceMessageRecord(
                message_id=str(message_id),
                raw_mime_sha256=str(raw_hash),
                source_table="mail_messages",
                millie_message_at=message_at,
            )
        for source_message_id, message_id, raw_hash, message_at in alias_rows:
            records.setdefault(
                str(source_message_id),
                SourceMessageRecord(
                    message_id=str(message_id),
                    raw_mime_sha256=str(raw_hash),
                    source_table="mail_source_message_aliases",
                    millie_message_at=message_at,
                ),
            )
    return records


def summarize_stats(
    accounts: list[dict[str, Any]],
    stats: list[VisibleSnapshotStats],
    candidates: list[PurgeCandidate],
    *,
    cutoff: datetime,
) -> dict[str, Any]:
    return {
        "prepared_by": "tools/millie_remote_purge_visible_snapshot.py",
        "accounts": [account_label(account) for account in accounts],
        "folders": sum(1 for item in stats if not item.failed),
        "failed_folders": sum(1 for item in stats if item.failed),
        "provider_uids": sum(item.provider_candidates for item in stats),
        "known_source_uids": len(candidates),
        "missing_source_uids": 0,
        "unique_messages": len({candidate.message_id for candidate in candidates}),
        "failed": [
            {"account": item.account, "folder": item.folder, "error": item.error}
            for item in stats
            if item.failed
        ],
        "metadata": {
            "cutoff_utc": cutoff.isoformat(),
            "provider_candidates": sum(item.provider_candidates for item in stats),
            "eligible_by_age": sum(item.eligible_by_age for item in stats),
            "verified_source_uids": len(candidates),
            "skipped_unverified_source_uids": sum(item.skipped_unverified_source_uids for item in stats),
            "scan_limited": False,
        },
    }


def source_message_id_for_uid(uidvalidity: str, uid: str) -> str:
    return f"{uidvalidity}:{uid}" if uidvalidity else uid


def source_uid_limit_reached(candidates: list[PurgeCandidate], limit: int | None) -> bool:
    return limit is not None and limit > 0 and len(candidates) >= limit


def remaining_source_uid_limit(candidates: list[PurgeCandidate], limit: int) -> int | None:
    if limit <= 0:
        return None
    return max(limit - len(candidates), 0)


def placeholders(values: list[object]) -> str:
    return ", ".join(["%s"] * len(values))


def batched(values: Iterable[T], size: int) -> Iterable[list[T]]:
    batch: list[T] = []
    batch_size = max(size, 1)
    for value in values:
        batch.append(value)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def datetime_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def parse_cutoff(value: str) -> datetime:
    if not value:
        return datetime.now(timezone.utc).replace(microsecond=0)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def default_manifest_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"remote-purge-visible-{stamp}"


if __name__ == "__main__":
    raise SystemExit(main())
