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

## Remaining

- Add POP3 import.
- Select and prototype Microsoft Graph / Exchange connector path.
- Replace direct profile-settings password storage with keychain/encrypted secret references before production credentials are used.
