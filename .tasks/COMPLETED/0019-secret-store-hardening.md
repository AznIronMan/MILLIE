# Secret Store Hardening

Status: COMPLETED

## Goal

Move connector credentials out of normal IMAP source config records before using real mail accounts.

## Completed

- Added a secret manager with `auto`, `keychain`, and `local-settings` backends.
- Added macOS Keychain support through the system `security` command.
- Added a profile-local secret fallback for deterministic development and tests.
- Changed IMAP source configs to persist `auth_ref` values instead of raw passwords.
- Added legacy IMAP password migration for older `imap.sources.v1` entries.
- Added CLI commands for secret backend status and IMAP secret migration.
- Added API support for IMAP secret migration and secret backend health status.
- Updated the web IMAP source row to show the secret backend.
- Added tests for secret-reference saving, lookup, and legacy migration.

## Verification

- Python unit tests cover local secret storage and legacy IMAP password migration.
- TypeScript build verifies the updated web source type and UI rendering.

## Notes

The local settings backend remains a development fallback and should still be treated as sensitive. macOS Keychain is preferred automatically when available.
