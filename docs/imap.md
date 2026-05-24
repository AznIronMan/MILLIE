# IMAP Sync

MILLIE includes an initial read-only IMAP connector for importing live mailbox messages into the active profile database.

## Scope

Current IMAP support is intentionally narrow:

- Password or app-password login through Python `imaplib`
- TLS by default, with plain IMAP available for local/dev testing
- One or more configured folders, defaulting to `INBOX`
- Incremental sync using per-folder UID cursors
- Raw RFC822 message preservation through the normal import pipeline
- Existing dedupe, search, HTML sanitization, attachment capture, and export support after import

This is not yet an OAuth flow, Exchange/Microsoft Graph connector, POP3 connector, or two-way sync path.

## Settings

Saved IMAP source configs live in the active profile SQLite settings file under the `imap.sources.v1` key. In the current development slice, the password/app password is stored there directly so the connector can be tested end-to-end.

Do not use production mailbox credentials until secret storage is moved to a safer backend such as the OS keychain or an encrypted secret store.

Incremental sync state lives in the profile mail database in `source_sync_states`, keyed by source and folder scope. The state currently stores `uidvalidity` and `last_uid`.

## CLI

```sh
PYTHONPATH=src python3 -m millie imap-add "Work Mail" \
  --host imap.example.com \
  --username user@example.com \
  --folder INBOX \
  --limit 100

PYTHONPATH=src python3 -m millie imap-sources
PYTHONPATH=src python3 -m millie imap-sync work-mail
```

Use `--no-tls` only for trusted local/dev servers.

## API

- `GET /api/v1/imap-sources`
- `POST /api/v1/imap-sources`
- `POST /api/v1/imap-sources/{id}/sync`

`GET /api/v1/imap-sources` redacts the stored password and only reports whether one is configured.

`POST /api/v1/imap-sources` accepts:

- `name`
- `host`
- `port`
- `username`
- `password`
- `use_tls`
- `folders`
- `sync_limit`

`POST /api/v1/imap-sources/{id}/sync` runs a read-only sync immediately and creates an import job.

## Follow-Up

- Replace direct password storage with OS keychain or encrypted secret references.
- Add OAuth/app-password setup flows.
- Add provider presets for common IMAP hosts.
- Capture IMAP flags and internal dates.
- Support folder discovery before sync.
- Add POP3 and Microsoft Graph/Exchange connectors.
