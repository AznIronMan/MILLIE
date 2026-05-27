# Background Sync, API Tokens, And Export Verification

Status: COMPLETED

## Goal

Move MILLIE closer to large-mailbox operation without depending on external desktop-client testing.

## Completed

- Added an in-process background sync queue for IMAP, POP, and Microsoft Graph sources.
- Added web Backfill status in the Operations panel with queued/running/completed/failed/cancel-requested visibility.
- Added export manifest verification through CLI, API, and web UI.
- Added connector failure classification for throttling, auth, network, partial cursor, and unknown failures.
- Added hashed API tokens for external tools and local integrations.
- Added web controls to create and revoke API tokens.

## Notes

The first background worker is scoped to the running API process. Persistent jobs, cross-restart resume, and true mid-connector cancellation remain future hardening work.
