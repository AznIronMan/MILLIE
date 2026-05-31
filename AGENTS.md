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
- During the temporary settings phase, `millie.settings` can contain plain text API keys, database passwords, and IMAP/SMTP passwords. Keep real secret values out of commits.
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
