# Build First Runnable Slice

Status: ACTIVE

## Goal

Create the first runnable MILLIE application slice with Python logic, SQLite storage, file import/export, API endpoints, and a TypeScript web client.

## Acceptance Criteria

- SQLite schema initializes.
- `.eml`, `mbox`, and `maildir` import paths exist.
- `.eml`, `mbox`, and `maildir` export paths exist.
- Local API server exposes health, sources, mailboxes, messages, import, and export endpoints.
- TypeScript web client can view mailboxes/messages and trigger import/export jobs.
- Basic tests verify import/export behavior.

## Notes

This is a foundation slice, not the final framework or production security posture.
