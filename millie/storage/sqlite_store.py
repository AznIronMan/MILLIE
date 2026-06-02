"""SQLite writer for normalized mail records."""

from __future__ import annotations

import json
import sqlite3
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from typing import Iterable

from millie.importing.models import NormalizedAddress, NormalizedMessage, stable_id

from .schema import apply_sqlite_schema


class SQLiteMailStore:
    """Persist normalized mail records into the SQLite canonical schema."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.connection.execute("PRAGMA foreign_keys = ON")

    @classmethod
    def open(cls, path: Path) -> "SQLiteMailStore":
        path.parent.mkdir(parents=True, exist_ok=True)
        return cls(sqlite3.connect(path))

    def initialize(self) -> None:
        apply_sqlite_schema(self.connection)

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
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                display_name = excluded.display_name,
                auth_mode = excluded.auth_mode,
                is_active = excluded.is_active,
                updated_at = CURRENT_TIMESTAMP
            """,
            (source_id, source_type, display_name, source_uri, auth_mode, int(is_active)),
        )
        return source_id

    def create_import_job(
        self,
        *,
        source_id: str,
        mode: str = "manual",
        status: str = "planned",
        metadata: dict[str, object] | None = None,
    ) -> str:
        job_id = stable_id(
            "import_job",
            source_id,
            mode,
            json.dumps(metadata or {}, sort_keys=True),
        )
        self.connection.execute(
            """
            INSERT INTO mail_import_jobs (id, source_id, status, mode, metadata_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (job_id, source_id, status, mode, _json(metadata or {})),
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
        """Store a complete message graph in one transaction."""

        _sanitize_message_text(message)
        with self.connection:
            self._replace_message(source_id, message, import_job_id)
            if folder:
                folder_id = self._upsert_folder(source_id, folder)
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO mail_message_folders (message_id, folder_id)
                    VALUES (?, ?)
                    """,
                    (message.id, folder_id),
                )

            self.connection.execute(
                "INSERT OR REPLACE INTO mail_raw_mime (message_id, content_blob) VALUES (?, ?)",
                (message.id, message.raw_mime),
            )
            self._replace_addresses(message)
            self._replace_headers(message)
            self._replace_parts(message)
            self._replace_metadata(message)
            self._replace_search_document(message)

    def get_raw_mime(self, message_id: str) -> bytes | None:
        row = self.connection.execute(
            "SELECT content_blob FROM mail_raw_mime WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        return bytes(row[0]) if row else None

    def get_email_message(self, message_id: str) -> EmailMessage | None:
        raw_mime = self.get_raw_mime(message_id)
        if raw_mime is None:
            return None
        return BytesParser(policy=policy.default).parsebytes(raw_mime)

    def _replace_message(
        self,
        source_id: str,
        message: NormalizedMessage,
        import_job_id: str | None,
    ) -> None:
        self.connection.execute("DELETE FROM mail_messages WHERE id = ?", (message.id,))
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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                int(message.has_attachments),
                _json(message.metadata),
            ),
        )

    def _upsert_folder(self, source_id: str, folder_path: str) -> str:
        folder_id = stable_id("folder", source_id, folder_path)
        display_name = folder_path.rsplit("/", 1)[-1]
        self.connection.execute(
            """
            INSERT INTO mail_folders (id, source_id, folder_path, display_name)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET display_name = excluded.display_name
            """,
            (folder_id, source_id, folder_path, display_name),
        )
        return folder_id

    def _replace_addresses(self, message: NormalizedMessage) -> None:
        self.connection.execute(
            "DELETE FROM mail_message_addresses WHERE message_id = ?",
            (message.id,),
        )
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
        self.connection.executemany(
            """
            INSERT INTO mail_message_addresses (
                id, message_id, role, ordinal, display_name, email_address, raw_value
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    def _replace_headers(self, message: NormalizedMessage) -> None:
        self.connection.execute(
            "DELETE FROM mail_message_headers WHERE message_id = ?",
            (message.id,),
        )
        self.connection.executemany(
            """
            INSERT INTO mail_message_headers (message_id, ordinal, header_name, header_value)
            VALUES (?, ?, ?, ?)
            """,
            [
                (message.id, header.ordinal, header.name, header.value)
                for header in message.headers
            ],
        )

    def _replace_parts(self, message: NormalizedMessage) -> None:
        self.connection.execute(
            "DELETE FROM mail_message_parts WHERE message_id = ?",
            (message.id,),
        )
        self.connection.executemany(
            """
            INSERT INTO mail_message_parts (
                id, message_id, parent_part_id, ordinal, part_path, content_type,
                content_disposition, charset, filename, content_id, content_location,
                transfer_encoding, is_container, is_body, is_attachment, is_inline,
                is_embedded_message, size_bytes, sha256, text_content, binary_content,
                metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    int(part.is_container),
                    int(part.is_body),
                    int(part.is_attachment),
                    int(part.is_inline),
                    int(part.is_embedded_message),
                    part.size_bytes,
                    part.sha256,
                    part.text_content,
                    part.binary_content,
                    _json(part.metadata),
                )
                for part in message.parts
            ],
        )

    def _replace_metadata(self, message: NormalizedMessage) -> None:
        self.connection.execute(
            "DELETE FROM mail_message_metadata WHERE message_id = ?",
            (message.id,),
        )
        self.connection.executemany(
            """
            INSERT INTO mail_message_metadata (message_id, metadata_key, value_text)
            VALUES (?, ?, ?)
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
            INSERT OR REPLACE INTO mail_search_documents (
                message_id, subject, body_text, from_text, to_text, cc_text,
                bcc_text, metadata_text, search_text
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        self.connection.execute(
            "DELETE FROM mail_search_fts WHERE message_id = ?",
            (message.id,),
        )
        self.connection.execute(
            """
            INSERT INTO mail_search_fts (
                message_id, subject, body_text, from_text, to_text, cc_text,
                bcc_text, metadata_text, search_text
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            " ".join(
                value
                for value in [address.display_name, address.email_address]
                if value
            )
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


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _metadata_value(value: object) -> str:
    if isinstance(value, str):
        return _db_text(value) or ""
    return _db_text(_json(_sanitize_metadata(value))) or ""
