# Canonical Data Model Draft

This is a planning draft plus notes about the first implemented SQLite schema. The schema will continue to evolve through migrations.

## Model Shape

MILLIE should expose a convenient single message object through the API, but store mail in normalized relational tables internally.

Email naturally contains one-to-many relationships:

- One message can have many headers.
- One message can have many recipients.
- One message can appear in many folders or labels.
- One message can have many attachments.
- One imported item can have many source identifiers over time.
- One export job can produce many output artifacts and warnings.

## Core Tables

### sources

Represents an import source or live account.

Fields to consider:

- `id`
- `kind`
- `display_name`
- `provider`
- `source_uri`
- `auth_ref`
- `created_at`
- `last_sync_at`
- `status`

### mailboxes

Represents folders, labels, or mailbox paths.

Fields to consider:

- `id`
- `source_id`
- `parent_id`
- `path`
- `display_name`
- `role`
- `created_at`

### messages

Represents the canonical message.

Fields to consider:

- `id`
- `stable_id`
- `source_id`
- `source_message_id`
- `internet_message_id`
- `subject`
- `sent_at`
- `received_at`
- `internal_date`
- `from_address_id`
- `reply_to_address_id`
- `in_reply_to`
- `references_raw`
- `conversation_id`
- `body_text`
- `body_html_ref`
- `body_sanitized_html_ref`
- `raw_message_ref`
- `content_hash`
- `size_bytes`
- `created_at`
- `updated_at`

### addresses

Represents normalized email identities.

Fields to consider:

- `id`
- `email`
- `normalized_email`
- `display_name`
- `first_seen_at`
- `last_seen_at`

### message_addresses

Represents recipients and sender-like roles.

Fields to consider:

- `message_id`
- `address_id`
- `role`
- `display_name_snapshot`
- `ordinal`

Roles should include at least `from`, `to`, `cc`, `bcc`, `reply_to`, and `sender`.

### message_mailboxes

Maps messages to folders or labels.

Fields to consider:

- `message_id`
- `mailbox_id`
- `source_uid`
- `flags_json`
- `labels_json`
- `seen_at_source`

### attachments

Represents attachment metadata and storage references.

Fields to consider:

- `id`
- `message_id`
- `filename`
- `mime_type`
- `content_id`
- `disposition`
- `size_bytes`
- `content_hash`
- `storage_ref`
- `is_inline`
- `created_at`

## HTML Rendering Strategy

Raw HTML bodies are preserved as blobs through `body_html_ref`. The viewer uses `body_sanitized_html_ref`, which is generated at import time for new messages and can be lazily generated for older imported messages. Sanitized HTML removes active content, event attributes, embedded forms, scripts, styles, iframes, and remote image loading.

### headers

Preserves raw headers.

Fields to consider:

- `message_id`
- `name`
- `value`
- `ordinal`

### import_jobs

Tracks import and sync work.

Fields to consider:

- `id`
- `source_id`
- `kind`
- `status`
- `started_at`
- `finished_at`
- `message_count`
- `new_message_count`
- `duplicate_count`
- `error_count`
- `options_json`

### import_errors

Records recoverable failures.

Fields to consider:

- `id`
- `import_job_id`
- `source_item_ref`
- `severity`
- `message`
- `detail_json`
- `created_at`

### source_sync_states

Tracks incremental connector state by source and scope.

Fields to consider:

- `source_id`
- `scope`
- `state_json`
- `updated_at`

IMAP scopes use `folder:{folder_path}` and store `uidvalidity`, `last_uid`, and latest-run recovery metadata such as failed UIDs.
POP uses `maildrop` and stores seen UIDLs plus latest failed UIDLs.
Graph scopes use `folder:{graph_folder_id}` and store delta/next links plus latest partial-run metadata.

### export_jobs

Tracks export work.

Fields to consider:

- `id`
- `target_profile`
- `format`
- `status`
- `started_at`
- `finished_at`
- `message_count`
- `error_count`
- `warning_count`
- `output_root`
- `options_json`
- `manifest_ref`

### export_items

Maps canonical messages to generated export artifacts.

Fields to consider:

- `id`
- `export_job_id`
- `message_id`
- `mailbox_id`
- `output_path`
- `output_hash`
- `format`
- `status`
- `warning_json`

### blobs

Optional content-addressed storage table for raw messages, HTML bodies, sanitized bodies, and attachments.

Fields to consider:

- `id`
- `content_hash`
- `kind`
- `mime_type`
- `size_bytes`
- `storage_ref`
- `created_at`

## Deduplication Strategy

Do not rely only on the `Message-ID` header. It is useful but not always present or globally trustworthy.

Use a layered strategy:

- Source-specific immutable ID, when available
- `Message-ID`
- Normalized subject/sender/date/size hints
- Raw message content hash
- Attachment hashes

The first implemented dedupe layer uses the raw MIME content hash as `messages.stable_id`. Re-importing the same raw message increments duplicate accounting on the import job instead of creating another canonical message. If the same raw message appears in another source or folder, MILLIE can still add source/mailbox provenance through `message_mailboxes`.

Keep enough provenance to explain why two items were or were not deduplicated. Future dedupe layers can add fuzzy matching for messages that differ only by transport headers, client-added metadata, or archive-specific wrapping.

## Export Strategy

Exports should be raw-message-first.

If the original raw MIME is present, write that content back out for `.eml`, `mbox`, and `maildir` exports. This gives the best chance of preserving original headers, body structure, attachments, inline images, signatures, and client-specific metadata.

If raw MIME is unavailable, reconstruct from canonical fields and clearly record that in the export manifest.

Target-specific export profiles should define:

- Folder and filename mapping
- Timestamp preservation rules
- Supported flag mappings
- Label/category behavior
- Attachment handling
- Unsupported metadata warnings
- Import instructions for the target client
