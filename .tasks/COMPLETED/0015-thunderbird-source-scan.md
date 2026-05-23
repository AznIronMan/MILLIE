# Thunderbird Source Scan

Status: COMPLETED

## Goal

Add a read-only helper that scans Thunderbird storage and presents importable mailbox candidates before import.

## Result

- Added a source scanner module with Thunderbird profile detection.
- Detected Thunderbird MBOX files, Maildir folders, and `.eml` folders under `Mail/` and `ImapMail/`.
- Ignored common Thunderbird metadata such as `.msf`, `panacea.dat`, `folderTree.json`, `global-messages-db.sqlite`, and filter/index files.
- Added `GET /api/v1/source-scan`.
- Added `millie scan`.
- Added web import-panel controls for scanning a path and importing selected candidates.

## Verification

- `PYTHONWARNINGS=error PYTHONPATH=src python3 -m unittest discover -s tests`
- `npm run build` from `web/`
- `PYTHONPATH=src python3 -m millie doctor`
- API smoke against `GET /api/v1/source-scan`
- Browser smoke scan and candidate import through the web import panel

## Notes

Scanning does not write to the database. Selected candidates are imported through the existing import endpoint.
