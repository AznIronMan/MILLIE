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
