"""Postgres writer/query helpers for MILLIE live prototypes."""

from __future__ import annotations

import hashlib
import json
import secrets
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from typing import Any, Iterable

import psycopg
from psycopg import Connection
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from millie.brain.proposals import compact_values, proposal_confidence, target_label
from millie.brain.retention import (
    HeldMessage,
    RetentionPolicy,
    human_duration,
    normalize_folder,
    retention_status,
)
from millie.importing.models import NormalizedAddress, NormalizedMessage, stable_id
from millie.importing.normalize import normalize_email
from millie.service.auth import (
    MillieIdentity,
    build_identity_sql,
    login_address_candidates,
    service_mail_domain,
    service_mail_domain_aliases,
    verify_password,
)
from millie.service.mailbox import default_mailbox_folders

from .schema import load_schema


RETENTION_REVIEW_DEFER_DURATION = timedelta(days=7)
RETENTION_POLICY_STATUSES = {"proposed", "active", "disabled", "retired"}
RETENTION_POLICY_ACTIONS = {
    "no_action",
    "hide_from_default_views",
    "expire_internal_copy",
    "delete_internal_copy",
}
RETENTION_POLICY_WEB_ACTIONS = {"activate", "disable", "update"}
PROVIDER_WRITE_AUDIT_ACTIONS = {"provider_purge_manifest", "block_provider_write"}
PROVIDER_WRITE_AUDIT_STATUSES = {"recorded", "blocked", "applied", "failed"}
BRAIN_RULE_STATUSES = {"proposed", "active", "disabled", "superseded", "retired"}
BRAIN_RULE_ACTIONS = {"activate", "disable", "retire", "update"}


