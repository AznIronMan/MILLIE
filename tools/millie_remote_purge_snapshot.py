#!/usr/bin/env python3
"""Create a remote purge manifest from already-copied MILLIE source UIDs."""

from __future__ import annotations

import argparse
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from millie.settings_loader import load_local_settings
from millie.storage.postgres_store import PostgresMailStore
from tools.millie_imap_bulk_import import account_label, selected_accounts
from tools.millie_remote_purge_prep import PurgeCandidate, write_manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create a MILLIE-side remote purge manifest from source UIDs already "
            "stored in Postgres. This is a snapshot boundary: provider mail that "
            "arrives after the cutoff is not included."
        )
    )
    parser.add_argument(
        "--account",
        action="append",
        default=[],
        help="Account email/id/display name to include. May be repeated.",
    )
    parser.add_argument(
        "--cutoff-utc",
        default="",
        help="Include source UID records created at or before this UTC ISO timestamp. Defaults to now.",
    )
    parser.add_argument(
        "--manifest-id",
        default="",
        help="Optional manifest id. Defaults to remote-purge-snapshot-<UTC timestamp>.",
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
        help="Maximum source UIDs to include in this manifest. Default is unlimited.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_local_settings()
    settings = config["settings"]
    if (settings.get("database_mode") or "").lower() != "postgres":
        raise SystemExit("Remote purge snapshot currently requires database_mode=postgres.")
    accounts = selected_accounts(config["accounts"], args.account)
    if not accounts:
        raise SystemExit("No enabled IMAP accounts matched.")

    cutoff = parse_cutoff(args.cutoff_utc)
    manifest_id = args.manifest_id or default_manifest_id()
    store = PostgresMailStore.connect(settings)
    try:
        store.initialize()
        candidates = load_snapshot_candidates(store, accounts, cutoff)
        if args.limit_source_uids > 0:
            candidates = candidates[: args.limit_source_uids]
        summary = {
            "accounts": [account_label(account) for account in accounts],
            "folders": len({(candidate.source_account, candidate.source_folder) for candidate in candidates}),
            "failed_folders": 0,
            "provider_uids": len(candidates),
            "known_source_uids": len(candidates),
            "missing_source_uids": 0,
            "unique_messages": len({candidate.message_id for candidate in candidates}),
            "failed": [],
        }
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
        "remote_purge_snapshot=prepared "
        f"manifest_id={manifest_id} cutoff_utc={cutoff.isoformat()} "
        f"accounts={len(accounts)} source_uids={len(candidates)} "
        f"protected_messages={summary['unique_messages']} provider_action_not_run=true",
        flush=True,
    )
    return 0


def load_snapshot_candidates(
    store: PostgresMailStore,
    accounts: list[dict[str, Any]],
    cutoff: datetime,
) -> list[PurgeCandidate]:
    candidates: list[PurgeCandidate] = []
    seen: set[tuple[str, str]] = set()
    for account in accounts:
        source_type = "exchange_imap_oauth" if account.get("auth_method") == "oauth" else "imap"
        source_prefix = account_source_uri_prefix(account)
        account_name = account_label(account)
        rows = store.connection.execute(
            """
            SELECT
                s.id, s.source_type, s.source_uri,
                m.source_message_id, m.id, m.raw_mime_sha256, 'mail_messages'
            FROM mail_sources s
            JOIN mail_messages m ON m.source_id = s.id
            WHERE s.source_type = %s
              AND s.source_uri LIKE %s
              AND m.created_at <= %s
              AND NOT EXISTS (
                  SELECT 1
                  FROM mail_remote_purge_manifest_messages pm
                  JOIN mail_remote_purge_manifests p
                    ON p.id = pm.manifest_id
                  WHERE p.status = 'provider_purged'
                    AND pm.source_id = s.id
                    AND pm.source_message_id = m.source_message_id
              )
            UNION ALL
            SELECT
                s.id, s.source_type, s.source_uri,
                a.source_message_id, a.message_id, a.raw_mime_sha256,
                'mail_source_message_aliases'
            FROM mail_sources s
            JOIN mail_source_message_aliases a ON a.source_id = s.id
            WHERE s.source_type = %s
              AND s.source_uri LIKE %s
              AND a.created_at <= %s
              AND NOT EXISTS (
                  SELECT 1
                  FROM mail_remote_purge_manifest_messages pm
                  JOIN mail_remote_purge_manifests p
                    ON p.id = pm.manifest_id
                  WHERE p.status = 'provider_purged'
                    AND pm.source_id = s.id
                    AND pm.source_message_id = a.source_message_id
              )
            ORDER BY source_uri, source_message_id
            """,
            (
                source_type,
                f"{source_prefix}%",
                cutoff,
                source_type,
                f"{source_prefix}%",
                cutoff,
            ),
        ).fetchall()
        for source_id, row_source_type, source_uri, source_message_id, message_id, raw_hash, source_table in rows:
            source_key = (str(source_id), str(source_message_id))
            if source_key in seen:
                continue
            seen.add(source_key)
            uidvalidity, uid = split_source_message_id(str(source_message_id))
            if not uid:
                continue
            candidates.append(
                PurgeCandidate(
                    message_id=str(message_id),
                    source_id=str(source_id),
                    source_message_id=str(source_message_id),
                    source_type=str(row_source_type),
                    source_uri=str(source_uri),
                    source_account=account_name,
                    source_folder=folder_name_from_source_uri(str(source_uri)),
                    imap_uidvalidity=uidvalidity,
                    imap_uid=uid,
                    raw_mime_sha256=str(raw_hash),
                    source_table=str(source_table),
                )
            )
    return candidates


def account_source_uri_prefix(account: dict[str, Any]) -> str:
    username = urllib.parse.quote(str(account["username"]), safe="")
    host = account["host"]
    port = int(account.get("port") or 993)
    return f"imap://{username}@{host}:{port}/"


def folder_name_from_source_uri(source_uri: str) -> str:
    parsed = urllib.parse.urlparse(source_uri)
    return urllib.parse.unquote(parsed.path.lstrip("/"))


def split_source_message_id(source_message_id: str) -> tuple[str, str]:
    if ":" not in source_message_id:
        return "", source_message_id if source_message_id.isdigit() else ""
    uidvalidity, uid = source_message_id.rsplit(":", 1)
    return uidvalidity, uid if uid.isdigit() else ""


def parse_cutoff(value: str) -> datetime:
    if not value:
        return datetime.now(timezone.utc).replace(microsecond=0)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def default_manifest_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"remote-purge-snapshot-{stamp}"


if __name__ == "__main__":
    raise SystemExit(main())
