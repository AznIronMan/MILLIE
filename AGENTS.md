# AGENTS.md

Project: MILLIE

Working acronym: Mail Ingestion, Library, Lookup, Indexing, and Exchange.

MILLIE is intended to normalize email from files, desktop mail clients, and live mail protocols into a portable store with a common API, web client, and eventually a local mail-server facade.
It should also support high-fidelity export back into importable mailbox formats for common clients.

## Project Documentation Rules

- Keep `README.md` updated with relevant high-level project information whenever architecture, setup, status, usage, or direction changes.
- Use semantic versioning in `major.minor.patch` format.
- Keep `CHANGELOG.md` updated for meaningful changes. Use an `Unreleased` section when work has not been assigned to a release yet.
- Use `docs/` for final app-facing documents, including architecture, user/developer guides, API references, schema references, and deployment notes.
- Use `.private/` for internal development-only notes, planning, risks, decisions, scratch docs, and non-public coordination. Do not store secrets there unless they are under an ignored secrets path.
- Use `.tasks/ACTIVE`, `.tasks/PENDING`, and `.tasks/COMPLETED` for Markdown issue-style task files. Move task files between folders as their status changes.

## Git Policy

- Git is enabled for this project.
- After verified changes, update docs/tasks/version notes as needed, then commit intentional MILLIE-scoped changes.
- Push to the MILLIE remote on `main` when credentials are available.
- Revisit the direct-to-main workflow before public or multi-contributor development.

## Engineering Direction

- Start with SQLite for simplicity and portability.
- Keep the persistence layer clean enough to add PostgreSQL, MySQL/MariaDB, and selected NoSQL connectors later.
- Prefer a relational canonical model internally. Expose flattened message records through the API where clients need a single object.
- Treat imported mail, attachments, OAuth tokens, app keys, and generated databases as sensitive data.
- Preserve raw message provenance wherever practical: source type, source path/account, original IDs, raw headers, import job, hashes, and errors.
- Preserve original raw MIME content whenever possible so exports can keep as much original message structure, headers, attachments, and timestamps as possible.
- Treat export as a first-class workflow. Prefer client-specific export profiles over one generic output path when preserving behavior matters.
- Avoid committing sample mailboxes, `.pst`, `.ost`, `.mbox`, `.olm`, `.eml`, `.emlx`, SQLite databases, attachment dumps, access tokens, or local credentials.

## Networking And Security Defaults

- Development may use non-secure HTTP paths.
- Production must be easy to run with HTTPS/TLS/SSL, either directly or behind a reverse proxy.
- Web/API listeners should bind to `0.0.0.0` by default, with a documented config override.
- Because `0.0.0.0` exposes the app beyond localhost, authenticated access and clear dev/prod profiles are required before real mail data is loaded.
- HTML email rendering must be sanitized before display.

## Task File Format

Task files should generally include:

- Title
- Status
- Goal
- Context
- Acceptance criteria
- Notes or decisions

Keep tasks short enough that a future Codex/dev pass can understand what to do without reading the entire project history.
