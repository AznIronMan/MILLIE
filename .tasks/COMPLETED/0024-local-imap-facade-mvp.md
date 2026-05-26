# Local IMAP Facade MVP

Status: COMPLETED

## Goal

Expose imported MILLIE mail through a first read-only IMAP service for external mail-client testing.

## Completed

- Added `millie imap-facade` on default port `22143`.
- Added folder listing, mailbox select/examine, status, search, UID search, fetch, and UID fetch support.
- Mapped MILLIE mailbox paths to IMAP folder names and local message ids to stable UIDs.
- Returned raw MIME from the blob store for message fetches.
- Rejected mutating commands to keep the facade read-only.
- Documented setup, scope, mapping, client notes, and security limits.

## Verification

- Unit coverage connects with Python `imaplib`, lists the imported mailbox, selects it, searches, fetches raw MIME, and verifies `STORE` is rejected.
