PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS mail_sources (
    id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL CHECK (
        source_type IN ('imap', 'exchange_imap_oauth', 'pst')
    ),
    display_name TEXT,
    source_uri TEXT NOT NULL,
    auth_mode TEXT,
    is_active INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source_type, source_uri)
);

CREATE TABLE IF NOT EXISTS mail_import_jobs (
    id TEXT PRIMARY KEY,
    source_id TEXT REFERENCES mail_sources(id) ON DELETE SET NULL,
    status TEXT NOT NULL CHECK (
        status IN ('planned', 'running', 'completed', 'failed', 'cancelled')
    ),
    mode TEXT NOT NULL DEFAULT 'manual',
    started_at TEXT,
    completed_at TEXT,
    error_message TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS mail_folders (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES mail_sources(id) ON DELETE CASCADE,
    parent_id TEXT REFERENCES mail_folders(id) ON DELETE SET NULL,
    folder_path TEXT NOT NULL,
    display_name TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE (source_id, folder_path)
);

CREATE TABLE IF NOT EXISTS mail_messages (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES mail_sources(id) ON DELETE CASCADE,
    import_job_id TEXT REFERENCES mail_import_jobs(id) ON DELETE SET NULL,
    source_message_id TEXT NOT NULL,
    internet_message_id TEXT,
    conversation_id TEXT,
    thread_id TEXT,
    subject TEXT,
    normalized_subject TEXT,
    sent_at TEXT,
    received_at TEXT,
    date_header TEXT,
    timezone_offset_minutes INTEGER,
    body_text TEXT,
    body_html TEXT,
    body_preview TEXT,
    raw_mime_sha256 TEXT NOT NULL,
    raw_mime_size_bytes INTEGER NOT NULL,
    has_attachments INTEGER NOT NULL DEFAULT 0 CHECK (has_attachments IN (0, 1)),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source_id, source_message_id)
);

CREATE TABLE IF NOT EXISTS mail_message_folders (
    message_id TEXT NOT NULL REFERENCES mail_messages(id) ON DELETE CASCADE,
    folder_id TEXT NOT NULL REFERENCES mail_folders(id) ON DELETE CASCADE,
    PRIMARY KEY (message_id, folder_id)
);

CREATE TABLE IF NOT EXISTS mail_message_addresses (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL REFERENCES mail_messages(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (
        role IN (
            'from', 'sender', 'reply_to', 'to', 'cc', 'bcc',
            'resent_from', 'resent_to'
        )
    ),
    ordinal INTEGER NOT NULL,
    display_name TEXT,
    email_address TEXT,
    raw_value TEXT
);

CREATE TABLE IF NOT EXISTS mail_message_headers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL REFERENCES mail_messages(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    header_name TEXT NOT NULL,
    header_value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mail_raw_mime (
    message_id TEXT PRIMARY KEY REFERENCES mail_messages(id) ON DELETE CASCADE,
    content_blob BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS mail_message_parts (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL REFERENCES mail_messages(id) ON DELETE CASCADE,
    parent_part_id TEXT REFERENCES mail_message_parts(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    part_path TEXT NOT NULL,
    content_type TEXT,
    content_disposition TEXT,
    charset TEXT,
    filename TEXT,
    content_id TEXT,
    content_location TEXT,
    transfer_encoding TEXT,
    is_container INTEGER NOT NULL DEFAULT 0 CHECK (is_container IN (0, 1)),
    is_body INTEGER NOT NULL DEFAULT 0 CHECK (is_body IN (0, 1)),
    is_attachment INTEGER NOT NULL DEFAULT 0 CHECK (is_attachment IN (0, 1)),
    is_inline INTEGER NOT NULL DEFAULT 0 CHECK (is_inline IN (0, 1)),
    is_embedded_message INTEGER NOT NULL DEFAULT 0 CHECK (is_embedded_message IN (0, 1)),
    size_bytes INTEGER,
    sha256 TEXT,
    text_content TEXT,
    binary_content BLOB,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS mail_message_metadata (
    message_id TEXT NOT NULL REFERENCES mail_messages(id) ON DELETE CASCADE,
    metadata_key TEXT NOT NULL,
    value_text TEXT NOT NULL,
    PRIMARY KEY (message_id, metadata_key)
);

CREATE TABLE IF NOT EXISTS mail_source_cursors (
    source_id TEXT NOT NULL REFERENCES mail_sources(id) ON DELETE CASCADE,
    cursor_key TEXT NOT NULL,
    cursor_value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source_id, cursor_key)
);

CREATE TABLE IF NOT EXISTS mail_search_documents (
    message_id TEXT PRIMARY KEY REFERENCES mail_messages(id) ON DELETE CASCADE,
    subject TEXT,
    body_text TEXT,
    from_text TEXT,
    to_text TEXT,
    cc_text TEXT,
    bcc_text TEXT,
    metadata_text TEXT,
    search_text TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE VIRTUAL TABLE IF NOT EXISTS mail_search_fts USING fts5(
    message_id UNINDEXED,
    subject,
    body_text,
    from_text,
    to_text,
    cc_text,
    bcc_text,
    metadata_text,
    search_text
);

CREATE INDEX IF NOT EXISTS idx_mail_sources_type_uri
    ON mail_sources(source_type, source_uri);
CREATE INDEX IF NOT EXISTS idx_mail_messages_source_message_id
    ON mail_messages(source_id, source_message_id);
CREATE INDEX IF NOT EXISTS idx_mail_messages_internet_message_id
    ON mail_messages(internet_message_id);
CREATE INDEX IF NOT EXISTS idx_mail_messages_sent_at
    ON mail_messages(sent_at);
CREATE INDEX IF NOT EXISTS idx_mail_addresses_email
    ON mail_message_addresses(email_address);
CREATE INDEX IF NOT EXISTS idx_mail_addresses_role
    ON mail_message_addresses(message_id, role);
CREATE INDEX IF NOT EXISTS idx_mail_headers_name
    ON mail_message_headers(header_name);
CREATE INDEX IF NOT EXISTS idx_mail_parts_message
    ON mail_message_parts(message_id, part_path);
CREATE INDEX IF NOT EXISTS idx_mail_parts_sha256
    ON mail_message_parts(sha256);
CREATE INDEX IF NOT EXISTS idx_mail_parts_content_id
    ON mail_message_parts(content_id);
CREATE INDEX IF NOT EXISTS idx_mail_parts_filename
    ON mail_message_parts(filename);
