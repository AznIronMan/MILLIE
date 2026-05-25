# Graph OAuth And Probe

Status: COMPLETED

## Goal

Turn the Microsoft Graph source skeleton into a usable OAuth connection path for real-account testing.

## Completed

- Added PKCE authorization-code callback handling for Graph sources.
- Added secret-backed token payload storage and pending-auth cleanup.
- Added access-token refresh before Graph metadata calls.
- Added read-only Graph account/folder probe through CLI and API.
- Added web controls to save, connect, probe, and delete Graph sources.
- Documented the Entra localhost redirect flow and CNB connector setup.

## Verification

- `PYTHONPATH=src python3 -m unittest discover -s tests`
- `npm run build` from `web/`

## Notes

The connector still needs selected-folder management and Graph delta sync before it imports mail into MILLIE.
