# Build SQLite Schema And Import Core

Status: PENDING

## Goal

Create the first working SQLite schema, migration path, and import job pipeline.

## Acceptance Criteria

- SQLite database can be created from migrations.
- Core tables exist for sources, mailboxes, messages, addresses, attachments, headers, import jobs, and import errors.
- Import jobs can record success and recoverable failures.
- Message content hashes and source provenance are stored.
- Raw MIME storage references are preserved when available.
- Basic search index strategy is defined.
