# Prototype Live Connectors

Status: PENDING

## Goal

Add initial live mail import/sync support.

## Acceptance Criteria

- IMAP connector can import a mailbox.
- POP3 connector can import messages.
- Microsoft Graph / Exchange connector path is selected.
- OAuth/app-password secret handling is designed before storing real credentials.
- Incremental sync state is tracked per source.

## Progress

- Initial read-only IMAP sync MVP is implemented with password/app-password config, active-profile storage, and per-folder UID cursor tracking.
- CLI, API, web controls, and unit coverage exist for the IMAP path.
- IMAP source configs now store credential references instead of raw passwords, with macOS Keychain support and a local development fallback.
- Saved IMAP sources can discover folders, apply selected folder lists, sync, and be deleted through CLI, API, and web controls.
- IMAP hardening now includes generic/Gmail provider presets, Gmail host normalization, one-off selected-folder sync, and capture of IMAP flags/internal dates.

## Remaining

- Add POP3 import.
- Select and prototype Microsoft Graph / Exchange connector path.
- Add OAuth/provider credential flows and broader provider presets.
