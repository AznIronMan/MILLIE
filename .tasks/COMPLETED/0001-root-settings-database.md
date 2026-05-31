# Root Settings Database

Status: Completed

## Goal

Create the first root-level MILLIE settings database and a temporary browser editor for development.

## Context

MILLIE uses `millie.settings` as a SQLite3 settings/config database instead of `.env` as the primary application configuration store.

## Acceptance Criteria

- Root `millie.settings` exists and contains the initial settings table.
- Temporary `tmp_settings.sh` launches a local browser editor.
- The editor shows setting values, descriptions, and options.
- The editor can add, edit, and remove multiple IMAP retrieval and SMTP sending accounts.
- The editor can save changes or cancel and reload.
- Customer-facing settings documentation exists under `docs/`.

## Notes

- Secrets are stored in plain text for now and must not be committed with real values.
