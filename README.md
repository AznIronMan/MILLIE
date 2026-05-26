# MILLIE

MILLIE stands for Mail Ingestion, Library, Lookup, Indexing, and Exchange.

MILLIE is a new project for importing, normalizing, storing, searching, viewing, and serving email from many sources through one portable model.

Version: `0.1.0`

## Goal

MILLIE should be able to take email from desktop exports, mailbox files, and live mail services, convert it into a shared canonical record model, store it in a SQL-style database, and expose it through:

- A simple webmail-like web app
- A local API for other apps
- High-fidelity export back into formats that common mail clients can import
- Future database connectors beyond SQLite
- Local read-only IMAP service support so external mail clients can browse imported mail

## Initial Sources

Planned file and desktop-client import targets:

- Outlook `.pst`
- Outlook `.ost`, where extractable
- Outlook for Mac `.olm`
- Thunderbird profiles, `mbox`, `maildir`, and `.eml`
- Evolution mail stores and exports
- Apple Mail `.emlx`, mailboxes, and exports
- Generic `.eml`, `.mbox`, and `maildir`

Planned live source targets:

- IMAP, with a first read-only sync MVP now in place
- POP3
- Microsoft Exchange / Microsoft Graph
- OAuth-based providers
- App-key or app-password based providers

Planned export targets:

- Generic `.eml`
- `mbox`
- `maildir`
- Client-oriented export bundles with manifests for Thunderbird, Evolution, Apple Mail, and Outlook workflows
- PST or OLM export only if a reliable writer/toolchain is selected

## Storage Direction

SQLite is the first target because it keeps the early app portable and easy to test. The data layer should be designed so PostgreSQL, MySQL/MariaDB, and possibly NoSQL stores can be added later without rewriting importers or clients.

The internal model should be relational rather than a single giant row. A message has many recipients, headers, folders, labels, attachments, and import events. The API can still return a single convenient message object to callers.

## High-Level Components

- Importer adapters: Read file exports and desktop mail stores.
- Source connectors: Pull from IMAP, POP3, Exchange, and provider APIs.
- Normalization pipeline: Parse, deduplicate, hash, extract metadata, index content, and preserve provenance.
- Storage layer: SQLite first, with database adapter boundaries for future engines.
- Search layer: SQLite FTS first, with room for external search engines later.
- Export layer: Rebuild importable mailbox outputs while preserving raw MIME, headers, attachments, folder paths, flags, labels, and provenance where possible.
- API: Versioned local HTTP API for the web app and third-party integrations.
- Web app: Outlook/webmail-style navigation over folders, conversations, messages, attachments, and search.
- Local IMAP facade: Read-only-first mail server interface for Mail, Outlook, Evolution, Thunderbird, and similar clients.
- Backup layer: Portable profile ZIP archives with manifests and secret redaction by default.

## Networking Direction

Development can use non-secure HTTP. HTTPS/TLS/SSL can be enabled by configuration for production or sensitive environments.

Web/API services should listen on `0.0.0.0` by default, with a config override. Because this can expose local mail data to the network, authentication and clear dev/prod profiles are part of the core design.

## Documentation Layout

- `AGENTS.md`: Project instructions for Codex and developers.
- `CHANGELOG.md`: Versioned change history.
- `.env.example`: Safe local environment template.
- `docs/`: App-facing architecture, API, schema, setup, and deployment docs.
- `.private/`: Internal planning, decisions, concerns, and dev-only notes.
- `.tasks/ACTIVE`: Tasks currently being worked.
- `.tasks/PENDING`: Planned tasks.
- `.tasks/COMPLETED`: Completed task records.

## Current Status

MILLIE now has a first runnable foundation slice:

- Python CLI and local API server
- SQLite schema and content-addressed blob storage
- Local profiles with last-selected profile persistence
- SQLite `.settings` files for global and profile-specific settings
- `.eml`, `mbox`, `maildir`, and `.eml` folder import paths
- PST import through the optional `readpst`/`libpst` adapter
- Thunderbird, Evolution, and Apple Mail source scanning to find importable mailbox candidates before import
- Read-only IMAP source configs, folder discovery, source management, and incremental folder sync using UID cursors
- Gmail-compatible IMAP folder discovery, including common Gmail special-folder roles and `imap.google.com` host normalization
- IMAP provider presets, selected-folder one-off sync, and IMAP flags/internal-date capture
- Read-only POP3 source configs, safe no-retrieve probes, UIDL-based incremental sync, and a never-delete server policy
- Microsoft Graph / Exchange source configs with PKCE OAuth callback/token storage, token refresh, folder discovery, and limited delta-backed read-only selected-folder sync
- Connector credential secret references with macOS Keychain support and a local development fallback
- HTTPS-ready server configuration while keeping HTTP as the default development path
- Read-only local IMAP facade on port `22143` with metadata/body fetch support, optional exact login, and direct IMAPS configuration for external client compatibility testing
- `.eml`, `mbox`, and `maildir` export paths with manifests
- Exact raw-MIME deduplication so repeat imports do not duplicate canonical messages
- SQLite FTS search over subject, participants, and body text
- Sanitized HTML message viewing with raw HTML preserved separately
- Attachment listing and downloads through the local API
- Local admin/session authentication path with development bypass currently enabled by default
- Import/export job drill-downs for errors and generated export items
- Export profiles for generic EML/MBOX/Maildir, Thunderbird, Evolution, Apple Mail, and Outlook workflow bundles
- Portable active-profile backup ZIPs with manifests and default redaction for local secret settings
- TypeScript/Vite web client for mailbox navigation, message viewing, import, and export
- Basic import/export test coverage

See [docs/development.md](docs/development.md) for setup and run commands, [docs/api.md](docs/api.md) for API notes, [docs/source-scanning.md](docs/source-scanning.md) for source scan helpers, [docs/imap.md](docs/imap.md) for IMAP sync, [docs/imap-facade.md](docs/imap-facade.md) for the local IMAP facade, [docs/pop.md](docs/pop.md) for POP sync, [docs/exchange-graph.md](docs/exchange-graph.md) for Microsoft Graph / Exchange planning, [docs/backup.md](docs/backup.md) for backup packaging, [docs/profiles.md](docs/profiles.md) for profile switching, and [docs/outlook.md](docs/outlook.md) for Outlook PST/OLM/OST notes.
