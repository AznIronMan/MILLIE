# MILLIE Roadmap

## Phase 0: Foundation

- Confirm project acronym and product direction.
- Choose the first implementation stack.
- Define the canonical schema.
- Decide local data paths and secret storage approach.
- Decide export fidelity goals and the first supported export targets.
- Keep git disabled until the owner re-enables it.

## Phase 1: File Import MVP

- Implement SQLite migrations.
- Implement import job tracking.
- Import `.eml`.
- Import `mbox`.
- Import `maildir`.
- Preserve raw headers and source provenance.
- Add deduplication and content hashing.
- Add SQLite FTS search over subject, addresses, and body text.
- Preserve raw MIME where available for future high-fidelity export.

## Phase 2: Export MVP

- Export selected messages or folders to `.eml`.
- Export folders to `mbox`.
- Export folders to `maildir`.
- Generate an export manifest with counts, hashes, warnings, and unsupported metadata.
- Add target profiles for Thunderbird, Evolution, Apple Mail, and generic import workflows.

## Phase 3: Web/API MVP

- Add `/api/v1` endpoints for sources, mailboxes, messages, search, attachments, and import jobs.
- Add export job endpoints.
- Build a practical webmail-style client.
- Include folder navigation, message list, message detail, and search.
- Add HTML email sanitization before rendering.
- Add local authentication before loading real mail.

## Phase 4: Advanced File Import And Export

- Add PST support through an adapter wrapper.
- Add OLM support through an adapter wrapper.
- Investigate OST extraction limits and document unsupported encrypted/cache-only cases.
- Add Thunderbird, Evolution, and Apple Mail profile helpers.
- Investigate reliable PST/OLM writer options before promising direct Outlook-native export.

## Phase 5: Live Connectors

- Add IMAP sync.
- Add POP3 import.
- Add Microsoft Graph / Exchange support.
- Add OAuth and app-password connection flows.
- Track incremental sync cursors and source UIDs.

## Phase 6: Local Mail Server Facade

- Add read-only local IMAP service.
- Map canonical mailboxes/messages into IMAP folders and UIDs.
- Test with Apple Mail, Outlook, Thunderbird, and Evolution.
- Consider write support only after read-only browsing is stable.

## Phase 7: Database Connectors And Hardening

- Add PostgreSQL adapter.
- Add MySQL/MariaDB adapter.
- Evaluate document/NoSQL storage options.
- Add backup/export tooling.
- Harden TLS, auth, audit logs, secrets, and diagnostics.
