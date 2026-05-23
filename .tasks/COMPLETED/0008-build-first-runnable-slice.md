# Build First Runnable Slice

Status: COMPLETED

## Goal

Create the first runnable MILLIE application slice with Python logic, SQLite storage, file import/export, API endpoints, and a TypeScript web client.

## Result

Implemented the first runnable foundation slice:

- SQLite schema and content-addressed blob storage.
- Python CLI for init, serve, import, and export.
- Local HTTP API for health, sources, mailboxes, messages, import, export, and export jobs.
- `.eml`, `.eml` folder, `mbox`, and `maildir` import paths.
- `.eml`, `mbox`, and `maildir` export paths with JSON manifests.
- TypeScript/Vite web client for mailbox navigation, message viewing, import, and export.
- Core Python import/export test.

## Verification

- `PYTHONPATH=src python3 -m unittest discover -s tests`
- `npm run build` from `web/`
- Browser smoke test during the initial slice

## Notes

Current development web/API ports should use the `22xxx` range.
