# Client Compatibility, Export Fidelity, And Sync UX

Status: COMPLETED

## Goal

Advance the current live-mail and mail-client bridge work in three practical areas: local IMAP client compatibility, export fidelity reporting, and safer staged sync/backfill controls.

## Completed

- Added local IMAP facade compatibility commands for common desktop clients: `ID`, `ENABLE`, `XLIST`, `CHECK`, `UNSELECT`, and `IDLE`.
- Added special-use hints for common folders so clients can better recognize Inbox, Sent, Drafts, Trash, Junk, Archive, and Flagged folders.
- Expanded export manifests with a fidelity summary, raw message hashes, raw-MIME preservation flags, output hash verification, and MBOX containerization notes.
- Added per-run sync-limit controls for saved IMAP, POP, and Graph sources in the web UI.
- Included effective sync limits in IMAP and POP sync API responses.
- Updated docs and tests for the new behavior.

## Notes

The IMAP facade remains read-only. Real-client setup flows for Thunderbird, Apple Mail, Evolution, and Outlook still need hands-on verification and follow-up compatibility fixes.
