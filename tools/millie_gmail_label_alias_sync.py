#!/usr/bin/env python3
"""Map Gmail label UIDs to existing MILLIE messages using X-GM-MSGID."""

from __future__ import annotations

import argparse
import imaplib
import re
import sys
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from millie.importing.models import stable_id
from millie.service.auth import default_service_login, identity_from_settings
from millie.settings_loader import load_local_settings
from millie.storage.postgres_store import PostgresMailStore
from tools.millie_imap_bulk_import import (
    FolderInfo,
    MailboxMessageMapper,
    account_label,
    connect_account,
    imap_target_folder,
    logout,
    map_existing_message_to_source_folder,
    map_to_mailbox_views,
    quote_imap_astring,
    record_source_message_alias,
    safe_folder_component,
    search_folder_uids,
    selected_accounts,
    selected_uidvalidity,
    source_uri_for_account_folder,
    special_mailbox_folders,
)


@dataclass(frozen=True, slots=True)
class KnownRef:
    source_uri: str
    source_id: str
    source_message_id: str
    message_id: str
    raw_mime_sha256: str
    source_table: str


@dataclass(frozen=True, slots=True)
class AliasCandidate:
    folder: FolderInfo
    source_id: str
    source_uri: str
    source_message_id: str
    uidvalidity: str
    uid: str
    gmail_message_id: str
    target: KnownRef


@dataclass(slots=True)
class FolderAliasStats:
    provider_uids: int = 0
    existing_source_uids: int = 0
    alias_candidates: int = 0
    unmatched_source_uids: int = 0
    applied_aliases: int = 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "For Gmail label folders, create missing MILLIE source UID aliases "
            "when Gmail X-GM-MSGID proves the label UID is the same message as an "
            "already copied canonical MILLIE message. This command never mutates Gmail."
        )
    )
    parser.add_argument("--account", action="append", default=[])
    parser.add_argument(
        "--folder",
        action="append",
        required=True,
        help="Exact Gmail label folder to reconcile. May be repeated.",
    )
    parser.add_argument("--apply", action="store_true", help="Write alias rows and mailbox mappings.")
    parser.add_argument("--login", default="", help="MILLIE login. Defaults to geon@<service_mail_domain>.")
    parser.add_argument("--display-name", default="Geon")
    parser.add_argument("--imap-timeout", type=int, default=120)
    parser.add_argument("--metadata-batch-size", type=int, default=500)
    parser.add_argument("--commit-every", type=int, default=1000)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_local_settings()
    settings = config["settings"]
    if (settings.get("database_mode") or "").lower() != "postgres":
        raise SystemExit("Gmail label alias sync currently requires database_mode=postgres.")

    accounts = [
        account
        for account in selected_accounts(config["accounts"], args.account)
        if str(account.get("host") or "").lower() == "imap.gmail.com"
    ]
    if not accounts:
        raise SystemExit("No enabled Gmail IMAP accounts matched.")

    store = PostgresMailStore.connect(settings)
    try:
        store.initialize()
        mailbox_id = ""
        if args.apply:
            login = args.login or default_service_login(settings, "geon")
            identity = identity_from_settings(login, args.display_name, settings)
            mailbox_id = store.ensure_identity(identity)
        store.connection.commit()

        total = FolderAliasStats()
        print("MILLIE Gmail label alias sync", flush=True)
        print(f"Mode: {'apply' if args.apply else 'dry-run'}", flush=True)
        print(f"Accounts: {len(accounts)}", flush=True)
        for account in accounts:
            account_stats = reconcile_account(
                store,
                mailbox_id=mailbox_id,
                account=account,
                settings=settings,
                folder_names=args.folder,
                args=args,
            )
            total.provider_uids += account_stats.provider_uids
            total.existing_source_uids += account_stats.existing_source_uids
            total.alias_candidates += account_stats.alias_candidates
            total.unmatched_source_uids += account_stats.unmatched_source_uids
            total.applied_aliases += account_stats.applied_aliases
        store.connection.commit()
    finally:
        store.close()

    print(
        "gmail_label_alias_sync=done "
        f"provider_uids={total.provider_uids} existing_source_uids={total.existing_source_uids} "
        f"alias_candidates={total.alias_candidates} applied_aliases={total.applied_aliases} "
        f"unmatched_source_uids={total.unmatched_source_uids}",
        flush=True,
    )
    return 0 if total.unmatched_source_uids == 0 else 1


