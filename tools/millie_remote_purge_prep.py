#!/usr/bin/env python3
"""Audit live mail coverage and tag MILLIE copies before any provider purge."""

from __future__ import annotations

import argparse
import imaplib
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from millie.importing.models import stable_id
from millie.settings_loader import load_local_settings
from millie.storage.postgres_store import PostgresMailStore
from tools.millie_imap_bulk_import import (
    FolderInfo,
    account_label,
    connect_account,
    list_account_folders,
    logout,
    quote_imap_astring,
    search_folder_uids,
    selected_accounts,
    selected_folders,
    selected_uidvalidity,
    source_uri_for_account_folder,
)


@dataclass(frozen=True, slots=True)
class SourceMessageRecord:
    source_message_id: str
    message_id: str
    raw_mime_sha256: str
    source_table: str


@dataclass(frozen=True, slots=True)
class PurgeCandidate:
    message_id: str
    source_id: str
    source_message_id: str
    source_type: str
    source_uri: str
    source_account: str
    source_folder: str
    imap_uidvalidity: str
    imap_uid: str
    raw_mime_sha256: str
    source_table: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class FolderAudit:
    account: str
    folder: str
    source_id: str
    source_type: str
    source_uri: str
    uidvalidity: str
    provider_count: int
    known_count: int
    missing_source_message_ids: list[str]
    candidates: list[PurgeCandidate]


