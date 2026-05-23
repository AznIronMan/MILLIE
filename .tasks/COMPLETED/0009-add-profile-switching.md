# Add Profile Switching

Status: COMPLETED

## Goal

Add a profile system so testing and real mail libraries can be isolated, and so the previously selected profile opens by default.

## Result

- Added a local profile registry in `.private/local/millie.settings`.
- Added per-profile SQLite database and data directory support.
- Added per-profile SQLite settings files named after the profile id.
- Added API endpoints to list, create, and switch profiles.
- Added CLI commands to list, create, and switch profiles.
- Added web UI controls for selecting and creating profiles.
- Added profile manager test coverage.

## Verification

- `PYTHONPATH=src python3 -m unittest discover -s tests`
- `npm run build` from `web/`

## Notes

Profiles are local runtime state and should remain ignored by git with imported mail, generated databases, and blob data.
