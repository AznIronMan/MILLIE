# Sync Recovery Hardening

Status: COMPLETED

## Goal

Make connector sync state safer for interrupted or partially failed live-mail syncs.

## Completed

- IMAP no longer advances `last_uid` past a failed message UID.
- POP failed `RETR` attempts remain retryable and run limits apply to attempted new UIDLs.
- Graph preserves the previous delta/next cursor when a MIME fetch fails or a sync limit splits a page.
- IMAP, POP, and Graph state now records latest-run recovery metadata for troubleshooting.
- Added unit coverage for retryable IMAP UID failures, POP UIDL failures, Graph MIME fetch failures, and Graph mid-page limits.

## Verification

- `PYTHONPATH=src python3 -m unittest discover -s tests`
- `PYTHONPATH=src python3 -m compileall -q src`
- `npm run build`
