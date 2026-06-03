#!/usr/bin/env python3
"""Import all configured IMAP account folders into MILLIE."""

from __future__ import annotations

import argparse
import imaplib
import json
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable

from psycopg.types.json import Jsonb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from millie.importing.models import stable_id
from millie.importing.normalize import normalize_email
from millie.importing.sources import ImportSourceError, _extract_imap_fetch_bytes
from millie.service.auth import default_service_login, identity_from_settings
from millie.settings_loader import load_local_settings
from millie.storage.postgres_store import PostgresMailStore


@dataclass(frozen=True, slots=True)
class FolderInfo:
    name: str
    delimiter: str | None
    flags: tuple[str, ...]


@dataclass(slots=True)
class ImportStats:
    folders: int = 0
    scanned: int = 0
    imported: int = 0
    skipped_existing: int = 0
    deduped_existing: int = 0
    failed_folders: int = 0

    @property
    def changed(self) -> int:
        return self.imported + self.deduped_existing


@dataclass(slots=True)
class MailboxMessageMapper:
    store: PostgresMailStore
    mailbox_id: str
    folder_ids: dict[str, str] = field(default_factory=dict)
    next_uids: dict[str, int] = field(default_factory=dict)

    def map_message(
        self,
        *,
        folder_path: str,
        message_id: str,
        binding_id: str | None = None,
    ) -> int:
        folder_id = self._folder_id(folder_path)
        next_uid = self._next_uid(folder_id)
        row_id = stable_id("millie_mailbox_message", self.mailbox_id, folder_id, message_id)
        row = self.store.connection.execute(
            """
            INSERT INTO millie_mailbox_messages (
                id, mailbox_id, folder_id, message_id, binding_id, imap_uid,
                internal_date, flags, is_recent
            )
            SELECT
                %s, %s, %s, m.id, %s, %s,
                coalesce(m.received_at, m.sent_at, now()), ARRAY[]::text[], TRUE
            FROM mail_messages m
            WHERE m.id = %s
            ON CONFLICT(folder_id, message_id) DO UPDATE SET
                updated_at = now()
            RETURNING imap_uid
            """,
            (
                row_id,
                self.mailbox_id,
                folder_id,
                binding_id,
                next_uid,
                message_id,
            ),
        ).fetchone()
        if not row:
            raise ValueError(f"Mailbox message source row not found: {message_id}")
        mapped_uid = int(row[0])
        if mapped_uid == next_uid:
            self.next_uids[folder_id] = next_uid + 1
        return mapped_uid

    def _folder_id(self, folder_path: str) -> str:
        if folder_path not in self.folder_ids:
            folder_id = self.store.folder_id(self.mailbox_id, folder_path)
            if not folder_id:
                raise ValueError(f"Mailbox folder not found: {folder_path}")
            self.folder_ids[folder_path] = folder_id
        return self.folder_ids[folder_path]

    def _next_uid(self, folder_id: str) -> int:
        if folder_id not in self.next_uids:
            row = self.store.connection.execute(
                """
                SELECT coalesce(max(imap_uid), 0) + 1
                FROM millie_mailbox_messages
                WHERE folder_id = %s
                """,
                (folder_id,),
            ).fetchone()
            self.next_uids[folder_id] = int(row[0])
        return self.next_uids[folder_id]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Import every selectable folder from configured IMAP accounts into MILLIE. "
            "By default this is a dry run; pass --apply to fetch and write messages."
        )
    )
    parser.add_argument("--apply", action="store_true", help="Fetch messages and write to MILLIE.")
    parser.add_argument(
        "--account",
        action="append",
        default=[],
        help="Account email, id, or display name to import. May be repeated. Defaults to all enabled IMAP accounts.",
    )
    parser.add_argument(
        "--folder",
        action="append",
        default=[],
        help="Exact IMAP folder name to import. May be repeated. Defaults to all selectable folders.",
    )
    parser.add_argument("--login", default="", help="MILLIE login. Defaults to geon@<service_mail_domain>.")
    parser.add_argument("--display-name", default="Geon", help="MILLIE mailbox display name.")
    parser.add_argument("--commit-every", type=int, default=100, help="Commit after this many writes.")
    parser.add_argument(
        "--fetch-batch-size",
        type=int,
        default=25,
        help="Fetch this many IMAP messages per network round trip.",
    )
    parser.add_argument("--imap-timeout", type=int, default=120, help="IMAP socket timeout in seconds.")
    parser.add_argument("--limit-per-folder", type=int, default=0, help="Import at most this many messages per folder.")
    parser.add_argument("--limit-total", type=int, default=0, help="Import at most this many messages per run.")
    parser.add_argument("--replace-existing", action="store_true", help="Replace existing source UID records.")
    parser.add_argument(
        "--newer-than-existing",
        action="store_true",
        help="Only search UIDs newer than the highest imported UID for each folder.",
    )
    parser.add_argument(
        "--allow-raw-duplicates",
        action="store_true",
        help="Do not dedupe against already imported raw MIME hashes.",
    )
    parser.add_argument(
        "--no-map-specials",
        action="store_true",
        help="Do not also map imported INBOX/Sent/Drafts/Trash/Junk folders to MILLIE top-level special folders.",
    )
    parser.add_argument(
        "--include-non-mail-folders",
        action="store_true",
        help="Also import obvious calendar/contact/task/system folders exposed over IMAP.",
    )
    parser.add_argument("--stop-on-error", action="store_true", help="Stop when one folder import fails.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_local_settings()
    settings = config["settings"]
    accounts = selected_accounts(config["accounts"], args.account)
    if not accounts:
        raise SystemExit("No enabled IMAP accounts matched.")

    print(f"MILLIE IMAP bulk import plan", flush=True)
    print(f"Mode: {'apply' if args.apply else 'dry-run'}", flush=True)
    print(f"Accounts: {len(accounts)}", flush=True)

    totals = ImportStats()
    if not args.apply:
        for account in accounts:
            folders = folders_for_args(account, settings, args)
            print_account_plan(account, folders)
        print("Dry run only. Re-run with --apply to fetch IMAP messages and write to MILLIE.", flush=True)
        return 0

    store = PostgresMailStore.connect(settings)
    try:
        store.initialize()
        login = args.login or default_service_login(settings, "geon")
        identity = identity_from_settings(login, args.display_name, settings)
        mailbox_id = store.ensure_identity(identity)
        store.connection.commit()

        for account in accounts:
            folders = folders_for_args(account, settings, args)
            print_account_plan(account, folders)
            account_stats = import_account_folders(
                store,
                mailbox_id=mailbox_id,
                account=account,
                folders=folders,
                settings=settings,
                args=args,
            )
            totals.folders += account_stats.folders
            totals.scanned += account_stats.scanned
            totals.imported += account_stats.imported
            totals.skipped_existing += account_stats.skipped_existing
            totals.deduped_existing += account_stats.deduped_existing
            totals.failed_folders += account_stats.failed_folders
            if args.limit_total and totals.scanned >= args.limit_total:
                break

        store.connection.commit()
    finally:
        store.close()

    print(
        "millie_imap_bulk_import=done "
        f"folders={totals.folders} scanned={totals.scanned} imported={totals.imported} "
        f"skipped_existing={totals.skipped_existing} deduped_existing={totals.deduped_existing} "
        f"failed_folders={totals.failed_folders}",
        flush=True,
    )
    return 0 if totals.failed_folders == 0 else 1


def selected_accounts(accounts: list[dict[str, Any]], selectors: list[str]) -> list[dict[str, Any]]:
    enabled = [
        account
        for account in accounts
        if account.get("enabled") and account.get("account_type") == "imap"
    ]
    if not selectors:
        return enabled
    wanted = {selector.lower() for selector in selectors}
    result = []
    for account in enabled:
        candidates = {
            str(account.get("id") or "").lower(),
            str(account.get("email_address") or "").lower(),
            str(account.get("username") or "").lower(),
            str(account.get("display_name") or "").lower(),
        }
        if candidates & wanted:
            result.append(account)
    return result


def folders_for_args(
    account: dict[str, Any],
    settings: dict[str, str],
    args: argparse.Namespace,
) -> list[FolderInfo]:
    if args.folder:
        folders: list[FolderInfo] = []
        seen: set[str] = set()
        for folder_name in args.folder:
            key = folder_name.lower()
            if key in seen:
                continue
            seen.add(key)
            folders.append(FolderInfo(name=folder_name, delimiter="/", flags=()))
        return folders
    folders = list_account_folders(account, settings, timeout=args.imap_timeout)
    return selected_folders(
        folders,
        args.folder,
        include_non_mail=args.include_non_mail_folders,
    )


def print_account_plan(account: dict[str, Any], folders: list[FolderInfo]) -> None:
    print(
        f"- {account.get('email_address') or account.get('username')} "
        f"{account.get('host')}:{account.get('port') or 993} "
        f"auth={account.get('auth_method') or 'password'} folders={len(folders)}",
        flush=True,
    )
    for folder in folders:
        print(f"  folder: {folder.name}", flush=True)


def list_account_folders(
    account: dict[str, Any],
    settings: dict[str, str],
    *,
    timeout: int,
) -> list[FolderInfo]:
    connection = connect_account(account, settings, timeout=timeout)
    try:
        status, data = connection.list()
        if status != "OK":
            raise ImportSourceError(f"IMAP LIST failed for {account_label(account)}")
        folders = []
        for line in data or []:
            parsed = parse_list_response(line)
            if parsed is None:
                continue
            if any(flag.lower() == "\\noselect" for flag in parsed.flags):
                continue
            folders.append(parsed)
        return sorted(unique_folders(folders), key=lambda item: folder_sort_key(item.name))
    finally:
        logout(connection)


def unique_folders(folders: Iterable[FolderInfo]) -> list[FolderInfo]:
    result: list[FolderInfo] = []
    seen: set[str] = set()
    for folder in folders:
        key = folder.name.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(folder)
    return result


def selected_folders(
    folders: list[FolderInfo],
    selectors: list[str],
    *,
    include_non_mail: bool = False,
) -> list[FolderInfo]:
    if not selectors:
        selected = folders
    else:
        wanted = {selector.lower() for selector in selectors}
        selected = [folder for folder in folders if folder.name.lower() in wanted]
    if include_non_mail or selectors:
        return selected
    return [folder for folder in selected if not is_default_non_mail_folder(folder.name)]


def is_default_non_mail_folder(name: str) -> bool:
    lowered = name.strip().lower()
    leaf = lowered.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if lowered.startswith("calendar/") or lowered.startswith("tasks/") or lowered.startswith("sync issues/"):
        return True
    return leaf in {
        "calendar",
        "contacts",
        "journal",
        "notes",
        "notes (this computer only)",
        "outbox",
        "rss feeds",
        "sync issues",
        "tasks",
    }


def import_account_folders(
    store: PostgresMailStore,
    *,
    mailbox_id: str,
    account: dict[str, Any],
    folders: list[FolderInfo],
    settings: dict[str, str],
    args: argparse.Namespace,
) -> ImportStats:
    stats = ImportStats()
    account_root = f"Sources/IMAP/{safe_folder_component(account.get('email_address') or account['username'])}"
    store.ensure_mailbox_folder(mailbox_id, account_root)
    store.connection.commit()

    for folder in folders:
        if args.limit_total and stats.scanned >= args.limit_total:
            break
        try:
            folder_stats = import_folder(
                store,
                mailbox_id=mailbox_id,
                account=account,
                account_root=account_root,
                folder=folder,
                settings=settings,
                args=args,
            )
            stats.folders += 1
            stats.scanned += folder_stats.scanned
            stats.imported += folder_stats.imported
            stats.skipped_existing += folder_stats.skipped_existing
            stats.deduped_existing += folder_stats.deduped_existing
        except Exception as exc:  # noqa: BLE001 - keep large imports moving unless requested.
            store.connection.rollback()
            stats.failed_folders += 1
            record_folder_sync_failure(
                store,
                account=account,
                folder=folder,
                error_message=f"{type(exc).__name__}: {exc}",
            )
            store.connection.commit()
            print(
                f"FAILED account={account_label(account)} folder={folder.name}: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            if args.stop_on_error:
                raise
    return stats


def import_folder(
    store: PostgresMailStore,
    *,
    mailbox_id: str,
    account: dict[str, Any],
    account_root: str,
    folder: FolderInfo,
    settings: dict[str, str],
    args: argparse.Namespace,
) -> ImportStats:
    source_type = "exchange_imap_oauth" if account.get("auth_method") == "oauth" else "imap"
    source_uri = source_uri_for_account_folder(account, folder.name)
    source_id = store.upsert_source(
        source_type=source_type,
        source_uri=source_uri,
        display_name=f"{account_label(account)} {folder.name}",
        auth_mode=account.get("auth_method") or "password",
        is_active=False,
    )
    store.record_sync_folder_start(
        account=account,
        folder_path=folder.name,
        source_id=source_id,
        source_type=source_type,
        source_uri=source_uri,
        metadata={
            "mode": "imap_bulk_import",
            "newer_than_existing": bool(args.newer_than_existing),
        },
    )
    job_id = store.create_import_job(
        source_id=source_id,
        mode="imap_bulk_import",
        metadata={
            "account": account_label(account),
            "folder": folder.name,
            "source_uri": source_uri,
        },
    )
    existing_source_message_ids = (
        set() if args.replace_existing else load_existing_source_message_ids(store, source_id)
    )
    target_folder = imap_target_folder(account_root, folder)
    store.ensure_mailbox_folder(mailbox_id, target_folder)
    for special_folder in special_mailbox_folders(folder, disabled=args.no_map_specials):
        store.ensure_mailbox_folder(mailbox_id, special_folder)
    store.connection.commit()
    mailbox_mapper = MailboxMessageMapper(store=store, mailbox_id=mailbox_id)

    stats = ImportStats()
    connection = connect_account(account, settings, timeout=args.imap_timeout)
    try:
        status, _ = connection.select(quote_imap_astring(folder.name), readonly=True)
        if status != "OK":
            raise ImportSourceError(f"Could not select IMAP folder: {folder.name}")
        uidvalidity = selected_uidvalidity(connection)
        min_uid = None
        if args.newer_than_existing and not args.replace_existing:
            min_uid = next_uid_after_existing(existing_source_message_ids, uidvalidity)
        uids = search_folder_uids(connection, folder.name, min_uid=min_uid)
        highest_uid = uids[-1].decode("ascii", errors="replace") if uids else ""
        print(
            f"Importing account={account_label(account)} folder={folder.name} messages={len(uids)}"
            f"{' min_uid=' + str(min_uid) if min_uid else ''}",
            flush=True,
        )
        pending_fetches: list[tuple[bytes, str, str]] = []
        for uid_bytes in uids:
            if args.limit_per_folder and stats.scanned >= args.limit_per_folder:
                break
            if args.limit_total and stats.scanned >= args.limit_total:
                break
            uid = uid_bytes.decode("ascii", errors="replace")
            source_message_id = f"{uidvalidity}:{uid}" if uidvalidity else uid
            stats.scanned += 1
            if (
                not args.replace_existing
                and source_message_id in existing_source_message_ids
            ):
                stats.skipped_existing += 1
                continue
            pending_fetches.append((uid_bytes, uid, source_message_id))
            if len(pending_fetches) >= max(args.fetch_batch_size, 1):
                process_pending_imap_fetches(
                    store,
                    connection=connection,
                    pending_fetches=pending_fetches,
                    source_id=source_id,
                    job_id=job_id,
                    source_uri=source_uri,
                    account=account,
                    folder=folder,
                    mailbox_mapper=mailbox_mapper,
                    target_folder=target_folder,
                    uidvalidity=uidvalidity,
                    existing_source_message_ids=existing_source_message_ids,
                    stats=stats,
                    args=args,
                )
                pending_fetches.clear()
        if pending_fetches:
            process_pending_imap_fetches(
                store,
                connection=connection,
                pending_fetches=pending_fetches,
                source_id=source_id,
                job_id=job_id,
                source_uri=source_uri,
                account=account,
                folder=folder,
                mailbox_mapper=mailbox_mapper,
                target_folder=target_folder,
                uidvalidity=uidvalidity,
                existing_source_message_ids=existing_source_message_ids,
                stats=stats,
                args=args,
            )
        store.connection.commit()
        store.record_sync_folder_success(
            account=account,
            folder_path=folder.name,
            source_id=source_id,
            source_type=source_type,
            source_uri=source_uri,
            remote_uid_count=len(uids),
            highest_uid=highest_uid,
            uidvalidity=uidvalidity,
            min_uid=min_uid,
            scanned=stats.scanned,
            imported=stats.imported,
            skipped_existing=stats.skipped_existing,
            deduped_existing=stats.deduped_existing,
            metadata={
                "mode": "imap_bulk_import",
                "target_folder": target_folder,
            },
        )
        store.connection.commit()
    finally:
        try:
            connection.close()
        except imaplib.IMAP4.error:
            pass
        logout(connection)

    print(
        f"  done folder={folder.name} scanned={stats.scanned} imported={stats.imported} "
        f"skipped_existing={stats.skipped_existing} deduped_existing={stats.deduped_existing}",
        flush=True,
    )
    return stats


def record_folder_sync_failure(
    store: PostgresMailStore,
    *,
    account: dict[str, Any],
    folder: FolderInfo,
    error_message: str,
) -> None:
    source_type = "exchange_imap_oauth" if account.get("auth_method") == "oauth" else "imap"
    source_uri = source_uri_for_account_folder(account, folder.name)
    source_id = stable_id("source", source_type, source_uri)
    store.record_sync_folder_failure(
        account=account,
        folder_path=folder.name,
        source_id=source_id,
        source_type=source_type,
        source_uri=source_uri,
        error_message=error_message,
        metadata={"mode": "imap_bulk_import"},
    )


def process_pending_imap_fetches(
    store: PostgresMailStore,
    *,
    connection: imaplib.IMAP4,
    pending_fetches: list[tuple[bytes, str, str]],
    source_id: str,
    job_id: str,
    source_uri: str,
    account: dict[str, Any],
    folder: FolderInfo,
    mailbox_mapper: MailboxMessageMapper,
    target_folder: str,
    uidvalidity: str,
    existing_source_message_ids: set[str],
    stats: ImportStats,
    args: argparse.Namespace,
) -> None:
    raw_messages = fetch_messages(connection, [item[0] for item in pending_fetches])
    for uid_bytes, uid, source_message_id in pending_fetches:
        raw_bytes = raw_messages.get(uid)
        if raw_bytes is None:
            continue
        normalized = normalize_email(
            raw_bytes,
            source_message_id=source_message_id,
            source_uri=source_uri,
            folder=folder.name,
            metadata={
                "imap_account": account_label(account),
                "imap_folder": folder.name,
                "imap_uid": uid,
                "imap_uidvalidity": uidvalidity,
                "millie_mailbox_folder": target_folder,
            },
        )
        existing_id = None
        if not args.allow_raw_duplicates:
            existing_id = existing_message_id_for_raw_hash(store, normalized.raw_mime_sha256)
        if existing_id and existing_id != normalized.id:
            record_source_message_alias(
                store,
                source_id=source_id,
                source_message_id=source_message_id,
                message_id=existing_id,
                raw_mime_sha256=normalized.raw_mime_sha256,
                metadata={
                    "imap_account": account_label(account),
                    "imap_folder": folder.name,
                    "imap_uid": uid,
                    "imap_uidvalidity": uidvalidity,
                    "dedupe": "raw_mime_sha256",
                },
            )
            map_existing_message_to_source_folder(
                store,
                source_id=source_id,
                folder_path=folder.name,
                message_id=existing_id,
            )
            map_to_mailbox_views(
                mailbox_mapper,
                target_folder=target_folder,
                message_id=existing_id,
                special_folders=special_mailbox_folders(folder, disabled=args.no_map_specials),
            )
            stats.deduped_existing += 1
            existing_source_message_ids.add(source_message_id)
        else:
            store.store_message(
                source_id=source_id,
                import_job_id=job_id,
                message=normalized,
                folder=folder.name,
            )
            map_to_mailbox_views(
                mailbox_mapper,
                target_folder=target_folder,
                message_id=normalized.id,
                special_folders=special_mailbox_folders(folder, disabled=args.no_map_specials),
            )
            stats.imported += 1
            existing_source_message_ids.add(source_message_id)
        if stats.changed and stats.changed % max(args.commit_every, 1) == 0:
            store.connection.commit()
            print(
                f"  folder={folder.name} scanned={stats.scanned} imported={stats.imported} "
                f"skipped_existing={stats.skipped_existing} deduped_existing={stats.deduped_existing}",
                flush=True,
            )


def load_existing_source_message_ids(store: PostgresMailStore, source_id: str) -> set[str]:
    rows = store.connection.execute(
        """
        SELECT source_message_id
        FROM mail_messages
        WHERE source_id = %s
        UNION
        SELECT source_message_id
        FROM mail_source_message_aliases
        WHERE source_id = %s
        """,
        (source_id, source_id),
    ).fetchall()
    return {str(row[0]) for row in rows}


def next_uid_after_existing(source_message_ids: Iterable[str], uidvalidity: str) -> int | None:
    max_uid = 0
    prefix = f"{uidvalidity}:" if uidvalidity else ""
    for source_message_id in source_message_ids:
        if prefix:
            if not source_message_id.startswith(prefix):
                continue
            uid_text = source_message_id[len(prefix) :]
        else:
            uid_text = source_message_id.rsplit(":", 1)[-1]
        if uid_text.isdigit():
            max_uid = max(max_uid, int(uid_text))
    return max_uid + 1 if max_uid else None


def search_folder_uids(
    connection: imaplib.IMAP4,
    folder_name: str,
    *,
    min_uid: int | None = None,
) -> list[bytes]:
    if min_uid is None:
        status, search_data = connection.uid("SEARCH", None, "ALL")
    else:
        status, search_data = connection.uid("SEARCH", None, "UID", f"{min_uid}:*")
    if status != "OK":
        raise ImportSourceError(f"UID SEARCH failed for folder: {folder_name}")
    return (search_data[0] or b"").split()


def connect_account(
    account: dict[str, Any],
    settings: dict[str, str],
    *,
    timeout: int,
) -> imaplib.IMAP4:
    host = account["host"]
    port = int(account.get("port") or 993)
    security = account.get("security") or "ssl_tls"
    if security == "ssl_tls":
        connection: imaplib.IMAP4 = imaplib.IMAP4_SSL(host, port, timeout=timeout)
    else:
        connection = imaplib.IMAP4(host, port, timeout=timeout)
        if security == "starttls":
            connection.starttls()

    auth_method = account.get("auth_method") or "password"
    if auth_method == "oauth":
        access_token = oauth_access_token(settings)
        auth_string = f"user={account['username']}\x01auth=Bearer {access_token}\x01\x01"
        connection.authenticate("XOAUTH2", lambda _: auth_string.encode("utf-8"))
        return connection
    if auth_method == "password":
        connection.login(account["username"], account.get("password") or "")
        return connection
    if auth_method == "none":
        return connection
    raise ImportSourceError(f"Unsupported IMAP auth method: {auth_method}")


def fetch_message(connection: imaplib.IMAP4, uid: bytes) -> bytes | None:
    status, fetch_data = connection.uid("FETCH", uid, "(BODY.PEEK[])")
    if status != "OK":
        raise ImportSourceError(f"UID FETCH failed for UID {uid.decode('ascii', errors='replace')}")
    return _extract_imap_fetch_bytes(fetch_data)


def fetch_messages(connection: imaplib.IMAP4, uids: list[bytes]) -> dict[str, bytes]:
    if not uids:
        return {}
    if len(uids) == 1:
        uid = uids[0]
        raw_bytes = fetch_message(connection, uid)
        return {uid.decode("ascii", errors="replace"): raw_bytes} if raw_bytes is not None else {}

    uid_set = b",".join(uids)
    status, fetch_data = connection.uid("FETCH", uid_set, "(UID BODY.PEEK[])")
    if status != "OK":
        raise ImportSourceError(f"UID FETCH failed for {len(uids)} IMAP messages")

    result = parse_uid_fetch_messages(fetch_data)
    for uid in uids:
        uid_text = uid.decode("ascii", errors="replace")
        if uid_text in result:
            continue
        raw_bytes = fetch_message(connection, uid)
        if raw_bytes is not None:
            result[uid_text] = raw_bytes
    return result


def parse_uid_fetch_messages(fetch_data: list[bytes | tuple[bytes, bytes]]) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for item in fetch_data:
        if not (isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], bytes)):
            continue
        metadata = item[0].decode("ascii", errors="replace")
        match = re.search(r"\bUID\s+(\d+)\b", metadata, flags=re.IGNORECASE)
        if match:
            result[match.group(1)] = item[1]
    return result


