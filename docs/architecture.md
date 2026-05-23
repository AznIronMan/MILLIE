# MILLIE Architecture

## Purpose

MILLIE provides a portable email library that can ingest mail from different clients and services, normalize it, store it in a database, export it back into importable mailbox formats, and expose it through a web UI, API, and future mail-client-compatible server interface.

## Core Principle

Every source has quirks. The system should preserve source-specific facts while projecting messages into a stable canonical model.

The canonical model should support:

- Message metadata
- Bodies in text and HTML form
- Headers
- Participants
- Folders and labels
- Flags and read state
- Attachments
- Conversations and references
- Raw provenance
- Import and sync history
- Export history and target-specific mappings

## Proposed Component Boundaries

### Importer Adapters

Importer adapters read exported or local mailbox data and emit normalized import events.

Initial adapters:

- PST
- OST, when extractable
- OLM
- MBOX
- Maildir
- EML / EMLX
- Thunderbird profile
- Evolution store/export
- Apple Mail export

Important detail: some formats will require external libraries or tools. PST and OST support should be wrapped behind a stable adapter interface so the core pipeline does not depend directly on a single parser.

### Source Connectors

Source connectors pull from live services and mail protocols.

Planned connectors:

- IMAP
- POP3
- Microsoft Graph / Exchange
- OAuth providers
- App-password and app-key providers

Connectors should store only secret references in normal app tables. Token values and private keys should use a protected secret store.

### Normalization Pipeline

The pipeline should:

- Parse headers and bodies
- Normalize addresses
- Preserve raw headers
- Extract text from HTML
- Sanitize HTML for viewing
- Extract attachment metadata
- Store attachment content by content hash
- Deduplicate messages
- Assign source provenance
- Index searchable text
- Record import errors without stopping the whole job

### Storage Layer

SQLite is the first database target. The first schema should avoid SQLite-only assumptions unless the feature is intentionally local, such as FTS5.

Future database adapters:

- PostgreSQL
- MySQL/MariaDB
- NoSQL or document-backed stores for specialized deployments

### Export Layer

The export layer should rebuild mail into formats that common mail clients can import while preserving as much original content as possible.

Default strategy:

- Prefer original raw MIME when available.
- Preserve original headers, `Message-ID`, `Date`, `References`, MIME boundaries, body parts, inline content, and attachments when possible.
- Reconstruct MIME only when raw source content is unavailable or must be transformed.
- Preserve folder paths, labels, flags, read/unread state, starred/flagged state, and source provenance through target-specific metadata where the target supports it.
- Produce an export manifest that records source IDs, output paths, counts, warnings, unsupported metadata, and hashes.

Initial portable export targets:

- `.eml`
- `mbox`
- `maildir`

Client-oriented export profiles:

- Thunderbird-friendly `mbox`/profile-style folder layout
- Evolution-friendly `mbox` or `maildir`
- Apple Mail-friendly `mbox` bundles
- Outlook workflow exports, likely `.eml` bundles first and PST only after a reliable writer/toolchain is selected

PST and OLM exports should be treated as advanced features because reliable writing is harder than producing open mailbox formats.

### API

The API should be versioned from the start, for example `/api/v1`.

Initial API areas:

- Sources and accounts
- Import jobs
- Export jobs
- Mailboxes and folders
- Messages
- Threads or conversations
- Search
- Attachments
- Health and diagnostics

### Web Client

The web client should behave like a practical mail reader:

- Account/source list
- Folder tree
- Search
- Message list
- Conversation/message detail
- Attachment browser
- Import status and errors
- Basic settings for local server and database configuration

### Local IMAP Facade

The local IMAP facade should come after the storage/API/web MVP. It should start read-only so external clients can browse imported mail without creating complex sync conflicts.

Later write support can be considered for:

- Read/unread flags
- Star/flag state
- Folder moves
- Deletes
- Labels/categories where clients support them

## Recommended Early Build Order

1. Define the canonical schema and SQLite migrations.
2. Build import-job orchestration and normalized parser output.
3. Implement EML, MBOX, and Maildir first.
4. Add PST/OLM through parser adapters.
5. Add the API and basic web client.
6. Add EML, MBOX, and Maildir export.
7. Add IMAP connector sync.
8. Add OAuth/provider connectors.
9. Add read-only local IMAP facade.
10. Add PostgreSQL/MySQL adapters.
11. Harden auth, TLS, backup/export, and operational tooling.
