# Changelog

All meaningful changes to MILLIE should be documented in this file.

The project uses semantic versioning in `major.minor.patch` format.

## [Unreleased]

- Enabled standalone Git tracking for the MILLIE folder and prepared it for the GitHub remote.
- Added a safe `.env.example` and expanded ignore rules for local databases, secrets, caches, logs, and generated output.
- Added export/round-trip planning for high-fidelity client-importable mailbox formats.
- Added the first runnable Python/SQLite backend, import/export core, local API server, TypeScript web client, and core test.
- Added local profile switching with last-selected profile persistence for isolated test and mail libraries.
- Switched profile/global settings to SQLite `.settings` files and moved development web ports to the `22xxx` range.
- Added `millie doctor` to check prerequisites and optionally install local dev dependencies.
- Added schema migration tracking, expanded open-format import/export tests, visible job history endpoints, and initial PST import through `readpst`.
- Added exact raw-MIME deduplication accounting, safer SQLite FTS search, and a `/api/v1/search` endpoint.
- Added sanitized HTML message viewing and attachment download endpoints in the API and web client.
- Added local admin/session authentication with development bypass enabled by default, plus import/export job drill-down views.
- Added export profiles, profile-aware export UI controls, and enriched export manifests with folder, attachment, source, and client workflow metadata.
- Added read-only source scanning for Thunderbird profiles, with API, CLI, web import-panel integration, and candidate import handoff.
- Added read-only source scanning for Evolution and Apple Mail stores, `.emlx` wrapper normalization, and candidate mailbox-path handoff during import.
- Added non-importable OLM/OST scan candidates, clearer direct-import errors, and Outlook format strategy notes.
- Added an initial read-only IMAP sync connector with profile-stored source configs, per-folder UID cursors, CLI/API endpoints, web controls, and tests.
- Added connector credential secret references, macOS Keychain support, a local development secret fallback, and legacy IMAP password migration.
- Added IMAP folder discovery and saved-source management through CLI, API, and web controls.

## [0.1.0] - 2026-05-23

- Added initial project guidance, documentation layout, roadmap, and task tracking structure.
- Defined the working MILLIE acronym.
- Set SQLite as the initial storage target with future connector support.
- Recorded the current git-disabled project state.