def reconcile_account(
    store: PostgresMailStore,
    *,
    mailbox_id: str,
    account: dict[str, Any],
    settings: dict[str, str],
    folder_names: list[str],
    args: argparse.Namespace,
) -> FolderAliasStats:
    folders = unique_folders([FolderInfo(name=name, delimiter="/", flags=()) for name in folder_names])
    print(
        f"- {account_label(account)} {account.get('host')}:{account.get('port') or 993} folders={len(folders)}",
        flush=True,
    )
    known_refs_by_folder = load_account_known_refs(store, account)
    gmail_index = build_gmail_message_index(
        account,
        settings,
        known_refs_by_folder,
        timeout=args.imap_timeout,
        batch_size=args.metadata_batch_size,
    )
    print(f"  indexed gmail_message_ids={len(gmail_index)}", flush=True)

    account_root = f"Sources/IMAP/{safe_folder_component(account.get('email_address') or account['username'])}"
    stats = FolderAliasStats()
    for folder in folders:
        folder_stats = reconcile_target_folder(
            store,
            mailbox_id=mailbox_id,
            account=account,
            settings=settings,
            account_root=account_root,
            folder=folder,
            gmail_index=gmail_index,
            args=args,
        )
        stats.provider_uids += folder_stats.provider_uids
        stats.existing_source_uids += folder_stats.existing_source_uids
        stats.alias_candidates += folder_stats.alias_candidates
        stats.unmatched_source_uids += folder_stats.unmatched_source_uids
        stats.applied_aliases += folder_stats.applied_aliases
    return stats


def build_gmail_message_index(
    account: dict[str, Any],
    settings: dict[str, str],
    refs_by_folder: dict[str, list[KnownRef]],
    *,
    timeout: int,
    batch_size: int,
) -> dict[str, KnownRef]:
    index: dict[str, KnownRef] = {}
    for folder_name, refs in sorted(refs_by_folder.items()):
        uid_refs = {uid_from_source_message_id(ref.source_message_id): ref for ref in refs}
        uid_refs = {uid: ref for uid, ref in uid_refs.items() if uid}
        if not uid_refs:
            continue
        fetched = fetch_gmail_message_ids(
            account,
            settings,
            folder_name,
            sorted(uid_refs),
            timeout=timeout,
            batch_size=batch_size,
        )
        for uid, gmail_message_id in fetched.items():
            ref = uid_refs.get(uid)
            if ref is not None:
                index.setdefault(gmail_message_id, ref)
        print(
            f"  indexed folder={folder_name} refs={len(uid_refs)} gmail_ids={len(fetched)}",
            flush=True,
        )
    return index


