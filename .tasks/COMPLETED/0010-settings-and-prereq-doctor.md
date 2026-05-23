# Add SQLite Settings And Prerequisite Doctor

Status: COMPLETED

## Goal

Use SQLite `.settings` files for global and profile-specific settings, keep `.env` optional, use `22xxx` development ports, and add prerequisite detection.

## Result

- Added global SQLite settings file support at `.private/local/millie.settings`.
- Added per-profile SQLite settings files such as `default.settings`.
- Kept `.env` optional as environment override documentation only.
- Moved backend default port to `22001`.
- Moved Vite dev and preview ports to `22002` and `22003`.
- Added `millie doctor` with optional `--install` and `--yes` flags.
- Doctor reports optional `readpst/libpst` availability for PST import.

## Verification

- `PYTHONPATH=src python3 -m millie doctor`
- `PYTHONPATH=src python3 -m unittest discover -s tests`
- `npm run build` from `web/`
