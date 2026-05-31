# MILLIE

Version: 1.0.0

MILLIE stands for Mail Ingestion, Library, Lookup, Indexing, and Exchange.

This repository has been reset for a fresh start. The prior version is archived locally at `.private/archived/version_0.tar.gz` for reference only.

## Status

- Current baseline: `1.0.0`
- Reset date: 2026-05-31
- Runtime setup: not defined yet beyond temporary tools and dormant scaffolds
- Application structure: early dormant import, storage, identity, and mailbox service scaffolds
- Settings store: local root `millie.settings` SQLite3 database, ignored by Git
- PST import status: read-only probe available through `tools/pst_probe.py`
- Mail import status: dormant source/normalization/storage pipeline scaffolded
- Mail service status: dormant Postgres identity/mailbox facade scaffolded

## Development Notes

- Keep real credentials out of commits. During the temporary settings phase, API keys, database passwords, and mail account passwords in `millie.settings` are encrypted locally but still sensitive.
- Secret values in `millie.settings` are encrypted at rest with AES-256-GCM. The encryption key is stored in macOS Keychain when available, or under ignored `.private/secrets/` as a fallback.
- Use `.env` only for shell-level overrides. Application settings belong in `millie.settings`.
- Keep generated mail data, local databases, exports, logs, secrets, and scratch work out of Git.
- `.private/`, `.tasks/`, `/data/`, `/logs/`, `*.settings`, and `*.millie` are ignored.
- Update `CHANGELOG.md` for meaningful changes.

## Temporary Settings Editor

Run the local settings editor from the project root:

```sh
./tmp_settings.sh
```

It opens `http://127.0.0.1:22011/`, shows the settings table, and can save edits back to `millie.settings` or cancel and reload the current database values. Starting the editor also migrates any existing plaintext secret values to encrypted values.

The temporary editor also supports repeatable IMAP retrieval accounts and SMTP sending accounts. Passwords are hidden in the page after save and encrypted at rest in `millie.settings`.

Microsoft Outlook IMAP OAuth settings are also stored there. Use `http://localhost:22013/oauth/microsoft/callback` as the local Entra redirect URI.

For the temporary Microsoft OAuth callback/token exchange helper, run:

```sh
./tmp_microsoft_oauth.sh
```

Customer-facing docs live in `docs/`.

## PST Probe

MILLIE can currently smoke-test PST access with the read-only probe:

```sh
python3 tools/pst_probe.py tmp/your-archive.pst --clean
```

The probe requires `readpst` from libpst, extracts derived MH-format email files under ignored `.private/local/pst-extract/`, writes a local JSON manifest, and prints only counts and metadata. It does not modify the source PST.

## Dormant Mail Import Pipeline

The initial import pipeline code and database schemas are present but not activated for live imports.

```sh
python3 tools/mail_import_plan.py --source pst --database postgres --pst tmp/your-archive.pst
```

The planned flow supports PST, IMAP password auth, and Exchange/Outlook OAuth IMAP sources. Normalized records have schema coverage for addresses, headers, dates, subjects, body projections, raw MIME, attachments, inline parts, embedded parts, metadata, folders, import jobs, and search indexes in SQLite or PostgreSQL.

## Dormant Mail Service Facade

Postgres schema now includes a `millie_*` service layer for identities such as `geon@MILLIE`, credentials, sessions, service mailboxes, IMAP/webmail folders, one-way source bindings, mailbox message flags, and webmail/IMAP query views.

```sh
python3 tools/millie_identity_plan.py --login geon@MILLIE --display-name Geon
```

The command generates bootstrap SQL only. It does not connect to Postgres or start an IMAP/webmail listener.
