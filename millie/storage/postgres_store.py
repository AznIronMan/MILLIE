"""Postgres writer/query helpers for MILLIE live prototypes."""

from __future__ import annotations

import json
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from typing import Any, Iterable

import psycopg
from psycopg import Connection
from psycopg.types.json import Jsonb

from millie.importing.models import NormalizedAddress, NormalizedMessage, stable_id
from millie.service.auth import MillieIdentity, build_identity_sql

from .schema import load_schema


class PostgresMailStore:
    """Persist and serve normalized mail records from Postgres."""

    def __init__(self, connection: Connection) -> None:
        self.connection = connection

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
        return cls(connection)

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
            self.connection.execute(build_identity_sql(identity, password_hash=password_hash))
        return identity.mailbox_id

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

    def store_message(
        self,
        *,
        source_id: str,
        message: NormalizedMessage,
        import_job_id: str | None = None,
        folder: str | None = None,
    ) -> None:
        with self.connection.transaction():
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

    def folder_id(self, mailbox_id: str, folder_path: str) -> str | None:
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
        from millie.service.auth import verify_password

        row = self.connection.execute(
            """
            SELECT i.id, c.secret_hash
            FROM millie_identities i
            JOIN millie_identity_credentials c ON c.identity_id = i.id
            WHERE lower(i.login_address) = lower(%s)
              AND i.status = 'active'
              AND c.disabled_at IS NULL
              AND (c.expires_at IS NULL OR c.expires_at > now())
            ORDER BY c.created_at DESC
            LIMIT 1
            """,
            (login,),
        ).fetchone()
        if not row:
            return None
        identity_id, secret_hash = row
        if not verify_password(password, secret_hash):
            return None
        return identity_id

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
            row = self.connection.execute(
                """
                SELECT mb.id, mb.mailbox_address, mb.display_name, mb.owner_identity_id
                FROM millie_mailboxes mb
                WHERE lower(mb.mailbox_address) = lower(%s)
                ORDER BY mb.created_at
                LIMIT 1
                """,
                (mailbox_address,),
            ).fetchone()
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


def _metadata_value(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