def reconcile_target_folder(
    store: PostgresMailStore,
    *,
    mailbox_id: str,
    account: dict[str, Any],
    settings: dict[str, str],
    account_root: str,
    folder: FolderInfo,
    gmail_index: dict[str, KnownRef],
    args: argparse.Namespace,
) -> FolderAliasStats:
    stats = FolderAliasStats()
    source_uri = source_uri_for_account_folder(account, folder.name)
    source_id = stable_id("source", "imap", source_uri)
    if args.apply:
        source_id = store.upsert_source(
            source_type="imap",
            source_uri=source_uri,
            display_name=f"{account_label(account)} {folder.name}",
            auth_mode=account.get("auth_method") or "password",
            is_active=False,
        )
    existing_source_ids = load_existing_source_ids(store, source_id)
    uidvalidity, uid_gmail_ids = target_folder_gmail_message_ids(
        account,
        settings,
        folder.name,
        timeout=args.imap_timeout,
        batch_size=args.metadata_batch_size,
    )
    stats.provider_uids = len(uid_gmail_ids)

    candidates: list[AliasCandidate] = []
    unmatched: list[str] = []
    for uid, gmail_message_id in uid_gmail_ids.items():
        source_message_id = f"{uidvalidity}:{uid}" if uidvalidity else uid
        if source_message_id in existing_source_ids:
            stats.existing_source_uids += 1
            continue
        target = gmail_index.get(gmail_message_id)
        if target is None:
            unmatched.append(source_message_id)
            continue
        candidates.append(
            AliasCandidate(
                folder=folder,
                source_id=source_id,
                source_uri=source_uri,
                source_message_id=source_message_id,
                uidvalidity=uidvalidity,
                uid=uid,
                gmail_message_id=gmail_message_id,
                target=target,
            )
        )
    stats.alias_candidates = len(candidates)
    stats.unmatched_source_uids = len(unmatched)
    print(
        f"  folder={folder.name} provider_uids={stats.provider_uids} "
        f"existing={stats.existing_source_uids} alias_candidates={stats.alias_candidates} "
        f"unmatched={stats.unmatched_source_uids}",
        flush=True,
    )
    if unmatched:
        print(f"    unmatched sample: {', '.join(unmatched[:5])}", flush=True)
    if not args.apply:
        return stats

    target_folder = imap_target_folder(account_root, folder)
    store.ensure_mailbox_folder(mailbox_id, target_folder)
    for special_folder in special_mailbox_folders(folder, disabled=False):
        store.ensure_mailbox_folder(mailbox_id, special_folder)
    store.connection.commit()
    mailbox_mapper = MailboxMessageMapper(store=store, mailbox_id=mailbox_id)
    for index, candidate in enumerate(candidates, start=1):
        record_source_message_alias(
            store,
            source_id=candidate.source_id,
            source_message_id=candidate.source_message_id,
            message_id=candidate.target.message_id,
            raw_mime_sha256=candidate.target.raw_mime_sha256,
            metadata={
                "dedupe": "gmail_x_gm_msgid",
                "gmail_x_gm_msgid": candidate.gmail_message_id,
                "imap_account": account_label(account),
                "imap_folder": candidate.folder.name,
                "imap_uid": candidate.uid,
                "imap_uidvalidity": candidate.uidvalidity,
                "matched_source_uri": candidate.target.source_uri,
                "matched_source_message_id": candidate.target.source_message_id,
            },
        )
        map_existing_message_to_source_folder(
            store,
            source_id=candidate.source_id,
            folder_path=candidate.folder.name,
            message_id=candidate.target.message_id,
        )
        map_to_mailbox_views(
            mailbox_mapper,
            target_folder=target_folder,
            message_id=candidate.target.message_id,
            special_folders=special_mailbox_folders(candidate.folder, disabled=False),
        )
        stats.applied_aliases += 1
        if index % max(args.commit_every, 1) == 0:
            store.connection.commit()
            print(f"    applied_aliases={stats.applied_aliases}", flush=True)
    store.connection.commit()
    return stats


