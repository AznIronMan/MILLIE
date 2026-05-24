# IMAP Read-Only Sync MVP

Status: COMPLETED

## Goal

Add the first live mail connector path with read-only IMAP sync into the active profile database.

## Completed

- Added profile-scoped IMAP source config storage in the active `.settings` SQLite file.
- Added `source_sync_states` migration for per-source/per-folder incremental cursors.
- Added read-only IMAP sync through Python `imaplib`, using folder UID searches and RFC822 fetches.
- Reused the raw-MIME parser, dedupe, blob storage, mailboxes, import jobs, and import errors.
- Added CLI commands for saving, listing, and syncing IMAP sources.
- Added API endpoints for listing/saving/syncing IMAP sources.
- Added web sidebar controls to save IMAP sources and run syncs.
- Added unit coverage with a fake IMAP client that verifies incremental UID sync.
- Documented scope, commands, API, and the current dev-only credential-storage caveat.

## Verification

- Python unit test coverage includes first sync plus incremental sync with a new UID.
- Web build verifies TypeScript integration.

## Notes

Passwords/app passwords are stored directly in the profile settings SQLite file in this development slice. Production use should wait for keychain or encrypted secret storage.
