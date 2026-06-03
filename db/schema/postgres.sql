CREATE TABLE IF NOT EXISTS mail_sources (
    id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL CHECK (
        source_type IN ('imap', 'exchange_imap_oauth', 'pst')
    ),
    display_name TEXT,
    source_uri TEXT NOT NULL,
    auth_mode TEXT,
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_type, source_uri)
);

CREATE TABLE IF NOT EXISTS mail_import_jobs (
    id TEXT PRIMARY KEY,
    source_id TEXT REFERENCES mail_sources(id) ON DELETE SET NULL,
    status TEXT NOT NULL CHECK (
        status IN ('planned', 'running', 'completed', 'failed', 'cancelled')
    ),
    mode TEXT NOT NULL DEFAULT 'manual',
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    error_message TEXT,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS mail_folders (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES mail_sources(id) ON DELETE CASCADE,
    parent_id TEXT REFERENCES mail_folders(id) ON DELETE SET NULL,
    folder_path TEXT NOT NULL,
    display_name TEXT,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
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
    sent_at TIMESTAMPTZ,
    received_at TIMESTAMPTZ,
    date_header TEXT,
    timezone_offset_minutes INTEGER,
    body_text TEXT,
    body_html TEXT,
    body_preview TEXT,
    raw_mime_sha256 TEXT NOT NULL,
    raw_mime_size_bytes BIGINT NOT NULL,
    normalized_body_sha256 TEXT,
    attachment_set_sha256 TEXT,
    normalized_message_fingerprint TEXT,
    has_attachments BOOLEAN NOT NULL DEFAULT FALSE,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_id, source_message_id)
);

ALTER TABLE mail_messages
    ADD COLUMN IF NOT EXISTS normalized_body_sha256 TEXT;
ALTER TABLE mail_messages
    ADD COLUMN IF NOT EXISTS attachment_set_sha256 TEXT;
ALTER TABLE mail_messages
    ADD COLUMN IF NOT EXISTS normalized_message_fingerprint TEXT;

CREATE INDEX IF NOT EXISTS idx_mail_messages_raw_mime_sha256
    ON mail_messages(raw_mime_sha256);
CREATE INDEX IF NOT EXISTS idx_mail_messages_normalized_body_sha256
    ON mail_messages(normalized_body_sha256);
CREATE INDEX IF NOT EXISTS idx_mail_messages_attachment_set_sha256
    ON mail_messages(attachment_set_sha256);
CREATE INDEX IF NOT EXISTS idx_mail_messages_normalized_fingerprint
    ON mail_messages(normalized_message_fingerprint);

CREATE TABLE IF NOT EXISTS mail_message_folders (
    message_id TEXT NOT NULL REFERENCES mail_messages(id) ON DELETE CASCADE,
    folder_id TEXT NOT NULL REFERENCES mail_folders(id) ON DELETE CASCADE,
    PRIMARY KEY (message_id, folder_id)
);

CREATE TABLE IF NOT EXISTS mail_source_message_aliases (
    source_id TEXT NOT NULL REFERENCES mail_sources(id) ON DELETE CASCADE,
    source_message_id TEXT NOT NULL,
    message_id TEXT NOT NULL REFERENCES mail_messages(id) ON DELETE CASCADE,
    raw_mime_sha256 TEXT NOT NULL,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source_id, source_message_id)
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
    id BIGSERIAL PRIMARY KEY,
    message_id TEXT NOT NULL REFERENCES mail_messages(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    header_name TEXT NOT NULL,
    header_value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mail_raw_mime (
    message_id TEXT PRIMARY KEY REFERENCES mail_messages(id) ON DELETE CASCADE,
    content_blob BYTEA NOT NULL
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
    is_container BOOLEAN NOT NULL DEFAULT FALSE,
    is_body BOOLEAN NOT NULL DEFAULT FALSE,
    is_attachment BOOLEAN NOT NULL DEFAULT FALSE,
    is_inline BOOLEAN NOT NULL DEFAULT FALSE,
    is_embedded_message BOOLEAN NOT NULL DEFAULT FALSE,
    size_bytes BIGINT,
    sha256 TEXT,
    text_content TEXT,
    binary_content BYTEA,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
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
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source_id, cursor_key)
);