def oauth_access_token(settings: dict[str, str]) -> str:
    token = settings.get("microsoft_oauth_access_token") or ""
    expires_at = parse_datetime(settings.get("microsoft_oauth_expires_at") or "")
    if token and expires_at and expires_at > datetime.now(timezone.utc) + timedelta(minutes=5):
        return token
    return refresh_oauth_access_token(settings)


def refresh_oauth_access_token(settings: dict[str, str]) -> str:
    refresh_token = settings.get("microsoft_oauth_refresh_token") or ""
    if not refresh_token:
        raise SystemExit("Microsoft OAuth refresh token is missing.")
    tenant = settings.get("microsoft_oauth_tenant") or "organizations"
    body = {
        "client_id": settings["microsoft_oauth_client_id"],
        "scope": settings["microsoft_oauth_scopes"],
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    if settings.get("microsoft_oauth_client_secret"):
        body["client_secret"] = settings["microsoft_oauth_client_secret"]
    request = urllib.request.Request(
        f"https://login.microsoftonline.com/{urllib.parse.quote(tenant)}/oauth2/v2.0/token",
        data=urllib.parse.urlencode(body).encode("utf-8"),
        headers={"content-type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    token = payload.get("access_token")
    if not token:
        raise SystemExit("Microsoft OAuth refresh did not return an access token.")
    return token


def parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def parse_list_response(line: bytes | str) -> FolderInfo | None:
    text = line.decode("utf-8", errors="replace") if isinstance(line, bytes) else line
    open_index = text.find("(")
    close_index = text.find(")", open_index + 1)
    if open_index < 0 or close_index < 0:
        return None
    flags = tuple(part for part in text[open_index + 1 : close_index].split() if part)
    rest = text[close_index + 1 :].strip()
    delimiter, index = read_imap_astring(rest)
    name, _ = read_imap_astring(rest[index:].strip())
    if not name:
        return None
    return FolderInfo(name=name, delimiter=None if delimiter == "NIL" else delimiter, flags=flags)


def read_imap_astring(value: str) -> tuple[str | None, int]:
    text = value.lstrip()
    offset = len(value) - len(text)
    if not text:
        return None, offset
    if text.startswith('"'):
        escaped = False
        chars: list[str] = []
        for index, char in enumerate(text[1:], start=1):
            if escaped:
                chars.append(char)
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == '"':
                return "".join(chars), offset + index + 1
            chars.append(char)
        return "".join(chars), len(value)
    match = re.match(r"(\S+)", text)
    if not match:
        return None, offset
    token = match.group(1)
    return token, offset + len(token)


def selected_uidvalidity(connection: imaplib.IMAP4) -> str:
    _, values = connection.response("UIDVALIDITY")
    if values:
        for value in values:
            if value:
                return value.decode("ascii", errors="replace") if isinstance(value, bytes) else str(value)
    return ""


def existing_message_id_for_raw_hash(store: PostgresMailStore, raw_hash: str) -> str | None:
    row = store.connection.execute(
        """
        SELECT id
        FROM mail_messages
        WHERE raw_mime_sha256 = %s
        ORDER BY created_at
        LIMIT 1
        """,
        (raw_hash,),
    ).fetchone()
    return str(row[0]) if row else None


def map_existing_message_to_source_folder(
    store: PostgresMailStore,
    *,
    source_id: str,
    folder_path: str,
    message_id: str,
) -> None:
    folder_id = store._upsert_folder(source_id, folder_path)
    store.connection.execute(
        """
        INSERT INTO mail_message_folders (message_id, folder_id)
        VALUES (%s, %s)
        ON CONFLICT DO NOTHING
        """,
        (message_id, folder_id),
    )


def record_source_message_alias(
    store: PostgresMailStore,
    *,
    source_id: str,
    source_message_id: str,
    message_id: str,
    raw_mime_sha256: str,
    metadata: dict[str, Any],
) -> None:
    store.connection.execute(
        """
        INSERT INTO mail_source_message_aliases (
            source_id, source_message_id, message_id, raw_mime_sha256, metadata_json
        )
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT(source_id, source_message_id) DO UPDATE SET
            message_id = excluded.message_id,
            raw_mime_sha256 = excluded.raw_mime_sha256,
            metadata_json = excluded.metadata_json
        """,
        (source_id, source_message_id, message_id, raw_mime_sha256, Jsonb(metadata)),
    )


def map_to_mailbox_views(
    mailbox_mapper: MailboxMessageMapper,
    *,
    target_folder: str,
    message_id: str,
    special_folders: Iterable[str],
) -> None:
    targets = [target_folder, "All Mail", *special_folders]
    seen: set[str] = set()
    for folder_path in targets:
        if folder_path in seen:
            continue
        seen.add(folder_path)
        mailbox_mapper.map_message(
            folder_path=folder_path,
            message_id=message_id,
        )


def imap_target_folder(account_root: str, folder: FolderInfo) -> str:
    delimiter = folder.delimiter or "/"
    parts = [part for part in folder.name.replace("\\", "/").split(delimiter) if part and part != "."]
    cleaned = "/".join(safe_folder_component(part) for part in parts)
    return f"{account_root}/{cleaned}" if cleaned else account_root


def special_mailbox_folders(folder: FolderInfo, *, disabled: bool) -> list[str]:
    if disabled:
        return []
    lowered_flags = {flag.lower() for flag in folder.flags}
    normalized_name = folder.name.strip().lower()
    leaf_name = normalized_name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if normalized_name == "inbox" or leaf_name == "inbox":
        return ["INBOX"]
    if "\\sent" in lowered_flags or leaf_name in {"sent", "sent mail", "sent items"}:
        return ["Sent"]
    if "\\drafts" in lowered_flags or leaf_name in {"drafts", "draft"}:
        return ["Drafts"]
    if "\\trash" in lowered_flags or leaf_name in {"trash", "deleted items", "deleted messages"}:
        return ["Trash"]
    if "\\junk" in lowered_flags or leaf_name in {"junk", "junk email", "spam"}:
        return ["Junk"]
    if "\\archive" in lowered_flags or leaf_name == "archive":
        return ["Archive"]
    return []


def source_uri_for_account_folder(account: dict[str, Any], folder_name: str) -> str:
    username = urllib.parse.quote(str(account["username"]), safe="")
    host = account["host"]
    port = int(account.get("port") or 993)
    folder = urllib.parse.quote(folder_name, safe="")
    return f"imap://{username}@{host}:{port}/{folder}"


def account_label(account: dict[str, Any]) -> str:
    return str(account.get("email_address") or account.get("username") or account.get("id"))


def quote_imap_astring(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def folder_sort_key(name: str) -> tuple[int, str]:
    lowered = name.lower()
    if lowered == "inbox":
        return (0, lowered)
    if "sent" in lowered:
        return (1, lowered)
    return (2, lowered)


def safe_folder_component(value: str) -> str:
    text = re.sub(r"[\r\n\t/\\:]+", "_", value).strip(" ._")
    text = re.sub(r"\s+", " ", text)
    return text or "Archive"


def logout(connection: imaplib.IMAP4) -> None:
    try:
        connection.logout()
    except imaplib.IMAP4.error:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
