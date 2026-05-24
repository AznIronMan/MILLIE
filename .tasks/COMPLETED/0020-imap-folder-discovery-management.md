# IMAP Folder Discovery And Source Management

Status: COMPLETED

## Goal

Make adding and managing IMAP sources practical enough for real-account testing.

## Completed

- Added IMAP `LIST` folder discovery with folder names, delimiters, flags, selectability, and role hints.
- Added CLI commands to discover folders, set saved folder lists, and delete saved IMAP sources.
- Added API endpoints for folder discovery and saved-source deletion.
- Added web controls to discover folders, apply selected folders, sync, and delete IMAP sources.
- Added unit coverage for IMAP folder parsing and selectable-folder discovery.
- Updated IMAP, API, development, roadmap, changelog, and live-connector task docs.

## Verification

- Python unit tests cover folder discovery parsing with a fake IMAP client.
- TypeScript build verifies the expanded IMAP web UI.
- API and browser smoke tests verified saved IMAP source controls render against the local server.

## Notes

This remains read-only. Provider presets, OAuth flows, flag capture, internal dates, and real-account compatibility notes are still future live-connector work.
