# Add Migrations, Job History, And PST Adapter

Status: COMPLETED

## Goal

Harden the first slice with schema migration tracking, broader import/export test coverage, visible job history, and an initial PST adapter path.

## Result

- Added `schema_migrations` tracking to the SQLite database.
- Added API endpoints for migrations, import jobs, import errors, export jobs, and export items.
- Added web UI operations panel for recent import/export jobs.
- Expanded tests for EML, multipart HTML/attachment mail, MBOX, Maildir, profile persistence, and PST format detection.
- Installed `libpst` locally and added PST import via `readpst`.
- Copied the user-provided PST to ignored local fixture storage for smoke testing.

## Verification

- `PYTHONWARNINGS=error PYTHONPATH=src python3 -m unittest discover -s tests`
- `PYTHONPATH=src python3 -m millie doctor`
- `npm run build` from `web/`
- PST smoke import from ignored local fixture: 193 messages, 6 mailboxes, 327 attachments, 0 import errors.

## Notes

Real PST files and extracted messages stay under ignored `.private/local/` paths.
