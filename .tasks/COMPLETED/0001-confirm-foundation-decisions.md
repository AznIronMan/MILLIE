# Confirm Foundation Decisions

Status: COMPLETED

## Goal

Confirm the core project decisions before implementation begins.

## Context

The project began in planning mode. The early foundation decisions are now accepted for the prototype: SQLite first, Python backend logic, TypeScript/Vite web app, HTTP in dev, easy HTTPS/TLS enablement later, web/API listeners on `0.0.0.0`, and direct commits to `main` while the project is private/solo.

## Acceptance Criteria

- MILLIE acronym is accepted: Mail Ingestion, Library, Lookup, Indexing, and Exchange.
- First implementation stack is chosen: Python logic/API, SQLite storage, TypeScript/Vite web app.
- Initial schema direction is accepted and implemented through migrations.
- Attachment storage strategy is chosen: content-addressed local blobs with SQLite metadata.
- Export fidelity goals and first export formats are confirmed: raw-MIME-first `.eml`, `mbox`, and `maildir`.
- Dev/prod network expectations are documented.
- Git policy is confirmed and git is enabled for direct push to `main`.

## Notes

Local authentication and production hardening remain tracked under web/API and security follow-up work.
