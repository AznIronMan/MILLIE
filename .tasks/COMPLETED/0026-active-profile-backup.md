# Active Profile Backup

Status: COMPLETED

## Goal

Create a portable backup package for the active MILLIE profile.

## Completed

- Added `millie backup --output <path>`.
- Added `POST /api/v1/backup` and web backup controls.
- Packages the active profile database, blob data directory, global settings snapshot, profile settings snapshot, and `manifest.json`.
- Includes file sizes and SHA-256 hashes in the manifest.
- Redacts known secret-bearing settings by default.
- Supports `--include-secrets` for controlled local moves where preserving local fallback secrets is explicitly needed.
- Added `restore-backup`, `POST /api/v1/restore-backup`, and web restore controls that validate manifest hashes before restoring into a new profile.
- Added unit coverage that verifies the archive contents and default secret redaction.
- Added unit coverage that rejects a tampered backup with a hash mismatch or unlisted archive file.

## Verification

- `PYTHONPATH=src python3 -m unittest discover -s tests`