class PostgresMailStore:
    """Persist and serve normalized mail records from Postgres."""

    def __init__(self, connection: Connection, settings: dict[str, str] | None = None) -> None:
        self.connection = connection
        self.settings = settings or {}

    def __enter__(self) -> "PostgresMailStore":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    @classmethod
    def connect(cls, settings: dict[str, str]) -> "PostgresMailStore":
        connection = psycopg.connect(
            host=settings["postgres_host_ip"],
            port=int(settings.get("postgres_port") or 5432),
            user=settings["postgres_username"],
            password=settings["postgres_password"],
            dbname=settings["postgres_database"],
            connect_timeout=10,
        )
        return cls(connection, settings=settings)

    def close(self) -> None:
        self.connection.close()

    def initialize(self) -> None:
        with self.connection.transaction():
            self.connection.execute(load_schema("postgres"))

    def ensure_identity(
        self,
        identity: MillieIdentity,
        *,
        password_hash: str | None = None,
    ) -> str:
        with self.connection.transaction():
            existing = self._existing_identity(identity.login_candidates)
            if existing:
                identity_id, mailbox_id = existing
                self._promote_existing_identity(
                    identity,
                    identity_id=identity_id,
                    mailbox_id=mailbox_id,
                    password_hash=password_hash,
                )
                return mailbox_id
            self.connection.execute(build_identity_sql(identity, password_hash=password_hash))
            return identity.mailbox_id

    def _existing_identity(self, login_candidates: list[str]) -> tuple[str, str] | None:
        if not login_candidates:
            return None
        rows = self.connection.execute(
            f"""
            SELECT i.login_address, i.id, mb.id
            FROM millie_identities i
            LEFT JOIN millie_mailboxes mb
              ON mb.owner_identity_id = i.id
             AND mb.is_primary = TRUE
            WHERE lower(i.login_address) IN ({placeholders(login_candidates)})
            ORDER BY i.created_at
            """,
            tuple(login_candidates),
        ).fetchall()
        by_login = {str(row[0]).lower(): row for row in rows}
        for candidate in login_candidates:
            row = by_login.get(candidate)
            if row:
                identity_id = row[1]
                mailbox_id = row[2] or stable_id("millie_mailbox", identity_id, candidate)
                return identity_id, mailbox_id
        return None

    def _promote_existing_identity(
        self,
        identity: MillieIdentity,
        *,
        identity_id: str,
        mailbox_id: str,
        password_hash: str | None,
    ) -> None:
        login = identity.normalized_login
        display_name = identity.display_name or identity.local_part
        self.connection.execute(
            """
            UPDATE millie_identities
            SET login_address = %s,
                login_local_part = %s,
                login_domain = %s,
                display_name = %s,
                status = 'active',
                updated_at = now()
            WHERE id = %s
            """,
            (login, identity.local_part, identity.domain, display_name, identity_id),
        )
        self.connection.execute(
            """
            INSERT INTO millie_mailboxes (
                id, owner_identity_id, mailbox_address, display_name, is_primary
            )
            VALUES (%s, %s, %s, %s, TRUE)
            ON CONFLICT(id) DO UPDATE SET
                mailbox_address = excluded.mailbox_address,
                display_name = excluded.display_name,
                is_primary = TRUE,
                updated_at = now()
            """,
            (mailbox_id, identity_id, login, display_name),
        )
        if password_hash:
            credential_id = stable_id("millie_credential", identity_id, "primary-password")
            self.connection.execute(
                """
                INSERT INTO millie_identity_credentials (
                    id, identity_id, credential_type, credential_label, secret_hash
                )
                VALUES (%s, %s, 'password_pbkdf2_sha256', 'primary password', %s)
                ON CONFLICT(id) DO UPDATE SET
                    secret_hash = excluded.secret_hash,
                    disabled_at = NULL
                """,
                (credential_id, identity_id, password_hash),
            )
        for folder in default_mailbox_folders(mailbox_id):
            self.connection.execute(
                """
                INSERT INTO millie_mailbox_folders (
                    id, mailbox_id, parent_id, folder_path, display_name, folder_role,
                    special_use, sort_order
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (mailbox_id, folder_path) DO UPDATE SET
                    display_name = excluded.display_name,
                    folder_role = excluded.folder_role,
                    special_use = excluded.special_use,
                    sort_order = excluded.sort_order,
                    updated_at = now()
                """,
                (
                    folder.id,
                    mailbox_id,
                    folder.parent_id,
                    folder.path,
                    folder.display_name,
                    folder.role,
                    folder.special_use,
                    folder.sort_order,
                ),
            )

    def upsert_source(
        self,
        *,
        source_type: str,
        source_uri: str,
        display_name: str | None = None,
        auth_mode: str | None = None,
        is_active: bool = False,
    ) -> str:
        source_id = stable_id("source", source_type, source_uri)
        self.connection.execute(
            """
            INSERT INTO mail_sources (
                id, source_type, display_name, source_uri, auth_mode, is_active
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT(id) DO UPDATE SET
                display_name = excluded.display_name,
                auth_mode = excluded.auth_mode,
                is_active = excluded.is_active,
                updated_at = now()
            """,
            (source_id, source_type, display_name, source_uri, auth_mode, is_active),
        )
        return source_id

    def create_import_job(
        self,
        *,
        source_id: str,
        mode: str = "sample",
        status: str = "completed",
        metadata: dict[str, object] | None = None,
    ) -> str:
        job_id = stable_id("import_job", source_id, mode, json.dumps(metadata or {}, sort_keys=True))
        self.connection.execute(
            """
            INSERT INTO mail_import_jobs (id, source_id, status, mode, metadata_json)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT(id) DO NOTHING
            """,
            (job_id, source_id, status, mode, Jsonb(metadata or {})),
        )
        return job_id

    def source_message_exists(self, *, source_id: str, source_message_id: str) -> bool:
        row = self.connection.execute(
            """
            SELECT 1
            FROM mail_messages
            WHERE source_id = %s AND source_message_id = %s
            LIMIT 1
            """,
            (source_id, source_message_id),
        ).fetchone()
        return row is not None

    def store_message(
        self,
        *,
        source_id: str,
        message: NormalizedMessage,
        import_job_id: str | None = None,
        folder: str | None = None,
    ) -> None:
        if self.connection.info.transaction_status == TransactionStatus.IDLE:
            with self.connection.transaction():
                self._store_message_records(source_id, message, import_job_id, folder)
            return
        self._store_message_records(source_id, message, import_job_id, folder)

    def _store_message_records(
        self,
        source_id: str,
        message: NormalizedMessage,
        import_job_id: str | None,
        folder: str | None,
    ) -> None:
        _sanitize_message_text(message)
        self._replace_message(source_id, message, import_job_id)
        if folder:
            folder_id = self._upsert_folder(source_id, folder)
            self.connection.execute(
                """
                INSERT INTO mail_message_folders (message_id, folder_id)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
                """,
                (message.id, folder_id),
            )
        self.connection.execute(
            """
            INSERT INTO mail_raw_mime (message_id, content_blob)
            VALUES (%s, %s)
            ON CONFLICT(message_id) DO UPDATE SET content_blob = excluded.content_blob
            """,
            (message.id, message.raw_mime),
        )
        self._replace_addresses(message)
        self._replace_headers(message)
        self._replace_parts(message)
        self._replace_metadata(message)
        self._replace_search_document(message)

    def map_message_to_mailbox(
        self,
        *,
        mailbox_id: str,
        folder_path: str,
        message_id: str,
        binding_id: str | None = None,
    ) -> int:
        folder_id = self.folder_id(mailbox_id, folder_path)
        if not folder_id:
            raise ValueError(f"Mailbox folder not found: {folder_path}")
        row = self.connection.execute(
            """
            SELECT imap_uid
            FROM millie_mailbox_messages
            WHERE folder_id = %s AND message_id = %s
            """,
            (folder_id, message_id),
        ).fetchone()
        if row:
            return int(row[0])
        next_uid = self.connection.execute(
            """
            SELECT coalesce(max(imap_uid), 0) + 1
            FROM millie_mailbox_messages
            WHERE folder_id = %s
            """,
            (folder_id,),
        ).fetchone()[0]
        row_id = stable_id("millie_mailbox_message", mailbox_id, folder_id, message_id)
        self.connection.execute(
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
            """,
            (row_id, mailbox_id, folder_id, binding_id, next_uid, message_id),
        )
        return int(next_uid)

    def ensure_mailbox_folder(
        self,
        mailbox_id: str,
        folder_path: str,
        *,
        selectable: bool = True,
        subscribed: bool = True,
    ) -> str:
        folder_path = normalize_mailbox_path(folder_path)
        existing = self.folder_id(mailbox_id, folder_path)
        parent_id = None
        if "/" in folder_path:
            parent_path = folder_path.rsplit("/", 1)[0]
            parent_id = self.ensure_mailbox_folder(
                mailbox_id,
                parent_path,
                selectable=True,
                subscribed=subscribed,
            )
        display_name = folder_path.rsplit("/", 1)[-1]
        folder_id = existing or stable_id("millie_folder", mailbox_id, folder_path)
        sort_order = self.connection.execute(
            """
            SELECT coalesce(max(sort_order), 1000) + 10
            FROM millie_mailbox_folders
            WHERE mailbox_id = %s
            """,
            (mailbox_id,),
        ).fetchone()[0]
        self.connection.execute(
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
            (
                folder_id,
                mailbox_id,
                parent_id,
                folder_path,
                display_name,
                selectable,
                subscribed,
                sort_order,
            ),
        )
        return folder_id

    def delete_mailbox_folder(self, mailbox_id: str, folder_path: str) -> str:
        folder_path = normalize_mailbox_path(folder_path)
        row = self.connection.execute(
            """
            SELECT id, folder_role
            FROM millie_mailbox_folders
            WHERE mailbox_id = %s AND folder_path = %s
            """,
            (mailbox_id, folder_path),
        ).fetchone()
        if not row:
            return "not_found"
        if row[1] != "custom":
            return "protected"
        self.connection.execute(
            """
            DELETE FROM millie_mailbox_folders
            WHERE mailbox_id = %s
              AND (folder_path = %s OR folder_path LIKE %s)
            """,
            (mailbox_id, folder_path, f"{folder_path}/%"),
        )
        return "deleted"

    def rename_mailbox_folder(self, mailbox_id: str, old_path: str, new_path: str) -> str:
        old_path = normalize_mailbox_path(old_path)
        new_path = normalize_mailbox_path(new_path)
        if not old_path or not new_path or old_path == new_path:
            return "invalid"
        row = self.connection.execute(
            """
            SELECT id, folder_role
            FROM millie_mailbox_folders
            WHERE mailbox_id = %s AND folder_path = %s
            """,
            (mailbox_id, old_path),
        ).fetchone()
        if not row:
            return "not_found"
        if row[1] != "custom":
            return "protected"
        target = self.folder_id(mailbox_id, new_path)
        if target:
            return "exists"
        parent_id = None
        if "/" in new_path:
            parent_id = self.ensure_mailbox_folder(mailbox_id, new_path.rsplit("/", 1)[0])
        rows = self.connection.execute(
            """
            SELECT id, folder_path
            FROM millie_mailbox_folders
            WHERE mailbox_id = %s
              AND (folder_path = %s OR folder_path LIKE %s)
            ORDER BY length(folder_path)
            """,
            (mailbox_id, old_path, f"{old_path}/%"),
        ).fetchall()
        for folder_id, current_path in rows:
            suffix = str(current_path)[len(old_path):]
            updated_path = f"{new_path}{suffix}"
            display_name = updated_path.rsplit("/", 1)[-1]
            updated_parent_id = parent_id if current_path == old_path else None
            if current_path != old_path and "/" in updated_path:
                current_parent_path = updated_path.rsplit("/", 1)[0]
                updated_parent_id = self.folder_id(mailbox_id, current_parent_path)
            self.connection.execute(
                """
                UPDATE millie_mailbox_folders
                SET folder_path = %s,
                    display_name = %s,
                    parent_id = %s,
                    updated_at = now()
                WHERE id = %s
                """,
                (updated_path, display_name, updated_parent_id, folder_id),
            )
        return "renamed"

    def set_folder_subscription(self, mailbox_id: str, folder_path: str, subscribed: bool) -> bool:
        result = self.connection.execute(
            """
            UPDATE millie_mailbox_folders
            SET subscribed = %s, updated_at = now()
            WHERE mailbox_id = %s AND folder_path = %s
            """,
            (subscribed, mailbox_id, normalize_mailbox_path(folder_path)),
        )
        return bool(result.rowcount)

    def update_message_flags(
        self,
        *,
        mailbox_id: str,
        folder_path: str,
        uids: list[int],
        mode: str,
        flags: list[str],
    ) -> list[dict[str, object]]:
        if not uids:
            return []
        normalized_flags = normalize_message_flags(flags)
        rows = self._mailbox_message_rows_by_uid(
            mailbox_id=mailbox_id,
            folder_path=folder_path,
            uids=uids,
        )
        updates: list[dict[str, object]] = []
        for row in rows:
            current = normalize_message_flags(row["flags"])
            if mode == "replace":
                updated = normalized_flags
            elif mode == "add":
                updated = normalize_message_flags([*current, *normalized_flags])
            elif mode == "remove":
                remove = {flag.lower() for flag in normalized_flags}
                updated = [flag for flag in current if flag.lower() not in remove]
            else:
                raise ValueError(f"Unsupported flag mode: {mode}")
            self._update_mailbox_message_flags(str(row["id"]), updated)
            updates.append({"uid": int(row["uid"]), "flags": updated})
        return updates

    def copy_messages(
        self,
        *,
        mailbox_id: str,
        source_folder_path: str,
        target_folder_path: str,
        uids: list[int],
    ) -> list[dict[str, int]]:
        if not uids:
            return []
        target_folder_id = self.folder_id(mailbox_id, normalize_mailbox_path(target_folder_path))
        if not target_folder_id:
            return []
        rows = self._mailbox_message_rows_by_uid(
            mailbox_id=mailbox_id,
            folder_path=source_folder_path,
            uids=uids,
        )
        copied: list[dict[str, int]] = []
        for row in rows:
            target_uid = self._copy_mailbox_message_row(
                mailbox_id=mailbox_id,
                target_folder_id=target_folder_id,
                source_row=row,
            )
            copied.append({"source_uid": int(row["uid"]), "target_uid": target_uid})
        return copied

    def move_messages(
        self,
        *,
        mailbox_id: str,
        source_folder_path: str,
        target_folder_path: str,
        uids: list[int],
    ) -> list[dict[str, int]]:
        copied = self.copy_messages(
            mailbox_id=mailbox_id,
            source_folder_path=source_folder_path,
            target_folder_path=target_folder_path,
            uids=uids,
        )
        self.expunge_uids(
            mailbox_id=mailbox_id,
            folder_path=source_folder_path,
            uids=[item["source_uid"] for item in copied],
            require_deleted=False,
        )
        return copied

    def expunge_deleted(self, *, mailbox_id: str, folder_path: str) -> list[int]:
        rows = self._mailbox_message_rows_by_uid(
            mailbox_id=mailbox_id,
            folder_path=folder_path,
            uids=[],
        )
        uids = [
            int(row["uid"])
            for row in rows
            if "\\Deleted" in normalize_message_flags(row["flags"]) or bool(row["is_deleted"])
        ]
        return self.expunge_uids(
            mailbox_id=mailbox_id,
            folder_path=folder_path,
            uids=uids,
            require_deleted=True,
        )

    def expunge_uids(
        self,
        *,
        mailbox_id: str,
        folder_path: str,
        uids: list[int],
        require_deleted: bool,
    ) -> list[int]:
        if not uids:
            return []
        rows = self._mailbox_message_rows_by_uid(
            mailbox_id=mailbox_id,
            folder_path=folder_path,
            uids=uids,
        )
        expunged: list[int] = []
        for row in rows:
            flags = normalize_message_flags(row["flags"])
            if require_deleted and "\\Deleted" not in flags and not bool(row["is_deleted"]):
                continue
            self.connection.execute(
                """
                UPDATE millie_mailbox_messages
                SET is_expunged = TRUE, updated_at = now()
                WHERE id = %s
                """,
                (row["id"],),
            )
            expunged.append(int(row["uid"]))
        return expunged

    def append_raw_message_to_mailbox(
        self,
        *,
        mailbox_id: str,
        folder_path: str,
        raw_bytes: bytes,
        flags: list[str] | None = None,
        internal_date: datetime | None = None,
    ) -> int:
        folder_path = normalize_mailbox_path(folder_path)
        folder_id = self.ensure_mailbox_folder(mailbox_id, folder_path)
        source_uri = f"millie://mailbox/{mailbox_id}/imap-append"
        source_id = self.upsert_source(
            source_type="imap",
            source_uri=source_uri,
            display_name="MILLIE IMAP append",
            auth_mode="imap_append",
            is_active=False,
        )
        job_id = self.create_import_job(
            source_id=source_id,
            mode="imap_append",
            metadata={"mailbox_id": mailbox_id, "folder_path": folder_path},
        )
        source_message_id = f"append:{uuid.uuid4()}"
        normalized = normalize_email(
            raw_bytes,
            source_message_id=source_message_id,
            source_uri=source_uri,
            folder=folder_path,
            metadata={
                "appended_to_mailbox": mailbox_id,
                "appended_to_folder": folder_path,
                "appended_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        self.store_message(
            source_id=source_id,
            import_job_id=job_id,
            message=normalized,
            folder=folder_path,
        )
        normalized_flags = normalize_message_flags(flags or [])
        uid = self._insert_mailbox_message(
            mailbox_id=mailbox_id,
            folder_id=folder_id,
            message_id=normalized.id,
            binding_id=None,
            internal_date=internal_date,
            flags=normalized_flags,
        )
        if folder_path != "All Mail":
            all_mail_id = self.folder_id(mailbox_id, "All Mail")
            if all_mail_id:
                self._insert_mailbox_message(
                    mailbox_id=mailbox_id,
                    folder_id=all_mail_id,
                    message_id=normalized.id,
                    binding_id=None,
                    internal_date=internal_date,
                    flags=normalized_flags,
                )
        return uid

    def folder_id(self, mailbox_id: str, folder_path: str) -> str | None:
        folder_path = normalize_mailbox_path(folder_path)
        row = self.connection.execute(
            """
            SELECT id
            FROM millie_mailbox_folders
            WHERE mailbox_id = %s AND folder_path = %s
            """,
            (mailbox_id, folder_path),
        ).fetchone()
        return row[0] if row else None

    def authenticate(self, login: str, password: str) -> str | None:
        candidates = login_address_candidates(
            login,
            primary_domain=service_mail_domain(self.settings),
            domain_aliases=service_mail_domain_aliases(self.settings),
        )
        rows = self.connection.execute(
            f"""
            SELECT i.login_address, i.id, c.secret_hash
            FROM millie_identities i
            JOIN millie_identity_credentials c ON c.identity_id = i.id
            WHERE lower(i.login_address) IN ({placeholders(candidates)})
              AND i.status = 'active'
              AND c.disabled_at IS NULL
              AND (c.expires_at IS NULL OR c.expires_at > now())
            ORDER BY c.created_at DESC
            """,
            tuple(candidates),
        ).fetchall()
        rows_by_login: dict[str, list[tuple[object, ...]]] = {}
        for row in rows:
            rows_by_login.setdefault(str(row[0]).lower(), []).append(row)
        for candidate in candidates:
            for row in rows_by_login.get(candidate, []):
                _, identity_id, secret_hash = row
                if verify_password(password, secret_hash):
                    return identity_id
        return None

    def create_web_session(
        self,
        *,
        login: str,
        password: str,
        remote_address: str,
        user_agent: str,
        duration: timedelta = timedelta(hours=12),
    ) -> dict[str, object] | None:
        identity_id = self.authenticate(login, password)
        if not identity_id:
            return None
        mailbox = self.mailbox_by_identity(identity_id)
        if mailbox is None:
            return None
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc).replace(microsecond=0) + duration
        session_id = str(uuid.uuid4())
        self.connection.execute(
            """
            INSERT INTO millie_auth_sessions (
                id, identity_id, session_type, token_hash, client_name,
                remote_address, user_agent, expires_at, last_seen_at
            )
            VALUES (%s, %s, 'web', %s, 'millie_webmail', %s, %s, %s, now())
            """,
            (
                session_id,
                identity_id,
                web_session_token_hash(token),
                remote_address,
                user_agent[:500],
                expires_at,
            ),
        )
        return {
            "token": token,
            "session_id": session_id,
            "expires_at": expires_at,
            "identity_id": identity_id,
            "mailbox": mailbox,
        }

    def web_session(self, token: str) -> dict[str, object] | None:
        if not token:
            return None
        row = self.connection.execute(
            """
            SELECT
                s.id,
                s.identity_id,
                s.expires_at,
                i.login_address,
                i.display_name,
                mb.id,
                mb.mailbox_address,
                mb.display_name
            FROM millie_auth_sessions s
            JOIN millie_identities i ON i.id = s.identity_id
            LEFT JOIN millie_mailboxes mb
              ON mb.owner_identity_id = i.id
             AND mb.is_primary = TRUE
            WHERE s.session_type = 'web'
              AND s.token_hash = %s
              AND s.revoked_at IS NULL
              AND s.expires_at > now()
              AND i.status = 'active'
            LIMIT 1
            """,
            (web_session_token_hash(token),),
        ).fetchone()
        if not row:
            return None
        self.connection.execute(
            "UPDATE millie_auth_sessions SET last_seen_at = now() WHERE id = %s",
            (row[0],),
        )
        return {
            "session_id": row[0],
            "identity_id": row[1],
            "expires_at": row[2],
            "login_address": row[3],
            "display_name": row[4],
            "mailbox": {
                "id": row[5],
                "mailbox_address": row[6],
                "display_name": row[7],
                "owner_identity_id": row[1],
            }
            if row[5]
            else None,
        }

    def revoke_web_session(self, token: str) -> None:
        if not token:
            return
        self.connection.execute(
            """
            UPDATE millie_auth_sessions
            SET revoked_at = now()
            WHERE session_type = 'web'
              AND token_hash = %s
              AND revoked_at IS NULL
            """,
            (web_session_token_hash(token),),
        )

    def primary_mailbox_for_identity(self, identity_id: str) -> str | None:
        row = self.connection.execute(
            """
            SELECT id
            FROM millie_mailboxes
            WHERE owner_identity_id = %s AND is_primary = TRUE
            ORDER BY created_at
            LIMIT 1
            """,
            (identity_id,),
        ).fetchone()
        return row[0] if row else None

    def mailbox_by_identity(self, identity_id: str) -> dict[str, object] | None:
        row = self.connection.execute(
            """
            SELECT id, mailbox_address, display_name, owner_identity_id
            FROM millie_mailboxes
            WHERE owner_identity_id = %s AND is_primary = TRUE
            ORDER BY created_at
            LIMIT 1
            """,
            (identity_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "mailbox_address": row[1],
            "display_name": row[2],
            "owner_identity_id": row[3],
        }

    def list_folders(self, mailbox_id: str) -> list[dict[str, object]]:
        rows = self.connection.execute(
            """
            SELECT id, folder_path, display_name, folder_role, selectable, subscribed
            FROM millie_mailbox_folders
            WHERE mailbox_id = %s
            ORDER BY sort_order, folder_path
            """,
            (mailbox_id,),
        ).fetchall()
        return [
            {
                "id": row[0],
                "path": row[1],
                "display_name": row[2],
                "role": row[3],
                "selectable": row[4],
                "subscribed": row[5],
            }
            for row in rows
        ]

    def mailbox_by_address(self, mailbox_address: str | None = None) -> dict[str, object] | None:
        if mailbox_address:
            candidates = login_address_candidates(
                mailbox_address,
                primary_domain=service_mail_domain(self.settings),
                domain_aliases=service_mail_domain_aliases(self.settings),
            )
            rows = self.connection.execute(
                f"""
                SELECT mb.id, mb.mailbox_address, mb.display_name, mb.owner_identity_id
                FROM millie_mailboxes mb
                WHERE lower(mb.mailbox_address) IN ({placeholders(candidates)})
                ORDER BY mb.created_at
                """,
                tuple(candidates),
            ).fetchall()
            by_address = {str(row[1]).lower(): row for row in rows}
            row = next((by_address[candidate] for candidate in candidates if candidate in by_address), None)
        else:
            row = self.connection.execute(
                """
                SELECT mb.id, mb.mailbox_address, mb.display_name, mb.owner_identity_id
                FROM millie_mailboxes mb
                JOIN millie_identities i ON i.id = mb.owner_identity_id
                WHERE mb.is_primary = TRUE
                  AND i.status = 'active'
                ORDER BY mb.created_at
                LIMIT 1
                """
            ).fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "mailbox_address": row[1],
            "display_name": row[2],
            "owner_identity_id": row[3],
        }

    def list_webmail_messages(
        self,
        *,
        mailbox_id: str,
        folder_path: str,
        limit: int | None = 100,
    ) -> list[dict[str, object]]:
        limit_clause = "" if limit is None else "LIMIT %s"
        params: tuple[object, ...] = (
            (mailbox_id, folder_path)
            if limit is None
            else (mailbox_id, folder_path, limit)
        )
        rows = self.connection.execute(
            f"""
            SELECT
                mm.imap_uid,
                mm.flags,
                mm.internal_date,
                m.id,
                m.internet_message_id,
                m.subject,
                coalesce(m.sent_at, m.received_at, mm.internal_date) AS message_date,
                m.body_preview,
                m.has_attachments,
                m.raw_mime_size_bytes,
                from_addr.value AS from_text,
                to_addr.value AS to_text,
                review_counts.proposed_count
            FROM millie_mailbox_messages mm
            JOIN millie_mailbox_folders mf ON mf.id = mm.folder_id
            JOIN mail_messages m ON m.id = mm.message_id
            LEFT JOIN LATERAL (
                SELECT string_agg(
                    CASE
                        WHEN coalesce(display_name, '') <> '' AND coalesce(email_address, '') <> ''
                            THEN display_name || ' <' || email_address || '>'
                        WHEN coalesce(email_address, '') <> '' THEN email_address
                        ELSE coalesce(raw_value, '')
                    END,
                    ', ' ORDER BY ordinal
                ) AS value
                FROM mail_message_addresses a
                WHERE a.message_id = m.id AND a.role = 'from'
            ) from_addr ON TRUE
            LEFT JOIN LATERAL (
                SELECT string_agg(
                    CASE
                        WHEN coalesce(display_name, '') <> '' AND coalesce(email_address, '') <> ''
                            THEN display_name || ' <' || email_address || '>'
                        WHEN coalesce(email_address, '') <> '' THEN email_address
                        ELSE coalesce(raw_value, '')
                    END,
                    ', ' ORDER BY ordinal
                ) AS value
                FROM mail_message_addresses a
                WHERE a.message_id = m.id AND a.role = 'to'
            ) to_addr ON TRUE
            LEFT JOIN LATERAL (
                SELECT count(*) AS proposed_count
                FROM millie_message_classifications c
                WHERE c.message_id = m.id
                  AND c.status = 'proposed'
            ) review_counts ON TRUE
            WHERE mm.mailbox_id = %s
              AND mf.folder_path = %s
              AND mm.is_expunged = FALSE
              AND mm.metadata_json->>'retention_hidden_from_default_views' IS DISTINCT FROM 'true'
            ORDER BY message_date DESC NULLS LAST, mm.imap_uid DESC
            {limit_clause}
            """,
            params,
        ).fetchall()
        return [
            {
                "uid": int(row[0]),
                "flags": list(row[1] or []),
                "internal_date": row[2],
                "message_id": row[3],
                "internet_message_id": row[4],
                "subject": row[5],
                "message_date": row[6],
                "body_preview": row[7],
                "has_attachments": bool(row[8]),
                "size": int(row[9] or 0),
                "from": row[10] or "",
                "to": row[11] or "",
                "proposed_classifications": int(row[12] or 0),
            }
            for row in rows
        ]

    def search_webmail_messages(
        self,
        *,
        mailbox_id: str,
        query: str = "",
        folder_path: str = "",
        source_type: str = "",
        source: str = "",
        sender: str = "",
        since: str = "",
        until: str = "",
        has_attachments: bool | None = None,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        where = [
            "mm.mailbox_id = %s",
            "mm.is_expunged = FALSE",
            "mm.metadata_json->>'retention_hidden_from_default_views' IS DISTINCT FROM 'true'",
        ]
        params: list[object] = [mailbox_id]
        rank_sql = "0::real"
        if query.strip():
            where.append(
                "to_tsvector('simple', coalesce(sd.search_text, '')) @@ websearch_to_tsquery('simple', %s)"
            )
            params.append(query.strip())
            rank_sql = "ts_rank(to_tsvector('simple', coalesce(sd.search_text, '')), websearch_to_tsquery('simple', %s))"
        if folder_path:
            where.append("mf.folder_path = %s")
            params.append(folder_path)
        if source_type:
            where.append("ms.source_type = %s")
            params.append(source_type)
        if source:
            where.append("(ms.source_uri ILIKE %s OR ms.display_name ILIKE %s)")
            params.extend([f"%{source}%", f"%{source}%"])
        if sender:
            where.append(
                """
                EXISTS (
                    SELECT 1
                    FROM mail_message_addresses a
                    WHERE a.message_id = m.id
                      AND a.role = 'from'
                      AND (
                        a.email_address ILIKE %s
                        OR a.display_name ILIKE %s
                        OR a.raw_value ILIKE %s
                      )
                )
                """
            )
            like_sender = f"%{sender}%"
            params.extend([like_sender, like_sender, like_sender])
        if since:
            where.append("coalesce(m.sent_at, m.received_at, mm.internal_date) >= %s::date")
            params.append(since)
        if until:
            where.append("coalesce(m.sent_at, m.received_at, mm.internal_date) < (%s::date + interval '1 day')")
            params.append(until)
        if has_attachments is not None:
            where.append("m.has_attachments = %s")
            params.append(has_attachments)

        rank_params: list[object] = [query.strip()] if query.strip() else []
        rows = self.connection.execute(
            f"""
            SELECT DISTINCT ON (m.id, mf.folder_path)
                {rank_sql} AS rank,
                mf.folder_path,
                mm.imap_uid,
                mm.flags,
                mm.internal_date,
                m.id,
                m.internet_message_id,
                m.subject,
                coalesce(m.sent_at, m.received_at, mm.internal_date) AS message_date,
                m.body_preview,
                m.has_attachments,
                m.raw_mime_size_bytes,
                ms.source_type,
                ms.source_uri,
                from_addr.value AS from_text,
                to_addr.value AS to_text,
                review_counts.proposed_count
            FROM millie_mailbox_messages mm
            JOIN millie_mailbox_folders mf ON mf.id = mm.folder_id
            JOIN mail_messages m ON m.id = mm.message_id
            JOIN mail_sources ms ON ms.id = m.source_id
            LEFT JOIN mail_search_documents sd ON sd.message_id = m.id
            LEFT JOIN LATERAL (
                SELECT string_agg(
                    CASE
                        WHEN coalesce(display_name, '') <> '' AND coalesce(email_address, '') <> ''
                            THEN display_name || ' <' || email_address || '>'
                        WHEN coalesce(email_address, '') <> '' THEN email_address
                        ELSE coalesce(raw_value, '')
                    END,
                    ', ' ORDER BY ordinal
                ) AS value
                FROM mail_message_addresses a
                WHERE a.message_id = m.id AND a.role = 'from'
            ) from_addr ON TRUE
            LEFT JOIN LATERAL (
                SELECT string_agg(
                    CASE
                        WHEN coalesce(display_name, '') <> '' AND coalesce(email_address, '') <> ''
                            THEN display_name || ' <' || email_address || '>'
                        WHEN coalesce(email_address, '') <> '' THEN email_address
                        ELSE coalesce(raw_value, '')
                    END,
                    ', ' ORDER BY ordinal
                ) AS value
                FROM mail_message_addresses a
                WHERE a.message_id = m.id AND a.role = 'to'
            ) to_addr ON TRUE
            LEFT JOIN LATERAL (
                SELECT count(*) AS proposed_count
                FROM millie_message_classifications c
                WHERE c.message_id = m.id
                  AND c.status = 'proposed'
            ) review_counts ON TRUE
            WHERE {" AND ".join(where)}
            ORDER BY m.id, mf.folder_path, rank DESC, message_date DESC NULLS LAST, mm.imap_uid DESC
            LIMIT %s
            """,
            tuple([*rank_params, *params, max(1, min(limit, 500))]),
        ).fetchall()
        results = [
            {
                "rank": float(row[0] or 0),
                "folder_path": row[1],
                "uid": int(row[2]),
                "flags": list(row[3] or []),
                "internal_date": row[4],
                "message_id": row[5],
                "internet_message_id": row[6],
                "subject": row[7],
                "message_date": row[8],
                "body_preview": row[9],
                "has_attachments": bool(row[10]),
                "size": int(row[11] or 0),
                "source_type": row[12],
                "source": row[13],
                "from": row[14] or "",
                "to": row[15] or "",
                "proposed_classifications": int(row[16] or 0),
            }
            for row in rows
        ]
        return sorted(
            results,
            key=lambda item: (item["rank"], item["message_date"] or datetime.min.replace(tzinfo=timezone.utc)),
            reverse=True,
        )

    def count_webmail_messages(self, *, mailbox_id: str, folder_path: str) -> int:
        row = self.connection.execute(
            """
            SELECT count(*)
            FROM millie_mailbox_messages mm
            JOIN millie_mailbox_folders mf ON mf.id = mm.folder_id
            WHERE mm.mailbox_id = %s
              AND mf.folder_path = %s
              AND mm.is_expunged = FALSE
              AND mm.metadata_json->>'retention_hidden_from_default_views' IS DISTINCT FROM 'true'
            """,
            (mailbox_id, folder_path),
        ).fetchone()
        return int(row[0] or 0)

    def webmail_folder_counts(self, *, mailbox_id: str) -> dict[str, int]:
        rows = self.connection.execute(
            """
            SELECT mf.folder_path, count(mm.id)
            FROM millie_mailbox_folders mf
            LEFT JOIN millie_mailbox_messages mm
             ON mm.folder_id = mf.id
             AND mm.mailbox_id = mf.mailbox_id
             AND mm.is_expunged = FALSE
             AND mm.metadata_json->>'retention_hidden_from_default_views' IS DISTINCT FROM 'true'
            WHERE mf.mailbox_id = %s
              AND mf.selectable = TRUE
            GROUP BY mf.folder_path
            """,
            (mailbox_id,),
        ).fetchall()
        return {str(row[0]): int(row[1] or 0) for row in rows}

    def get_webmail_message_by_uid(
        self,
        *,
        mailbox_id: str,
        folder_path: str,
        uid: int,
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT
                mm.imap_uid,
                mm.flags,
                mm.internal_date,
                m.id,
                m.internet_message_id,
                m.subject,
                coalesce(m.sent_at, m.received_at, mm.internal_date) AS message_date,
                m.body_preview,
                m.body_text,
                m.body_html,
                m.has_attachments,
                m.raw_mime_size_bytes,
                r.content_blob,
                mm.id,
                mm.copied_at
            FROM millie_mailbox_messages mm
            JOIN millie_mailbox_folders mf ON mf.id = mm.folder_id
            JOIN mail_messages m ON m.id = mm.message_id
            LEFT JOIN mail_raw_mime r ON r.message_id = m.id
            WHERE mm.mailbox_id = %s
              AND mf.folder_path = %s
              AND mm.imap_uid = %s
              AND mm.is_expunged = FALSE
              AND mm.metadata_json->>'retention_hidden_from_default_views' IS DISTINCT FROM 'true'
            LIMIT 1
            """,
            (mailbox_id, folder_path, uid),
        ).fetchone()
        if not row:
            return None
        message_id = row[3]
        mailbox_message_id = str(row[13])
        copied_at = row[14] or row[2] or datetime.now(timezone.utc)
        return {
            "uid": int(row[0]),
            "flags": list(row[1] or []),
            "internal_date": row[2],
            "mailbox_message_id": mailbox_message_id,
            "copied_at": copied_at,
            "message_id": message_id,
            "internet_message_id": row[4],
            "subject": row[5],
            "message_date": row[6],
            "body_preview": row[7],
            "body_text": row[8],
            "body_html": row[9],
            "has_attachments": bool(row[10]),
            "size": int(row[11] or 0),
            "raw_mime": bytes(row[12]) if row[12] is not None else None,
            "addresses": self.message_addresses(message_id),
            "attachments": self.message_attachments(message_id),
            "classifications": self.message_classifications(message_id),
            "unsubscribe_candidates": self.message_unsubscribe_candidates(message_id),
            "retention_status": self.message_retention_status(
                folder_path=folder_path,
                mailbox_message_id=mailbox_message_id,
                message_id=str(message_id),
                uid=uid,
                copied_at=copied_at,
                subject=str(row[5] or ""),
            ),
        }

    def message_retention_status(
        self,
        *,
        folder_path: str,
        mailbox_message_id: str,
        message_id: str,
        uid: int,
        copied_at: datetime,
        subject: str,
    ) -> list[dict[str, object]]:
        normalized_folder = normalize_folder(folder_path)
        rows = self.connection.execute(
            """
            SELECT id, policy_name, status, target_kind, target_value, hold_duration,
                   action, requires_review
            FROM millie_retention_policies
            WHERE target_kind = 'folder'
              AND status IN ('proposed', 'active')
              AND (target_value = %s OR target_value = %s)
            ORDER BY
                CASE status WHEN 'active' THEN 0 WHEN 'proposed' THEN 1 ELSE 2 END,
                policy_name
            """,
            (folder_path, normalized_folder),
        ).fetchall()
        if not rows:
            return []
        message = HeldMessage(
            mailbox_message_id=mailbox_message_id,
            message_id=message_id,
            folder_path=folder_path,
            imap_uid=uid,
            copied_at=copied_at,
            subject=subject,
        )
        now = datetime.now(timezone.utc)
        statuses: list[dict[str, object]] = []
        for row in rows:
            policy = RetentionPolicy(
                id=str(row[0]),
                name=str(row[1]),
                status=str(row[2]),
                target_kind=str(row[3]),
                target_value=str(row[4]),
                hold_duration=row[5],
                action=str(row[6]),
                requires_review=bool(row[7]),
            )
            status = retention_status(policy, message, now=now)
            if status is None:
                continue
            hold_duration = policy.hold_duration
            statuses.append(
                {
                    "id": policy.id,
                    "policy_name": policy.name,
                    "status": policy.status,
                    "target_kind": policy.target_kind,
                    "target_value": policy.target_value,
                    "hold_duration_seconds": (
                        int(hold_duration.total_seconds()) if hold_duration is not None else None
                    ),
                    "hold_duration_text": human_duration(hold_duration),
                    "action": policy.action,
                    "requires_review": policy.requires_review,
                    "copied_at": status.message.copied_at,
                    "eligible_at": status.eligible_at,
                    "is_eligible": status.is_eligible,
                    "age_seconds": status.age_seconds,
                }
            )
        return statuses

    def message_classifications(self, message_id: str) -> list[dict[str, object]]:
        rows = self.connection.execute(
            """
            SELECT
                id,
                classification_kind,
                classification_value,
                target_folder_path,
                target_tags,
                status,
                automation_level,
                confidence,
                reason_text,
                evidence_json,
                created_at,
                updated_at
            FROM millie_message_classifications
            WHERE message_id = %s
            ORDER BY
                CASE status
                    WHEN 'proposed' THEN 0
                    WHEN 'approved' THEN 1
                    WHEN 'rejected' THEN 2
                    ELSE 3
                END,
                confidence DESC,
                created_at DESC
            """,
            (message_id,),
        ).fetchall()
        return [
            {
                "id": row[0],
                "kind": row[1],
                "value": row[2],
                "target_folder_path": row[3],
                "target_tags": list(row[4] or []),
                "status": row[5],
                "automation_level": row[6],
                "confidence": float(row[7] or 0),
                "reason": row[8] or "",
                "evidence": row[9] or {},
                "created_at": row[10],
                "updated_at": row[11],
            }
            for row in rows
        ]

    def message_unsubscribe_candidates(self, message_id: str) -> list[dict[str, object]]:
        rows = self.connection.execute(
            """
            SELECT
                id,
                candidate_type,
                unsubscribe_url,
                unsubscribe_mailto,
                status,
                confidence,
                requires_browser,
                discovered_at,
                result_json
            FROM millie_unsubscribe_candidates
            WHERE message_id = %s
            ORDER BY
                CASE status
                    WHEN 'review_required' THEN 0
                    WHEN 'detected' THEN 1
                    WHEN 'approved' THEN 2
                    ELSE 3
                END,
                confidence DESC,
                discovered_at DESC
            """,
            (message_id,),
        ).fetchall()
        return [
            {
                "id": row[0],
                "candidate_type": row[1],
                "unsubscribe_url": row[2],
                "unsubscribe_mailto": row[3],
                "status": row[4],
                "confidence": float(row[5] or 0),
                "requires_browser": bool(row[6]),
                "discovered_at": row[7],
                "evidence": row[8] or {},
            }
            for row in rows
        ]

    def list_unsubscribe_review_items(
        self,
        *,
        limit: int = 50,
        statuses: list[str] | None = None,
    ) -> list[dict[str, object]]:
        allowed_statuses = statuses or ["review_required", "detected", "approved", "attempting", "unsafe", "failed"]
        rows = self.connection.execute(
            f"""
            WITH from_addresses AS (
                SELECT
                    message_id,
                    string_agg(
                        CASE
                            WHEN coalesce(display_name, '') <> '' AND coalesce(email_address, '') <> ''
                                THEN display_name || ' <' || email_address || '>'
                            WHEN coalesce(email_address, '') <> '' THEN email_address
                            ELSE coalesce(raw_value, '')
                        END,
                        ', ' ORDER BY ordinal
                    ) AS from_text
                FROM mail_message_addresses
                WHERE role = 'from'
                GROUP BY message_id
            )
            SELECT DISTINCT ON (u.id)
                u.id,
                u.message_id,
                u.candidate_type,
                u.unsubscribe_url,
                u.unsubscribe_mailto,
                u.status,
                u.confidence,
                u.requires_browser,
                u.discovered_at,
                u.approved_at,
                u.result_json,
                u.error_message,
                coalesce(m.subject, '(no subject)') AS subject,
                coalesce(m.sent_at, m.received_at, v.internal_date) AS message_date,
                coalesce(fa.from_text, '') AS from_text,
                v.folder_path,
                v.imap_uid
            FROM millie_unsubscribe_candidates u
            JOIN mail_messages m ON m.id = u.message_id
            LEFT JOIN millie_v_mailbox_messages v ON v.message_id = u.message_id
            LEFT JOIN from_addresses fa ON fa.message_id = u.message_id
            WHERE u.status IN ({placeholders(allowed_statuses)})
            ORDER BY
                u.id,
                CASE WHEN coalesce(v.folder_path, '') = 'All Mail' THEN 1 ELSE 0 END,
                CASE u.status
                    WHEN 'approved' THEN 0
                    WHEN 'review_required' THEN 1
                    WHEN 'detected' THEN 2
                    WHEN 'attempting' THEN 3
                    WHEN 'failed' THEN 4
                    WHEN 'unsafe' THEN 5
                    ELSE 6
                END,
                u.confidence DESC,
                u.discovered_at DESC
            LIMIT %s
            """,
            tuple([*allowed_statuses, limit]),
        ).fetchall()
        return [
            {
                "id": row[0],
                "message_id": row[1],
                "candidate_type": row[2],
                "unsubscribe_url": row[3],
                "unsubscribe_mailto": row[4],
                "status": row[5],
                "confidence": float(row[6] or 0),
                "requires_browser": bool(row[7]),
                "discovered_at": row[8],
                "approved_at": row[9],
                "evidence": row[10] or {},
                "error_message": row[11] or "",
                "subject": row[12] or "(no subject)",
                "message_date": row[13],
                "from": row[14] or "",
                "folder_path": row[15],
                "uid": int(row[16]) if row[16] is not None else None,
            }
            for row in rows
        ]

    def list_review_suggestions(self, *, limit: int = 50) -> list[dict[str, object]]:
        rows = self.connection.execute(
            """
            WITH from_addresses AS (
                SELECT
                    message_id,
                    string_agg(
                        CASE
                            WHEN coalesce(display_name, '') <> '' AND coalesce(email_address, '') <> ''
                                THEN display_name || ' <' || email_address || '>'
                            WHEN coalesce(email_address, '') <> '' THEN email_address
                            ELSE coalesce(raw_value, '')
                        END,
                        ', ' ORDER BY ordinal
                    ) AS from_text
                FROM mail_message_addresses
                WHERE role = 'from'
                GROUP BY message_id
            )
            SELECT DISTINCT ON (c.id)
                c.id,
                c.message_id,
                c.classification_kind,
                c.classification_value,
                c.target_folder_path,
                c.target_tags,
                c.confidence,
                c.reason_text,
                m.subject,
                coalesce(m.sent_at, m.received_at, v.internal_date) AS message_date,
                coalesce(fa.from_text, '') AS from_text,
                v.folder_path,
                v.imap_uid
            FROM millie_message_classifications c
            JOIN mail_messages m ON m.id = c.message_id
            LEFT JOIN millie_v_mailbox_messages v ON v.message_id = c.message_id
            LEFT JOIN from_addresses fa ON fa.message_id = c.message_id
            WHERE c.status = 'proposed'
            ORDER BY
                c.id,
                CASE WHEN coalesce(v.folder_path, '') = 'All Mail' THEN 1 ELSE 0 END,
                c.confidence DESC,
                c.created_at DESC
            LIMIT %s
            """,
            (limit,),
        ).fetchall()
        return [
            {
                "classification_id": row[0],
                "message_id": row[1],
                "kind": row[2],
                "value": row[3],
                "target_folder_path": row[4],
                "target_tags": list(row[5] or []),
                "confidence": float(row[6] or 0),
                "reason": row[7] or "",
                "subject": row[8] or "(no subject)",
                "message_date": row[9],
                "from": row[10] or "",
                "folder_path": row[11],
                "uid": int(row[12]) if row[12] is not None else None,
            }
            for row in rows
        ]

    def list_retention_review_items(self, *, mailbox_id: str, limit: int = 50) -> list[dict[str, object]]:
        rows = self.connection.execute(
            """
            WITH from_addresses AS (
                SELECT
                    message_id,
                    string_agg(
                        CASE
                            WHEN coalesce(display_name, '') <> '' AND coalesce(email_address, '') <> ''
                                THEN display_name || ' <' || email_address || '>'
                            WHEN coalesce(email_address, '') <> '' THEN email_address
                            ELSE coalesce(raw_value, '')
                        END,
                        ', ' ORDER BY ordinal
                    ) AS from_text
                FROM mail_message_addresses
                WHERE role = 'from'
                GROUP BY message_id
            )
            SELECT
                p.id,
                p.policy_name,
                p.status,
                p.target_value,
                p.hold_duration,
                p.action,
                p.requires_review,
                mm.id,
                mm.message_id,
                mf.folder_path,
                mm.imap_uid,
                mm.copied_at,
                mm.copied_at + p.hold_duration AS eligible_at,
                coalesce(m.subject, '(no subject)') AS subject,
                coalesce(m.sent_at, m.received_at, mm.internal_date) AS message_date,
                coalesce(fa.from_text, '') AS from_text,
                latest_feedback.created_at AS latest_feedback_at,
                latest_feedback.new_value_json AS latest_feedback_json
            FROM millie_retention_policies p
            JOIN millie_mailbox_folders mf
              ON p.target_kind = 'folder'
             AND p.target_value = mf.folder_path
            JOIN millie_mailbox_messages mm
              ON mm.folder_id = mf.id
             AND mm.mailbox_id = mf.mailbox_id
             AND mm.is_expunged = FALSE
            JOIN mail_messages m ON m.id = mm.message_id
            LEFT JOIN from_addresses fa ON fa.message_id = m.id
            LEFT JOIN LATERAL (
                SELECT e.created_at, e.new_value_json
                FROM millie_user_feedback_events e
                WHERE e.message_id = m.id
                  AND e.feedback_type = 'retention_override'
                  AND e.metadata_json->>'retention_policy_id' = p.id
                  AND e.metadata_json->>'mailbox_message_id' = mm.id
                ORDER BY e.created_at DESC
                LIMIT 1
            ) latest_feedback ON TRUE
            WHERE mf.mailbox_id = %s
              AND p.status IN ('proposed', 'active')
              AND p.hold_duration IS NOT NULL
              AND mm.copied_at + p.hold_duration <= now()
              AND (
                latest_feedback.created_at IS NULL
                OR (
                    latest_feedback.new_value_json->>'action' = 'defer'
                    AND (latest_feedback.new_value_json->>'review_after')::timestamptz <= now()
                )
              )
            ORDER BY mm.copied_at + p.hold_duration, mf.folder_path, mm.imap_uid
            LIMIT %s
            """,
            (mailbox_id, limit),
        ).fetchall()
        return [
            {
                "policy_id": row[0],
                "policy_name": row[1],
                "policy_status": row[2],
                "target_value": row[3],
                "hold_duration_text": human_duration(row[4]),
                "hold_duration_seconds": int(row[4].total_seconds()) if row[4] is not None else None,
                "policy_action": row[5],
                "requires_review": bool(row[6]),
                "mailbox_message_id": row[7],
                "message_id": row[8],
                "folder_path": row[9],
                "uid": int(row[10]),
                "copied_at": row[11],
                "eligible_at": row[12],
                "subject": row[13],
                "message_date": row[14],
                "from": row[15],
                "latest_feedback_at": row[16],
                "latest_feedback": row[17] or {},
            }
            for row in rows
        ]

    def list_retention_policies(
        self,
        *,
        statuses: list[str] | None = None,
    ) -> list[dict[str, object]]:
        status_filter = ""
        params: list[object] = []
        if statuses:
            normalized = [
                str(status).strip().lower()
                for status in statuses
                if str(status).strip().lower() in RETENTION_POLICY_STATUSES
            ]
            if not normalized:
                return []
            status_filter = f"WHERE status IN ({placeholders(normalized)})"
            params.extend(normalized)
        rows = self.connection.execute(
            f"""
            SELECT
                id, policy_name, status, target_kind, target_value, hold_duration,
                action, requires_review, condition_json, created_by_identity_id,
                created_at, updated_at, metadata_json
            FROM millie_retention_policies
            {status_filter}
            ORDER BY
                CASE status
                    WHEN 'active' THEN 0
                    WHEN 'proposed' THEN 1
                    WHEN 'disabled' THEN 2
                    ELSE 3
                END,
                target_kind,
                target_value,
                policy_name
            """,
            tuple(params),
        ).fetchall()
        return [self._retention_policy_dict(row) for row in rows]

    def record_retention_policy_action(
        self,
        *,
        policy_id: str,
        action: str,
        updates: dict[str, object] | None = None,
        identity_id: str | None = None,
    ) -> dict[str, object]:
        action = action.strip().lower()
        if action not in RETENTION_POLICY_WEB_ACTIONS:
            raise ValueError(f"Unsupported retention policy action: {action}")
        before = self.load_retention_policy(policy_id)
        if not before:
            raise KeyError(f"Retention policy not found: {policy_id}")

        values = dict(updates or {})
        changes: dict[str, object] = {}
        if action == "activate":
            changes["status"] = "active"
        elif action == "disable":
            changes["status"] = "disabled"
        elif action == "update":
            if "policy_name" in values:
                name = str(values["policy_name"] or "").strip()
                if not name:
                    raise ValueError("policy_name cannot be blank")
                changes["policy_name"] = name
            if "status" in values:
                status = str(values["status"] or "").strip().lower()
                if status not in RETENTION_POLICY_STATUSES:
                    raise ValueError(f"Unsupported retention policy status: {status}")
                changes["status"] = status
            if "policy_action" in values:
                policy_action = str(values["policy_action"] or "").strip()
                if policy_action not in RETENTION_POLICY_ACTIONS:
                    raise ValueError(f"Unsupported retention policy action: {policy_action}")
                changes["action"] = policy_action
            if "requires_review" in values:
                changes["requires_review"] = bool(values["requires_review"])
            if "hold_duration_seconds" in values:
                seconds = int(values["hold_duration_seconds"] or 0)
                if seconds <= 0:
                    raise ValueError("hold_duration_seconds must be greater than zero")
                changes["hold_duration"] = timedelta(seconds=seconds)

        if not changes:
            return before

        assignments = [f"{column} = %s" for column in changes]
        params = [*changes.values(), Jsonb({"managed_by": "millie_webmail"}), policy_id]
        self.connection.execute(
            f"""
            UPDATE millie_retention_policies
            SET {", ".join(assignments)},
                updated_at = now(),
                metadata_json = metadata_json || %s
            WHERE id = %s
            """,
            tuple(params),
        )
        after = self.load_retention_policy(policy_id)
        self._insert_automation_audit(
            action_type="custom",
            identity_id=identity_id,
            retention_policy_id=policy_id,
            before_json=before,
            after_json={
                "policy_change": action,
                "policy_id": policy_id,
                "policy": after,
            },
        )
        return after

    def load_retention_policy(self, policy_id: str) -> dict[str, object]:
        row = self.connection.execute(
            """
            SELECT
                id, policy_name, status, target_kind, target_value, hold_duration,
                action, requires_review, condition_json, created_by_identity_id,
                created_at, updated_at, metadata_json
            FROM millie_retention_policies
            WHERE id = %s
            """,
            (policy_id,),
        ).fetchone()
        return self._retention_policy_dict(row) if row else {}

    def _retention_policy_dict(self, row: object) -> dict[str, object]:
        hold_duration = row[5]
        return {
            "id": row[0],
            "policy_name": row[1],
            "status": row[2],
            "target_kind": row[3],
            "target_value": row[4],
            "hold_duration_seconds": int(hold_duration.total_seconds()) if hold_duration is not None else None,
            "hold_duration_text": human_duration(hold_duration),
            "policy_action": row[6],
            "requires_review": bool(row[7]),
            "condition": row[8] or {},
            "created_by_identity_id": row[9],
            "created_at": row[10],
            "updated_at": row[11],
            "metadata": row[12] or {},
        }

    def record_classification_feedback(
        self,
        *,
        classification_id: str,
        action: str,
        identity_id: str | None = None,
        feedback_source: str = "webmail",
    ) -> dict[str, object]:
        if action not in {"approve", "reject", "always", "never"}:
            raise ValueError(f"Unsupported classification action: {action}")
        row = self.connection.execute(
            """
            SELECT
                id,
                message_id,
                classification_kind,
                classification_value,
                target_folder_path,
                target_tags,
                confidence,
                reason_text,
                evidence_json,
                status,
                rule_id
            FROM millie_message_classifications
            WHERE id = %s
            """,
            (classification_id,),
        ).fetchone()
        if not row:
            raise KeyError(f"Classification not found: {classification_id}")

        status = "approved" if action in {"approve", "always"} else "rejected"
        source_rule_id = row[10]
        rule_id = source_rule_id if action in {"approve", "reject"} else None
        if action in {"always", "never"}:
            rule_id = self._upsert_feedback_rule(
                classification_row=row,
                identity_id=identity_id,
                action=action,
            )
        elif source_rule_id:
            self._record_rule_feedback_signal(
                rule_id=str(source_rule_id),
                positive=action == "approve",
            )
        self.connection.execute(
            """
            UPDATE millie_message_classifications
            SET status = %s,
                rule_id = coalesce(%s, rule_id),
                reviewed_by_identity_id = %s,
                reviewed_at = now(),
                updated_at = now()
            WHERE id = %s
            """,
            (status, rule_id, identity_id, classification_id),
        )
        feedback_type = {
            "approve": "approve_classification",
            "reject": "reject_classification",
            "always": "create_rule",
            "never": "create_rule",
        }[action]
        self.connection.execute(
            """
            INSERT INTO millie_user_feedback_events (
                id, identity_id, message_id, classification_id, rule_id,
                feedback_type, feedback_source, previous_value_json, new_value_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                str(uuid.uuid4()),
                identity_id,
                row[1],
                classification_id,
                rule_id,
                feedback_type,
                feedback_source,
                Jsonb({"status": row[9], "rule_id": source_rule_id}),
                Jsonb({"status": status, "action": action, "rule_id": rule_id}),
            ),
        )
        audit_action = {
            "approve": "approve_classification",
            "reject": "reject_classification",
            "always": "create_rule",
            "never": "disable_rule",
        }[action]
        self._insert_automation_audit(
            action_type=audit_action,
            identity_id=identity_id,
            message_id=str(row[1]),
            classification_id=classification_id,
            rule_id=rule_id,
            after_json={
                "status": status,
                "action": action,
                "rule_id": rule_id,
            },
        )
        return {
            "id": classification_id,
            "message_id": row[1],
            "status": status,
            "action": action,
            "rule_id": rule_id,
        }

    def _record_rule_feedback_signal(self, *, rule_id: str, positive: bool) -> None:
        column = "positive_feedback_count" if positive else "negative_feedback_count"
        self.connection.execute(
            f"""
            UPDATE millie_brain_rules
            SET {column} = {column} + 1,
                updated_at = now()
            WHERE id = %s
            """,
            (rule_id,),
        )

    def _upsert_feedback_rule(
        self,
        *,
        classification_row: object,
        identity_id: str | None,
        action: str,
    ) -> str:
        (
            _classification_id,
            _message_id,
            kind,
            value,
            target_folder_path,
            target_tags,
            confidence,
            reason_text,
            evidence_json,
            _status,
            _source_rule_id,
        ) = classification_row
        context = self._classification_rule_context(str(_message_id))
        sender_domain = str(context.get("sender_domain") or "")
        folder_path = str(context.get("folder_path") or "")
        message_year = str(context.get("message_year") or "")
        rule_id = stable_id(
            "millie_brain_rule",
            action,
            kind,
            value,
            target_folder_path,
            ",".join(target_tags or []),
            sender_domain,
            folder_path,
            message_year,
        )
        block = action == "never"
        context_parts = [
            part
            for part in (sender_domain, folder_path, message_year)
            if part
        ]
        context_suffix = f" for {' / '.join(context_parts)}" if context_parts else ""
        rule_name = (
            f"Never suggest {kind}:{value}{context_suffix}"
            if block
            else f"Always suggest {kind}:{value}{context_suffix}"
        )
        condition = {
            "classification_kind": kind,
            "classification_value": value,
            "target_folder_path": target_folder_path,
            "target_tags": list(target_tags or []),
        }
        condition.update({key: value for key, value in context.items() if value})
        rule_action = {
            "action": "block_suggestion" if block else "suggest",
            "classification_kind": kind,
            "classification_value": value,
            "target_folder_path": target_folder_path,
            "target_tags": list(target_tags or []),
        }
        self.connection.execute(
            """
            INSERT INTO millie_brain_rules (
                id, rule_name, rule_type, rule_source, status, automation_level,
                priority, condition_json, action_json, confidence, evidence_count,
                positive_feedback_count, negative_feedback_count,
                created_by_identity_id, updated_at, metadata_json
            )
            VALUES (
                %s, %s, %s, 'user', 'active', 'review',
                %s, %s, %s, %s, 1, %s, %s, %s, now(), %s
            )
            ON CONFLICT(id) DO UPDATE SET
                status = 'active',
                confidence = greatest(millie_brain_rules.confidence, excluded.confidence),
                evidence_count = millie_brain_rules.evidence_count + 1,
                positive_feedback_count = millie_brain_rules.positive_feedback_count + excluded.positive_feedback_count,
                negative_feedback_count = millie_brain_rules.negative_feedback_count + excluded.negative_feedback_count,
                updated_at = now(),
                metadata_json = millie_brain_rules.metadata_json || excluded.metadata_json
            """,
            (
                rule_id,
                rule_name,
                kind,
                10 if block else 100,
                Jsonb(condition),
                Jsonb(rule_action),
                confidence,
                0 if block else 1,
                1 if block else 0,
                identity_id,
                Jsonb(
                    {
                        "feedback_action": action,
                        "reason": reason_text,
                        "evidence": evidence_json or {},
                        "context": context,
                    }
                ),
            ),
        )
        return rule_id

    def _classification_rule_context(self, message_id: str) -> dict[str, object]:
        row = self.connection.execute(
            """
            WITH from_addresses AS (
                SELECT
                    message_id,
                    string_agg(
                        CASE
                            WHEN coalesce(display_name, '') <> '' AND coalesce(email_address, '') <> ''
                                THEN display_name || ' <' || email_address || '>'
                            WHEN coalesce(email_address, '') <> '' THEN email_address
                            ELSE coalesce(raw_value, '')
                        END,
                        ', ' ORDER BY ordinal
                    ) AS from_text,
                    min(lower(email_address)) FILTER (
                        WHERE coalesce(email_address, '') <> ''
                    ) AS from_email
                FROM mail_message_addresses
                WHERE role = 'from'
                GROUP BY message_id
            )
            SELECT DISTINCT ON (m.id)
                coalesce(v.folder_path, '') AS folder_path,
                coalesce(m.sent_at, m.received_at, v.internal_date) AS message_date,
                coalesce(fa.from_email, '') AS from_email,
                coalesce(fa.from_text, '') AS from_text
            FROM mail_messages m
            LEFT JOIN millie_v_mailbox_messages v ON v.message_id = m.id
            LEFT JOIN from_addresses fa ON fa.message_id = m.id
            WHERE m.id = %s
            ORDER BY
                m.id,
                CASE WHEN coalesce(v.folder_path, '') = 'All Mail' THEN 1 ELSE 0 END
            """,
            (message_id,),
        ).fetchone()
        if not row:
            return {}
        message_date = row[1]
        return {
            "sender_domain": self._sender_domain(str(row[2] or ""), str(row[3] or "")),
            "folder_path": str(row[0] or ""),
            "message_year": str(message_date.year) if message_date else "",
        }

    def list_brain_rules(
        self,
        *,
        statuses: list[str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        status_filter = ""
        params: list[object] = []
        if statuses:
            normalized = [
                str(status).strip().lower()
                for status in statuses
                if str(status).strip().lower() in BRAIN_RULE_STATUSES
            ]
            if not normalized:
                return []
            status_filter = f"WHERE status IN ({placeholders(normalized)})"
            params.extend(normalized)
        rows = self.connection.execute(
            f"""
            SELECT
                id, rule_name, rule_type, rule_source, status, automation_level,
                priority, condition_json, action_json, confidence, evidence_count,
                positive_feedback_count, negative_feedback_count,
                last_matched_at, created_at, updated_at, metadata_json
            FROM millie_brain_rules
            {status_filter}
            ORDER BY
                CASE status
                    WHEN 'active' THEN 0
                    WHEN 'proposed' THEN 1
                    WHEN 'disabled' THEN 2
                    ELSE 3
                END,
                priority DESC,
                evidence_count DESC,
                updated_at DESC
            LIMIT %s
            """,
            tuple([*params, max(1, min(limit, 500))]),
        ).fetchall()
        return [self._brain_rule_dict(row) for row in rows]

    def proposal_review(
        self,
        *,
        statuses: list[str] | None = None,
        limit: int = 100,
    ) -> dict[str, object]:
        normalized_statuses = self._normalized_rule_statuses(statuses)
        if statuses is not None and not normalized_statuses:
            return {
                "proposals": [],
                "status_counts": {},
                "total": 0,
                "limit": max(1, min(limit, 500)),
            }
        status_filter = ""
        params: list[object] = []
        if normalized_statuses:
            status_filter = f"AND status IN ({placeholders(normalized_statuses)})"
            params.extend(normalized_statuses)
        rows = self.connection.execute(
            f"""
            SELECT
                id, rule_name, rule_type, rule_source, status, automation_level,
                priority, condition_json, action_json, confidence, evidence_count,
                positive_feedback_count, negative_feedback_count,
                last_matched_at, created_at, updated_at, metadata_json
            FROM millie_brain_rules
            WHERE coalesce(metadata_json, '{{}}'::jsonb) ? 'proposal'
              {status_filter}
            ORDER BY
                CASE status
                    WHEN 'proposed' THEN 0
                    WHEN 'disabled' THEN 1
                    WHEN 'active' THEN 2
                    WHEN 'retired' THEN 3
                    ELSE 4
                END,
                evidence_count DESC,
                confidence DESC,
                updated_at DESC
            LIMIT %s
            """,
            tuple([*params, max(1, min(limit, 500))]),
        ).fetchall()
        count_rows = self.connection.execute(
            """
            SELECT status, count(*)
            FROM millie_brain_rules
            WHERE coalesce(metadata_json, '{}'::jsonb) ? 'proposal'
            GROUP BY status
            ORDER BY status
            """
        ).fetchall()
        proposals = [self._proposal_rule_dict(row) for row in rows]
        counts = {str(row[0] or "unknown"): int(row[1] or 0) for row in count_rows}
        return {
            "proposals": proposals,
            "status_counts": counts,
            "total": sum(counts.values()),
            "limit": max(1, min(limit, 500)),
        }

    def record_proposal_batch_action(
        self,
        *,
        rule_ids: list[str],
        action: str,
        identity_id: str | None = None,
    ) -> dict[str, object]:
        action = action.strip().lower()
        if action not in {"activate", "disable", "retire"}:
            raise ValueError(f"Unsupported proposal action: {action}")
        normalized_ids = []
        for rule_id in rule_ids:
            value = str(rule_id or "").strip()
            if value and value not in normalized_ids:
                normalized_ids.append(value)
        if not normalized_ids:
            raise ValueError("At least one proposal rule id is required")
        if len(normalized_ids) > 100:
            raise ValueError("Proposal batch actions are limited to 100 rules")
        results = []
        for rule_id in normalized_ids:
            before = self.load_brain_rule(rule_id)
            if not before:
                raise KeyError(f"Brain rule not found: {rule_id}")
            metadata = before.get("metadata") if isinstance(before, dict) else {}
            if not isinstance(metadata, dict) or "proposal" not in metadata:
                raise ValueError(f"Brain rule is not a saved proposal: {rule_id}")
            results.append(
                self.record_brain_rule_action(
                    rule_id=rule_id,
                    action=action,
                    identity_id=identity_id,
                )
            )
        return {
            "ok": True,
            "action": action,
            "count": len(results),
            "rules": results,
        }

    def _normalized_rule_statuses(self, statuses: list[str] | None) -> list[str]:
        normalized: list[str] = []
        for status in statuses or []:
            value = str(status).strip().lower()
            if value in BRAIN_RULE_STATUSES and value not in normalized:
                normalized.append(value)
        return normalized

    def record_brain_rule_action(
        self,
        *,
        rule_id: str,
        action: str,
        updates: dict[str, object] | None = None,
        identity_id: str | None = None,
    ) -> dict[str, object]:
        action = action.strip().lower()
        if action not in BRAIN_RULE_ACTIONS:
            raise ValueError(f"Unsupported rule action: {action}")
        before = self.load_brain_rule(rule_id)
        if not before:
            raise KeyError(f"Brain rule not found: {rule_id}")
        values = dict(updates or {})
        changes: dict[str, object] = {}
        if action == "activate":
            changes["status"] = "active"
        elif action == "disable":
            changes["status"] = "disabled"
        elif action == "retire":
            changes["status"] = "retired"
        elif action == "update":
            if "rule_name" in values:
                name = str(values["rule_name"] or "").strip()
                if not name:
                    raise ValueError("rule_name cannot be blank")
                changes["rule_name"] = name
            if "status" in values:
                status = str(values["status"] or "").strip().lower()
                if status not in BRAIN_RULE_STATUSES:
                    raise ValueError(f"Unsupported rule status: {status}")
                changes["status"] = status
            if "priority" in values:
                changes["priority"] = int(values["priority"])
        if not changes:
            return before
        assignments = [f"{column} = %s" for column in changes]
        self.connection.execute(
            f"""
            UPDATE millie_brain_rules
            SET {", ".join(assignments)},
                updated_at = now(),
                metadata_json = metadata_json || %s
            WHERE id = %s
            """,
            tuple([*changes.values(), Jsonb({"managed_by": "millie_webmail"}), rule_id]),
        )
        after = self.load_brain_rule(rule_id)
        self._insert_automation_audit(
            action_type="custom",
            identity_id=identity_id,
            rule_id=rule_id,
            before_json=before,
            after_json={
                "rule_change": action,
                "rule_id": rule_id,
                "rule": after,
            },
        )
        return after

    def load_brain_rule(self, rule_id: str) -> dict[str, object]:
        row = self.connection.execute(
            """
            SELECT
                id, rule_name, rule_type, rule_source, status, automation_level,
                priority, condition_json, action_json, confidence, evidence_count,
                positive_feedback_count, negative_feedback_count,
                last_matched_at, created_at, updated_at, metadata_json
            FROM millie_brain_rules
            WHERE id = %s
            """,
            (rule_id,),
        ).fetchone()
        return self._brain_rule_dict(row) if row else {}

    def _brain_rule_dict(self, row: object) -> dict[str, object]:
        return {
            "id": row[0],
            "rule_name": row[1],
            "rule_type": row[2],
            "rule_source": row[3],
            "status": row[4],
            "automation_level": row[5],
            "priority": int(row[6] or 0),
            "condition": row[7] or {},
            "rule_action": row[8] or {},
            "confidence": float(row[9] or 0),
            "evidence_count": int(row[10] or 0),
            "positive_feedback_count": int(row[11] or 0),
            "negative_feedback_count": int(row[12] or 0),
            "last_matched_at": row[13],
            "created_at": row[14],
            "updated_at": row[15],
            "metadata": row[16] or {},
        }

    def _proposal_rule_dict(self, row: object) -> dict[str, object]:
        rule = self._brain_rule_dict(row)
        metadata = rule.get("metadata") if isinstance(rule.get("metadata"), dict) else {}
        proposal = metadata.get("proposal") if isinstance(metadata, dict) else {}
        if not isinstance(proposal, dict):
            proposal = {}
        nested_metadata = proposal.get("metadata")
        if not isinstance(nested_metadata, dict):
            nested_metadata = {}
        condition = proposal.get("condition")
        if not isinstance(condition, dict):
            condition = rule.get("condition") if isinstance(rule.get("condition"), dict) else {}
        rule_action = rule.get("rule_action") if isinstance(rule.get("rule_action"), dict) else {}
        backtest = proposal.get("backtest")
        if not isinstance(backtest, dict):
            backtest = {}
        samples = backtest.get("samples") or proposal.get("samples") or []
        proposal_type = (
            nested_metadata.get("proposal_type")
            or condition.get("proposal_type")
            or rule_action.get("action")
            or rule.get("rule_type")
            or "proposal"
        )
        rule["proposal"] = proposal
        rule["proposal_type"] = str(proposal_type)
        rule["proposal_seed_action"] = str(proposal.get("action") or "")
        rule["proposal_target"] = (
            proposal.get("target")
            or rule_action.get("target")
            or condition.get("target")
            or condition.get("target_folder_path")
            or ", ".join(str(tag) for tag in condition.get("target_tags") or [])
            or ""
        )
        rule["proposal_samples"] = samples if isinstance(samples, list) else []
        rule["proposal_context"] = {
            "sender_domains": proposal.get("sender_domains") or nested_metadata.get("sender_domain") or [],
            "source_folders": proposal.get("source_folders") or nested_metadata.get("folder_path") or [],
            "message_years": proposal.get("message_years") or nested_metadata.get("message_year") or [],
        }
        return rule

    def learning_metrics(self, *, limit: int = 12) -> dict[str, object]:
        limit = max(1, min(limit, 50))
        classification_rows = self.connection.execute(
            """
            SELECT
                classifier_type,
                status,
                count(*) AS count,
                avg(confidence) AS avg_confidence
            FROM millie_message_classifications
            GROUP BY classifier_type, status
            ORDER BY classifier_type, status
            """
        ).fetchall()
        rule_status_rows = self.connection.execute(
            """
            SELECT status, count(*)
            FROM millie_brain_rules
            GROUP BY status
            ORDER BY status
            """
        ).fetchall()
        feedback_rows = self.connection.execute(
            """
            SELECT feedback_type, count(*), max(created_at)
            FROM millie_user_feedback_events
            GROUP BY feedback_type
            ORDER BY feedback_type
            """
        ).fetchall()
        target_rows = self.connection.execute(
            """
            SELECT
                classification_kind,
                classification_value,
                target_folder_path,
                target_tags,
                status,
                count(*) AS count,
                avg(confidence) AS avg_confidence
            FROM millie_message_classifications
            GROUP BY
                classification_kind,
                classification_value,
                target_folder_path,
                target_tags,
                status
            ORDER BY count DESC, avg_confidence DESC NULLS LAST
            LIMIT %s
            """,
            (limit,),
        ).fetchall()
        top_rule_rows = self.connection.execute(
            """
            SELECT
                id, rule_name, rule_type, rule_source, status, automation_level,
                priority, condition_json, action_json, confidence, evidence_count,
                positive_feedback_count, negative_feedback_count,
                last_matched_at, created_at, updated_at, metadata_json
            FROM millie_brain_rules
            WHERE status = 'active'
            ORDER BY
                evidence_count DESC,
                positive_feedback_count DESC,
                priority DESC,
                updated_at DESC
            LIMIT %s
            """,
            (limit,),
        ).fetchall()
        attention_rule_rows = self.connection.execute(
            """
            SELECT
                id, rule_name, rule_type, rule_source, status, automation_level,
                priority, condition_json, action_json, confidence, evidence_count,
                positive_feedback_count, negative_feedback_count,
                last_matched_at, created_at, updated_at, metadata_json
            FROM millie_brain_rules
            WHERE status = 'active'
              AND (
                negative_feedback_count > 0
                OR evidence_count <= 1
                OR last_matched_at IS NULL
              )
            ORDER BY
                negative_feedback_count DESC,
                evidence_count ASC,
                last_matched_at ASC NULLS FIRST,
                updated_at DESC
            LIMIT %s
            """,
            (limit,),
        ).fetchall()

        classification_by_status: dict[str, int] = {}
        classification_by_classifier: dict[str, int] = {}
        classifier_status_rows = []
        for row in classification_rows:
            classifier = str(row[0] or "unknown")
            status = str(row[1] or "unknown")
            count = int(row[2] or 0)
            classification_by_status[status] = classification_by_status.get(status, 0) + count
            classification_by_classifier[classifier] = classification_by_classifier.get(classifier, 0) + count
            classifier_status_rows.append(
                {
                    "classifier_type": classifier,
                    "status": status,
                    "count": count,
                    "avg_confidence": round(float(row[3] or 0), 4),
                }
            )
        rule_by_status = {str(row[0] or "unknown"): int(row[1] or 0) for row in rule_status_rows}
        feedback_by_type = {
            str(row[0] or "unknown"): {
                "count": int(row[1] or 0),
                "last_at": row[2],
            }
            for row in feedback_rows
        }
        feedback_total = sum(item["count"] for item in feedback_by_type.values())
        return {
            "summary": {
                "classification_total": sum(classification_by_status.values()),
                "proposed": classification_by_status.get("proposed", 0),
                "approved": classification_by_status.get("approved", 0),
                "rejected": classification_by_status.get("rejected", 0),
                "applied": classification_by_status.get("applied", 0),
                "rule_total": sum(rule_by_status.values()),
                "active_rules": rule_by_status.get("active", 0),
                "attention_rules": len(attention_rule_rows),
                "feedback_total": feedback_total,
            },
            "classifications_by_status": classification_by_status,
            "classifications_by_classifier": classification_by_classifier,
            "classifier_status": classifier_status_rows,
            "rules_by_status": rule_by_status,
            "feedback_by_type": feedback_by_type,
            "target_breakdown": [
                {
                    "kind": row[0],
                    "value": row[1],
                    "target_folder_path": row[2],
                    "target_tags": list(row[3] or []),
                    "status": row[4],
                    "count": int(row[5] or 0),
                    "avg_confidence": round(float(row[6] or 0), 4),
                }
                for row in target_rows
            ],
            "top_rules": [self._brain_rule_dict(row) for row in top_rule_rows],
            "attention_rules": [self._brain_rule_dict(row) for row in attention_rule_rows],
            "limit": limit,
        }

    def rule_backtest_candidates(
        self,
        *,
        limit: int = 25,
        sample_limit: int = 5,
        candidate_limit: int = 5000,
        min_messages: int = 1,
    ) -> list[dict[str, object]]:
        rows = self.connection.execute(
            """
            WITH from_addresses AS (
                SELECT
                    message_id,
                    string_agg(
                        CASE
                            WHEN coalesce(display_name, '') <> '' AND coalesce(email_address, '') <> ''
                                THEN display_name || ' <' || email_address || '>'
                            WHEN coalesce(email_address, '') <> '' THEN email_address
                            ELSE coalesce(raw_value, '')
                        END,
                        ', ' ORDER BY ordinal
                    ) AS from_text,
                    min(lower(email_address)) FILTER (
                        WHERE coalesce(email_address, '') <> ''
                    ) AS from_email
                FROM mail_message_addresses
                WHERE role = 'from'
                GROUP BY message_id
            ),
            candidates AS (
                SELECT DISTINCT ON (c.id)
                    c.id,
                    c.message_id,
                    c.classification_kind,
                    c.classification_value,
                    c.target_folder_path,
                    c.target_tags,
                    c.confidence,
                    c.status,
                    coalesce(m.subject, '(no subject)') AS subject,
                    coalesce(m.sent_at, m.received_at, v.internal_date) AS message_date,
                    coalesce(fa.from_text, '') AS from_text,
                    coalesce(fa.from_email, '') AS from_email,
                    coalesce(v.folder_path, '') AS folder_path,
                    v.imap_uid
                FROM millie_message_classifications c
                JOIN mail_messages m ON m.id = c.message_id
                LEFT JOIN millie_v_mailbox_messages v ON v.message_id = c.message_id
                LEFT JOIN from_addresses fa ON fa.message_id = c.message_id
                WHERE c.status IN ('proposed', 'approved', 'applied')
                  AND c.classification_kind IN ('folder', 'tag', 'spam', 'trash')
                ORDER BY
                    c.id,
                    CASE WHEN coalesce(v.folder_path, '') = 'All Mail' THEN 1 ELSE 0 END,
                    c.confidence DESC,
                    c.created_at DESC
                LIMIT %s
            )
            SELECT *
            FROM candidates
            ORDER BY confidence DESC
            """,
            (max(1, min(candidate_limit, 20000)),),
        ).fetchall()
        groups: dict[tuple[object, ...], dict[str, object]] = {}
        sample_limit = max(1, min(sample_limit, 12))
        for row in rows:
            message_date = row[9]
            message_year = str(message_date.year) if message_date else ""
            sender_domain = self._sender_domain(str(row[11] or ""), str(row[10] or ""))
            target_tags = tuple(str(tag) for tag in row[5] or [])
            key = (
                row[2],
                row[3],
                row[4],
                target_tags,
                sender_domain,
                row[12] or "",
                message_year,
            )
            group = groups.setdefault(
                key,
                {
                    "kind": row[2],
                    "value": row[3],
                    "target_folder_path": row[4],
                    "target_tags": list(target_tags),
                    "sender_domain": sender_domain,
                    "folder_path": row[12] or "",
                    "message_year": message_year,
                    "evidence_count": 0,
                    "confidence_total": 0.0,
                    "classification_ids": [],
                    "samples": [],
                },
            )
            group["evidence_count"] = int(group["evidence_count"]) + 1
            group["confidence_total"] = float(group["confidence_total"]) + float(row[6] or 0)
            classification_ids = group["classification_ids"]
            if isinstance(classification_ids, list):
                classification_ids.append(row[0])
            samples = group["samples"]
            if isinstance(samples, list) and len(samples) < sample_limit:
                samples.append(
                    {
                        "classification_id": row[0],
                        "message_id": row[1],
                        "subject": row[8],
                        "message_date": row[9],
                        "from": row[10] or "",
                        "folder_path": row[12],
                        "uid": int(row[13]) if row[13] is not None else None,
                    }
                )

        candidates: list[dict[str, object]] = []
        for group in groups.values():
            evidence_count = int(group["evidence_count"] or 0)
            if evidence_count < max(1, min_messages):
                continue
            avg_confidence = float(group.pop("confidence_total")) / max(evidence_count, 1)
            candidate = self._rule_candidate_from_group(
                group,
                avg_confidence=avg_confidence,
                sample_limit=sample_limit,
            )
            if candidate.get("existing_rule_status") in {"active", "retired"}:
                continue
            candidates.append(candidate)

        candidates.sort(
            key=lambda item: (
                -int(item["evidence_count"]),
                -float(item["confidence"]),
                int(item["backtest"]["conflicting_suggestions"]),
                str(item["target"]),
            )
        )
        return candidates[: max(1, min(limit, 100))]

    def _rule_candidate_from_group(
        self,
        group: dict[str, object],
        *,
        avg_confidence: float,
        sample_limit: int,
    ) -> dict[str, object]:
        condition = {
            "classification_kind": group["kind"],
            "classification_value": group["value"],
            "target_folder_path": group["target_folder_path"],
            "target_tags": list(group["target_tags"] or []),
        }
        for key in ("sender_domain", "folder_path", "message_year"):
            if group.get(key):
                condition[key] = group[key]
        rule_action = {
            "action": "suggest",
            "classification_kind": group["kind"],
            "classification_value": group["value"],
            "target_folder_path": group["target_folder_path"],
            "target_tags": list(group["target_tags"] or []),
        }
        rule_id = self._rule_id_from_condition("always", condition)
        target = target_label(
            kind=str(group["kind"] or ""),
            value=str(group["value"] or ""),
            target_folder_path=str(group["target_folder_path"] or "") or None,
            target_tags=list(group["target_tags"] or []),
        )
        confidence = proposal_confidence(avg_confidence, group["evidence_count"])
        existing = self.load_brain_rule(rule_id)
        backtest = {
            "matched_messages": int(group["evidence_count"] or 0),
            "existing_suggestions": int(group["evidence_count"] or 0),
            "conflicting_suggestions": 0,
            "samples": list(group.get("samples") or [])[:sample_limit],
            "scope": "classification_evidence",
        }
        candidate_id = stable_id("millie_rule_candidate", rule_id)
        return {
            "id": candidate_id,
            "rule_id": rule_id,
            "rule_name": f"Propose {group['kind']}:{group['value']} for {target}",
            "target": target,
            "condition": condition,
            "rule_action": rule_action,
            "confidence": confidence,
            "avg_confidence": round(avg_confidence, 4),
            "evidence_count": int(group["evidence_count"] or 0),
            "classification_ids": list(group.get("classification_ids") or []),
            "samples": list(group.get("samples") or []),
            "backtest": backtest,
            "existing_rule_status": existing.get("status") if existing else "",
            "metadata": {
                "proposal_type": "classification_rule",
                "source": "millie_rule_backtest",
                "sender_domain": group.get("sender_domain") or "",
                "folder_path": group.get("folder_path") or "",
                "message_year": group.get("message_year") or "",
            },
        }

    def backtest_rule_condition(
        self,
        *,
        condition: dict[str, object],
        rule_action: dict[str, object],
        sample_limit: int = 5,
    ) -> dict[str, object]:
        where, params = self._rule_condition_where(condition)
        context_sql = f"AND {' AND '.join(where)}" if where else ""
        match_params = self._classification_match_params(rule_action)
        aggregate = self.connection.execute(
            f"""
            WITH from_addresses AS (
                SELECT
                    message_id,
                    string_agg(
                        CASE
                            WHEN coalesce(display_name, '') <> '' AND coalesce(email_address, '') <> ''
                                THEN display_name || ' <' || email_address || '>'
                            WHEN coalesce(email_address, '') <> '' THEN email_address
                            ELSE coalesce(raw_value, '')
                        END,
                        ', ' ORDER BY ordinal
                    ) AS from_text,
                    min(lower(email_address)) FILTER (
                        WHERE coalesce(email_address, '') <> ''
                    ) AS from_email
                FROM mail_message_addresses
                WHERE role = 'from'
                GROUP BY message_id
            ),
            matches AS (
                SELECT
                    m.id,
                    c.classification_kind,
                    c.classification_value,
                    c.target_folder_path,
                    c.target_tags,
                    coalesce(m.sent_at, m.received_at, v.internal_date) AS message_date,
                    coalesce(v.folder_path, '') AS folder_path,
                    coalesce(fa.from_email, '') AS from_email,
                    coalesce(fa.from_text, '') AS from_text
                FROM millie_message_classifications c
                JOIN mail_messages m ON m.id = c.message_id
                LEFT JOIN millie_v_mailbox_messages v ON v.message_id = m.id
                LEFT JOIN from_addresses fa ON fa.message_id = m.id
                WHERE c.status IN ('proposed', 'approved', 'applied')
                  AND c.classification_kind IN ('folder', 'tag', 'spam', 'trash')
                  {context_sql}
            ),
            scored AS (
                SELECT
                    matches.id,
                    bool_or(
                        matches.classification_kind = %s
                        AND matches.classification_value = %s
                        AND coalesce(matches.target_folder_path, '') = %s
                        AND matches.target_tags = %s
                    ) AS has_matching_suggestion,
                    bool_or(
                        matches.classification_kind = %s
                        AND NOT (
                            matches.classification_kind = %s
                            AND matches.classification_value = %s
                            AND coalesce(matches.target_folder_path, '') = %s
                            AND matches.target_tags = %s
                        )
                    ) AS has_conflicting_suggestion
                FROM matches
                GROUP BY matches.id
            )
            SELECT
                count(*),
                count(*) FILTER (WHERE has_matching_suggestion),
                count(*) FILTER (WHERE has_conflicting_suggestion)
            FROM scored
            """,
            tuple([
                *params,
                *match_params,
                rule_action.get("classification_kind") or "",
                *match_params,
            ]),
        ).fetchone()
        sample_rows = self.connection.execute(
            f"""
            WITH from_addresses AS (
                SELECT
                    message_id,
                    string_agg(
                        CASE
                            WHEN coalesce(display_name, '') <> '' AND coalesce(email_address, '') <> ''
                                THEN display_name || ' <' || email_address || '>'
                            WHEN coalesce(email_address, '') <> '' THEN email_address
                            ELSE coalesce(raw_value, '')
                        END,
                        ', ' ORDER BY ordinal
                    ) AS from_text,
                    min(lower(email_address)) FILTER (
                        WHERE coalesce(email_address, '') <> ''
                    ) AS from_email
                FROM mail_message_addresses
                WHERE role = 'from'
                GROUP BY message_id
            )
            SELECT DISTINCT ON (m.id)
                m.id,
                coalesce(m.subject, '(no subject)') AS subject,
                coalesce(m.sent_at, m.received_at, v.internal_date) AS message_date,
                coalesce(fa.from_text, '') AS from_text,
                coalesce(v.folder_path, '') AS folder_path,
                v.imap_uid
            FROM millie_message_classifications c
            JOIN mail_messages m ON m.id = c.message_id
            LEFT JOIN millie_v_mailbox_messages v ON v.message_id = m.id
            LEFT JOIN from_addresses fa ON fa.message_id = m.id
            WHERE c.status IN ('proposed', 'approved', 'applied')
              AND c.classification_kind IN ('folder', 'tag', 'spam', 'trash')
              {context_sql}
            ORDER BY
                m.id,
                CASE WHEN coalesce(v.folder_path, '') = 'All Mail' THEN 1 ELSE 0 END,
                coalesce(m.received_at, m.sent_at, now()) DESC
            LIMIT %s
            """,
            tuple([*params, max(1, min(sample_limit, 12))]),
        ).fetchall()
        return {
            "matched_messages": int(aggregate[0] or 0) if aggregate else 0,
            "existing_suggestions": int(aggregate[1] or 0) if aggregate else 0,
            "conflicting_suggestions": int(aggregate[2] or 0) if aggregate else 0,
            "samples": [
                {
                    "message_id": row[0],
                    "subject": row[1],
                    "message_date": row[2],
                    "from": row[3] or "",
                    "folder_path": row[4],
                    "uid": int(row[5]) if row[5] is not None else None,
                }
                for row in sample_rows
            ],
        }

    def record_rule_candidate_action(
        self,
        *,
        candidate_id: str,
        action: str,
        identity_id: str | None = None,
    ) -> dict[str, object]:
        action = action.strip().lower()
        if action not in {"seed", "dismiss"}:
            raise ValueError(f"Unsupported rule candidate action: {action}")
        candidate = self._load_rule_candidate(candidate_id)
        if not candidate:
            raise KeyError(f"Rule candidate not found: {candidate_id}")
        status = "proposed" if action == "seed" else "retired"
        rule = self._upsert_rule_proposal(
            rule_id=str(candidate["rule_id"]),
            rule_name=str(candidate["rule_name"]),
            rule_type=str(candidate["condition"].get("classification_kind") or "custom"),
            rule_source="heuristic",
            status=status,
            priority=80,
            condition=dict(candidate["condition"]),
            rule_action=dict(candidate["rule_action"]),
            confidence=float(candidate["confidence"] or 0),
            evidence_count=int(candidate["evidence_count"] or 0),
            identity_id=identity_id,
            metadata={
                "proposal": {
                    **candidate,
                    "action": action,
                }
            },
        )
        self._insert_automation_audit(
            action_type="create_rule" if action == "seed" else "disable_rule",
            identity_id=identity_id,
            rule_id=str(rule["id"]),
            after_json={
                "rule_candidate_action": action,
                "candidate_id": candidate_id,
                "rule": rule,
            },
        )
        return {
            "ok": True,
            "action": action,
            "candidate_id": candidate_id,
            "rule": rule,
        }

    def _load_rule_candidate(self, candidate_id: str) -> dict[str, object]:
        for candidate in self.rule_backtest_candidates(
            limit=100,
            sample_limit=5,
            candidate_limit=5000,
            min_messages=1,
        ):
            if candidate["id"] == candidate_id:
                return candidate
        return {}

    def taxonomy_proposals(
        self,
        *,
        limit: int = 20,
        sample_limit: int = 5,
    ) -> list[dict[str, object]]:
        rows = self.connection.execute(
            """
            WITH from_addresses AS (
                SELECT
                    message_id,
                    min(lower(email_address)) FILTER (
                        WHERE coalesce(email_address, '') <> ''
                    ) AS from_email
                FROM mail_message_addresses
                WHERE role = 'from'
                GROUP BY message_id
            ),
            classified AS (
                SELECT DISTINCT ON (c.id)
                    c.id,
                    c.message_id,
                    c.classification_kind,
                    c.classification_value,
                    c.target_folder_path,
                    c.target_tags,
                    c.confidence,
                    c.status,
                    coalesce(m.sent_at, m.received_at, v.internal_date) AS message_date,
                    coalesce(fa.from_email, '') AS from_email,
                    coalesce(v.folder_path, '') AS folder_path
                FROM millie_message_classifications c
                JOIN mail_messages m ON m.id = c.message_id
                LEFT JOIN millie_v_mailbox_messages v ON v.message_id = c.message_id
                LEFT JOIN from_addresses fa ON fa.message_id = c.message_id
                WHERE c.status IN ('proposed', 'approved', 'applied')
                  AND (
                    c.target_folder_path IS NOT NULL
                    OR cardinality(c.target_tags) > 0
                  )
                ORDER BY
                    c.id,
                    CASE WHEN coalesce(v.folder_path, '') = 'All Mail' THEN 1 ELSE 0 END,
                    c.confidence DESC,
                    c.created_at DESC
            )
            SELECT
                coalesce(target_folder_path, '') AS target_folder_path,
                target_tags,
                classification_kind,
                classification_value,
                count(*) AS evidence_count,
                avg(confidence) AS avg_confidence,
                array_agg(DISTINCT lower(split_part(from_email, '@', 2))) FILTER (
                    WHERE from_email LIKE '%%@%%'
                ) AS sender_domains,
                array_agg(DISTINCT folder_path) FILTER (
                    WHERE coalesce(folder_path, '') <> ''
                ) AS source_folders,
                array_agg(DISTINCT extract(year FROM message_date)::text) FILTER (
                    WHERE message_date IS NOT NULL
                ) AS message_years
            FROM classified
            GROUP BY
                coalesce(target_folder_path, ''),
                target_tags,
                classification_kind,
                classification_value
            ORDER BY evidence_count DESC, avg_confidence DESC NULLS LAST
            LIMIT %s
            """,
            (max(1, min(limit, 100)),),
        ).fetchall()
        proposals: list[dict[str, object]] = []
        for row in rows:
            target_folder_path = str(row[0] or "")
            target_tags = list(row[1] or [])
            target = target_label(
                kind=str(row[2] or ""),
                value=str(row[3] or ""),
                target_folder_path=target_folder_path or None,
                target_tags=target_tags,
            )
            evidence_count = int(row[4] or 0)
            confidence = proposal_confidence(row[5], evidence_count)
            sender_domains = compact_values(list(row[6] or []), limit=8)
            source_folders = compact_values(list(row[7] or []), limit=8)
            message_years = compact_values(list(row[8] or []), limit=8)
            proposal_id = stable_id("millie_taxonomy_proposal", target, row[2], row[3])
            rule_id = stable_id("millie_brain_rule", "taxonomy", target, row[2], row[3])
            existing = self.load_brain_rule(rule_id)
            if existing.get("status") in {"active", "retired"}:
                continue
            condition = {
                "proposal_type": "taxonomy",
                "target": target,
                "classification_kind": row[2],
                "classification_value": row[3],
                "target_folder_path": target_folder_path or None,
                "target_tags": target_tags,
            }
            rule_action = {
                "action": "taxonomy_proposal",
                "target": target,
                "target_folder_path": target_folder_path or None,
                "target_tags": target_tags,
                "review_only": True,
            }
            proposals.append(
                {
                    "id": proposal_id,
                    "rule_id": rule_id,
                    "rule_name": f"Taxonomy proposal: {target}",
                    "target": target,
                    "condition": condition,
                    "rule_action": rule_action,
                    "confidence": confidence,
                    "evidence_count": evidence_count,
                    "sender_domains": sender_domains,
                    "source_folders": source_folders,
                    "message_years": message_years,
                    "sample_limit": max(1, min(sample_limit, 12)),
                    "existing_rule_status": existing.get("status") if existing else "",
                    "llm_context": {
                        "target": target,
                        "evidence_count": evidence_count,
                        "sender_domains": sender_domains,
                        "source_folders": source_folders,
                        "message_years": message_years,
                        "instruction": (
                            "Review this aggregate-only context and suggest whether the "
                            "folder/tag taxonomy should be kept, renamed, merged, or split."
                        ),
                    },
                }
            )
        return proposals

    def record_taxonomy_proposal_action(
        self,
        *,
        proposal_id: str,
        action: str,
        identity_id: str | None = None,
    ) -> dict[str, object]:
        action = action.strip().lower()
        if action not in {"seed", "dismiss"}:
            raise ValueError(f"Unsupported taxonomy proposal action: {action}")
        proposal = self._load_taxonomy_proposal(proposal_id)
        if not proposal:
            raise KeyError(f"Taxonomy proposal not found: {proposal_id}")
        status = "proposed" if action == "seed" else "retired"
        rule = self._upsert_rule_proposal(
            rule_id=str(proposal["rule_id"]),
            rule_name=str(proposal["rule_name"]),
            rule_type="custom",
            rule_source="heuristic",
            status=status,
            priority=60,
            condition=dict(proposal["condition"]),
            rule_action=dict(proposal["rule_action"]),
            confidence=float(proposal["confidence"] or 0),
            evidence_count=int(proposal["evidence_count"] or 0),
            identity_id=identity_id,
            metadata={
                "proposal": {
                    **proposal,
                    "action": action,
                }
            },
        )
        self._insert_automation_audit(
            action_type="create_rule" if action == "seed" else "disable_rule",
            identity_id=identity_id,
            rule_id=str(rule["id"]),
            after_json={
                "taxonomy_proposal_action": action,
                "proposal_id": proposal_id,
                "rule": rule,
            },
        )
        return {
            "ok": True,
            "action": action,
            "proposal_id": proposal_id,
            "rule": rule,
        }

    def _load_taxonomy_proposal(self, proposal_id: str) -> dict[str, object]:
        for proposal in self.taxonomy_proposals(limit=100, sample_limit=5):
            if proposal["id"] == proposal_id:
                return proposal
        return {}

    def _upsert_rule_proposal(
        self,
        *,
        rule_id: str,
        rule_name: str,
        rule_type: str,
        rule_source: str,
        status: str,
        priority: int,
        condition: dict[str, object],
        rule_action: dict[str, object],
        confidence: float,
        evidence_count: int,
        identity_id: str | None,
        metadata: dict[str, object],
    ) -> dict[str, object]:
        self.connection.execute(
            """
            INSERT INTO millie_brain_rules (
                id, rule_name, rule_type, rule_source, status, automation_level,
                priority, condition_json, action_json, confidence, evidence_count,
                created_by_identity_id, updated_at, metadata_json
            )
            VALUES (
                %s, %s, %s, %s, %s, 'review',
                %s, %s, %s, %s, %s, %s, now(), %s
            )
            ON CONFLICT(id) DO UPDATE SET
                rule_name = excluded.rule_name,
                status = CASE
                    WHEN millie_brain_rules.status = 'active' THEN millie_brain_rules.status
                    ELSE excluded.status
                END,
                confidence = greatest(millie_brain_rules.confidence, excluded.confidence),
                evidence_count = greatest(millie_brain_rules.evidence_count, excluded.evidence_count),
                priority = greatest(millie_brain_rules.priority, excluded.priority),
                condition_json = excluded.condition_json,
                action_json = excluded.action_json,
                updated_at = now(),
                metadata_json = millie_brain_rules.metadata_json || excluded.metadata_json
            """,
            (
                rule_id,
                rule_name,
                self._safe_rule_type(rule_type),
                rule_source,
                status,
                int(priority),
                Jsonb(condition),
                Jsonb(rule_action),
                max(0.0, min(float(confidence or 0), 1.0)),
                max(0, int(evidence_count or 0)),
                identity_id,
                Jsonb(_sanitize_metadata(metadata)),
            ),
        )
        return self.load_brain_rule(rule_id)

    def _rule_id_from_condition(self, action: str, condition: dict[str, object]) -> str:
        return stable_id(
            "millie_brain_rule",
            action,
            condition.get("classification_kind") or "",
            condition.get("classification_value") or "",
            condition.get("target_folder_path") or "",
            ",".join(str(tag) for tag in condition.get("target_tags") or []),
            condition.get("sender_domain") or "",
            condition.get("folder_path") or "",
            condition.get("message_year") or "",
        )

    def _rule_condition_where(self, condition: dict[str, object]) -> tuple[list[str], list[object]]:
        where: list[str] = []
        params: list[object] = []
        sender_domain = str(condition.get("sender_domain") or "").strip().lower()
        if sender_domain:
            where.append(
                "(lower(split_part(coalesce(fa.from_email, ''), '@', 2)) = %s "
                "OR lower(coalesce(fa.from_text, '')) LIKE %s)"
            )
            params.extend([sender_domain, f"%@{sender_domain}%"])
        folder_path = str(condition.get("folder_path") or "").strip()
        if folder_path:
            where.append("coalesce(v.folder_path, '') = %s")
            params.append(folder_path)
        message_year = str(condition.get("message_year") or "").strip()
        if message_year:
            where.append("extract(year FROM coalesce(m.sent_at, m.received_at, v.internal_date))::text = %s")
            params.append(message_year)
        return where, params

    def _classification_match_params(self, rule_action: dict[str, object]) -> list[object]:
        return [
            rule_action.get("classification_kind") or "",
            rule_action.get("classification_value") or "",
            str(rule_action.get("target_folder_path") or ""),
            list(rule_action.get("target_tags") or []),
        ]

    def _safe_rule_type(self, rule_type: str) -> str:
        normalized = str(rule_type or "custom").strip().lower()
        if normalized in {
            "folder",
            "tag",
            "spam",
            "trash",
            "unsubscribe",
            "retention",
            "priority",
            "custom",
        }:
            return normalized
        return "custom"

    def internal_apply_status(self, *, mailbox_id: str, limit: int = 100) -> dict[str, object]:
        approved = self.connection.execute(
            """
            SELECT count(*)
            FROM millie_message_classifications c
            WHERE c.status = 'approved'
              AND c.target_folder_path IS NOT NULL
              AND NOT EXISTS (
                SELECT 1
                FROM millie_automation_audit_log applied
                WHERE applied.classification_id = c.id
                  AND applied.action_type = 'apply_internal_tag'
                  AND applied.status = 'applied'
              )
            """
        ).fetchone()
        retention = self.connection.execute(
            """
            SELECT count(*)
            FROM millie_retention_policies p
            JOIN millie_mailbox_folders mf
              ON p.target_kind = 'folder'
             AND p.target_value = mf.folder_path
            JOIN millie_mailbox_messages mm ON mm.folder_id = mf.id
            JOIN LATERAL (
                SELECT e.id, e.new_value_json
                FROM millie_user_feedback_events e
                WHERE e.message_id = mm.message_id
                  AND e.feedback_type = 'retention_override'
                  AND e.metadata_json->>'retention_policy_id' = p.id
                  AND e.metadata_json->>'mailbox_message_id' = mm.id
                ORDER BY e.created_at DESC
                LIMIT 1
            ) latest_feedback ON TRUE
            WHERE mm.mailbox_id = %s
              AND latest_feedback.new_value_json->>'action' = 'acknowledge'
              AND mm.is_expunged = FALSE
              AND p.status = 'active'
              AND p.hold_duration IS NOT NULL
              AND p.action IN ('no_action', 'hide_from_default_views')
              AND mm.copied_at + p.hold_duration <= now()
              AND NOT EXISTS (
                SELECT 1
                FROM millie_automation_audit_log applied
                WHERE applied.message_id = mm.message_id
                  AND applied.retention_policy_id = p.id
                  AND applied.action_type = 'retention_apply'
                  AND applied.status = 'applied'
                  AND applied.after_json->>'mailbox_message_id' = mm.id
              )
            """,
            (mailbox_id,),
        ).fetchone()
        return {
            "approved_suggestions_pending": int(approved[0] or 0),
            "retention_pending": int(retention[0] or 0),
            "limit": max(1, min(limit, 500)),
        }

    def ensure_sync_health_schema(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS millie_sync_health (
                id TEXT PRIMARY KEY,
                account_key TEXT NOT NULL,
                account_email TEXT,
                account_display_name TEXT,
                account_type TEXT,
                auth_method TEXT,
                host TEXT,
                source_id TEXT,
                source_type TEXT,
                source_uri TEXT,
                folder_path TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'unknown' CHECK (
                    status IN ('unknown', 'running', 'ok', 'failed', 'skipped')
                ),
                started_at TIMESTAMPTZ,
                completed_at TIMESTAMPTZ,
                last_success_at TIMESTAMPTZ,
                last_error_at TIMESTAMPTZ,
                last_error_message TEXT,
                remote_uid_count INTEGER NOT NULL DEFAULT 0 CHECK (remote_uid_count >= 0),
                highest_uid TEXT,
                uidvalidity TEXT,
                min_uid TEXT,
                scanned INTEGER NOT NULL DEFAULT 0 CHECK (scanned >= 0),
                imported INTEGER NOT NULL DEFAULT 0 CHECK (imported >= 0),
                skipped_existing INTEGER NOT NULL DEFAULT 0 CHECK (skipped_existing >= 0),
                deduped_existing INTEGER NOT NULL DEFAULT 0 CHECK (deduped_existing >= 0),
                metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (account_key, folder_path)
            )
            """
        )
        self.connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_millie_sync_health_account
                ON millie_sync_health(account_key, status, updated_at)
            """
        )
        self.connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_millie_sync_health_source
                ON millie_sync_health(source_id)
            """
        )
        self.connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_millie_sync_health_status
                ON millie_sync_health(status, last_success_at, last_error_at)
            """
        )

    def record_sync_folder_start(
        self,
        *,
        account: dict[str, Any],
        folder_path: str,
        source_id: str,
        source_type: str,
        source_uri: str,
        metadata: dict[str, object] | None = None,
    ) -> str:
        self.ensure_sync_health_schema()
        account_key = self._sync_account_key(account)
        health_id = stable_id("millie_sync_health", account_key, folder_path)
        self.connection.execute(
            """
            INSERT INTO millie_sync_health (
                id, account_key, account_email, account_display_name, account_type,
                auth_method, host, source_id, source_type, source_uri, folder_path,
                status, started_at, completed_at, last_error_message, metadata_json
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                'running', now(), NULL, NULL, %s
            )
            ON CONFLICT(account_key, folder_path) DO UPDATE SET
                account_email = excluded.account_email,
                account_display_name = excluded.account_display_name,
                account_type = excluded.account_type,
                auth_method = excluded.auth_method,
                host = excluded.host,
                source_id = excluded.source_id,
                source_type = excluded.source_type,
                source_uri = excluded.source_uri,
                status = 'running',
                started_at = now(),
                completed_at = NULL,
                last_error_message = NULL,
                metadata_json = millie_sync_health.metadata_json || excluded.metadata_json,
                updated_at = now()
            """,
            (
                health_id,
                account_key,
                account.get("email_address") or "",
                account.get("display_name") or "",
                account.get("account_type") or "",
                account.get("auth_method") or "",
                account.get("host") or "",
                source_id,
                source_type,
                source_uri,
                folder_path,
                Jsonb(metadata or {}),
            ),
        )
        return health_id

    def record_sync_folder_success(
        self,
        *,
        account: dict[str, Any],
        folder_path: str,
        source_id: str,
        source_type: str,
        source_uri: str,
        remote_uid_count: int,
        highest_uid: str,
        uidvalidity: str,
        min_uid: str | int | None,
        scanned: int,
        imported: int,
        skipped_existing: int,
        deduped_existing: int,
        metadata: dict[str, object] | None = None,
    ) -> None:
        self.ensure_sync_health_schema()
        account_key = self._sync_account_key(account)
        health_id = stable_id("millie_sync_health", account_key, folder_path)
        self.connection.execute(
            """
            INSERT INTO millie_sync_health (
                id, account_key, account_email, account_display_name, account_type,
                auth_method, host, source_id, source_type, source_uri, folder_path,
                status, started_at, metadata_json
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                'running', now(), %s
            )
            ON CONFLICT(account_key, folder_path) DO NOTHING
            """,
            (
                health_id,
                account_key,
                account.get("email_address") or "",
                account.get("display_name") or "",
                account.get("account_type") or "",
                account.get("auth_method") or "",
                account.get("host") or "",
                source_id,
                source_type,
                source_uri,
                folder_path,
                Jsonb(metadata or {}),
            ),
        )
        self.connection.execute(
            """
            UPDATE millie_sync_health
            SET status = 'ok',
                completed_at = now(),
                last_success_at = now(),
                last_error_message = NULL,
                remote_uid_count = %s,
                highest_uid = %s,
                uidvalidity = %s,
                min_uid = %s,
                scanned = %s,
                imported = %s,
                skipped_existing = %s,
                deduped_existing = %s,
                metadata_json = metadata_json || %s,
                updated_at = now()
            WHERE account_key = %s
              AND folder_path = %s
            """,
            (
                max(remote_uid_count, 0),
                highest_uid,
                uidvalidity,
                str(min_uid) if min_uid else "",
                max(scanned, 0),
                max(imported, 0),
                max(skipped_existing, 0),
                max(deduped_existing, 0),
                Jsonb(metadata or {}),
                account_key,
                folder_path,
            ),
        )

    def record_sync_folder_failure(
        self,
        *,
        account: dict[str, Any],
        folder_path: str,
        source_id: str,
        source_type: str,
        source_uri: str,
        error_message: str,
        metadata: dict[str, object] | None = None,
    ) -> None:
        self.record_sync_folder_start(
            account=account,
            folder_path=folder_path,
            source_id=source_id,
            source_type=source_type,
            source_uri=source_uri,
            metadata=metadata,
        )
        account_key = self._sync_account_key(account)
        self.connection.execute(
            """
            UPDATE millie_sync_health
            SET status = 'failed',
                completed_at = now(),
                last_error_at = now(),
                last_error_message = %s,
                metadata_json = metadata_json || %s,
                updated_at = now()
            WHERE account_key = %s
              AND folder_path = %s
            """,
            (
                error_message[:2000],
                Jsonb(metadata or {}),
                account_key,
                folder_path,
            ),
        )

    def _sync_account_key(self, account: dict[str, Any]) -> str:
        for key in ["email_address", "username", "id", "display_name"]:
            value = str(account.get(key) or "").strip().lower()
            if value:
                return value
        return "unknown"

    def review_workbench_groups(
        self,
        *,
        group_limit: int = 25,
        sample_limit: int = 5,
        candidate_limit: int = 1000,
    ) -> list[dict[str, object]]:
        rows = self.connection.execute(
            """
            WITH from_addresses AS (
                SELECT
                    message_id,
                    string_agg(
                        CASE
                            WHEN coalesce(display_name, '') <> '' AND coalesce(email_address, '') <> ''
                                THEN display_name || ' <' || email_address || '>'
                            WHEN coalesce(email_address, '') <> '' THEN email_address
                            ELSE coalesce(raw_value, '')
                        END,
                        ', ' ORDER BY ordinal
                    ) AS from_text,
                    min(lower(email_address)) FILTER (
                        WHERE coalesce(email_address, '') <> ''
                    ) AS from_email
                FROM mail_message_addresses
                WHERE role = 'from'
                GROUP BY message_id
            ),
            candidates AS (
                SELECT DISTINCT ON (c.id)
                    c.id,
                    c.message_id,
                    c.classification_kind,
                    c.classification_value,
                    c.target_folder_path,
                    c.target_tags,
                    c.confidence,
                    c.reason_text,
                    c.created_at,
                    coalesce(m.subject, '(no subject)') AS subject,
                    coalesce(m.sent_at, m.received_at, v.internal_date) AS message_date,
                    coalesce(fa.from_text, '') AS from_text,
                    coalesce(fa.from_email, '') AS from_email,
                    coalesce(v.folder_path, '') AS folder_path,
                    v.imap_uid,
                    coalesce(ms.display_name, ms.source_uri, '') AS source_name,
                    ms.source_type
                FROM millie_message_classifications c
                JOIN mail_messages m ON m.id = c.message_id
                LEFT JOIN millie_v_mailbox_messages v ON v.message_id = c.message_id
                LEFT JOIN from_addresses fa ON fa.message_id = c.message_id
                LEFT JOIN mail_sources ms ON ms.id = m.source_id
                WHERE c.status = 'proposed'
                ORDER BY
                    c.id,
                    CASE WHEN coalesce(v.folder_path, '') = 'All Mail' THEN 1 ELSE 0 END,
                    c.confidence DESC,
                    c.created_at DESC
                LIMIT %s
            )
            SELECT *
            FROM candidates
            ORDER BY confidence DESC, created_at DESC
            """,
            (max(1, min(candidate_limit, 5000)),),
        ).fetchall()
        groups: dict[tuple[str, str, str, str], dict[str, object]] = {}
        sample_limit = max(1, min(sample_limit, 12))
        for row in rows:
            message_date = row[10]
            year = str(message_date.year) if message_date else "unknown"
            target = row[4] or ",".join(row[5] or []) or f"{row[2]}:{row[3]}"
            sender_domain = self._sender_domain(str(row[12] or ""), str(row[11] or ""))
            folder_path = row[13] or "(unknown)"
            key = (str(target), sender_domain, str(folder_path), year)
            group = groups.setdefault(
                key,
                {
                    "group_key": stable_id("sort_workbench_group", *key),
                    "target": target,
                    "sender_domain": sender_domain,
                    "current_folder": folder_path,
                    "message_year": year,
                    "classification_ids": [],
                    "count": 0,
                    "confidence_total": 0.0,
                    "samples": [],
                },
            )
            classification_ids = group["classification_ids"]
            if isinstance(classification_ids, list):
                classification_ids.append(row[0])
            group["count"] = int(group["count"]) + 1
            group["confidence_total"] = float(group["confidence_total"]) + float(row[6] or 0)
            samples = group["samples"]
            if isinstance(samples, list) and len(samples) < sample_limit:
                samples.append(
                    {
                        "classification_id": row[0],
                        "message_id": row[1],
                        "kind": row[2],
                        "value": row[3],
                        "target_folder_path": row[4],
                        "target_tags": list(row[5] or []),
                        "confidence": float(row[6] or 0),
                        "reason": row[7] or "",
                        "subject": row[9] or "(no subject)",
                        "message_date": row[10],
                        "from": row[11] or "",
                        "folder_path": row[13],
                        "uid": int(row[14]) if row[14] is not None else None,
                        "source": row[15] or "",
                        "source_type": row[16] or "",
                    }
                )
        ordered = sorted(
            groups.values(),
            key=lambda item: (
                -int(item["count"]),
                -float(item["confidence_total"]) / max(int(item["count"]), 1),
                str(item["target"]),
            ),
        )[: max(1, min(group_limit, 100))]
        for group in ordered:
            count = max(int(group["count"]), 1)
            group["avg_confidence"] = round(float(group.pop("confidence_total")) / count, 4)
        return ordered

    def record_classification_batch_feedback(
        self,
        *,
        classification_ids: list[str],
        action: str,
        identity_id: str | None = None,
        feedback_source: str = "webmail",
    ) -> dict[str, object]:
        normalized = []
        seen = set()
        for classification_id in classification_ids:
            value = str(classification_id or "").strip()
            if value and value not in seen:
                normalized.append(value)
                seen.add(value)
        if not normalized:
            raise ValueError("At least one classification_id is required")
        if len(normalized) > 250:
            raise ValueError("Batch review is limited to 250 classifications")
        results = [
            self.record_classification_feedback(
                classification_id=classification_id,
                action=action,
                identity_id=identity_id,
                feedback_source=feedback_source,
            )
            for classification_id in normalized
        ]
        return {
            "action": action,
            "requested": len(normalized),
            "updated": len(results),
            "results": results,
        }

    def _sender_domain(self, from_email: str, from_text: str) -> str:
        value = from_email.strip().lower()
        if "@" not in value:
            for token in from_text.replace("<", " ").replace(">", " ").replace(",", " ").split():
                if "@" in token:
                    value = token.strip().lower()
                    break
        if "@" not in value:
            return "(unknown)"
        return value.rsplit("@", 1)[-1].strip(" .>;") or "(unknown)"

    def operations_status(
        self,
        *,
        mailbox_id: str,
        accounts: list[dict[str, Any]] | None = None,
        run_limit: int = 10,
    ) -> dict[str, object]:
        self.ensure_sync_health_schema()
        stale_after_hours = self._sync_stale_after_hours()
        health_rows = self.connection.execute(
            """
            SELECT
                id,
                account_key,
                account_email,
                account_display_name,
                account_type,
                auth_method,
                host,
                source_id,
                source_type,
                source_uri,
                folder_path,
                status,
                started_at,
                completed_at,
                last_success_at,
                last_error_at,
                last_error_message,
                remote_uid_count,
                highest_uid,
                uidvalidity,
                min_uid,
                scanned,
                imported,
                skipped_existing,
                deduped_existing,
                metadata_json,
                updated_at
            FROM millie_sync_health
            ORDER BY
                CASE status WHEN 'failed' THEN 0 WHEN 'running' THEN 1 ELSE 2 END,
                coalesce(last_error_at, last_success_at, updated_at) DESC,
                account_key,
                folder_path
            """
        ).fetchall()
        sync_health = [
            self._sync_health_dict(row, stale_after_hours=stale_after_hours)
            for row in health_rows
        ]
        health_by_source = {
            str(item["source_id"]): item
            for item in sync_health
            if item.get("source_id")
        }
        health_by_account: dict[str, list[dict[str, object]]] = {}
        for item in sync_health:
            health_by_account.setdefault(str(item.get("account_key") or ""), []).append(item)
        source_rows = self.connection.execute(
            """
            SELECT
                s.id,
                s.source_type,
                coalesce(s.display_name, ''),
                s.source_uri,
                coalesce(s.auth_mode, ''),
                s.is_active,
                s.created_at,
                s.updated_at,
                (SELECT count(*) FROM mail_folders f WHERE f.source_id = s.id) AS folder_count,
                (SELECT count(*) FROM mail_messages m WHERE m.source_id = s.id) AS message_count,
                (
                    SELECT max(coalesce(m.received_at, m.sent_at, m.created_at))
                    FROM mail_messages m
                    WHERE m.source_id = s.id
                ) AS newest_message_at,
                (
                    SELECT max(c.updated_at)
                    FROM mail_source_cursors c
                    WHERE c.source_id = s.id
                ) AS last_cursor_at
            FROM mail_sources s
            ORDER BY
                CASE s.source_type WHEN 'imap' THEN 0 WHEN 'exchange_imap_oauth' THEN 1 ELSE 2 END,
                lower(coalesce(nullif(s.display_name, ''), s.source_uri)),
                s.source_uri
            """
        ).fetchall()
        source_ids = [str(row[0]) for row in source_rows]
        cursors_by_source: dict[str, list[dict[str, object]]] = {source_id: [] for source_id in source_ids}
        if source_ids:
            cursor_rows = self.connection.execute(
                f"""
                SELECT source_id, cursor_key, cursor_value, updated_at
                FROM (
                    SELECT
                        source_id,
                        cursor_key,
                        cursor_value,
                        updated_at,
                        row_number() OVER (
                            PARTITION BY source_id
                            ORDER BY updated_at DESC, cursor_key
                        ) AS row_number
                    FROM mail_source_cursors
                    WHERE source_id IN ({placeholders(source_ids)})
                ) ranked
                WHERE row_number <= 8
                ORDER BY source_id, updated_at DESC, cursor_key
                """,
                tuple(source_ids),
            ).fetchall()
            for row in cursor_rows:
                source_id = str(row[0])
                cursors_by_source.setdefault(source_id, []).append(
                    {
                        "key": row[1],
                        "value": str(row[2] or "")[:160],
                        "updated_at": row[3],
                    }
                )

        sources: list[dict[str, object]] = []
        for row in source_rows:
            source_id = str(row[0])
            source = {
                "id": source_id,
                "source_type": row[1],
                "display_name": row[2],
                "source_uri": row[3],
                "auth_mode": row[4],
                "is_active": bool(row[5]),
                "created_at": row[6],
                "updated_at": row[7],
                "folder_count": int(row[8] or 0),
                "message_count": int(row[9] or 0),
                "newest_message_at": row[10],
                "last_cursor_at": row[11],
                "cursors": cursors_by_source.get(source_id, []),
            }
            source["sync_health"] = health_by_source.get(source_id) or self._unknown_sync_health_for_source(source)
            source["_match_text"] = " ".join(
                str(value or "").lower()
                for value in [source["display_name"], source["source_uri"]]
            )
            sources.append(source)

        configured_accounts = self._operations_accounts(
            accounts or [],
            sources,
            health_by_account=health_by_account,
        )
        matched_source_ids = {
            str(source["id"])
            for account in configured_accounts
            for source in account.get("sources", [])
            if isinstance(source, dict)
        }
        exposed_sources = []
        for source in sources:
            cleaned = dict(source)
            cleaned.pop("_match_text", None)
            exposed_sources.append(cleaned)

        classification_counts = {
            str(row[0]): int(row[1] or 0)
            for row in self.connection.execute(
                """
                SELECT status, count(*)
                FROM millie_message_classifications
                GROUP BY status
                ORDER BY status
                """
            ).fetchall()
        }
        unsubscribe_counts = {
            str(row[0]): int(row[1] or 0)
            for row in self.connection.execute(
                """
                SELECT status, count(*)
                FROM millie_unsubscribe_candidates
                GROUP BY status
                ORDER BY status
                """
            ).fetchall()
        }
        retention_counts = {
            str(row[0]): int(row[1] or 0)
            for row in self.connection.execute(
                """
                SELECT status, count(*)
                FROM millie_retention_policies
                GROUP BY status
                ORDER BY status
                """
            ).fetchall()
        }
        mailbox_row = self.connection.execute(
            """
            SELECT count(*), count(DISTINCT folder_id)
            FROM millie_mailbox_messages
            WHERE mailbox_id = %s
              AND is_expunged = FALSE
            """,
            (mailbox_id,),
        ).fetchone()
        run_rows = self.connection.execute(
            """
            SELECT
                id,
                run_type,
                automation_level,
                status,
                trigger_source,
                started_at,
                completed_at,
                messages_scanned,
                suggestions_created,
                actions_applied,
                error_message,
                metadata_json,
                created_at
            FROM millie_automation_runs
            ORDER BY coalesce(started_at, created_at) DESC, created_at DESC
            LIMIT %s
            """,
            (max(1, min(run_limit, 50)),),
        ).fetchall()
        runs = [
            {
                "id": row[0],
                "run_type": row[1],
                "automation_level": row[2],
                "status": row[3],
                "trigger_source": row[4],
                "started_at": row[5],
                "completed_at": row[6],
                "messages_scanned": int(row[7] or 0),
                "suggestions_created": int(row[8] or 0),
                "actions_applied": int(row[9] or 0),
                "error_message": row[10] or "",
                "metadata": row[11] or {},
                "created_at": row[12],
            }
            for row in run_rows
        ]
        total_messages = sum(int(source["message_count"] or 0) for source in exposed_sources)
        total_folders = sum(int(source["folder_count"] or 0) for source in exposed_sources)
        health_counts: dict[str, int] = {}
        for source in exposed_sources:
            health = source.get("sync_health")
            if not isinstance(health, dict):
                continue
            state = str(health.get("health_state") or "unknown")
            health_counts[state] = health_counts.get(state, 0) + 1
        return {
            "summary": {
                "source_count": len(exposed_sources),
                "live_source_count": sum(
                    1 for source in exposed_sources if source["source_type"] in {"imap", "exchange_imap_oauth"}
                ),
                "pst_source_count": sum(1 for source in exposed_sources if source["source_type"] == "pst"),
                "message_count": total_messages,
                "folder_count": total_folders,
                "mailbox_message_count": int(mailbox_row[0] or 0),
                "mailbox_folder_count": int(mailbox_row[1] or 0),
            },
            "sync_health": {
                "stale_after_hours": stale_after_hours,
                "counts": health_counts,
                "recent": sync_health[:50],
            },
            "accounts": configured_accounts,
            "sources": exposed_sources,
            "unmatched_sources": [
                source for source in exposed_sources if str(source["id"]) not in matched_source_ids
            ],
            "queues": {
                "classifications": classification_counts,
                "unsubscribes": unsubscribe_counts,
                "retention_policies": retention_counts,
                "internal_apply": self.internal_apply_status(mailbox_id=mailbox_id, limit=100),
            },
            "automation_runs": runs,
        }

    def _operations_accounts(
        self,
        accounts: list[dict[str, Any]],
        sources: list[dict[str, object]],
        *,
        health_by_account: dict[str, list[dict[str, object]]] | None = None,
    ) -> list[dict[str, object]]:
        configured_accounts: list[dict[str, object]] = []
        health_by_account = health_by_account or {}
        for account in accounts:
            keys = [
                str(account.get("email_address") or "").strip().lower(),
                str(account.get("username") or "").strip().lower(),
                str(account.get("display_name") or "").strip().lower(),
            ]
            keys = [key for key in keys if key]
            matched_sources: list[dict[str, object]] = []
            if keys:
                for source in sources:
                    match_text = str(source.get("_match_text") or "")
                    if not any(key in match_text for key in keys):
                        continue
                    cleaned = dict(source)
                    cleaned.pop("_match_text", None)
                    matched_sources.append(cleaned)
            account_key = self._sync_account_key(account)
            account_health = list(health_by_account.get(account_key, []))
            matched_source_ids = {str(source.get("id") or "") for source in matched_sources}
            if matched_source_ids:
                account_health.extend(
                    source.get("sync_health")
                    for source in matched_sources
                    if isinstance(source.get("sync_health"), dict)
                    and str(source.get("id") or "") in matched_source_ids
                )
            account_health = [
                health for health in account_health if isinstance(health, dict)
            ]
            configured_accounts.append(
                {
                    "id": account.get("id") or "",
                    "account_type": account.get("account_type") or "",
                    "display_name": account.get("display_name") or "",
                    "email_address": account.get("email_address") or "",
                    "host": account.get("host") or "",
                    "port": account.get("port") or "",
                    "username": account.get("username") or "",
                    "security": account.get("security") or "",
                    "auth_method": account.get("auth_method") or "",
                    "enabled": bool(account.get("enabled")),
                    "credential_status": "configured" if account.get("password") else "missing",
                    "health_state": self._aggregate_health_state(account_health),
                    "sync_health": account_health[:20],
                    "source_count": len(matched_sources),
                    "sources": matched_sources,
                }
            )
        return configured_accounts

    def _sync_health_dict(self, row: object, *, stale_after_hours: int) -> dict[str, object]:
        status = str(row[11] or "unknown")
        last_success_at = row[14]
        health_state = status
        if status == "ok":
            health_state = "ok"
            if last_success_at:
                now = datetime.now(timezone.utc)
                success_at = last_success_at
                if success_at.tzinfo is None:
                    success_at = success_at.replace(tzinfo=timezone.utc)
                if now - success_at > timedelta(hours=stale_after_hours):
                    health_state = "stale"
            else:
                health_state = "unknown"
        elif status not in {"running", "failed", "skipped"}:
            health_state = "unknown"
        return {
            "id": row[0],
            "account_key": row[1],
            "account_email": row[2] or "",
            "account_display_name": row[3] or "",
            "account_type": row[4] or "",
            "auth_method": row[5] or "",
            "host": row[6] or "",
            "source_id": row[7] or "",
            "source_type": row[8] or "",
            "source_uri": row[9] or "",
            "folder_path": row[10] or "",
            "status": status,
            "health_state": health_state,
            "started_at": row[12],
            "completed_at": row[13],
            "last_success_at": row[14],
            "last_error_at": row[15],
            "last_error_message": row[16] or "",
            "remote_uid_count": int(row[17] or 0),
            "highest_uid": row[18] or "",
            "uidvalidity": row[19] or "",
            "min_uid": row[20] or "",
            "scanned": int(row[21] or 0),
            "imported": int(row[22] or 0),
            "skipped_existing": int(row[23] or 0),
            "deduped_existing": int(row[24] or 0),
            "metadata": row[25] or {},
            "updated_at": row[26],
        }

    def _unknown_sync_health_for_source(self, source: dict[str, object]) -> dict[str, object] | None:
        if source.get("source_type") not in {"imap", "exchange_imap_oauth"}:
            return None
        return {
            "source_id": source.get("id") or "",
            "source_type": source.get("source_type") or "",
            "source_uri": source.get("source_uri") or "",
            "folder_path": self._folder_from_source_uri(str(source.get("source_uri") or "")),
            "status": "unknown",
            "health_state": "unknown",
            "last_error_message": "",
        }

    def _folder_from_source_uri(self, source_uri: str) -> str:
        if "/" not in source_uri:
            return ""
        path = urllib.parse.urlparse(source_uri).path.lstrip("/")
        return urllib.parse.unquote(path)

    def _sync_stale_after_hours(self) -> int:
        try:
            value = int(str(self.settings.get("sync_stale_after_hours") or "24"))
        except ValueError:
            return 24
        return max(1, min(value, 24 * 30))

    def _aggregate_health_state(self, health_items: list[dict[str, object]]) -> str:
        if not health_items:
            return "unknown"
        states = {str(item.get("health_state") or "unknown") for item in health_items}
        for state in ["failed", "running", "stale", "unknown", "skipped", "ok"]:
            if state in states:
                return state
        return "unknown"

    def record_unsubscribe_feedback(
        self,
        *,
        candidate_id: str,
        action: str,
        identity_id: str | None = None,
        feedback_source: str = "webmail",
    ) -> dict[str, object]:
        if action not in {"approve", "reject"}:
            raise ValueError(f"Unsupported unsubscribe action: {action}")
        row = self.connection.execute(
            """
            SELECT id, message_id, status, candidate_type, unsubscribe_url, unsubscribe_mailto
            FROM millie_unsubscribe_candidates
            WHERE id = %s
            """,
            (candidate_id,),
        ).fetchone()
        if not row:
            raise KeyError(f"Unsubscribe candidate not found: {candidate_id}")
        status = "approved" if action == "approve" else "ignored"
        self.connection.execute(
            """
            UPDATE millie_unsubscribe_candidates
            SET status = %s,
                approved_by_identity_id = CASE WHEN %s = 'approved' THEN %s ELSE approved_by_identity_id END,
                approved_at = CASE WHEN %s = 'approved' THEN now() ELSE approved_at END,
                metadata_json = metadata_json || %s
            WHERE id = %s
            """,
            (
                status,
                status,
                identity_id,
                status,
                Jsonb({"review_action": action}),
                candidate_id,
            ),
        )
        feedback_type = "unsubscribe_approve" if action == "approve" else "unsubscribe_reject"
        self.connection.execute(
            """
            INSERT INTO millie_user_feedback_events (
                id, identity_id, message_id, feedback_type, feedback_source,
                previous_value_json, new_value_json, metadata_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                str(uuid.uuid4()),
                identity_id,
                row[1],
                feedback_type,
                feedback_source,
                Jsonb({"status": row[2]}),
                Jsonb({"status": status, "action": action}),
                Jsonb({"unsubscribe_candidate_id": candidate_id}),
            ),
        )
        self._insert_automation_audit(
            action_type=feedback_type,
            identity_id=identity_id,
            message_id=str(row[1]),
            unsubscribe_candidate_id=candidate_id,
            after_json={
                "status": status,
                "action": action,
                "candidate_type": row[3],
                "unsubscribe_url": row[4],
                "unsubscribe_mailto": row[5],
            },
        )
        return {
            "id": candidate_id,
            "message_id": row[1],
            "status": status,
            "action": action,
        }

    def record_retention_feedback(
        self,
        *,
        mailbox_id: str,
        policy_id: str,
        mailbox_message_id: str,
        action: str,
        identity_id: str | None = None,
        feedback_source: str = "webmail",
    ) -> dict[str, object]:
        if action not in {"acknowledge", "defer"}:
            raise ValueError(f"Unsupported retention action: {action}")
        row = self.connection.execute(
            """
            SELECT
                p.id,
                p.policy_name,
                p.status,
                p.target_kind,
                p.target_value,
                p.hold_duration,
                p.action,
                p.requires_review,
                mm.id,
                mm.message_id,
                mf.folder_path,
                mm.imap_uid,
                mm.copied_at,
                coalesce(m.subject, '(no subject)') AS subject
            FROM millie_retention_policies p
            JOIN millie_mailbox_messages mm ON mm.id = %s
            JOIN millie_mailbox_folders mf ON mf.id = mm.folder_id
            JOIN mail_messages m ON m.id = mm.message_id
            WHERE p.id = %s
              AND mm.mailbox_id = %s
              AND mm.is_expunged = FALSE
            LIMIT 1
            """,
            (mailbox_message_id, policy_id, mailbox_id),
        ).fetchone()
        if not row:
            raise KeyError("Retention review item not found")
        policy = RetentionPolicy(
            id=str(row[0]),
            name=str(row[1]),
            status=str(row[2]),
            target_kind=str(row[3]),
            target_value=str(row[4]),
            hold_duration=row[5],
            action=str(row[6]),
            requires_review=bool(row[7]),
        )
        message = HeldMessage(
            mailbox_message_id=str(row[8]),
            message_id=str(row[9]),
            folder_path=str(row[10]),
            imap_uid=int(row[11]),
            copied_at=row[12],
            subject=str(row[13]),
        )
        status = retention_status(policy, message)
        if status is None:
            raise KeyError("Retention policy does not match this message")
        if not status.is_eligible:
            raise ValueError("Retention item is not eligible for review yet")

        review_after = (
            datetime.now(timezone.utc).replace(microsecond=0) + RETENTION_REVIEW_DEFER_DURATION
            if action == "defer"
            else None
        )
        previous = self.connection.execute(
            """
            SELECT id, new_value_json, created_at
            FROM millie_user_feedback_events
            WHERE message_id = %s
              AND feedback_type = 'retention_override'
              AND metadata_json->>'retention_policy_id' = %s
              AND metadata_json->>'mailbox_message_id' = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (message.message_id, policy.id, message.mailbox_message_id),
        ).fetchone()
        new_value = {
            "action": action,
            "policy_action": policy.action,
            "policy_status": policy.status,
            "eligible_at": status.eligible_at.isoformat() if status.eligible_at else None,
            "is_eligible": status.is_eligible,
        }
        if review_after is not None:
            new_value["review_after"] = review_after.isoformat()
        metadata = {
            "retention_policy_id": policy.id,
            "mailbox_message_id": message.mailbox_message_id,
            "folder_path": message.folder_path,
            "imap_uid": message.imap_uid,
        }
        self.connection.execute(
            """
            INSERT INTO millie_user_feedback_events (
                id, identity_id, message_id, feedback_type, feedback_source,
                previous_value_json, new_value_json, metadata_json
            )
            VALUES (%s, %s, %s, 'retention_override', %s, %s, %s, %s)
            """,
            (
                str(uuid.uuid4()),
                identity_id,
                message.message_id,
                feedback_source,
                Jsonb(
                    {
                        "latest_feedback_id": previous[0] if previous else None,
                        "latest_feedback": previous[1] if previous else {},
                        "latest_feedback_at": previous[2].isoformat() if previous else None,
                    }
                ),
                Jsonb(new_value),
                Jsonb(metadata),
            ),
        )
        self._insert_automation_audit(
            action_type="retention_evaluate",
            identity_id=identity_id,
            message_id=message.message_id,
            retention_policy_id=policy.id,
            before_json={
                "latest_feedback_id": previous[0] if previous else None,
                "latest_feedback": previous[1] if previous else {},
            },
            after_json={
                **new_value,
                **metadata,
                "policy_name": policy.name,
            },
        )
        return {
            "policy_id": policy.id,
            "policy_name": policy.name,
            "mailbox_message_id": message.mailbox_message_id,
            "message_id": message.message_id,
            "folder_path": message.folder_path,
            "uid": message.imap_uid,
            "action": action,
            "review_after": review_after,
        }

    def _insert_automation_audit(
        self,
        *,
        action_type: str,
        identity_id: str | None = None,
        message_id: str | None = None,
        classification_id: str | None = None,
        rule_id: str | None = None,
        retention_policy_id: str | None = None,
        unsubscribe_candidate_id: str | None = None,
        before_json: dict[str, object] | None = None,
        after_json: dict[str, object] | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO millie_automation_audit_log (
                id, identity_id, message_id, classification_id, rule_id,
                retention_policy_id, unsubscribe_candidate_id, action_type,
                automation_level, status, before_json, after_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'review', 'recorded', %s, %s)
            """,
            (
                str(uuid.uuid4()),
                identity_id,
                message_id,
                classification_id,
                rule_id,
                retention_policy_id,
                unsubscribe_candidate_id,
                action_type,
                Jsonb(_sanitize_metadata(before_json or {})),
                Jsonb(_sanitize_metadata(after_json or {})),
            ),
        )

    def record_provider_write_audit(
        self,
        *,
        action_type: str,
        status: str,
        before_json: dict[str, object] | None = None,
        after_json: dict[str, object] | None = None,
        error_message: str | None = None,
        metadata_json: dict[str, object] | None = None,
    ) -> None:
        if action_type not in PROVIDER_WRITE_AUDIT_ACTIONS:
            raise ValueError(f"Unsupported provider-write audit action: {action_type}")
        if status not in PROVIDER_WRITE_AUDIT_STATUSES:
            raise ValueError(f"Unsupported provider-write audit status: {status}")
        self.connection.execute(
            """
            INSERT INTO millie_automation_audit_log (
                id, action_type, automation_level, status,
                before_json, after_json, error_message, metadata_json
            )
            VALUES (%s, %s, 'provider_write', %s, %s, %s, %s, %s)
            """,
            (
                str(uuid.uuid4()),
                action_type,
                status,
                Jsonb(_sanitize_metadata(before_json or {})),
                Jsonb(_sanitize_metadata(after_json or {})),
                error_message,
                Jsonb(_sanitize_metadata(metadata_json or {})),
            ),
        )

    def message_addresses(self, message_id: str) -> list[dict[str, object]]:
        rows = self.connection.execute(
            """
            SELECT role, ordinal, display_name, email_address, raw_value
            FROM mail_message_addresses
            WHERE message_id = %s
            ORDER BY role, ordinal
            """,
            (message_id,),
        ).fetchall()
        return [
            {
                "role": row[0],
                "ordinal": row[1],
                "display_name": row[2],
                "email_address": row[3],
                "raw_value": row[4],
                "display": format_address_value(row[2], row[3], row[4]),
            }
            for row in rows
        ]

    def message_attachments(self, message_id: str) -> list[dict[str, object]]:
        rows = self.connection.execute(
            """
            SELECT filename, content_type, size_bytes, sha256
            FROM mail_message_parts
            WHERE message_id = %s
              AND is_attachment = TRUE
            ORDER BY ordinal
            """,
            (message_id,),
        ).fetchall()
        return [
            {
                "filename": row[0] or "attachment",
                "content_type": row[1] or "application/octet-stream",
                "size": int(row[2] or 0),
                "sha256": row[3],
            }
            for row in rows
        ]

    def list_imap_messages(self, mailbox_id: str, folder_path: str) -> list[dict[str, object]]:
        rows = self.connection.execute(
            """
            SELECT
                mm.imap_uid,
                mm.flags,
                mm.internal_date,
                m.id,
                m.subject,
                m.raw_mime_size_bytes,
                m.raw_mime_sha256
            FROM millie_mailbox_messages mm
            JOIN millie_mailbox_folders mf ON mf.id = mm.folder_id
            JOIN mail_messages m ON m.id = mm.message_id
            WHERE mm.mailbox_id = %s
              AND mf.folder_path = %s
              AND mm.is_expunged = FALSE
              AND mm.metadata_json->>'retention_hidden_from_default_views' IS DISTINCT FROM 'true'
            ORDER BY mm.imap_uid
            """,
            (mailbox_id, folder_path),
        ).fetchall()
        return [
            {
                "uid": int(row[0]),
                "flags": list(row[1] or []),
                "internal_date": row[2],
                "message_id": row[3],
                "subject": row[4],
                "size": int(row[5] or 0),
                "sha256": row[6],
            }
            for row in rows
        ]

    def get_raw_mime_by_uid(
        self,
        *,
        mailbox_id: str,
        folder_path: str,
        uid: int,
    ) -> bytes | None:
        row = self.connection.execute(
            """
            SELECT r.content_blob
            FROM millie_mailbox_messages mm
            JOIN millie_mailbox_folders mf ON mf.id = mm.folder_id
            JOIN mail_raw_mime r ON r.message_id = mm.message_id
            WHERE mm.mailbox_id = %s
              AND mf.folder_path = %s
              AND mm.imap_uid = %s
              AND mm.is_expunged = FALSE
              AND mm.metadata_json->>'retention_hidden_from_default_views' IS DISTINCT FROM 'true'
            """,
            (mailbox_id, folder_path, uid),
        ).fetchone()
        return bytes(row[0]) if row else None

    def get_email_message_by_uid(
        self,
        *,
        mailbox_id: str,
        folder_path: str,
        uid: int,
    ) -> EmailMessage | None:
        raw = self.get_raw_mime_by_uid(mailbox_id=mailbox_id, folder_path=folder_path, uid=uid)
        if raw is None:
            return None
        return BytesParser(policy=policy.default).parsebytes(raw)

    def _mailbox_message_rows_by_uid(
        self,
        *,
        mailbox_id: str,
        folder_path: str,
        uids: list[int],
    ) -> list[dict[str, object]]:
        folder_path = normalize_mailbox_path(folder_path)
        params: list[object] = [mailbox_id, folder_path]
        uid_filter = ""
        if uids:
            uid_filter = f"AND mm.imap_uid IN ({placeholders(uids)})"
            params.extend(uids)
        rows = self.connection.execute(
            f"""
            SELECT
                mm.id,
                mm.imap_uid,
                mm.flags,
                mm.keywords,
                mm.is_deleted,
                mm.message_id,
                mm.binding_id,
                mm.internal_date
            FROM millie_mailbox_messages mm
            JOIN millie_mailbox_folders mf ON mf.id = mm.folder_id
            WHERE mm.mailbox_id = %s
              AND mf.folder_path = %s
              AND mm.is_expunged = FALSE
              AND mm.metadata_json->>'retention_hidden_from_default_views' IS DISTINCT FROM 'true'
              {uid_filter}
            ORDER BY mm.imap_uid
            """,
            tuple(params),
        ).fetchall()
        return [
            {
                "id": row[0],
                "uid": int(row[1]),
                "flags": list(row[2] or []),
                "keywords": list(row[3] or []),
                "is_deleted": bool(row[4]),
                "message_id": row[5],
                "binding_id": row[6],
                "internal_date": row[7],
            }
            for row in rows
        ]

    def _copy_mailbox_message_row(
        self,
        *,
        mailbox_id: str,
        target_folder_id: str,
        source_row: dict[str, object],
    ) -> int:
        existing = self.connection.execute(
            """
            SELECT imap_uid
            FROM millie_mailbox_messages
            WHERE folder_id = %s AND message_id = %s
            """,
            (target_folder_id, source_row["message_id"]),
        ).fetchone()
        if existing:
            return int(existing[0])
        return self._insert_mailbox_message(
            mailbox_id=mailbox_id,
            folder_id=target_folder_id,
            message_id=str(source_row["message_id"]),
            binding_id=source_row["binding_id"],
            internal_date=source_row["internal_date"],
            flags=normalize_message_flags(source_row["flags"]),
        )

    def _insert_mailbox_message(
        self,
        *,
        mailbox_id: str,
        folder_id: str,
        message_id: str,
        binding_id: object | None,
        internal_date: object | None,
        flags: list[str],
    ) -> int:
        existing = self.connection.execute(
            """
            SELECT imap_uid
            FROM millie_mailbox_messages
            WHERE folder_id = %s AND message_id = %s
            """,
            (folder_id, message_id),
        ).fetchone()
        if existing:
            return int(existing[0])
        next_uid = self.connection.execute(
            """
            SELECT coalesce(max(imap_uid), 0) + 1
            FROM millie_mailbox_messages
            WHERE folder_id = %s
            """,
            (folder_id,),
        ).fetchone()[0]
        row_id = stable_id("millie_mailbox_message", mailbox_id, folder_id, message_id)
        date_value = internal_date or datetime.now(timezone.utc)
        booleans = message_flag_booleans(flags)
        keywords = [flag for flag in flags if not flag.startswith("\\")]
        self.connection.execute(
            """
            INSERT INTO millie_mailbox_messages (
                id, mailbox_id, folder_id, message_id, binding_id, imap_uid,
                internal_date, flags, keywords, is_seen, is_answered, is_flagged,
                is_deleted, is_draft, is_recent
            )
            VALUES (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s,
                %s, %s, FALSE
            )
            """,
            (
                row_id,
                mailbox_id,
                folder_id,
                message_id,
                binding_id,
                next_uid,
                date_value,
                flags,
                keywords,
                booleans["is_seen"],
                booleans["is_answered"],
                booleans["is_flagged"],
                booleans["is_deleted"],
                booleans["is_draft"],
            ),
        )
        return int(next_uid)

    def _update_mailbox_message_flags(self, row_id: str, flags: list[str]) -> None:
        booleans = message_flag_booleans(flags)
        keywords = [flag for flag in flags if not flag.startswith("\\")]
        self.connection.execute(
            """
            UPDATE millie_mailbox_messages
            SET flags = %s,
                keywords = %s,
                is_seen = %s,
                is_answered = %s,
                is_flagged = %s,
                is_deleted = %s,
                is_draft = %s,
                updated_at = now()
            WHERE id = %s
            """,
            (
                flags,
                keywords,
                booleans["is_seen"],
                booleans["is_answered"],
                booleans["is_flagged"],
                booleans["is_deleted"],
                booleans["is_draft"],
                row_id,
            ),
        )

    def _replace_message(
        self,
        source_id: str,
        message: NormalizedMessage,
        import_job_id: str | None,
    ) -> None:
        self.connection.execute(
            """
            DELETE FROM mail_messages
            WHERE id = %s
               OR (source_id = %s AND source_message_id = %s)
            """,
            (message.id, source_id, message.source_message_id),
        )
        self.connection.execute(
            """
            INSERT INTO mail_messages (
                id, source_id, import_job_id, source_message_id, internet_message_id,
                conversation_id, thread_id, subject, normalized_subject, sent_at,
                received_at, date_header, timezone_offset_minutes, body_text, body_html,
                body_preview, raw_mime_sha256, raw_mime_size_bytes,
                normalized_body_sha256, attachment_set_sha256,
                normalized_message_fingerprint, has_attachments, metadata_json
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s
            )
            """,
            (
                message.id,
                source_id,
                import_job_id,
                message.source_message_id,
                message.internet_message_id,
                message.conversation_id,
                message.thread_id,
                message.subject,
                message.normalized_subject,
                message.sent_at,
                message.received_at,
                message.date_header,
                message.timezone_offset_minutes,
                message.body_text,
                message.body_html,
                message.body_preview,
                message.raw_mime_sha256,
                message.raw_mime_size_bytes,
                message.normalized_body_sha256,
                message.attachment_set_sha256,
                message.normalized_message_fingerprint,
                message.has_attachments,
                Jsonb(message.metadata),
            ),
        )

    def _upsert_folder(self, source_id: str, folder_path: str) -> str:
        folder_id = stable_id("folder", source_id, folder_path)
        display_name = folder_path.rsplit("/", 1)[-1]
        self.connection.execute(
            """
            INSERT INTO mail_folders (id, source_id, folder_path, display_name)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT(id) DO UPDATE SET display_name = excluded.display_name
            """,
            (folder_id, source_id, folder_path, display_name),
        )
        return folder_id

    def _replace_addresses(self, message: NormalizedMessage) -> None:
        self.connection.execute("DELETE FROM mail_message_addresses WHERE message_id = %s", (message.id,))
        rows = [
            (
                stable_id("address", message.id, address.role, address.ordinal),
                message.id,
                address.role,
                address.ordinal,
                address.display_name,
                address.email_address,
                address.raw_value,
            )
            for address in message.addresses
        ]
        with self.connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO mail_message_addresses (
                    id, message_id, role, ordinal, display_name, email_address, raw_value
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                rows,
            )

    def _replace_headers(self, message: NormalizedMessage) -> None:
        self.connection.execute("DELETE FROM mail_message_headers WHERE message_id = %s", (message.id,))
        with self.connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO mail_message_headers (message_id, ordinal, header_name, header_value)
                VALUES (%s, %s, %s, %s)
                """,
                [(message.id, header.ordinal, header.name, header.value) for header in message.headers],
            )

    def _replace_parts(self, message: NormalizedMessage) -> None:
        self.connection.execute("DELETE FROM mail_message_parts WHERE message_id = %s", (message.id,))
        with self.connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO mail_message_parts (
                    id, message_id, parent_part_id, ordinal, part_path, content_type,
                    content_disposition, charset, filename, content_id, content_location,
                    transfer_encoding, is_container, is_body, is_attachment, is_inline,
                    is_embedded_message, size_bytes, sha256, text_content, binary_content,
                    metadata_json
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                [
                    (
                        part.id,
                        message.id,
                        part.parent_part_id,
                        part.ordinal,
                        part.part_path,
                        part.content_type,
                        part.content_disposition,
                        part.charset,
                        part.filename,
                        part.content_id,
                        part.content_location,
                        part.transfer_encoding,
                        part.is_container,
                        part.is_body,
                        part.is_attachment,
                        part.is_inline,
                        part.is_embedded_message,
                        part.size_bytes,
                        part.sha256,
                        part.text_content,
                        part.binary_content,
                        Jsonb(part.metadata),
                    )
                    for part in message.parts
                ],
            )

    def _replace_metadata(self, message: NormalizedMessage) -> None:
        self.connection.execute("DELETE FROM mail_message_metadata WHERE message_id = %s", (message.id,))
        with self.connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO mail_message_metadata (message_id, metadata_key, value_text)
                VALUES (%s, %s, %s)
                """,
                [
                    (message.id, key, _metadata_value(value))
                    for key, value in sorted(message.metadata.items())
                    if value is not None
                ],
            )

    def _replace_search_document(self, message: NormalizedMessage) -> None:
        role_text = _role_text(message.addresses)
        metadata_text = " ".join(
            _metadata_value(value)
            for value in message.metadata.values()
            if value is not None
        )
        search_text = " ".join(
            value
            for value in [
                message.subject,
                message.body_text,
                message.body_html,
                role_text.get("from"),
                role_text.get("to"),
                role_text.get("cc"),
                role_text.get("bcc"),
                metadata_text,
            ]
            if value
        )
        search_text = _truncate_search_text(search_text)
        self.connection.execute(
            """
            INSERT INTO mail_search_documents (
                message_id, subject, body_text, from_text, to_text, cc_text,
                bcc_text, metadata_text, search_text
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT(message_id) DO UPDATE SET
                subject = excluded.subject,
                body_text = excluded.body_text,
                from_text = excluded.from_text,
                to_text = excluded.to_text,
                cc_text = excluded.cc_text,
                bcc_text = excluded.bcc_text,
                metadata_text = excluded.metadata_text,
                search_text = excluded.search_text,
                updated_at = now()
            """,
            (
                message.id,
                message.subject,
                message.body_text,
                role_text.get("from"),
                role_text.get("to"),
                role_text.get("cc"),
                role_text.get("bcc"),
                metadata_text,
                search_text,
            ),
        )


