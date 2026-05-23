# Outlook Unsupported Store UX

Status: COMPLETED

## Goal

Make OLM and OST handling explicit so users do not accidentally treat vendor store files as MBOX.

## Result

- Added OLM and OST format detection.
- Added non-importable source scan candidates for OLM and OST.
- Added `importable` to source scan candidate API responses.
- Updated the web scan candidate list to disable unsupported candidates.
- Added explicit direct-import errors and import job error rows for OLM and OST.
- Documented the PST/OLM/OST Outlook strategy.

## Verification

- `PYTHONWARNINGS=error PYTHONPATH=src python3 -m unittest discover -s tests`
- `npm run build` from `web/`
- `PYTHONPATH=src python3 -m millie doctor`
- CLI smoke scans for OLM and OST fixtures
- CLI direct OLM import smoke with clean failure output and exit 1
- API smoke scans for OLM and OST fixtures with `importable: false`
- API direct OST import smoke with HTTP 400 and an actionable error
- Browser smoke confirmed the rebuilt web app loads and shows failed OLM/OST import jobs in Operations

## Notes

PST remains the only implemented Outlook file import path. OLM/OST require adapter selection before import support is promised.
