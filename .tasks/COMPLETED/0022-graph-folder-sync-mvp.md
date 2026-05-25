# Graph Folder Sync MVP

Status: COMPLETED

## Goal

Turn the connected Microsoft Graph account into a cautious read-only import path.

## Completed

- Added Graph folder tree discovery, including nested folders and count metadata.
- Added selected-folder storage on Graph source configs.
- Added limited read-only Graph sync from saved selected folders.
- Fetches message MIME with Microsoft Graph `/$value` and imports through the existing raw-MIME parser.
- Tracks seen Graph message ids per folder in `source_sync_states`.
- Added CLI, API, web controls, and unit coverage.

## Verification

- `PYTHONPATH=src python3 -m unittest discover -s tests`
- `npm run build` from `web/`

## Notes

This is not the final Graph delta sync. It is a safe first selected-folder import path that avoids remote mailbox mutation.
