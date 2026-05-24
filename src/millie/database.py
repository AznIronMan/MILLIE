from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from .html_sanitize import sanitize_html_document


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    display_name TEXT NOT NULL,
    provider TEXT,
    source_uri TEXT,
    auth_ref TEXT,
    created_at TEXT NOT NULL,
    last_sync_at TEXT,
    status TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS mailboxes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    parent_id INTEGER REFERENCES mailboxes(id) ON DELETE SET NULL,
    path TEXT NOT NULL,
    display_name TEXT NOT NULL,
    role TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(source_id, path)
);

CREATE TABLE IF NOT EXISTS addresses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    normalized_email TEXT NOT NULL UNIQUE,
    display_name TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS blobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_hash TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    mime_type TEXT,
    size_bytes INTEGER NOT NULL,
    storage_ref TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id TEXT NOT NULL UNIQUE,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    source_message_id TEXT,
    internet_message_id TEXT,
    subject TEXT,
    sent_at TEXT,
    received_at TEXT,
    internal_date TEXT,
    from_address_id INTEGER REFERENCES addresses(id) ON DELETE SET NULL,
    reply_to_address_id INTEGER REFERENCES addresses(id) ON DELETE SET NULL,
    in_reply_to TEXT,
    references_raw TEXT,
    conversation_id TEXT,
    body_text TEXT,
    body_html_ref TEXT,
    body_sanitized_html_ref TEXT,
    raw_message_ref TEXT,
    content_hash TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS message_addresses (
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    address_id INTEGER NOT NULL REFERENCES addresses(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    display_name_snapshot TEXT,
    ordinal INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(message_id, address_id, role, ordinal)
);

CREATE TABLE IF NOT EXISTS message_mailboxes (
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    mailbox_id INTEGER NOT NULL REFERENCES mailboxes(id) ON DELETE CASCADE,
    source_uid TEXT,
    flags_json TEXT NOT NULL DEFAULT '[]',
    labels_json TEXT NOT NULL DEFAULT '[]',
    seen_at_source TEXT,
    PRIMARY KEY(message_id, mailbox_id, source_uid)
);

CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    filename TEXT,
    mime_type TEXT,
    content_id TEXT,
    disposition TEXT,
    size_bytes INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    storage_ref TEXT NOT NULL,
    is_inline INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS headers (
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    value TEXT,
    ordinal INTEGER NOT NULL,
    PRIMARY KEY(message_id, ordinal)
);

CREATE TABLE IF NOT EXISTS import_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    message_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    options_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS import_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    import_job_id INTEGER NOT NULL REFERENCES import_jobs(id) ON DELETE CASCADE,
    source_item_ref TEXT,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_sync_states (
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    scope TEXT NOT NULL,
    state_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(source_id, scope)
);

CREATE TABLE IF NOT EXISTS export_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_profile TEXT NOT NULL,
    format TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    message_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    warning_count INTEGER NOT NULL DEFAULT 0,
    output_root TEXT NOT NULL,
    options_json TEXT NOT NULL DEFAULT '{}',
    manifest_ref TEXT
);

