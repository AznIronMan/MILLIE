# Add Local Auth And Job Drill-Downs

Status: COMPLETED

## Goal

Add a first local authentication path and make import/export operations inspectable from the web client.

## Result

- Added global-settings-backed auth configuration in `millie.settings`.
- Added `auth.dev_bypass`, defaulting to `true` for the current development phase.
- Added first-run admin setup, login, logout, PBKDF2-SHA256 password hashing, and HTTP-only session cookies.
- Added auth status to health/API responses and a web login/setup screen for bypass-off mode.
- Added clickable import/export job rows in the Operations panel.
- Added import job error and export item drill-down details.

## Verification

- `PYTHONWARNINGS=error PYTHONPATH=src python3 -m unittest discover -s tests`
- `npm run build` from `web/`

## Notes

Disable `auth.dev_bypass` before treating a profile as real-mail accessible outside trusted local development.
