"""Postgres writer/query helpers for MILLIE live prototypes."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from typing import Any, Iterable

import psycopg
from psycopg import Connection
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

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
        limit: int = 100,
    ) -> list[dict[str, object]]:
        rows = self.connection.execute(
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
                m.has_attachments,
                m.raw_mime_size_bytes,
                from_addr.value AS from_text,
                to_addr.value AS to_text
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
            WHERE mm.mailbox_id = %s
              AND mf.folder_path = %s
              AND mm.is_expunged = FALSE
            ORDER BY message_date DESC NULLS LAST, mm.imap_uid DESC
            LIMIT %s
            """,
            (mailbox_id, folder_path, limit),
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
            }
            for row in rows
        ]

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
                r.content_blob
            FROM millie_mailbox_messages mm
            JOIN millie_mailbox_folders mf ON mf.id = mm.folder_id
            JOIN mail_messages m ON m.id = mm.message_id
            LEFT JOIN mail_raw_mime r ON r.message_id = m.id
            WHERE mm.mailbox_id = %s
              AND mf.folder_path = %s
              AND mm.imap_uid = %s
              AND mm.is_expunged = FALSE
            LIMIT 1
            """,
            (mailbox_id, folder_path, uid),
        ).fetchone()
        if not row:
            return None
        message_id = row[3]
        return {
            "uid": int(row[0]),
            "flags": list(row[1] or []),
            "internal_date": row[2],
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
        }

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
                body_preview, raw_mime_sha256, raw_mime_size_bytes, has_attachments,
                metadata_json
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
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