CREATE TABLE IF NOT EXISTS export_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    export_job_id INTEGER NOT NULL REFERENCES export_jobs(id) ON DELETE CASCADE,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    mailbox_id INTEGER REFERENCES mailboxes(id) ON DELETE SET NULL,
    output_path TEXT NOT NULL,
    output_hash TEXT,
    format TEXT NOT NULL,
    status TEXT NOT NULL,
    warning_json TEXT NOT NULL DEFAULT '[]'
);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    message_id UNINDEXED,
    subject,
    participants,
    body_text
);
"""


MIGRATIONS = [
    (1, "initial_schema", SCHEMA),
    (
        2,
        "import_dedupe_accounting",
        """
        ALTER TABLE import_jobs ADD COLUMN new_message_count INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE import_jobs ADD COLUMN duplicate_count INTEGER NOT NULL DEFAULT 0;
        UPDATE import_jobs
        SET new_message_count = message_count
        WHERE message_count > 0 AND new_message_count = 0 AND duplicate_count = 0;
        CREATE INDEX IF NOT EXISTS idx_messages_content_hash ON messages(content_hash);
        CREATE INDEX IF NOT EXISTS idx_message_mailboxes_message_id ON message_mailboxes(message_id);
        CREATE INDEX IF NOT EXISTS idx_message_mailboxes_mailbox_id ON message_mailboxes(mailbox_id);
        """,
    ),
    (
        3,
        "source_sync_state",
        """
        CREATE TABLE IF NOT EXISTS source_sync_states (
            source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
            scope TEXT NOT NULL,
            state_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL,
            PRIMARY KEY(source_id, scope)
        );
        CREATE INDEX IF NOT EXISTS idx_source_sync_states_source_id
            ON source_sync_states(source_id);
        """,
    ),
]


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def normalize_fts_query(value: str | None) -> str | None:
    if not value or not value.strip():
        return None
    tokens = re.findall(r"[\w@.+-]+", value, flags=re.UNICODE)
    quoted = []
    for token in tokens:
        if token.strip():
            escaped = token.replace('"', '""')
            quoted.append(f'"{escaped}"')
    return " ".join(quoted) or None


@dataclass(slots=True)
class InsertMessageResult:
    message_id: int
    created: bool
    mailbox_link_created: bool


class MillieDatabase:
    def __init__(self, db_path: Path, data_dir: Path):
        self.db_path = db_path
        self.data_dir = data_dir

    @contextmanager
    def connect(self) -> Iterable[sqlite3.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            self.run_migrations(conn)

    def run_migrations(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
        applied = {
            int(row["version"])
            for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
        for version, name, sql in MIGRATIONS:
            if version in applied:
                continue
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                (version, name, utc_now()),
            )

    def list_migrations(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
                """
            )
            rows = conn.execute("SELECT * FROM schema_migrations ORDER BY version").fetchall()
            return [row_to_dict(row) for row in rows if row is not None]

    def store_blob(self, kind: str, content: bytes, mime_type: str | None = None) -> dict[str, Any]:
        digest = hashlib.sha256(content).hexdigest()
        blob_dir = self.data_dir / "blobs" / digest[:2]
        blob_dir.mkdir(parents=True, exist_ok=True)
        blob_path = blob_dir / digest
        if not blob_path.exists():
            blob_path.write_bytes(content)

        storage_ref = str(blob_path.relative_to(self.data_dir))
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO blobs
                    (content_hash, kind, mime_type, size_bytes, storage_ref, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (digest, kind, mime_type, len(content), storage_ref, now),
            )
        return {
            "content_hash": digest,
            "storage_ref": storage_ref,
            "size_bytes": len(content),
        }

    def read_blob(self, storage_ref: str) -> bytes:
        path = self.data_dir / storage_ref
        return path.read_bytes()

    def create_source(self, kind: str, display_name: str, source_uri: str | None = None) -> int:
        now = utc_now()
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO sources (kind, display_name, source_uri, created_at, status)
                VALUES (?, ?, ?, ?, 'active')
                """,
                (kind, display_name, source_uri, now),
            )
            return int(cur.lastrowid)

    def get_or_create_source(self, kind: str, display_name: str, source_uri: str | None = None) -> int:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id FROM sources
                WHERE kind = ? AND display_name = ? AND COALESCE(source_uri, '') = COALESCE(?, '')
                """,
                (kind, display_name, source_uri),
            ).fetchone()
            if row:
                return int(row["id"])
        return self.create_source(kind, display_name, source_uri)

    def touch_source_sync(self, source_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE sources SET last_sync_at = ?, status = 'active' WHERE id = ?",
                (utc_now(), source_id),
            )

    def get_source_sync_state(self, source_id: int, scope: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT state_json
                FROM source_sync_states
                WHERE source_id = ? AND scope = ?
                """,
                (source_id, scope),
            ).fetchone()
        if row is None:
            return {}
        try:
            value = json.loads(str(row["state_json"]))
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    def set_source_sync_state(self, source_id: int, scope: str, state: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO source_sync_states (source_id, scope, state_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(source_id, scope) DO UPDATE SET
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
                """,
                (source_id, scope, json.dumps(state, sort_keys=True), utc_now()),
            )

    def get_or_create_mailbox(self, source_id: int, path: str, role: str | None = None) -> int:
        clean_path = path.strip("/") or "Imported"
        display_name = clean_path.rsplit("/", 1)[-1]
        now = utc_now()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id FROM mailboxes WHERE source_id = ? AND path = ?",
                (source_id, clean_path),
            ).fetchone()
            if row:
                return int(row["id"])
            cur = conn.execute(
                """
                INSERT INTO mailboxes (source_id, parent_id, path, display_name, role, created_at)
                VALUES (?, NULL, ?, ?, ?, ?)
                """,
                (source_id, clean_path, display_name, role, now),
            )
            return int(cur.lastrowid)

    def get_or_create_address(self, email: str, display_name: str | None = None) -> int:
        normalized = email.strip().lower()
        if not normalized:
            normalized = "unknown@millie.local"
        now = utc_now()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id FROM addresses WHERE normalized_email = ?",
                (normalized,),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE addresses SET last_seen_at = ?, display_name = COALESCE(display_name, ?) WHERE id = ?",
                    (now, display_name, int(row["id"])),
                )
                return int(row["id"])
            cur = conn.execute(
                """
                INSERT INTO addresses
                    (email, normalized_email, display_name, first_seen_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (email.strip() or normalized, normalized, display_name, now, now),
            )
            return int(cur.lastrowid)

    def insert_message(
        self,
        *,
        source_id: int,
        mailbox_id: int,
        source_uid: str,
        fields: dict[str, Any],
        headers: list[tuple[str, str]],
        addresses: list[dict[str, Any]],
        attachments: list[dict[str, Any]],
        participants_text: str,
        flags: list[str] | None = None,
        labels: list[str] | None = None,
        seen_at_source: str | None = None,
    ) -> InsertMessageResult:
        now = utc_now()
        stable_id = fields["content_hash"]
        flags_json = json.dumps(flags or [], sort_keys=True)
        labels_json = json.dumps(labels or [], sort_keys=True)
        with self.connect() as conn:
            created = False
            existing = conn.execute(
                "SELECT id FROM messages WHERE stable_id = ?",
                (stable_id,),
            ).fetchone()
            if existing:
                message_id = int(existing["id"])
                if fields.get("internal_date"):
                    conn.execute(
                        """
                        UPDATE messages
                        SET internal_date = ?, updated_at = ?
                        WHERE id = ? AND internal_date IS NULL
                        """,
                        (fields.get("internal_date"), now, message_id),
                    )
            else:
                created = True
                cur = conn.execute(
                    """
                    INSERT INTO messages (
                        stable_id, source_id, source_message_id, internet_message_id,
                        subject, sent_at, received_at, internal_date,
                        from_address_id, reply_to_address_id, in_reply_to, references_raw,
                        conversation_id, body_text, body_html_ref, body_sanitized_html_ref,
                        raw_message_ref, content_hash, size_bytes, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        stable_id,
                        source_id,
                        fields.get("source_message_id"),
                        fields.get("internet_message_id"),
                        fields.get("subject"),
                        fields.get("sent_at"),
                        fields.get("received_at"),
                        fields.get("internal_date"),
                        fields.get("from_address_id"),
                        fields.get("reply_to_address_id"),
                        fields.get("in_reply_to"),
                        fields.get("references_raw"),
                        fields.get("conversation_id"),
                        fields.get("body_text"),
                        fields.get("body_html_ref"),
                        fields.get("body_sanitized_html_ref"),
                        fields.get("raw_message_ref"),
                        fields["content_hash"],
                        fields["size_bytes"],
                        now,
                        now,
                    ),
                )
                message_id = int(cur.lastrowid)
                conn.executemany(
                    "INSERT INTO headers (message_id, name, value, ordinal) VALUES (?, ?, ?, ?)",
                    [(message_id, name, value, ordinal) for ordinal, (name, value) in enumerate(headers)],
                )
                conn.executemany(
                    """
                    INSERT OR IGNORE INTO message_addresses
                        (message_id, address_id, role, display_name_snapshot, ordinal)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            message_id,
                            item["address_id"],
                            item["role"],
                            item.get("display_name"),
                            item.get("ordinal", 0),
                        )
                        for item in addresses
                    ],
                )
                conn.executemany(
                    """
                    INSERT INTO attachments (
                        message_id, filename, mime_type, content_id, disposition,
                        size_bytes, content_hash, storage_ref, is_inline, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            message_id,
                            item.get("filename"),
                            item.get("mime_type"),
                            item.get("content_id"),
                            item.get("disposition"),
                            item["size_bytes"],
                            item["content_hash"],
                            item["storage_ref"],
                            1 if item.get("is_inline") else 0,
                            now,
                        )
                        for item in attachments
                    ],
                )
            self.index_message_fts(conn, message_id, fields, participants_text)

            exists = conn.execute(
                """
                SELECT 1 FROM message_mailboxes
                WHERE message_id = ? AND mailbox_id = ? AND source_uid = ?
                """,
                (message_id, mailbox_id, source_uid),
            ).fetchone()
            mailbox_link_created = exists is None
            if not exists:
                conn.execute(
                    """
                    INSERT INTO message_mailboxes
                        (message_id, mailbox_id, source_uid, flags_json, labels_json, seen_at_source)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (message_id, mailbox_id, source_uid, flags_json, labels_json, seen_at_source or now),
                )
            return InsertMessageResult(message_id, created, mailbox_link_created)

    def index_message_fts(
        self,
        conn: sqlite3.Connection,
        message_id: int,
        fields: dict[str, Any],
        participants_text: str,
    ) -> None:
        conn.execute("DELETE FROM messages_fts WHERE message_id = ?", (message_id,))
        conn.execute(
            """
            INSERT INTO messages_fts (message_id, subject, participants, body_text)
            VALUES (?, ?, ?, ?)
            """,
            (
                message_id,
                fields.get("subject") or "",
                participants_text,
                fields.get("body_text") or "",
            ),
        )

    def start_import_job(self, source_id: int, kind: str, options: dict[str, Any]) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO import_jobs (source_id, kind, status, started_at, options_json)
                VALUES (?, ?, 'running', ?, ?)
                """,
                (source_id, kind, utc_now(), json.dumps(options, sort_keys=True)),
            )
            return int(cur.lastrowid)

    def finish_import_job(
        self,
        import_job_id: int,
        status: str,
        message_count: int,
        error_count: int,
        *,
        new_message_count: int = 0,
        duplicate_count: int = 0,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE import_jobs
                SET status = ?, finished_at = ?, message_count = ?, error_count = ?,
                    new_message_count = ?, duplicate_count = ?
                WHERE id = ?
                """,
                (
                    status,
                    utc_now(),
                    message_count,
                    error_count,
                    new_message_count,
                    duplicate_count,
                    import_job_id,
                ),
            )

    def record_import_error(
        self,
        import_job_id: int,
        source_item_ref: str,
        severity: str,
        message: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO import_errors
                    (import_job_id, source_item_ref, severity, message, detail_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    import_job_id,
                    source_item_ref,
                    severity,
                    message,
                    json.dumps(detail or {}, sort_keys=True),
                    utc_now(),
                ),
            )

    def start_export_job(
        self,
        target_profile: str,
        export_format: str,
        output_root: Path,
        options: dict[str, Any],
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO export_jobs
                    (target_profile, format, status, started_at, output_root, options_json)
                VALUES (?, ?, 'running', ?, ?, ?)
                """,
                (
                    target_profile,
                    export_format,
                    utc_now(),
                    str(output_root),
                    json.dumps(options, sort_keys=True),
                ),
            )
            return int(cur.lastrowid)

    def record_export_item(
        self,
        export_job_id: int,
        message_id: int,
        mailbox_id: int | None,
        output_path: Path,
        output_hash: str | None,
        export_format: str,
        status: str,
        warnings: list[str] | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO export_items
                    (export_job_id, message_id, mailbox_id, output_path, output_hash, format, status, warning_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    export_job_id,
                    message_id,
                    mailbox_id,
                    str(output_path),
                    output_hash,
                    export_format,
                    status,
                    json.dumps(warnings or []),
                ),
            )

    def finish_export_job(
        self,
        export_job_id: int,
        status: str,
        message_count: int,
        error_count: int,
        warning_count: int,
        manifest_ref: str | None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE export_jobs
                SET status = ?, finished_at = ?, message_count = ?, error_count = ?,
                    warning_count = ?, manifest_ref = ?
                WHERE id = ?
                """,
                (
                    status,
                    utc_now(),
                    message_count,
                    error_count,
                    warning_count,
                    manifest_ref,
                    export_job_id,
                ),
            )

    def list_sources(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM sources ORDER BY created_at DESC").fetchall()
            return [row_to_dict(row) for row in rows if row is not None]

    def list_mailboxes(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT m.*, s.display_name AS source_name, COUNT(mm.message_id) AS message_count
                FROM mailboxes m
                JOIN sources s ON s.id = m.source_id
                LEFT JOIN message_mailboxes mm ON mm.mailbox_id = m.id
                GROUP BY m.id
                ORDER BY s.display_name, m.path
                """
            ).fetchall()
            return [row_to_dict(row) for row in rows if row is not None]

    def list_messages(
        self,
        mailbox_id: int | None = None,
        query: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        params: list[Any] = []
        mailbox_join = ""
        mailbox_where = ""
        if mailbox_id is not None:
            mailbox_join = "JOIN message_mailboxes mm ON mm.message_id = msg.id"
            mailbox_where = "AND mm.mailbox_id = ?"
            params.append(mailbox_id)

        search_query = normalize_fts_query(query)
        if query and search_query is None:
            return []

        if search_query:
            params = [search_query] + params
            sql = f"""
                SELECT msg.*, mb.path AS mailbox_path, src.display_name AS source_name
                FROM messages_fts fts
                JOIN messages msg ON msg.id = fts.message_id
                JOIN sources src ON src.id = msg.source_id
                LEFT JOIN message_mailboxes mm_first ON mm_first.message_id = msg.id
                LEFT JOIN mailboxes mb ON mb.id = mm_first.mailbox_id
                {mailbox_join}
                WHERE messages_fts MATCH ? {mailbox_where}
                GROUP BY msg.id
                ORDER BY COALESCE(msg.sent_at, msg.received_at, msg.created_at) DESC
                LIMIT ? OFFSET ?
            """
        else:
            sql = f"""
                SELECT msg.*, mb.path AS mailbox_path, src.display_name AS source_name
                FROM messages msg
                JOIN sources src ON src.id = msg.source_id
                LEFT JOIN message_mailboxes mm_first ON mm_first.message_id = msg.id
                LEFT JOIN mailboxes mb ON mb.id = mm_first.mailbox_id
                {mailbox_join}
                WHERE 1 = 1 {mailbox_where}
                GROUP BY msg.id
                ORDER BY COALESCE(msg.sent_at, msg.received_at, msg.created_at) DESC
                LIMIT ? OFFSET ?
            """
        params.extend([limit, offset])
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [row_to_dict(row) for row in rows if row is not None]

    def get_message(self, message_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT msg.*, src.display_name AS source_name
                FROM messages msg
                JOIN sources src ON src.id = msg.source_id
                WHERE msg.id = ?
                """,
                (message_id,),
            ).fetchone()
            message = row_to_dict(row)
            if message is None:
                return None
            message["headers"] = [
                row_to_dict(item)
                for item in conn.execute(
                    "SELECT name, value, ordinal FROM headers WHERE message_id = ? ORDER BY ordinal",
                    (message_id,),
                ).fetchall()
            ]
            message["addresses"] = [
                row_to_dict(item)
                for item in conn.execute(
                    """
                    SELECT ma.role, ma.ordinal, ma.display_name_snapshot, a.email, a.display_name
                    FROM message_addresses ma
                    JOIN addresses a ON a.id = ma.address_id
                    WHERE ma.message_id = ?
                    ORDER BY ma.role, ma.ordinal
                    """,
                    (message_id,),
                ).fetchall()
            ]
            message["attachments"] = [
                row_to_dict(item)
                for item in conn.execute(
                    """
                    SELECT id, filename, mime_type, content_id, disposition, size_bytes,
                           content_hash, storage_ref, is_inline
                    FROM attachments
                    WHERE message_id = ?
                    ORDER BY id
                    """,
                    (message_id,),
                ).fetchall()
            ]
            message["mailboxes"] = [
                row_to_dict(item)
                for item in conn.execute(
                    """
                    SELECT mb.id, mb.path, mb.display_name, mm.source_uid, mm.flags_json, mm.labels_json
                    FROM message_mailboxes mm
                    JOIN mailboxes mb ON mb.id = mm.mailbox_id
                    WHERE mm.message_id = ?
                    ORDER BY mb.path
                    """,
                    (message_id,),
                ).fetchall()
            ]
            return message

    def get_raw_message(self, message_id: int) -> bytes | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT raw_message_ref FROM messages WHERE id = ?",
                (message_id,),
            ).fetchone()
        if not row or not row["raw_message_ref"]:
            return None
        return self.read_blob(row["raw_message_ref"])

    def get_sanitized_message_html(self, message_id: int) -> bytes | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT body_html_ref, body_sanitized_html_ref FROM messages WHERE id = ?",
                (message_id,),
            ).fetchone()
        if row is None:
            return None
        if row["body_sanitized_html_ref"]:
            return self.read_blob(row["body_sanitized_html_ref"])
        if not row["body_html_ref"]:
            return None

        raw_html = self.read_blob(row["body_html_ref"]).decode("utf-8", errors="replace")
        sanitized = sanitize_html_document(raw_html).encode("utf-8")
        blob = self.store_blob("body_sanitized_html", sanitized, "text/html")
        with self.connect() as conn:
            conn.execute(
                "UPDATE messages SET body_sanitized_html_ref = ?, updated_at = ? WHERE id = ?",
                (blob["storage_ref"], utc_now(), message_id),
            )
        return sanitized

    def get_attachment(self, attachment_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT att.*, msg.subject, src.display_name AS source_name
                FROM attachments att
                JOIN messages msg ON msg.id = att.message_id
                JOIN sources src ON src.id = msg.source_id
                WHERE att.id = ?
                """,
                (attachment_id,),
            ).fetchone()
            attachment = row_to_dict(row)
        if attachment is None:
            return None
        attachment["content"] = self.read_blob(str(attachment["storage_ref"]))
        return attachment

    def attachment_count_for_messages(self, message_ids: list[int]) -> int:
        if not message_ids:
            return 0
        placeholders = ",".join("?" for _ in message_ids)
        with self.connect() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS count FROM attachments WHERE message_id IN ({placeholders})",
                message_ids,
            ).fetchone()
            return int(row["count"]) if row else 0

    def messages_for_export(
        self,
        mailbox_id: int | None = None,
        message_ids: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = ["1 = 1"]
        if mailbox_id is not None:
            where.append("mm.mailbox_id = ?")
            params.append(mailbox_id)
        if message_ids:
            placeholders = ",".join("?" for _ in message_ids)
            where.append(f"msg.id IN ({placeholders})")
            params.extend(message_ids)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT msg.id, msg.source_id, msg.subject, msg.sent_at, msg.raw_message_ref,
                       msg.content_hash, mb.id AS mailbox_id, mb.path AS mailbox_path
                FROM messages msg
                JOIN message_mailboxes mm ON mm.message_id = msg.id
                JOIN mailboxes mb ON mb.id = mm.mailbox_id
                WHERE {' AND '.join(where)}
                GROUP BY msg.id, mb.id
                ORDER BY mb.path, COALESCE(msg.sent_at, msg.created_at), msg.id
                """,
                params,
            ).fetchall()
            return [row_to_dict(row) for row in rows if row is not None]

    def list_export_jobs(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM export_jobs ORDER BY started_at DESC, id DESC").fetchall()
            return [row_to_dict(row) for row in rows if row is not None]

    def list_import_jobs(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT ij.*, s.display_name AS source_name, s.source_uri
                FROM import_jobs ij
                JOIN sources s ON s.id = ij.source_id
                ORDER BY ij.started_at DESC, ij.id DESC
                """
            ).fetchall()
            return [row_to_dict(row) for row in rows if row is not None]

    def get_import_job_errors(self, import_job_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM import_errors
                WHERE import_job_id = ?
                ORDER BY created_at DESC, id DESC
                """,
                (import_job_id,),
            ).fetchall()
            return [row_to_dict(row) for row in rows if row is not None]

    def list_export_job_items(self, export_job_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM export_items
                WHERE export_job_id = ?
                ORDER BY id
                """,
                (export_job_id,),
            ).fetchall()
            return [row_to_dict(row) for row in rows if row is not None]
