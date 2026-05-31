# Changelog

All notable changes to MILLIE will be documented in this file.

## [1.0.0] - 2026-05-31

### Added

- Added root `millie.settings` SQLite3 settings database.
- Added a temporary browser-based settings editor launched by `tmp_settings.sh`.
- Added repeatable IMAP retrieval and SMTP sending account settings in `millie.settings`.
- Added Microsoft Outlook/Exchange OAuth settings for IMAP authorization.
- Added a separate Microsoft OAuth client secret ID setting so the secret value and ID are not conflated.
- Added a temporary Microsoft OAuth callback helper for saving Outlook authorization tokens locally.
- Added a read-only PST probe using libpst/readpst with metadata-only reporting.
- Added dormant mail import pipeline models for PST, IMAP, and Exchange OAuth IMAP sources.
- Added SQLite and PostgreSQL canonical mail storage schemas.
- Added SQLite storage writer coverage for normalized message graphs.
- Added PST password input handling with explicit backend capability reporting.
- Added a dry-run import planner that does not connect, extract, or write data.
- Added AES-256-GCM protection for secret values stored in `millie.settings`.
- Added automatic migration of existing plaintext settings secrets and mail account passwords.
- Updated the Microsoft OAuth helper to read and write encrypted settings secrets.
- Added dormant Postgres identity/authentication tables for MILLIE logins.
- Added dormant Postgres mailbox facade tables and views for IMAP/webmail clients.
- Added a bootstrap SQL planner for identities such as `geon@MILLIE`.
- Added a live sample importer for copying one PST, one IMAP, and one Exchange OAuth message into MILLIE.
- Added a minimal development IMAP listener backed by the Postgres mailbox facade.
- Added a minimal authenticated development SMTP listener for mail client account setup.
- Added customer-facing settings documentation under `docs/`.
- Added IMAP mailbox-copy mutation support for folder changes, `APPEND`, flag updates, copy/move, delete, and expunge.
- Added a dry-run-first bulk PST import tool that separates archives under `Sources/PST/<archive name>/...`.

### Changed

- Stopped tracking local settings and task/private workspace files.
- Updated ignore rules for `.private/`, `.tasks/`, `/data/`, `/logs/`, `*.settings`, and `*.millie`.
- Reset the repository for a fresh 1.0.0 start.
- Archived the previous implementation locally at `.private/archived/version_0.tar.gz`.
- Recreated baseline project documentation, task lanes, and local secret/archive ignore rules.
- Updated the dev IMAP/SMTP listeners so SSL-off ports do not advertise STARTTLS, added sanitized listener diagnostics, and corrected IMAP `BODY.PEEK` fetch responses for stricter mail clients.
- Added a no-auth development webmail view for browsing the current MILLIE mailbox with Gmail, Outlook, and Microsoft 365-inspired themes.
- Added configurable service mailbox domains in `millie.settings`, defaulting to `millie.cnbsk.cloud` with local `MILLIE` aliases for identities such as `geon@millie.cnbsk.cloud`.
- Changed the temporary SMTP listener into a setup-only blackhole shim that accepts any or no SMTP authentication, discards submitted message data, and never relays, stores, queues, or delivers outbound mail from MILLIE.
- Clarified that IMAP client edits mutate only MILLIE's copied mailbox facade and do not write back to original IMAP, Exchange, or PST sources.
