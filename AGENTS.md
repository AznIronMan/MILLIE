# AGENTS.md

Project: MILLIE

Working acronym: Mail Ingestion, Library, Lookup, Indexing, and Exchange.

This repository was reset for a fresh 1.0.0 start on 2026-05-31. The previous implementation is preserved locally at `.private/archived/version_0.tar.gz`; treat it as reference material only when explicitly needed. Do not recreate the old project structure by default.

## Working Rules

- Keep `README.md` updated when project purpose, setup, status, usage, or direction changes.
- Keep `CHANGELOG.md` updated for meaningful changes. Use semantic versioning in `major.minor.patch` format.
- Use `docs/` for customer-facing instructions and references, including install, requirements, operation, CLI usage, web GUI usage, and troubleshooting.
- `.tasks/` is local-only and ignored by Git. Use it for scratch task files only when helpful; do not rely on it for committed project state.
- Commit intentional MILLIE-scoped changes after they are verified.
- Push `main` to the MILLIE remote when credentials are available.
- Do not rewrite GitHub history or force-push unless the user explicitly asks for that operation.

## Credentials And Sensitive Data

- Never commit real credentials, imported mail, generated databases, attachment dumps, access tokens, app keys, local logs, or mailbox archives.
- Use root `millie.settings` as the primary local settings/config database. It is a SQLite3 file and is ignored by Git.
- Secret values in `millie.settings` are encrypted at rest with AES-256-GCM. The settings encryption key lives in macOS Keychain when available, or in ignored `.private/secrets/millie_settings.key` as a fallback.
- Keep real secret values out of commits even when they are encrypted locally.
- Use `.env` only for shell-level local overrides when needed; keep only placeholder examples in `.env.example`.
- Put local credential files, OAuth notes, recovery material, copied secrets, generated runtime data, test databases, imported mail, exports, logs, scratch fixtures, and local archives under `.private/`.
- `.private/`, `.tasks/`, `/data/`, `/logs/`, `*.settings`, and `*.millie` must stay ignored by Git.
- GitHub credentials should stay outside the repo in the GitHub CLI, macOS keychain, or the configured Git credential helper.
- Prefer OS-managed secure storage for connector credentials. If a file-based secret is temporarily required, store it under `.private/secrets/` and do not commit it.

## Security Defaults

- Treat email content and metadata as sensitive by default.
- Development may use local non-secure HTTP paths.
- Production-facing work must have a clear HTTPS/TLS story before real mail data is loaded.
- If a web or API listener is introduced, document its bind address, port, authentication state, and development versus production behavior.
- Sanitize HTML email before display whenever message rendering is implemented.

## Database Recovery Safety

- The recovered MILLIE archive must stay on the dedicated Postgres recovery cluster: `10.0.10.81:55432/millie`.
- Do not point MILLIE clients, importers, sync jobs, webmail, IMAP, automation, or maintenance scripts at Phoebe/Jazmine's main Postgres port `10.0.10.81:5432`.
- The old main-cluster `millie` database is quarantined and must stay connection-disabled. Do not re-enable it and do not import MILLIE back into the main Jazmine database cluster.
- Treat the recovered archive as read-mostly until a clean successor database exists.
- Avoid `VACUUM FULL`, broad `ANALYZE`, `pg_amcheck`, large index rebuilds, and aggressive autovacuum against the large MILLIE mail tables unless the user explicitly approves a staged rebuild or maintenance plan.
- Keep autovacuum disabled on the dedicated MILLIE recovery cluster until the archive is rebuilt into a clean successor database.
- Before any future MILLIE database maintenance, take a fresh backup/snapshot and use `/data/backup/millie/` or `/data/backups/` as the work area.
- The safe long-term fix is a staged clean rebuild: create a fresh MILLIE DB, copy only readable rows in batches, skip damaged records, rebuild derived search data, validate counts, then switch MILLIE over.
