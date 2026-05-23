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

## [0.1.0] - 2026-05-23

- Added initial project guidance, documentation layout, roadmap, and task tracking structure.
- Defined the working MILLIE acronym.
- Set SQLite as the initial storage target with future connector support.
- Recorded the current git-disabled project state.