CREATE TABLE IF NOT EXISTS mail_remote_purge_manifests (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'prepared' CHECK (
        status IN (
            'prepared', 'confirmed', 'provider_moved_to_trash',
            'provider_purged', 'cancelled', 'failed'
        )
    ),
    action TEXT NOT NULL DEFAULT 'move_to_trash' CHECK (
        action IN ('move_to_trash', 'delete', 'hard_delete')
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    prepared_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    confirmed_at TIMESTAMPTZ,
    executed_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    total_messages INTEGER NOT NULL DEFAULT 0,
    total_source_uids INTEGER NOT NULL DEFAULT 0,
    missing_source_uids INTEGER NOT NULL DEFAULT 0,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS mail_remote_purge_manifest_messages (
    manifest_id TEXT NOT NULL REFERENCES mail_remote_purge_manifests(id) ON DELETE CASCADE,
    message_id TEXT NOT NULL REFERENCES mail_messages(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL REFERENCES mail_sources(id) ON DELETE CASCADE,
    source_message_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    source_account TEXT NOT NULL,
    source_folder TEXT NOT NULL,
    imap_uidvalidity TEXT,
    imap_uid TEXT,
    action TEXT NOT NULL DEFAULT 'move_to_trash',
    protected_in_millie BOOLEAN NOT NULL DEFAULT TRUE,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (manifest_id, source_id, source_message_id)
);

CREATE INDEX IF NOT EXISTS idx_mail_remote_purge_messages_manifest
    ON mail_remote_purge_manifest_messages(manifest_id);
CREATE INDEX IF NOT EXISTS idx_mail_remote_purge_messages_message
    ON mail_remote_purge_manifest_messages(message_id);
CREATE INDEX IF NOT EXISTS idx_mail_remote_purge_messages_source
    ON mail_remote_purge_manifest_messages(source_id, source_message_id);

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
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_mail_sources_type_uri
    ON mail_sources(source_type, source_uri);
CREATE INDEX IF NOT EXISTS idx_mail_messages_source_message_id
    ON mail_messages(source_id, source_message_id);
CREATE INDEX IF NOT EXISTS idx_mail_source_aliases_message
    ON mail_source_message_aliases(message_id);
CREATE INDEX IF NOT EXISTS idx_mail_source_aliases_raw_mime_sha256
    ON mail_source_message_aliases(raw_mime_sha256);
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
CREATE INDEX IF NOT EXISTS idx_mail_search_documents_fts
    ON mail_search_documents
    USING GIN (to_tsvector('simple', coalesce(search_text, '')));

CREATE TABLE IF NOT EXISTS millie_identities (
    id TEXT PRIMARY KEY,
    login_address TEXT NOT NULL,
    login_local_part TEXT NOT NULL,
    login_domain TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active' CHECK (
        status IN ('pending', 'active', 'disabled', 'locked')
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_millie_identities_login
    ON millie_identities (lower(login_address));

CREATE TABLE IF NOT EXISTS millie_identity_credentials (
    id TEXT PRIMARY KEY,
    identity_id TEXT NOT NULL REFERENCES millie_identities(id) ON DELETE CASCADE,
    credential_type TEXT NOT NULL CHECK (
        credential_type IN ('password_pbkdf2_sha256', 'app_password_pbkdf2_sha256')
    ),
    credential_label TEXT NOT NULL DEFAULT '',
    secret_hash TEXT NOT NULL,
    secret_hint TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ,
    disabled_at TIMESTAMPTZ,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_millie_credentials_identity
    ON millie_identity_credentials(identity_id);

CREATE TABLE IF NOT EXISTS millie_auth_sessions (
    id TEXT PRIMARY KEY,
    identity_id TEXT NOT NULL REFERENCES millie_identities(id) ON DELETE CASCADE,
    session_type TEXT NOT NULL CHECK (
        session_type IN ('web', 'api', 'imap')
    ),
    token_hash TEXT NOT NULL,
    client_name TEXT,
    remote_address TEXT,
    user_agent TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_millie_auth_sessions_token
    ON millie_auth_sessions(token_hash);
CREATE INDEX IF NOT EXISTS idx_millie_auth_sessions_identity
    ON millie_auth_sessions(identity_id, expires_at);

CREATE TABLE IF NOT EXISTS millie_mailboxes (
    id TEXT PRIMARY KEY,
    owner_identity_id TEXT NOT NULL REFERENCES millie_identities(id) ON DELETE CASCADE,
    mailbox_address TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    is_primary BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_millie_mailboxes_address
    ON millie_mailboxes (lower(mailbox_address));
CREATE INDEX IF NOT EXISTS idx_millie_mailboxes_owner
    ON millie_mailboxes(owner_identity_id);

CREATE TABLE IF NOT EXISTS millie_mailbox_folders (
    id TEXT PRIMARY KEY,
    mailbox_id TEXT NOT NULL REFERENCES millie_mailboxes(id) ON DELETE CASCADE,
    parent_id TEXT REFERENCES millie_mailbox_folders(id) ON DELETE SET NULL,
    folder_path TEXT NOT NULL,
    display_name TEXT NOT NULL,
    folder_role TEXT NOT NULL DEFAULT 'custom' CHECK (
        folder_role IN (
            'inbox', 'sent', 'archive', 'drafts', 'trash', 'junk',
            'all_mail', 'source_root', 'source', 'custom'
        )
    ),
    special_use TEXT,
    uid_validity BIGINT NOT NULL DEFAULT 1,
    uid_next BIGINT NOT NULL DEFAULT 1,
    selectable BOOLEAN NOT NULL DEFAULT TRUE,
    subscribed BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (mailbox_id, folder_path)
);

CREATE INDEX IF NOT EXISTS idx_millie_mailbox_folders_mailbox
    ON millie_mailbox_folders(mailbox_id, folder_path);

CREATE TABLE IF NOT EXISTS millie_source_mailbox_bindings (
    id TEXT PRIMARY KEY,
    mailbox_id TEXT NOT NULL REFERENCES millie_mailboxes(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL REFERENCES mail_sources(id) ON DELETE CASCADE,
    source_folder_id TEXT REFERENCES mail_folders(id) ON DELETE SET NULL,
    target_folder_id TEXT NOT NULL REFERENCES millie_mailbox_folders(id) ON DELETE CASCADE,
    mode TEXT NOT NULL DEFAULT 'one_way_copy' CHECK (
        mode IN ('one_way_copy', 'manual_import')
    ),
    preserve_source_folders BOOLEAN NOT NULL DEFAULT TRUE,
    status TEXT NOT NULL DEFAULT 'planned' CHECK (
        status IN ('planned', 'active', 'paused', 'failed', 'disabled')
    ),
    last_planned_at TIMESTAMPTZ,
    last_synced_at TIMESTAMPTZ,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_millie_bindings_mailbox
    ON millie_source_mailbox_bindings(mailbox_id);
CREATE INDEX IF NOT EXISTS idx_millie_bindings_source
    ON millie_source_mailbox_bindings(source_id, source_folder_id);

CREATE TABLE IF NOT EXISTS millie_mailbox_messages (
    id TEXT PRIMARY KEY,
    mailbox_id TEXT NOT NULL REFERENCES millie_mailboxes(id) ON DELETE CASCADE,
    folder_id TEXT NOT NULL REFERENCES millie_mailbox_folders(id) ON DELETE CASCADE,
    message_id TEXT NOT NULL REFERENCES mail_messages(id) ON DELETE CASCADE,
    binding_id TEXT REFERENCES millie_source_mailbox_bindings(id) ON DELETE SET NULL,
    imap_uid BIGINT NOT NULL,
    internal_date TIMESTAMPTZ,
    flags TEXT[] NOT NULL DEFAULT '{}'::text[],
    keywords TEXT[] NOT NULL DEFAULT '{}'::text[],
    is_seen BOOLEAN NOT NULL DEFAULT FALSE,
    is_answered BOOLEAN NOT NULL DEFAULT FALSE,
    is_flagged BOOLEAN NOT NULL DEFAULT FALSE,
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    is_draft BOOLEAN NOT NULL DEFAULT FALSE,
    is_recent BOOLEAN NOT NULL DEFAULT FALSE,
    is_expunged BOOLEAN NOT NULL DEFAULT FALSE,
    copied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (folder_id, imap_uid),
    UNIQUE (folder_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_millie_mailbox_messages_mailbox
    ON millie_mailbox_messages(mailbox_id, folder_id, imap_uid);
CREATE INDEX IF NOT EXISTS idx_millie_mailbox_messages_message
    ON millie_mailbox_messages(message_id);
CREATE INDEX IF NOT EXISTS idx_millie_mailbox_messages_flags
    ON millie_mailbox_messages USING GIN(flags);

CREATE TABLE IF NOT EXISTS millie_protocol_clients (
    id TEXT PRIMARY KEY,
    identity_id TEXT NOT NULL REFERENCES millie_identities(id) ON DELETE CASCADE,
    protocol TEXT NOT NULL CHECK (
        protocol IN ('imap', 'webmail', 'api')
    ),
    client_name TEXT NOT NULL DEFAULT '',
    app_password_required BOOLEAN NOT NULL DEFAULT TRUE,
    last_seen_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_millie_protocol_clients_identity
    ON millie_protocol_clients(identity_id, protocol);

CREATE TABLE IF NOT EXISTS millie_automation_runs (
    id TEXT PRIMARY KEY,
    run_type TEXT NOT NULL CHECK (
        run_type IN (
            'sort_observe', 'sort_review', 'live_upkeep', 'retention_scan',
            'unsubscribe_scan', 'manual_review', 'system'
        )
    ),
    automation_level TEXT NOT NULL DEFAULT 'observe' CHECK (
        automation_level IN ('observe', 'review', 'auto_internal', 'provider_write')
    ),
    status TEXT NOT NULL DEFAULT 'planned' CHECK (
        status IN ('planned', 'running', 'completed', 'failed', 'cancelled')
    ),
    trigger_source TEXT NOT NULL DEFAULT 'cli' CHECK (
        trigger_source IN ('cli', 'webmail', 'imap', 'scheduler', 'system')
    ),
    model_provider TEXT,
    model_name TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    messages_scanned INTEGER NOT NULL DEFAULT 0 CHECK (messages_scanned >= 0),
    suggestions_created INTEGER NOT NULL DEFAULT 0 CHECK (suggestions_created >= 0),
    actions_applied INTEGER NOT NULL DEFAULT 0 CHECK (actions_applied >= 0),
    error_message TEXT,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_millie_automation_runs_status
    ON millie_automation_runs(status, run_type, created_at);

CREATE TABLE IF NOT EXISTS millie_brain_rules (
    id TEXT PRIMARY KEY,
    rule_name TEXT NOT NULL,
    rule_type TEXT NOT NULL CHECK (
        rule_type IN (
            'folder', 'tag', 'spam', 'trash', 'unsubscribe',
            'retention', 'priority', 'custom'
        )
    ),
    rule_source TEXT NOT NULL DEFAULT 'system' CHECK (
        rule_source IN ('system', 'user', 'heuristic', 'llm')
    ),
    status TEXT NOT NULL DEFAULT 'proposed' CHECK (
        status IN ('proposed', 'active', 'disabled', 'superseded', 'retired')
    ),
    automation_level TEXT NOT NULL DEFAULT 'observe' CHECK (
        automation_level IN ('observe', 'review', 'auto_internal', 'provider_write')
    ),
    priority INTEGER NOT NULL DEFAULT 100,
    condition_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    action_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    confidence NUMERIC(5,4) NOT NULL DEFAULT 0 CHECK (
        confidence >= 0 AND confidence <= 1
    ),
    evidence_count INTEGER NOT NULL DEFAULT 0 CHECK (evidence_count >= 0),
    positive_feedback_count INTEGER NOT NULL DEFAULT 0 CHECK (positive_feedback_count >= 0),
    negative_feedback_count INTEGER NOT NULL DEFAULT 0 CHECK (negative_feedback_count >= 0),
    created_by_identity_id TEXT REFERENCES millie_identities(id) ON DELETE SET NULL,
    created_by_run_id TEXT REFERENCES millie_automation_runs(id) ON DELETE SET NULL,
    last_matched_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_millie_brain_rules_status_type
    ON millie_brain_rules(status, rule_type, priority);
CREATE INDEX IF NOT EXISTS idx_millie_brain_rules_condition
    ON millie_brain_rules USING GIN(condition_json);
CREATE INDEX IF NOT EXISTS idx_millie_brain_rules_action
    ON millie_brain_rules USING GIN(action_json);

CREATE TABLE IF NOT EXISTS millie_message_classifications (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL REFERENCES mail_messages(id) ON DELETE CASCADE,
    rule_id TEXT REFERENCES millie_brain_rules(id) ON DELETE SET NULL,
    run_id TEXT REFERENCES millie_automation_runs(id) ON DELETE SET NULL,
    classifier_type TEXT NOT NULL CHECK (
        classifier_type IN ('heuristic', 'rule', 'llm', 'user', 'system')
    ),
    classifier_version TEXT NOT NULL DEFAULT '',
    classification_kind TEXT NOT NULL CHECK (
        classification_kind IN (
            'folder', 'tag', 'spam', 'trash', 'unsubscribe',
            'priority', 'retention', 'custom'
        )
    ),
    classification_value TEXT NOT NULL,
    target_folder_path TEXT,
    target_tags TEXT[] NOT NULL DEFAULT '{}'::text[],
    status TEXT NOT NULL DEFAULT 'proposed' CHECK (
        status IN ('proposed', 'reviewed', 'approved', 'rejected', 'applied', 'expired', 'superseded')
    ),
    automation_level TEXT NOT NULL DEFAULT 'observe' CHECK (
        automation_level IN ('observe', 'review', 'auto_internal', 'provider_write')
    ),
    confidence NUMERIC(5,4) NOT NULL DEFAULT 0 CHECK (
        confidence >= 0 AND confidence <= 1
    ),
    reason_text TEXT,
    evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    reviewed_by_identity_id TEXT REFERENCES millie_identities(id) ON DELETE SET NULL,
    reviewed_at TIMESTAMPTZ,
    applied_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_millie_classifications_message
    ON millie_message_classifications(message_id, status);
CREATE INDEX IF NOT EXISTS idx_millie_classifications_rule
    ON millie_message_classifications(rule_id);
CREATE INDEX IF NOT EXISTS idx_millie_classifications_run
    ON millie_message_classifications(run_id);
CREATE INDEX IF NOT EXISTS idx_millie_classifications_kind_value
    ON millie_message_classifications(classification_kind, classification_value);
CREATE INDEX IF NOT EXISTS idx_millie_classifications_target_folder
    ON millie_message_classifications(target_folder_path)
    WHERE target_folder_path IS NOT NULL;

CREATE TABLE IF NOT EXISTS millie_user_feedback_events (
    id TEXT PRIMARY KEY,
    identity_id TEXT REFERENCES millie_identities(id) ON DELETE SET NULL,
    message_id TEXT REFERENCES mail_messages(id) ON DELETE CASCADE,
    classification_id TEXT REFERENCES millie_message_classifications(id) ON DELETE SET NULL,
    rule_id TEXT REFERENCES millie_brain_rules(id) ON DELETE SET NULL,
    feedback_type TEXT NOT NULL CHECK (
        feedback_type IN (
            'approve_classification', 'reject_classification', 'move_message',
            'mark_spam', 'mark_not_spam', 'mark_trash', 'mark_not_trash',
            'create_rule', 'disable_rule', 'retention_override',
            'unsubscribe_approve', 'unsubscribe_reject', 'custom'
        )
    ),
    feedback_source TEXT NOT NULL DEFAULT 'webmail' CHECK (
        feedback_source IN ('webmail', 'imap', 'cli', 'system')
    ),
    previous_value_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    new_value_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_millie_feedback_message
    ON millie_user_feedback_events(message_id, created_at);
CREATE INDEX IF NOT EXISTS idx_millie_feedback_classification
    ON millie_user_feedback_events(classification_id);
CREATE INDEX IF NOT EXISTS idx_millie_feedback_rule
    ON millie_user_feedback_events(rule_id);
CREATE INDEX IF NOT EXISTS idx_millie_feedback_type
    ON millie_user_feedback_events(feedback_type, created_at);

CREATE TABLE IF NOT EXISTS millie_retention_policies (
    id TEXT PRIMARY KEY,
    policy_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'proposed' CHECK (
        status IN ('proposed', 'active', 'disabled', 'retired')
    ),
    target_kind TEXT NOT NULL CHECK (
        target_kind IN ('classification', 'folder', 'tag', 'source', 'account', 'custom')
    ),
    target_value TEXT NOT NULL DEFAULT '',
    hold_duration INTERVAL,
    action TEXT NOT NULL DEFAULT 'no_action' CHECK (
        action IN (
            'no_action', 'hide_from_default_views',
            'expire_internal_copy', 'delete_internal_copy'
        )
    ),
    requires_review BOOLEAN NOT NULL DEFAULT TRUE,
    condition_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by_identity_id TEXT REFERENCES millie_identities(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_millie_retention_status_target
    ON millie_retention_policies(status, target_kind, target_value);
CREATE INDEX IF NOT EXISTS idx_millie_retention_condition
    ON millie_retention_policies USING GIN(condition_json);

CREATE TABLE IF NOT EXISTS millie_unsubscribe_candidates (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL REFERENCES mail_messages(id) ON DELETE CASCADE,
    run_id TEXT REFERENCES millie_automation_runs(id) ON DELETE SET NULL,
    candidate_type TEXT NOT NULL CHECK (
        candidate_type IN ('header_mailto', 'header_url', 'body_url', 'provider_api', 'browser')
    ),
    source_header TEXT,
    unsubscribe_url TEXT,
    unsubscribe_mailto TEXT,
    status TEXT NOT NULL DEFAULT 'detected' CHECK (
        status IN (
            'detected', 'review_required', 'approved', 'attempting',
            'succeeded', 'failed', 'ignored', 'unsafe'
        )
    ),
    confidence NUMERIC(5,4) NOT NULL DEFAULT 0 CHECK (
        confidence >= 0 AND confidence <= 1
    ),
    requires_browser BOOLEAN NOT NULL DEFAULT FALSE,
    approved_by_identity_id TEXT REFERENCES millie_identities(id) ON DELETE SET NULL,
    discovered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_at TIMESTAMPTZ,
    attempted_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    result_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_message TEXT,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_millie_unsubscribe_message
    ON millie_unsubscribe_candidates(message_id, status);
CREATE INDEX IF NOT EXISTS idx_millie_unsubscribe_run
    ON millie_unsubscribe_candidates(run_id);
CREATE INDEX IF NOT EXISTS idx_millie_unsubscribe_status
    ON millie_unsubscribe_candidates(status, discovered_at);

CREATE TABLE IF NOT EXISTS millie_automation_audit_log (
    id TEXT PRIMARY KEY,
    run_id TEXT REFERENCES millie_automation_runs(id) ON DELETE SET NULL,
    identity_id TEXT REFERENCES millie_identities(id) ON DELETE SET NULL,
    message_id TEXT REFERENCES mail_messages(id) ON DELETE SET NULL,
    classification_id TEXT REFERENCES millie_message_classifications(id) ON DELETE SET NULL,
    rule_id TEXT REFERENCES millie_brain_rules(id) ON DELETE SET NULL,
    retention_policy_id TEXT REFERENCES millie_retention_policies(id) ON DELETE SET NULL,
    unsubscribe_candidate_id TEXT REFERENCES millie_unsubscribe_candidates(id) ON DELETE SET NULL,
    action_type TEXT NOT NULL CHECK (
        action_type IN (
            'suggest_classification', 'approve_classification', 'reject_classification',
            'create_rule', 'disable_rule', 'unsubscribe_approve', 'unsubscribe_reject',
            'apply_internal_move', 'apply_internal_tag', 'block_provider_write',
            'retention_evaluate', 'retention_apply', 'unsubscribe_detect',
            'unsubscribe_attempt', 'provider_purge_manifest', 'custom'
        )
    ),
    automation_level TEXT NOT NULL DEFAULT 'observe' CHECK (
        automation_level IN ('observe', 'review', 'auto_internal', 'provider_write')
    ),
    status TEXT NOT NULL DEFAULT 'recorded' CHECK (
        status IN ('recorded', 'blocked', 'applied', 'failed')
    ),
    before_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    after_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_millie_audit_run
    ON millie_automation_audit_log(run_id, created_at);
CREATE INDEX IF NOT EXISTS idx_millie_audit_message
    ON millie_automation_audit_log(message_id, created_at);
CREATE INDEX IF NOT EXISTS idx_millie_audit_action
    ON millie_automation_audit_log(action_type, status, created_at);

ALTER TABLE millie_automation_audit_log
    DROP CONSTRAINT IF EXISTS millie_automation_audit_log_action_type_check;
ALTER TABLE millie_automation_audit_log
    ADD CONSTRAINT millie_automation_audit_log_action_type_check CHECK (
        action_type IN (
            'suggest_classification', 'approve_classification', 'reject_classification',
            'create_rule', 'disable_rule', 'unsubscribe_approve', 'unsubscribe_reject',
            'apply_internal_move', 'apply_internal_tag', 'block_provider_write',
            'retention_evaluate', 'retention_apply', 'unsubscribe_detect',
            'unsubscribe_attempt', 'provider_purge_manifest', 'custom'
        )
    );

CREATE OR REPLACE VIEW millie_v_mailbox_messages AS
SELECT
    mb.owner_identity_id,
    mb.mailbox_address,
    mf.folder_path,
    mm.imap_uid,
    mm.flags,
    mm.keywords,
    mm.is_seen,
    mm.is_answered,
    mm.is_flagged,
    mm.is_deleted,
    mm.is_draft,
    mm.is_recent,
    mm.is_expunged,
    mm.internal_date,
    m.id AS message_id,
    m.internet_message_id,
    m.subject,
    m.sent_at,
    m.received_at,
    m.body_preview,
    m.has_attachments,
    m.raw_mime_size_bytes,
    m.raw_mime_sha256,
    sd.search_text
FROM millie_mailbox_messages mm
JOIN millie_mailboxes mb ON mb.id = mm.mailbox_id
JOIN millie_mailbox_folders mf ON mf.id = mm.folder_id
JOIN mail_messages m ON m.id = mm.message_id
LEFT JOIN mail_search_documents sd ON sd.message_id = m.id
WHERE mm.is_expunged = FALSE;

CREATE OR REPLACE VIEW millie_v_webmail_threads AS
SELECT
    owner_identity_id,
    mailbox_address,
    coalesce(thread_id, internet_message_id, message_id) AS thread_key,
    min(coalesce(sent_at, received_at, internal_date)) AS first_message_at,
    max(coalesce(sent_at, received_at, internal_date)) AS last_message_at,
    count(*) AS message_count,
    bool_or(has_attachments) AS has_attachments,
    max(subject) AS latest_subject
FROM (
    SELECT
        v.*,
        m.thread_id
    FROM millie_v_mailbox_messages v
    JOIN mail_messages m ON m.id = v.message_id
) threaded
GROUP BY owner_identity_id, mailbox_address, coalesce(thread_id, internet_message_id, message_id);
