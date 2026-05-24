# IMAP Sync

MILLIE includes an initial read-only IMAP connector for importing live mailbox messages into the active profile database.

## Scope

Current IMAP support is intentionally narrow:

- Password or app-password login through Python `imaplib`
- Secret references for stored credentials
- TLS by default, with plain IMAP available for local/dev testing
- One or more configured folders, defaulting to `INBOX`
- Folder discovery through IMAP `LIST`
- Incremental sync using per-folder UID cursors
- Raw RFC822 message preservation through the normal import pipeline
- Existing dedupe, search, HTML sanitization, attachment capture, and export support after import

This is not yet an OAuth flow, Exchange/Microsoft Graph connector, POP3 connector, or two-way sync path.

## Settings

Saved IMAP source configs live in the active profile SQLite settings file under the `imap.sources.v1` key.

The source config stores an `auth_ref`, not the password/app password. On macOS, MILLIE uses Keychain by default when the `security` command is available. Other environments fall back to a profile-local development store under `secrets.local.v1`.

Use the local settings fallback only for development or throwaway test credentials. It still stores the secret in the profile `.settings` SQLite file, just outside the source config.

Use this to inspect the active backend:

```sh
PYTHONPATH=src python3 -m millie secrets-status
```

To force a backend for local testing:

```sh
PYTHONPATH=src python3 -m millie --secret-backend local imap-add "Test Mail" \
  --host imap.example.com \
  --username user@example.com
```

Incremental sync state lives in the profile mail database in `source_sync_states`, keyed by source and folder scope. The state currently stores `uidvalidity` and `last_uid`.

## CLI

```sh
PYTHONPATH=src python3 -m millie imap-add "Work Mail" \
  --host imap.example.com \
  --username user@example.com \
  --folder INBOX \
  --limit 100

PYTHONPATH=src python3 -m millie imap-sources
PYTHONPATH=src python3 -m millie imap-folders work-mail
PYTHONPATH=src python3 -m millie imap-set-folders work-mail --folder INBOX --folder "Sent Items"
PYTHONPATH=src python3 -m millie imap-migrate-secrets
PYTHONPATH=src python3 -m millie imap-sync work-mail
```

Use `--no-tls` only for trusted local/dev servers.

`imap-migrate-secrets` moves any legacy raw IMAP passwords from `imap.sources.v1` into the configured secret backend.

`imap-delete` removes a saved IMAP source and deletes its stored secret reference.

## API

- `GET /api/v1/imap-sources`
- `POST /api/v1/imap-sources`
- `POST /api/v1/imap-sources/{id}/folders`
- `POST /api/v1/imap-sources/{id}/sync`
- `POST /api/v1/imap-sources/{id}/delete`

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

`POST /api/v1/imap-sources/{id}/folders` logs in read-only and returns discovered folders with flags, delimiter, role, and selectability.

`POST /api/v1/imap-sources/{id}/delete` removes the saved source and deletes its secret reference.

`POST /api/v1/imap-sources/migrate-secrets` moves legacy raw IMAP passwords into the configured secret backend.

## Follow-Up

- Add OAuth/app-password setup flows.
- Add provider presets for common IMAP hosts.
- Capture IMAP flags and internal dates.
- Add POP3 and Microsoft Graph/Exchange connectors.
