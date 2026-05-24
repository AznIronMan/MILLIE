# Development

MILLIE currently uses:

- Python for storage, import/export logic, CLI, and the local API server.
- SQLite as the first database.
- TypeScript/Vite for the web app.

The backend intentionally starts with the Python standard library so the project can run before choosing heavier framework dependencies.

## Local Environment

MILLIE does not require a `.env` file. Defaults are built in, and local runtime state is stored in SQLite `.settings` files.

For reference only, optional environment overrides are listed in `.env.example`.

Runtime databases, imported mail, attachment payloads, secrets, settings files, and generated export output should stay under ignored local paths such as `.private/local/` or `.private/secrets/`.

Global settings are stored at `.private/local/millie.settings` by default. Profiles are stored under `.private/local/profiles/`, and each profile has a profile-specific SQLite settings file such as `default.settings` or `fixture-mail.settings`.

The previously selected profile opens automatically when the server starts.

Local auth also uses `.private/local/millie.settings`. For the current development phase, `auth.dev_bypass` defaults to `true`. Set it to `false` in the global settings database when testing the first-run admin setup and session-cookie login flow.

Connector credentials use a secret manager. The default `auto` backend uses macOS Keychain when available and falls back to a profile-local settings store for development. Use `--secret-backend local` for deterministic tests and smoke runs that should avoid Keychain writes.

## Prerequisites

Run the doctor command to check Python, SQLite, Node.js, npm, optional `readpst/libpst`, `web/package.json`, `web/node_modules`, and optional `.venv` state:

```sh
PYTHONPATH=src python3 -m millie doctor
```

To let MILLIE prompt for local dependency setup:

```sh
PYTHONPATH=src python3 -m millie doctor --install
```

For non-interactive setup:

```sh
PYTHONPATH=src python3 -m millie doctor --install --yes
```

For PST import support:

```sh
brew install libpst
```

## Run The Backend

```sh
PYTHONPATH=src python3 -m millie init-db
PYTHONPATH=src python3 -m millie serve
```

The server binds to `0.0.0.0:22001` by default.

When `auth.dev_bypass` is `false`, the web app shows first-run setup or login before loading protected API data.

## Profiles

```sh
PYTHONPATH=src python3 -m millie profiles
PYTHONPATH=src python3 -m millie profile-create "Fixture Mail"
PYTHONPATH=src python3 -m millie profile-use fixture-mail
```

The web app can also create and switch profiles from the sidebar.

## Build The Web App

```sh
cd web
npm install
npm run build
```

After the web app is built, the Python server serves it from `web/dist`.

For Vite dev-server work, point the web app at the backend:

```sh
cd web
VITE_MILLIE_API_BASE=http://localhost:22001 npm run dev
```

The Vite dev server uses `22002`, and Vite preview uses `22003`.

## Import Mail

```sh
PYTHONPATH=src python3 -m millie import /path/to/message.eml --format eml
PYTHONPATH=src python3 -m millie import /path/to/archive.mbox --format mbox
PYTHONPATH=src python3 -m millie import /path/to/Maildir --format maildir
PYTHONPATH=src python3 -m millie import /path/to/archive.pst --format pst
```

Import output reports processed messages, newly created canonical messages, exact duplicates, errors, and the resolved import format. Exact duplicates are detected by raw MIME content hash, so importing the same archive again should not create duplicate canonical messages.

OLM and OST are detected but not importable yet. Direct import attempts produce failed import jobs with actionable errors instead of trying to parse the files as MBOX.

## Scan Mail Sources

```sh
PYTHONPATH=src python3 -m millie scan /path/to/Thunderbird --type thunderbird
PYTHONPATH=src python3 -m millie scan /path/to/evolution/mail --type evolution
PYTHONPATH=src python3 -m millie scan ~/Library/Mail --type apple-mail
PYTHONPATH=src python3 -m millie scan /path/to/archive.olm --type auto
PYTHONPATH=src python3 -m millie scan /path/to/Thunderbird --type thunderbird --json
```

The web app has the same scan path in the import panel. Scanning is read-only and currently covers Thunderbird profiles, Evolution local stores, Apple Mail stores/exports, Outlook vendor-store detection, and generic open mailbox files.

## Sync IMAP

```sh
PYTHONPATH=src python3 -m millie imap-add "Test IMAP" --host imap.example.com --username user@example.com --folder INBOX
PYTHONPATH=src python3 -m millie imap-sources
PYTHONPATH=src python3 -m millie imap-folders test-imap
PYTHONPATH=src python3 -m millie imap-set-folders test-imap --folder INBOX --folder "Sent Items"
PYTHONPATH=src python3 -m millie secrets-status
PYTHONPATH=src python3 -m millie imap-sync test-imap
```

The initial IMAP connector is read-only and uses TLS by default. It stores source configs in the active profile `.settings` file, stores only secret references in those configs, discovers remote folders with IMAP `LIST`, and tracks per-folder UID cursors in the active profile mail database.

If a profile has legacy IMAP source configs with raw passwords from an older development build, migrate them:

```sh
PYTHONPATH=src python3 -m millie imap-migrate-secrets
```

## Search

The web app search box and `/api/v1/search?q=...` use SQLite FTS5 over message subject, participants, and text body. Query text is normalized before it reaches FTS so searches with punctuation or email addresses are safe to run.

## HTML And Attachments

MILLIE preserves raw HTML body blobs when available, stores a sanitized HTML copy, and serves sanitized HTML through `/api/v1/messages/{id}/html`. Attachments are exposed through `/api/v1/attachments/{id}` with download-oriented response headers.

## Export Mail

```sh
PYTHONPATH=src python3 -m millie export --format eml --output .private/local/exports
PYTHONPATH=src python3 -m millie export --format mbox --output .private/local/exports
PYTHONPATH=src python3 -m millie export --format maildir --output .private/local/exports
PYTHONPATH=src python3 -m millie export --profile thunderbird --format auto --output .private/local/exports/thunderbird
```

Export profiles currently include `generic-eml`, `generic-mbox`, `generic-maildir`, `thunderbird`, `evolution`, `apple-mail`, and `outlook-workflow`.

## Test

```sh
PYTHONPATH=src python3 -m unittest discover -s tests
cd web
npm run build
```