@dataclass(slots=True)
class FailedFolder:
    account: str
    folder: str
    error: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Confirm live provider UIDs are copied into MILLIE, then optionally "
            "tag those MILLIE copies as protected before a future provider-side purge. "
            "This command never deletes or moves provider mail."
        )
    )
    parser.add_argument(
        "--account",
        action="append",
        default=[],
        help="Account email, id, username, or display name to audit. May be repeated.",
    )
    parser.add_argument(
        "--folder",
        action="append",
        default=[],
        help="Exact provider folder to audit. May be repeated. Defaults to all selectable mail folders.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the MILLIE-side protection manifest and message tags after a complete audit.",
    )
    parser.add_argument(
        "--manifest-id",
        default="",
        help="Optional manifest id. Defaults to remote-purge-prep-<UTC timestamp>.",
    )
    parser.add_argument(
        "--action",
        choices=("move_to_trash", "delete", "hard_delete"),
        default="move_to_trash",
        help="Future provider-side action recorded in the manifest. No provider action is run here.",
    )
    parser.add_argument("--imap-timeout", type=int, default=120)
    parser.add_argument("--include-non-mail-folders", action="store_true")
    parser.add_argument("--stop-on-error", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print a compact JSON summary after text output.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_local_settings()
    settings = config["settings"]
    if (settings.get("database_mode") or "").lower() != "postgres":
        raise SystemExit("Remote purge preparation currently requires database_mode=postgres.")

    accounts = selected_accounts(config["accounts"], args.account)
    if not accounts:
        raise SystemExit("No enabled IMAP accounts matched.")

    store = PostgresMailStore.connect(settings)
    try:
        store.initialize()
        store.connection.commit()
        audits, failed = audit_accounts(store, accounts, settings, args)
        summary = summarize_audits(audits, failed)
        print_summary(summary)
        if args.json:
            print(json.dumps(summary, sort_keys=True), flush=True)

        if failed:
            print("Not prepared: at least one provider folder failed audit.", flush=True)
            return 2
        if summary["missing_source_uids"]:
            print("Not prepared: at least one provider UID is not copied into MILLIE.", flush=True)
            return 2
        if not args.apply:
            print("Dry run only. Re-run with --apply to tag protected MILLIE copies.", flush=True)
            return 0

        manifest_id = args.manifest_id or default_manifest_id()
        candidates = flatten_candidates(audits)
        write_manifest(
            store,
            manifest_id=manifest_id,
            action=args.action,
            candidates=candidates,
            summary=summary,
        )
        store.connection.commit()
        print(
            "remote_purge_prep=prepared "
            f"manifest_id={manifest_id} protected_messages={summary['unique_messages']} "
            f"source_uids={summary['known_source_uids']} provider_action_not_run=true",
            flush=True,
        )
    finally:
        store.close()
    return 0


def audit_accounts(
    store: PostgresMailStore,
    accounts: list[dict[str, Any]],
    settings: dict[str, str],
    args: argparse.Namespace,
) -> tuple[list[FolderAudit], list[FailedFolder]]:
    audits: list[FolderAudit] = []
    failed: list[FailedFolder] = []
    print("MILLIE remote purge prep audit", flush=True)
    print(f"Mode: {'apply' if args.apply else 'dry-run'}", flush=True)
    print(f"Accounts: {len(accounts)}", flush=True)
    for account in accounts:
        folders = list_account_folders(account, settings, timeout=args.imap_timeout)
        folders = selected_folders(
            folders,
            args.folder,
            include_non_mail=args.include_non_mail_folders,
        )
        print(
            f"- {account_label(account)} {account.get('host')}:{account.get('port') or 993} "
            f"auth={account.get('auth_method') or 'password'} folders={len(folders)}",
            flush=True,
        )
        for folder in folders:
            try:
                audit = audit_folder(
                    store,
                    account=account,
                    folder=folder,
                    settings=settings,
                    timeout=args.imap_timeout,
                )
                audits.append(audit)
                status = "ok" if not audit.missing_source_message_ids else "missing"
                print(
                    f"  {status} folder={folder.name} provider_uids={audit.provider_count} "
                    f"known={audit.known_count} missing={len(audit.missing_source_message_ids)}",
                    flush=True,
                )
                if audit.missing_source_message_ids:
                    sample = ", ".join(audit.missing_source_message_ids[:5])
                    print(f"    missing sample: {sample}", flush=True)
            except Exception as exc:  # noqa: BLE001 - audit every folder unless asked to stop.
                error = f"{type(exc).__name__}: {exc}"
                failed.append(FailedFolder(account=account_label(account), folder=folder.name, error=error))
                print(f"  FAILED folder={folder.name}: {error}", flush=True)
                if args.stop_on_error:
                    raise
    return audits, failed


def audit_folder(
    store: PostgresMailStore,
    *,
    account: dict[str, Any],
    folder: FolderInfo,
    settings: dict[str, str],
    timeout: int,
) -> FolderAudit:
    source_type = "exchange_imap_oauth" if account.get("auth_method") == "oauth" else "imap"
    source_uri = source_uri_for_account_folder(account, folder.name)
    source_id = stable_id("source", source_type, source_uri)
    known_records = load_existing_source_message_map(store, source_id)
    connection = connect_account(account, settings, timeout=timeout)
    try:
        status, _ = connection.select(quote_imap_astring(folder.name), readonly=True)
        if status != "OK":
            raise RuntimeError(f"Could not select IMAP folder: {folder.name}")
        uidvalidity = selected_uidvalidity(connection)
        provider_uids = [
            uid.decode("ascii", errors="replace")
            for uid in search_folder_uids(connection, folder.name)
        ]
    finally:
        try:
            connection.close()
        except imaplib.IMAP4.error:
            pass
        logout(connection)

    candidates: list[PurgeCandidate] = []
    missing: list[str] = []
    account_name = account_label(account)
    for uid in provider_uids:
        source_message_id = f"{uidvalidity}:{uid}" if uidvalidity else uid
        record = known_records.get(source_message_id)
        if record is None:
            missing.append(source_message_id)
            continue
        candidates.append(
            PurgeCandidate(
                message_id=record.message_id,
                source_id=source_id,
                source_message_id=source_message_id,
                source_type=source_type,
                source_uri=source_uri,
                source_account=account_name,
                source_folder=folder.name,
                imap_uidvalidity=uidvalidity,
                imap_uid=uid,
                raw_mime_sha256=record.raw_mime_sha256,
                source_table=record.source_table,
            )
        )

    return FolderAudit(
        account=account_name,
        folder=folder.name,
        source_id=source_id,
        source_type=source_type,
        source_uri=source_uri,
        uidvalidity=uidvalidity,
        provider_count=len(provider_uids),
        known_count=len(candidates),
        missing_source_message_ids=missing,
        candidates=candidates,
    )


def load_existing_source_message_map(
    store: PostgresMailStore,
    source_id: str,
) -> dict[str, SourceMessageRecord]:
    direct_rows = store.connection.execute(
        """
        SELECT source_message_id, id, raw_mime_sha256
        FROM mail_messages
        WHERE source_id = %s
        """,
        (source_id,),
    ).fetchall()
    alias_rows = store.connection.execute(
        """
        SELECT source_message_id, message_id, raw_mime_sha256
        FROM mail_source_message_aliases
        WHERE source_id = %s
        """,
        (source_id,),
    ).fetchall()
    records: dict[str, SourceMessageRecord] = {}
    for source_message_id, message_id, raw_hash in direct_rows:
        records[str(source_message_id)] = SourceMessageRecord(
            source_message_id=str(source_message_id),
            message_id=str(message_id),
            raw_mime_sha256=str(raw_hash),
            source_table="mail_messages",
        )
    for source_message_id, message_id, raw_hash in alias_rows:
        records.setdefault(
            str(source_message_id),
            SourceMessageRecord(
                source_message_id=str(source_message_id),
                message_id=str(message_id),
                raw_mime_sha256=str(raw_hash),
                source_table="mail_source_message_aliases",
            ),
        )
    return records


def summarize_audits(audits: list[FolderAudit], failed: list[FailedFolder]) -> dict[str, Any]:
    candidates = flatten_candidates(audits)
    missing = sum(len(audit.missing_source_message_ids) for audit in audits)
    return {
        "accounts": sorted({audit.account for audit in audits} | {item.account for item in failed}),
        "folders": len(audits),
        "failed_folders": len(failed),
        "provider_uids": sum(audit.provider_count for audit in audits),
        "known_source_uids": len(candidates),
        "missing_source_uids": missing,
        "unique_messages": len({candidate.message_id for candidate in candidates}),
        "failed": [
            {"account": item.account, "folder": item.folder, "error": item.error}
            for item in failed
        ],
    }


def print_summary(summary: dict[str, Any]) -> None:
    print(
        "remote_purge_prep_audit=done "
        f"accounts={len(summary['accounts'])} folders={summary['folders']} "
        f"provider_uids={summary['provider_uids']} known_source_uids={summary['known_source_uids']} "
        f"unique_messages={summary['unique_messages']} missing_source_uids={summary['missing_source_uids']} "
        f"failed_folders={summary['failed_folders']}",
        flush=True,
    )


def flatten_candidates(audits: list[FolderAudit]) -> list[PurgeCandidate]:
    result: list[PurgeCandidate] = []
    seen: set[tuple[str, str, str]] = set()
    for audit in audits:
        for candidate in audit.candidates:
            key = (candidate.source_id, candidate.source_message_id, candidate.message_id)
            if key in seen:
                continue
            seen.add(key)
            result.append(candidate)
    return result


def write_manifest(
    store: PostgresMailStore,
    *,
    manifest_id: str,
    action: str,
    candidates: list[PurgeCandidate],
    summary: dict[str, Any],
) -> None:
    prepared_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    metadata = {
        "prepared_by": summary.get("prepared_by") or "tools/millie_remote_purge_prep.py",
        "provider_action_not_run": True,
        "provider_purge_requires_separate_confirmation": True,
        "note": "MILLIE canonical message copies and raw MIME are protected before any provider-side action.",
        "accounts": summary["accounts"],
    }
    metadata.update(summary.get("metadata") or {})
    unique_message_ids = sorted({candidate.message_id for candidate in candidates})
    with store.connection.transaction():
        store.connection.execute(
            """
            INSERT INTO mail_remote_purge_manifests (
                id, status, action, prepared_at, total_messages,
                total_source_uids, missing_source_uids, metadata_json
            )
            VALUES (%s, 'prepared', %s, %s, %s, %s, 0, %s)
            ON CONFLICT(id) DO UPDATE SET
                status = 'prepared',
                action = excluded.action,
                prepared_at = excluded.prepared_at,
                total_messages = excluded.total_messages,
                total_source_uids = excluded.total_source_uids,
                missing_source_uids = 0,
                metadata_json = excluded.metadata_json
            """,
            (
                manifest_id,
                action,
                prepared_at,
                len(unique_message_ids),
                len(candidates),
                Jsonb(metadata),
            ),
        )
        with store.connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO mail_remote_purge_manifest_messages (
                    manifest_id, message_id, source_id, source_message_id,
                    source_type, source_uri, source_account, source_folder,
                    imap_uidvalidity, imap_uid, action, protected_in_millie,
                    metadata_json
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s)
                ON CONFLICT(manifest_id, source_id, source_message_id) DO UPDATE SET
                    message_id = excluded.message_id,
                    source_type = excluded.source_type,
                    source_uri = excluded.source_uri,
                    source_account = excluded.source_account,
                    source_folder = excluded.source_folder,
                    imap_uidvalidity = excluded.imap_uidvalidity,
                    imap_uid = excluded.imap_uid,
                    action = excluded.action,
                    protected_in_millie = TRUE,
                    metadata_json = excluded.metadata_json
                """,
                [
                    (
                        manifest_id,
                        candidate.message_id,
                        candidate.source_id,
                        candidate.source_message_id,
                        candidate.source_type,
                        candidate.source_uri,
                        candidate.source_account,
                        candidate.source_folder,
                        candidate.imap_uidvalidity,
                        candidate.imap_uid,
                        action,
                        Jsonb(
                            {
                                "raw_mime_sha256": candidate.raw_mime_sha256,
                                "source_table": candidate.source_table,
                                **candidate.metadata,
                            }
                        ),
                    )
                    for candidate in candidates
                ],
            )
            metadata_rows = [
                (message_id, key, value)
                for message_id in unique_message_ids
                for key, value in (
                    ("archive_status", "protected_for_remote_provider_purge"),
                    ("millie_archive_tag", "remote-provider-purge-prepared"),
                    ("remote_purge_protected", "true"),
                    ("remote_purge_last_manifest_id", manifest_id),
                    ("remote_purge_prepared_at", prepared_at),
                    ("remote_purge_action", action),
                )
            ]
            cursor.executemany(
                """
                INSERT INTO mail_message_metadata (message_id, metadata_key, value_text)
                VALUES (%s, %s, %s)
                ON CONFLICT(message_id, metadata_key) DO UPDATE SET
                    value_text = excluded.value_text
                """,
                metadata_rows,
            )


def default_manifest_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"remote-purge-prep-{stamp}"


if __name__ == "__main__":
    raise SystemExit(main())
