#!/usr/bin/env python3
"""Delete provider-side IMAP messages from a prepared MILLIE manifest."""

from __future__ import annotations

import argparse
import imaplib
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from psycopg.types.json import Jsonb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from millie.settings_loader import load_local_settings
from millie.storage.postgres_store import PostgresMailStore
from tools.millie_imap_bulk_import import (
    account_label,
    connect_account,
    logout,
    quote_imap_astring,
    selected_accounts,
    selected_uidvalidity,
)


@dataclass(frozen=True, slots=True)
class ManifestUid:
    manifest_id: str
    message_id: str
    source_id: str
    source_message_id: str
    source_account: str
    source_folder: str
    imap_uidvalidity: str
    imap_uid: str


@dataclass(slots=True)
class FolderPurgeStats:
    account: str
    folder: str
    manifest_uids: int = 0
    found_uids: int = 0
    deleted_uids: int = 0
    already_absent_uids: int = 0
    failed: bool = False
    error: str = ""


@dataclass(slots=True)
class PurgeSummary:
    manifest_id: str
    dry_run: bool
    folders: list[FolderPurgeStats] = field(default_factory=list)

    @property
    def manifest_uids(self) -> int:
        return sum(item.manifest_uids for item in self.folders)

    @property
    def found_uids(self) -> int:
        return sum(item.found_uids for item in self.folders)

    @property
    def deleted_uids(self) -> int:
        return sum(item.deleted_uids for item in self.folders)

    @property
    def absent_uids(self) -> int:
        return sum(item.already_absent_uids for item in self.folders)

    @property
    def failed_folders(self) -> int:
        return sum(1 for item in self.folders if item.failed)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Delete provider-side IMAP messages listed in a prepared MILLIE manifest. "
            "The tool only addresses exact manifest UIDs; provider mail that arrived "
            "after the manifest snapshot is not targeted."
        )
    )
    parser.add_argument("--manifest-id", required=True)
    parser.add_argument(
        "--account",
        action="append",
        default=[],
        help="Account email/id/display name to purge. May be repeated.",
    )
    parser.add_argument(
        "--folder",
        action="append",
        default=[],
        help="Exact source folder to purge. May be repeated.",
    )
    parser.add_argument("--execute", action="store_true", help="Actually delete provider-side UIDs.")
    parser.add_argument(
        "--resume-completed",
        action="store_true",
        help="Allow execute mode against a manifest already marked provider_purged.",
    )
    parser.add_argument("--imap-timeout", type=int, default=120)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--stop-on-error", action="store_true")
    parser.add_argument(
        "--require-uidplus",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Require UIDPLUS/UID EXPUNGE support so unrelated pre-deleted messages are not expunged.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_local_settings()
    settings = config["settings"]
    if (settings.get("database_mode") or "").lower() != "postgres":
        raise SystemExit("Remote provider purge currently requires database_mode=postgres.")

    accounts = selected_accounts(config["accounts"], args.account)
    if not accounts:
        raise SystemExit("No enabled IMAP accounts matched.")
    accounts_by_label = {account_label(account).lower(): account for account in accounts}

    store = PostgresMailStore.connect(settings)
    try:
        manifest = load_manifest(store, args.manifest_id)
        if manifest is None:
            raise SystemExit(f"Manifest not found: {args.manifest_id}")
        allowed_statuses = {"prepared", "confirmed", "failed"}
        if not args.execute:
            allowed_statuses.add("provider_purged")
        elif args.resume_completed:
            allowed_statuses.add("provider_purged")
        if manifest["status"] not in allowed_statuses:
            raise SystemExit(f"Manifest status does not allow purge: {manifest['status']}")
        if int(manifest["missing_source_uids"]) != 0:
            raise SystemExit("Manifest has missing_source_uids > 0.")
        uids = load_manifest_uids(
            store,
            manifest_id=args.manifest_id,
            accounts=[account_label(account) for account in accounts],
            folders=args.folder,
        )
        summary = PurgeSummary(manifest_id=args.manifest_id, dry_run=not args.execute)
        print("MILLIE remote provider purge", flush=True)
        print(f"Mode: {'execute' if args.execute else 'dry-run'}", flush=True)
        print(f"Manifest: {args.manifest_id}", flush=True)
        print(f"Accounts: {len(accounts)} source_uids={len(uids)}", flush=True)
        for account_name, account_uids in group_by_account(uids).items():
            account = accounts_by_label.get(account_name.lower())
            if account is None:
                continue
            account_summary = purge_account(
                account,
                account_uids,
                settings=settings,
                execute=args.execute,
                timeout=args.imap_timeout,
                batch_size=args.batch_size,
                require_uidplus=args.require_uidplus,
                stop_on_error=args.stop_on_error,
            )
            summary.folders.extend(account_summary)
        if args.execute:
            update_manifest_after_execute(store, summary)
            store.connection.commit()
    finally:
        store.close()

    print_summary(summary)
    return 0 if summary.failed_folders == 0 else 2


def load_manifest(store: PostgresMailStore, manifest_id: str) -> dict[str, Any] | None:
    row = store.connection.execute(
        """
        SELECT id, status, action, total_messages, total_source_uids, missing_source_uids
        FROM mail_remote_purge_manifests
        WHERE id = %s
        """,
        (manifest_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "status": row[1],
        "action": row[2],
        "total_messages": int(row[3] or 0),
        "total_source_uids": int(row[4] or 0),
        "missing_source_uids": int(row[5] or 0),
    }


def load_manifest_uids(
    store: PostgresMailStore,
    *,
    manifest_id: str,
    accounts: list[str],
    folders: list[str],
) -> list[ManifestUid]:
    params: list[object] = [manifest_id]
    account_filter = ""
    if accounts:
        account_filter = f"AND lower(source_account) IN ({placeholders(accounts)})"
        params.extend(account.lower() for account in accounts)
    folder_filter = ""
    if folders:
        folder_filter = f"AND source_folder IN ({placeholders(folders)})"
        params.extend(folders)
    rows = store.connection.execute(
        f"""
        SELECT
            manifest_id, message_id, source_id, source_message_id,
            source_account, source_folder, imap_uidvalidity, imap_uid
        FROM mail_remote_purge_manifest_messages
        WHERE manifest_id = %s
          {account_filter}
          {folder_filter}
        ORDER BY source_account, source_folder, imap_uid::bigint
        """,
        tuple(params),
    ).fetchall()
    return [
        ManifestUid(
            manifest_id=str(row[0]),
            message_id=str(row[1]),
            source_id=str(row[2]),
            source_message_id=str(row[3]),
            source_account=str(row[4]),
            source_folder=str(row[5]),
            imap_uidvalidity=str(row[6] or ""),
            imap_uid=str(row[7] or ""),
        )
        for row in rows
        if str(row[7] or "").isdigit()
    ]


def purge_account(
    account: dict[str, Any],
    uids: list[ManifestUid],
    *,
    settings: dict[str, str],
    execute: bool,
    timeout: int,
    batch_size: int,
    require_uidplus: bool,
    stop_on_error: bool,
) -> list[FolderPurgeStats]:
    print(
        f"- {account_label(account)} {account.get('host')}:{account.get('port') or 993} "
        f"folders={len(group_by_folder(uids))}",
        flush=True,
    )
    connection = connect_account(account, settings, timeout=timeout)
    try:
        capabilities = normalized_capabilities(connection)
        if execute and require_uidplus and "UIDPLUS" not in capabilities:
            raise RuntimeError(f"{account_label(account)} does not advertise UIDPLUS.")
        gmail_message_ids: set[str] = set()
        if execute and is_gmail_account(account):
            gmail_message_ids = collect_gmail_message_ids(
                connection,
                group_by_folder(uids),
                batch_size=batch_size,
            )
            print(f"  gmail_snapshot_message_ids={len(gmail_message_ids)}", flush=True)
        folder_stats: list[FolderPurgeStats] = []
        for folder, folder_uids in sorted(
            group_by_folder(uids).items(),
            key=lambda item: folder_sort_key(item[0]),
        ):
            stats = purge_folder(
                connection,
                account_name=account_label(account),
                folder=folder,
                rows=folder_uids,
                execute=execute,
                batch_size=batch_size,
                gmail_move_to_trash=execute and is_gmail_account(account) and is_gmail_all_mail(folder),
            )
            folder_stats.append(stats)
            status = "failed" if stats.failed else "ok"
            print(
                f"  {status} folder={folder} manifest_uids={stats.manifest_uids} "
                f"found={stats.found_uids} deleted={stats.deleted_uids} "
                f"already_absent={stats.already_absent_uids}"
                f"{' error=' + stats.error if stats.error else ''}",
                flush=True,
            )
            if stats.failed and stop_on_error:
                break
        if execute and gmail_message_ids:
            stats = purge_gmail_trash_by_message_id(
                connection,
                account_name=account_label(account),
                gmail_message_ids=gmail_message_ids,
                batch_size=batch_size,
            )
            folder_stats.append(stats)
            status = "failed" if stats.failed else "ok"
            print(
                f"  {status} folder={stats.folder} manifest_uids={stats.manifest_uids} "
                f"found={stats.found_uids} deleted={stats.deleted_uids} "
                f"already_absent={stats.already_absent_uids}"
                f"{' error=' + stats.error if stats.error else ''}",
                flush=True,
            )
        return folder_stats
    finally:
        logout(connection)


def purge_folder(
    connection: imaplib.IMAP4,
    *,
    account_name: str,
    folder: str,
    rows: list[ManifestUid],
    execute: bool,
    batch_size: int,
    gmail_move_to_trash: bool = False,
) -> FolderPurgeStats:
    stats = FolderPurgeStats(account=account_name, folder=folder, manifest_uids=len(rows))
    uidvalidities = {row.imap_uidvalidity for row in rows if row.imap_uidvalidity}
    try:
        status, _ = connection.select(quote_imap_astring(folder), readonly=not execute)
        if status != "OK":
            raise RuntimeError("select failed")
        current_uidvalidity = selected_uidvalidity(connection)
        if uidvalidities and current_uidvalidity not in uidvalidities:
            raise RuntimeError(
                f"UIDVALIDITY mismatch current={current_uidvalidity} manifest={sorted(uidvalidities)}"
            )
        manifest_uids = sorted({row.imap_uid for row in rows if row.imap_uid.isdigit()}, key=int)
        existing_uids = existing_uids_in_folder(connection, manifest_uids, batch_size=batch_size)
        stats.found_uids = len(existing_uids)
        stats.already_absent_uids = len(manifest_uids) - len(existing_uids)
        if execute and existing_uids:
            if gmail_move_to_trash:
                stats.deleted_uids = move_gmail_uids_to_trash(
                    connection,
                    existing_uids,
                    batch_size=batch_size,
                )
            else:
                stats.deleted_uids = delete_uids(connection, existing_uids, batch_size=batch_size)
    except Exception as exc:  # noqa: BLE001 - report folder failures and continue by default.
        stats.failed = True
        stats.error = f"{type(exc).__name__}: {exc}"
    return stats


def existing_uids_in_folder(
    connection: imaplib.IMAP4,
    uids: list[str],
    *,
    batch_size: int,
) -> list[str]:
    existing: set[str] = set()
    for batch in batched(uids, batch_size):
        uid_set = ",".join(batch)
        status, data = connection.uid("SEARCH", None, "UID", uid_set)
        if status != "OK":
            raise RuntimeError(f"UID SEARCH failed for batch starting {batch[0]}")
        for uid in (data[0] or b"").split():
            existing.add(uid.decode("ascii", errors="replace"))
    return sorted(existing, key=int)


def delete_uids(
    connection: imaplib.IMAP4,
    uids: list[str],
    *,
    batch_size: int,
) -> int:
    deleted = 0
    for batch in batched(uids, batch_size):
        uid_set = ",".join(batch)
        status, _ = connection.uid("STORE", uid_set, "+FLAGS.SILENT", r"(\Deleted)")
        if status != "OK":
            raise RuntimeError(f"UID STORE \\Deleted failed for batch starting {batch[0]}")
        status, _ = connection.uid("EXPUNGE", uid_set)
        if status != "OK":
            raise RuntimeError(f"UID EXPUNGE failed for batch starting {batch[0]}")
        deleted += len(batch)
    return deleted


def move_gmail_uids_to_trash(
    connection: imaplib.IMAP4,
    uids: list[str],
    *,
    batch_size: int,
) -> int:
    moved = 0
    for batch in batched(uids, batch_size):
        uid_set = ",".join(batch)
        status, _ = connection.uid("MOVE", uid_set, quote_imap_astring("[Gmail]/Trash"))
        if status != "OK":
            raise RuntimeError(f"UID MOVE to Gmail Trash failed for batch starting {batch[0]}")
        moved += len(batch)
    return moved


def collect_gmail_message_ids(
    connection: imaplib.IMAP4,
    rows_by_folder: dict[str, list[ManifestUid]],
    *,
    batch_size: int,
) -> set[str]:
    message_ids: set[str] = set()
    for folder, rows in rows_by_folder.items():
        uidvalidities = {row.imap_uidvalidity for row in rows if row.imap_uidvalidity}
        status, _ = connection.select(quote_imap_astring(folder), readonly=True)
        if status != "OK":
            raise RuntimeError(f"Gmail X-GM-MSGID select failed for {folder}")
        current_uidvalidity = selected_uidvalidity(connection)
        if uidvalidities and current_uidvalidity not in uidvalidities:
            raise RuntimeError(
                f"Gmail UIDVALIDITY mismatch folder={folder} "
                f"current={current_uidvalidity} manifest={sorted(uidvalidities)}"
            )
        uids = sorted({row.imap_uid for row in rows if row.imap_uid.isdigit()}, key=int)
        message_ids.update(fetch_gmail_message_ids_for_uids(connection, uids, batch_size=batch_size).values())
    return message_ids


def purge_gmail_trash_by_message_id(
    connection: imaplib.IMAP4,
    *,
    account_name: str,
    gmail_message_ids: set[str],
    batch_size: int,
) -> FolderPurgeStats:
    folder = "[Gmail]/Trash"
    stats = FolderPurgeStats(
        account=account_name,
        folder=f"{folder} (X-GM-MSGID final)",
        manifest_uids=len(gmail_message_ids),
    )
    try:
        status, _ = connection.select(quote_imap_astring(folder), readonly=False)
        if status != "OK":
            raise RuntimeError("select failed")
        status, data = connection.uid("SEARCH", None, "ALL")
        if status != "OK":
            raise RuntimeError("UID SEARCH ALL failed")
        all_uids = [
            uid.decode("ascii", errors="replace")
            for uid in (data[0] or b"").split()
        ]
        uid_to_msgid = fetch_gmail_message_ids_for_uids(connection, all_uids, batch_size=batch_size)
        target_uids = sorted(
            [uid for uid, msgid in uid_to_msgid.items() if msgid in gmail_message_ids],
            key=int,
        )
        stats.found_uids = len(target_uids)
        stats.already_absent_uids = len(gmail_message_ids) - len(set(uid_to_msgid[uid] for uid in target_uids))
        if target_uids:
            stats.deleted_uids = delete_uids(connection, target_uids, batch_size=batch_size)
    except Exception as exc:  # noqa: BLE001 - keep account summary explicit.
        stats.failed = True
        stats.error = f"{type(exc).__name__}: {exc}"
    return stats


def fetch_gmail_message_ids_for_uids(
    connection: imaplib.IMAP4,
    uids: list[str],
    *,
    batch_size: int,
) -> dict[str, str]:
    result: dict[str, str] = {}
    for batch in batched(uids, batch_size):
        uid_set = ",".join(batch)
        status, data = connection.uid("FETCH", uid_set, "(X-GM-MSGID)")
        if status != "OK":
            raise RuntimeError(f"X-GM-MSGID fetch failed for batch starting {batch[0]}")
        result.update(parse_x_gm_msgid_fetch(data or []))
    return result


def parse_x_gm_msgid_fetch(data: list[bytes | tuple[bytes, bytes]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in data:
        if isinstance(item, tuple):
            text = item[0].decode("ascii", errors="replace")
        elif isinstance(item, bytes):
            text = item.decode("ascii", errors="replace")
        else:
            continue
        uid_match = re.search(r"\bUID\s+(\d+)\b", text, flags=re.IGNORECASE)
        msgid_match = re.search(r"\bX-GM-MSGID\s+(\d+)\b", text, flags=re.IGNORECASE)
        if uid_match and msgid_match:
            result[uid_match.group(1)] = msgid_match.group(1)
    return result


def is_gmail_account(account: dict[str, Any]) -> bool:
    return str(account.get("host") or "").lower() == "imap.gmail.com"


def is_gmail_all_mail(folder: str) -> bool:
    return folder.strip().lower() == "[gmail]/all mail"


def update_manifest_after_execute(store: PostgresMailStore, summary: PurgeSummary) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    status = "failed" if summary.failed_folders else "provider_purged"
    metadata = {
        "provider_action": "uid_store_deleted_uid_expunge",
        "manifest_uids": summary.manifest_uids,
        "found_uids": summary.found_uids,
        "deleted_uids": summary.deleted_uids,
        "already_absent_uids": summary.absent_uids,
        "failed_folders": summary.failed_folders,
        "folders": [
            {
                "account": item.account,
                "folder": item.folder,
                "manifest_uids": item.manifest_uids,
                "found_uids": item.found_uids,
                "deleted_uids": item.deleted_uids,
                "already_absent_uids": item.already_absent_uids,
                "failed": item.failed,
                "error": item.error,
            }
            for item in summary.folders
        ],
    }
    store.connection.execute(
        """
        UPDATE mail_remote_purge_manifests
        SET status = %s,
            executed_at = coalesce(executed_at, %s),
            completed_at = %s,
            metadata_json = metadata_json || %s
        WHERE id = %s
        """,
        (status, now, now, Jsonb(metadata), summary.manifest_id),
    )


def print_summary(summary: PurgeSummary) -> None:
    print(
        "remote_provider_purge=done "
        f"mode={'dry-run' if summary.dry_run else 'execute'} "
        f"manifest_id={summary.manifest_id} folders={len(summary.folders)} "
        f"manifest_uids={summary.manifest_uids} found_uids={summary.found_uids} "
        f"deleted_uids={summary.deleted_uids} already_absent_uids={summary.absent_uids} "
        f"failed_folders={summary.failed_folders}",
        flush=True,
    )


def group_by_account(uids: Iterable[ManifestUid]) -> dict[str, list[ManifestUid]]:
    grouped: dict[str, list[ManifestUid]] = defaultdict(list)
    for row in uids:
        grouped[row.source_account].append(row)
    return dict(grouped)


def group_by_folder(uids: Iterable[ManifestUid]) -> dict[str, list[ManifestUid]]:
    grouped: dict[str, list[ManifestUid]] = defaultdict(list)
    for row in uids:
        grouped[row.source_folder].append(row)
    return dict(grouped)


def batched(values: list[str], size: int) -> Iterable[list[str]]:
    batch_size = max(size, 1)
    for index in range(0, len(values), batch_size):
        yield values[index : index + batch_size]


def normalized_capabilities(connection: imaplib.IMAP4) -> set[str]:
    raw = getattr(connection, "capabilities", ()) or ()
    try:
        status, data = connection.capability()
        if status == "OK" and data:
            raw = tuple(part for value in data for part in value.split())
    except imaplib.IMAP4.error:
        pass
    return {
        (item.decode("ascii", errors="replace") if isinstance(item, bytes) else str(item)).upper()
        for item in raw
    }


def folder_sort_key(folder: str) -> tuple[int, str]:
    lowered = folder.lower()
    leaf = lowered.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "trash" in leaf or "deleted" in leaf:
        return (90, lowered)
    if "all mail" in lowered:
        return (80, lowered)
    if "spam" in leaf or "junk" in leaf:
        return (70, lowered)
    if "sent" in leaf:
        return (20, lowered)
    if leaf == "inbox":
        return (10, lowered)
    return (30, lowered)


def placeholders(values: list[object]) -> str:
    return ", ".join(["%s"] * len(values))


if __name__ == "__main__":
    raise SystemExit(main())
