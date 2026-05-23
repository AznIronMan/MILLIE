# Build SQLite Schema And Import Core

Status: COMPLETED

## Goal

Create the first working SQLite schema, migration path, and import job pipeline.

## Result

- Added SQLite schema migrations with `schema_migrations` tracking.
- Added normalized tables for sources, mailboxes, messages, addresses, attachments, headers, blobs, import jobs, import errors, export jobs, and export items.
- Added content-addressed raw message, HTML body, and attachment blob storage.
- Added import job success/error tracking.
- Added raw-content-hash deduplication so repeat imports do not duplicate canonical messages.
- Added source/mailbox provenance through `message_mailboxes`.
- Added SQLite FTS5 search over subject, participants, and body text.

## Acceptance Criteria

- SQLite database can be created from migrations.
- Core tables exist for sources, mailboxes, messages, addresses, attachments, headers, import jobs, and import errors.
- Import jobs can record success and recoverable failures.
- Message content hashes and source provenance are stored.
- Raw MIME storage references are preserved when available.
- Basic search index strategy is implemented through SQLite FTS5.

## Verification

- `PYTHONWARNINGS=error PYTHONPATH=src python3 -m unittest discover -s tests`
