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
- Service mail domain: configured in `millie.settings`; current default is `millie.cnbsk.cloud` with local `MILLIE` aliases
- PST import status: read-only probe available through `tools/pst_probe.py`
- Mail import status: dormant source/normalization/storage pipeline scaffolded
- Mail service status: dormant Postgres identity/mailbox facade scaffolded
- Dev IMAP status: minimal listener available for local/LAN testing
- Dev SMTP status: optional setup-only blackhole listener; MILLIE never sends outbound SMTP
- Dev webmail status: no-auth browser view available for local/LAN testing

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

The temporary editor also supports service mailbox domain settings plus repeatable IMAP retrieval accounts and SMTP account metadata. Passwords are hidden in the page after save and encrypted at rest in `millie.settings`. MILLIE does not send outbound SMTP.

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

Postgres schema now includes a `millie_*` service layer for identities such as `geon@millie.cnbsk.cloud`, credentials, sessions, service mailboxes, IMAP/webmail folders, one-way source bindings, mailbox message flags, and webmail/IMAP query views. Local aliases such as `geon@MILLIE` are accepted when configured in `millie.settings`.

```sh
python3 tools/millie_identity_plan.py --login geon@millie.cnbsk.cloud --display-name Geon
```

The command generates bootstrap SQL only. It does not connect to Postgres or start an IMAP/webmail listener.

## Dev IMAP Listener

After importing samples, start the temporary IMAP listener:

```sh
.private/venv/bin/python tools/millie_live_sample_import.py --display-name Geon
.private/venv/bin/python tools/millie_imap_listener.py --host 0.0.0.0 --plain-port 22143 --tls-port 22993 --daemon
```

Credentials are written to ignored `.private/local/millie_ios_mail_credentials.txt`. The listener is a development prototype only; it supports enough IMAP to browse copied messages, but it is not a hardened mail server.

For mail clients that require an outgoing server during account setup, the temporary SMTP setup shim is available:

```sh
.private/venv/bin/python tools/millie_smtp_listener.py --host 0.0.0.0 --submission-port 22587 --tls-port 22465 --daemon
```

This shim accepts any SMTP username/password or no SMTP authentication so client configuration screens can pass, then discards `DATA`. It does not relay, store, queue, or deliver outbound mail from MILLIE.

For SSL-off client testing, use IMAP port `22143` and, only if needed, SMTP port `22587`. Those plaintext dev ports intentionally do not advertise STARTTLS because some clients auto-upgrade and then reject the local self-signed certificate. The TLS ports remain `22993` for IMAP and `22465` for SMTP setup checks. Sanitized listener diagnostics are written under `.private/local/`.

## Dev Webmail

Start the temporary no-auth webmail view:

```sh
.private/venv/bin/python tools/millie_webmail_server.py --host 0.0.0.0 --port 22001 --daemon
```

It opens the current `geon@millie.cnbsk.cloud` mailbox through the Postgres mailbox facade and provides Gmail, Outlook, and Microsoft 365-inspired theme options. The first pass is read-only and does not include SMTP or compose behavior.