def load_account_known_refs(
    store: PostgresMailStore,
    account: dict[str, Any],
) -> dict[str, list[KnownRef]]:
    prefix = account_source_uri_prefix(account)
    rows = store.connection.execute(
        """
        SELECT s.source_uri, s.id, m.source_message_id, m.id, m.raw_mime_sha256, 'mail_messages'
        FROM mail_sources s
        JOIN mail_messages m ON m.source_id = s.id
        WHERE s.source_type = 'imap'
          AND s.source_uri LIKE %s
        UNION ALL
        SELECT s.source_uri, s.id, a.source_message_id, a.message_id, a.raw_mime_sha256,
               'mail_source_message_aliases'
        FROM mail_sources s
        JOIN mail_source_message_aliases a ON a.source_id = s.id
        WHERE s.source_type = 'imap'
          AND s.source_uri LIKE %s
        """,
        (f"{prefix}%", f"{prefix}%"),
    ).fetchall()
    refs_by_folder: dict[str, list[KnownRef]] = {}
    for source_uri, source_id, source_message_id, message_id, raw_hash, source_table in rows:
        folder = folder_name_from_source_uri(str(source_uri))
        refs_by_folder.setdefault(folder, []).append(
            KnownRef(
                source_uri=str(source_uri),
                source_id=str(source_id),
                source_message_id=str(source_message_id),
                message_id=str(message_id),
                raw_mime_sha256=str(raw_hash),
                source_table=str(source_table),
            )
        )
    return refs_by_folder


def load_existing_source_ids(store: PostgresMailStore, source_id: str) -> set[str]:
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


def target_folder_gmail_message_ids(
    account: dict[str, Any],
    settings: dict[str, str],
    folder_name: str,
    *,
    timeout: int,
    batch_size: int,
) -> tuple[str, dict[str, str]]:
    connection = connect_account(account, settings, timeout=timeout)
    try:
        status, _ = connection.select(quote_imap_astring(folder_name), readonly=True)
        if status != "OK":
            raise RuntimeError(f"Could not select Gmail folder: {folder_name}")
        uidvalidity = selected_uidvalidity(connection)
        uids = [
            uid.decode("ascii", errors="replace")
            for uid in search_folder_uids(connection, folder_name)
        ]
        return uidvalidity, fetch_gmail_message_ids_on_connection(connection, uids, batch_size=batch_size)
    finally:
        try:
            connection.close()
        except imaplib.IMAP4.error:
            pass
        logout(connection)


def fetch_gmail_message_ids(
    account: dict[str, Any],
    settings: dict[str, str],
    folder_name: str,
    uids: list[str],
    *,
    timeout: int,
    batch_size: int,
) -> dict[str, str]:
    connection = connect_account(account, settings, timeout=timeout)
    try:
        status, _ = connection.select(quote_imap_astring(folder_name), readonly=True)
        if status != "OK":
            return {}
        return fetch_gmail_message_ids_on_connection(connection, uids, batch_size=batch_size)
    finally:
        try:
            connection.close()
        except imaplib.IMAP4.error:
            pass
        logout(connection)


def fetch_gmail_message_ids_on_connection(
    connection: imaplib.IMAP4,
    uids: list[str],
    *,
    batch_size: int,
) -> dict[str, str]:
    result: dict[str, str] = {}
    for index in range(0, len(uids), max(batch_size, 1)):
        batch = [uid for uid in uids[index : index + max(batch_size, 1)] if uid.isdigit()]
        if not batch:
            continue
        status, data = connection.uid("FETCH", ",".join(batch), "(X-GM-MSGID)")
        if status != "OK":
            continue
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


def account_source_uri_prefix(account: dict[str, Any]) -> str:
    username = urllib.parse.quote(str(account["username"]), safe="")
    host = account["host"]
    port = int(account.get("port") or 993)
    return f"imap://{username}@{host}:{port}/"


def folder_name_from_source_uri(source_uri: str) -> str:
    path = urllib.parse.urlparse(source_uri).path.lstrip("/")
    return urllib.parse.unquote(path)


def uid_from_source_message_id(source_message_id: str) -> str:
    uid = source_message_id.rsplit(":", 1)[-1]
    return uid if uid.isdigit() else ""


def unique_folders(folders: list[FolderInfo]) -> list[FolderInfo]:
    result: list[FolderInfo] = []
    seen: set[str] = set()
    for folder in folders:
        key = folder.name.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(folder)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