def _role_text(addresses: Iterable[NormalizedAddress]) -> dict[str, str]:
    values: dict[str, list[str]] = {}
    for address in addresses:
        values.setdefault(address.role, []).append(
            " ".join(value for value in [address.display_name, address.email_address] if value)
        )
    return {
        role: " ".join(value for value in role_values if value)
        for role, role_values in values.items()
    }


def _truncate_search_text(value: str, *, max_bytes: int = 800_000) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _sanitize_message_text(message: NormalizedMessage) -> None:
    for attr in [
        "source_message_id",
        "internet_message_id",
        "conversation_id",
        "thread_id",
        "subject",
        "normalized_subject",
        "sent_at",
        "received_at",
        "date_header",
        "body_text",
        "body_html",
        "body_preview",
        "normalized_body_sha256",
        "attachment_set_sha256",
        "normalized_message_fingerprint",
    ]:
        setattr(message, attr, _db_text(getattr(message, attr)))
    for address in message.addresses:
        address.display_name = _db_text(address.display_name)
        address.email_address = _db_text(address.email_address)
        address.raw_value = _db_text(address.raw_value)
    for header in message.headers:
        header.name = _db_text(header.name) or ""
        header.value = _db_text(header.value) or ""
    for part in message.parts:
        for attr in [
            "content_type",
            "content_disposition",
            "charset",
            "filename",
            "content_id",
            "content_location",
            "transfer_encoding",
            "text_content",
        ]:
            setattr(part, attr, _db_text(getattr(part, attr)))
        part.metadata = _sanitize_metadata(part.metadata)
    message.metadata = _sanitize_metadata(message.metadata)


