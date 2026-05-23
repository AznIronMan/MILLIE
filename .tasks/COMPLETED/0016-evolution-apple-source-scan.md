# Evolution And Apple Mail Source Scan

Status: COMPLETED

## Goal

Extend read-only desktop-client source scanning beyond Thunderbird.

## Result

- Added Evolution source scanning for MBOX, Maildir, and `.eml`/`.emlx` folders.
- Added Apple Mail source scanning for `.mbox` packages, raw `mbox` files, and `Messages/*.emlx` folders.
- Added `.emlx` normalization so Apple Mail wrapper bytes are stripped before parsing message content.
- Added candidate mailbox-path handoff to import requests so scanned directory sources preserve discovered mailbox names.
- Added CLI and web scan type choices for Evolution and Apple Mail.
- Updated source-scanning docs and API notes.

## Verification

- `PYTHONWARNINGS=error PYTHONPATH=src python3 -m unittest discover -s tests`
- `npm run build` from `web/`
- `PYTHONPATH=src python3 -m millie doctor`
- CLI smoke scans for Evolution and Apple Mail fixtures
- API smoke scans for Evolution and Apple Mail fixtures
- API import smoke for Apple Mail `.emlx` candidate with `mailboxPath`
- Browser smoke verified the updated scan type controls in the web import panel

## Notes

The scanner remains read-only. Selected candidates still import through the existing import endpoint.