def _sanitize_metadata(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return _db_text(value) or ""
    if isinstance(value, dict):
        return {
            str(_db_text(str(key)) or ""): _sanitize_metadata(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_metadata(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_metadata(item) for item in value]
    return value


def _db_text(value: str | None) -> str | None:
    if value is None:
        return None
    return value.replace("\x00", "").encode("utf-8", errors="replace").decode("utf-8")


def format_address_value(
    display_name: str | None,
    email_address: str | None,
    raw_value: str | None,
) -> str:
    if display_name and email_address:
        return f"{display_name} <{email_address}>"
    if email_address:
        return email_address
    return raw_value or ""


def placeholders(values: Iterable[object]) -> str:
    values = list(values)
    if not values:
        raise ValueError("At least one value is required.")
    return ", ".join(["%s"] * len(values))


def web_session_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalize_mailbox_path(value: str) -> str:
    path = str(value).strip().strip('"')
    path = path.replace("\\", "/")
    while "//" in path:
        path = path.replace("//", "/")
    path = path.strip("/")
    if path.upper() == "INBOX":
        return "INBOX"
    return path


SYSTEM_FLAGS = {
    "\\seen": "\\Seen",
    "\\answered": "\\Answered",
    "\\flagged": "\\Flagged",
    "\\deleted": "\\Deleted",
    "\\draft": "\\Draft",
    "\\recent": "\\Recent",
}
SYSTEM_FLAG_ORDER = ["\\Seen", "\\Answered", "\\Flagged", "\\Deleted", "\\Draft", "\\Recent"]


def normalize_message_flags(flags: Iterable[object]) -> list[str]:
    seen: dict[str, str] = {}
    for value in flags:
        flag = str(value).strip()
        if not flag:
            continue
        if flag.startswith("\\"):
            flag = SYSTEM_FLAGS.get(flag.lower(), flag)
        key = flag.lower()
        seen[key] = flag
    ordered: list[str] = []
    for flag in SYSTEM_FLAG_ORDER:
        value = seen.pop(flag.lower(), None)
        if value:
            ordered.append(value)
    ordered.extend(seen[key] for key in sorted(seen))
    return ordered


def message_flag_booleans(flags: Iterable[object]) -> dict[str, bool]:
    values = {str(flag).lower() for flag in flags}
    return {
        "is_seen": "\\seen" in values,
        "is_answered": "\\answered" in values,
        "is_flagged": "\\flagged" in values,
        "is_deleted": "\\deleted" in values,
        "is_draft": "\\draft" in values,
    }


def _metadata_value(value: object) -> str:
    if isinstance(value, str):
        return _db_text(value) or ""
    return _db_text(json.dumps(_sanitize_metadata(value), sort_keys=True, separators=(",", ":"))) or ""
