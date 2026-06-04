# MILLIE

Version: 1.3.1

MILLIE stands for Mail Ingestion, Library, Lookup, Indexing, and Exchange.

This repository has been reset for a fresh start. The prior version is archived locally at `.private/archived/version_0.tar.gz` for reference only.

## Status

- Current baseline: `1.3.1`
- Reset date: 2026-05-31
- Runtime setup: not defined yet beyond temporary tools and dormant scaffolds
- Application structure: early dormant import, storage, identity, and mailbox service scaffolds
- Settings store: local root `millie.settings` SQLite3 database, ignored by Git
- Postgres archive status: recovered archive isolated on `10.0.10.81:55432/millie`; the old main-cluster `millie` database remains quarantined and must not be reused
- Service mail domain: configured in `millie.settings`; current default is `millie.cnbsk.cloud` with local `MILLIE` aliases
- PST import status: read-only probe and duplicate-safe bulk importer available
- Mail import status: duplicate-safe bulk PST and IMAP import tools available
- Dedupe status: exact raw-message dedupe plus normalized duplicate fingerprints/reporting
- Live sync status: runtime IMAP/OAuth checker with persisted per-folder sync health while MILLIE is running
- Automation status: Postgres brain schema foundation, observe-only sorter, active learned-rule matching, rule proposal seeding, taxonomy proposals, manual aggregate-only LLM taxonomy assistance, proposal review activation, webmail review feedback, grouped sorting workbench, and learning metrics available
- Mail service status: dormant Postgres identity/mailbox facade scaffolded
- Dev IMAP status: development listener available for local/LAN browse and mailbox-copy mutation testing
- Dev SMTP status: optional setup-only blackhole listener; MILLIE never sends outbound SMTP
- Dev webmail status: authenticated browser/admin view available for local/LAN testing, with explicit `--no-auth` override

## Development Notes

- Keep real credentials out of commits. During the temporary settings phase, API keys, database passwords, and mail account passwords in `millie.settings` are encrypted locally but still sensitive.
- Secret values in `millie.settings` are encrypted at rest with AES-256-GCM. The encryption key is stored in macOS Keychain when available, or under ignored `.private/secrets/` as a fallback.
- Use `.env` only for shell-level overrides. Application settings belong in `millie.settings`.
- Keep generated mail data, local databases, exports, logs, secrets, and scratch work out of Git.
- `.private/`, `.tasks/`, `/data/`, `/logs/`, `*.settings`, and `*.millie` are ignored.
- Update `CHANGELOG.md` for meaningful changes.
- Database recovery and containment rules live in `docs/database-recovery.md`. Runtime Postgres connections refuse the known quarantined endpoint `10.0.10.81:5432/millie`.

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

To plan a multi-PST import without extracting or writing data:

```sh
.private/venv/bin/python tools/millie_pst_bulk_import.py "/Users/ironman/HomeDrive/Outlook Files"
```

When applied, each PST is mapped under its own mailbox root such as `Sources/PST/CSU_Archive`, with original PST folders nested below that root. Imported messages are also mapped into `All Mail` by default.

## Mail Import Pipeline

The import pipeline can copy PST files and configured IMAP accounts into the Postgres-backed MILLIE mailbox facade.

```sh
python3 tools/mail_import_plan.py --source pst --database postgres --pst tmp/your-archive.pst
```

For configured IMAP accounts in `millie.settings`, the bulk importer lists every selectable folder and fetches messages read-only with `BODY.PEEK[]`:

```sh
.private/venv/bin/python tools/millie_imap_bulk_import.py --apply
```

When exact `--folder` values are supplied, the importer trusts those folders and skips a broad provider folder listing. This is useful for targeted catch-up of one known folder.

The flow supports PST, generic IMAP password auth, iCloud Mail/me.com/mac.com IMAP with Apple app-specific passwords, and Exchange/Outlook OAuth IMAP sources. Normalized records have schema coverage for addresses, headers, dates, subjects, body projections, raw MIME, attachments, inline parts, embedded parts, metadata, folders, import jobs, and search indexes in SQLite or PostgreSQL.

After a full import, use the incremental live checker to import only newer IMAP UIDs:

```sh
.private/venv/bin/python tools/millie_live_sync.py --once
```

For a runtime loop that stops when the command stops:

```sh
.private/venv/bin/python tools/millie_live_sync.py --interval 900
```

Duplicate fingerprints can be backfilled and reported without merging or deleting messages:

```sh
.private/venv/bin/python tools/millie_dedupe_report.py --backfill
```

Gmail label folders can be reconciled faster with Gmail's stable `X-GM-MSGID` before falling back to raw imports:

```sh
.private/venv/bin/python tools/millie_gmail_label_alias_sync.py \
  --apply \
  --account geoff@example.com \
  --folder '[Gmail]/All Mail' \
  --folder '[Gmail]/Important'
```

Before any future provider-side cleanup, audit live provider UIDs against MILLIE and tag the protected MILLIE copies:

```sh
.private/venv/bin/python tools/millie_remote_purge_prep.py
.private/venv/bin/python tools/millie_remote_purge_prep.py --apply
```

The purge-prep command never deletes or moves provider mail. It writes a Postgres manifest plus message metadata tags only after every audited provider UID is already copied into MILLIE.

For a sync-cutoff cleanup, create a manifest from source UIDs already copied into MILLIE, then execute provider-side UID deletion from that manifest:

```sh
.private/venv/bin/python tools/millie_remote_purge_snapshot.py \
  --account geoff@example.com \
  --action delete

.private/venv/bin/python tools/millie_remote_provider_purge.py \
  --manifest-id remote-purge-snapshot-YYYYMMDDTHHMMSSZ

.private/venv/bin/python tools/millie_remote_provider_purge.py \
  --execute \
  --manifest-id remote-purge-snapshot-YYYYMMDDTHHMMSSZ
```

The provider purge executor only targets exact manifest UIDs and checks folder UIDVALIDITY before deletion, so mail arriving after the manifest snapshot is not selected. Execute mode is blocked unless `automation_level=provider_write`, `automation_provider_write_enabled=true`, and `--manifest-id` are present; blocked and executed attempts are recorded in `millie_automation_audit_log`.

## Dormant Mail Service Facade

Postgres schema now includes a `millie_*` service layer for identities such as `geon@millie.cnbsk.cloud`, credentials, sessions, service mailboxes, IMAP/webmail folders, one-way source bindings, mailbox message flags, and webmail/IMAP query views. Local aliases such as `geon@MILLIE` are accepted when configured in `millie.settings`.

```sh
python3 tools/millie_identity_plan.py --login geon@millie.cnbsk.cloud --display-name Geon
```

The command generates bootstrap SQL only. It does not connect to Postgres or start an IMAP/webmail listener.

## Automation And Learning

MILLIE now has a dormant Postgres brain layer for future safe sorting and learning work. It stores automation runs, learned rules, message classification suggestions, user feedback events, retention policies, unsubscribe candidates, and audit log entries.

The current default automation level is `observe`: write suggestions/audit records only. The observe sorter is dry-run by default:

```sh
.private/venv/bin/python tools/millie_sort_mail.py --observe --limit 250
```

To persist suggestions without moving or deleting anything:

```sh
.private/venv/bin/python tools/millie_sort_mail.py --observe --apply --limit 250
```

The sorter supports `--account`, `--folder`, `--message-id`, `--since`, and `--until` filters. Active learned rules can propose or suppress future sorting suggestions in observe mode. Webmail shows pending suggestion badges, message-level suggestion panels, a Review queue, grouped Workbench, Proposal Review, Rules, and Metrics. Metrics includes rule candidates with bounded evidence previews, review-only taxonomy proposals, and a manual **Ask LLM** taxonomy assistant. The assistant sends aggregate proposal data only and returns advisory JSON; it does not apply changes. Proposal Review lists saved proposal rules with status counts, filters, single-row actions, bulk activate/disable/retire controls, and an observe dry-run preview. Review actions write feedback, learned rule evidence, proposed rules, and audit rows only.

Automation guardrails live in `millie.settings` as `automation_level` and `automation_provider_write_enabled`. Provider writes require both `automation_level=provider_write` and `automation_provider_write_enabled=true`. Remote provider purge execution also requires an explicit manifest id and writes provider-write audit rows; dry-runs remain available without provider-write settings.

Reviewed unsubscribe candidates can be prepared without contacting providers:

```sh
.private/venv/bin/python tools/millie_unsubscribe_review.py list --status approved --include-browser
.private/venv/bin/python tools/millie_unsubscribe_review.py prepare --execute
.private/venv/bin/python tools/millie_unsubscribe_review.py assist
```

Preparation records `attempting` or `unsafe` state and audit rows only. The manual-assist checklist is written under ignored `.private/local/` for human follow-up; MILLIE does not click links or submit provider forms.

Approved suggestions can be applied inside MILLIE with a dry-run-first command:

```sh
.private/venv/bin/python tools/millie_apply_suggestions.py --limit 100
```

Execution requires `automation_level=auto_internal` or higher. It creates missing MILLIE folders and maps approved messages into those folders without expunging existing mappings or writing to source providers.

Retention execution and unsubscribe execution are planned follow-up work. Provider-side cleanup remains separate and must use the manifest-driven purge flow.

Retention hold policies can be seeded and scanned without deleting anything:

```sh
.private/venv/bin/python tools/millie_retention_scan.py --seed-defaults
.private/venv/bin/python tools/millie_retention_scan.py --limit 100
```

Default policies are proposed, review-required, and `no_action`: `Hold/Trash` reviews after 30 days and `Hold/Spam` reviews after 14 days.

Acknowledged retention decisions can be applied internally with a dry-run-first command:

```sh
.private/venv/bin/python tools/millie_apply_retention.py --limit 100
```

Execution requires `automation_level=auto_internal` or higher. The command only supports `no_action` audit application and non-destructive `hide_from_default_views`, which hides matching `INBOX` and `All Mail` facade rows while keeping hold/source folders and provider mail intact.

Manage retention policies with:

```sh
.private/venv/bin/python tools/millie_retention_policies.py list
.private/venv/bin/python tools/millie_retention_policies.py activate --default-holds --execute
```

The webmail **Policies** button can list, activate, disable, and edit retention policy names, hold durations, review requirements, and internal actions. These policy controls do not touch source providers.

Run sync, dedupe backfill, observe sorting, retention scan, and safe internal apply checks in one runtime pass:

```sh
.private/venv/bin/python tools/millie_live_upkeep.py --once
```

For a runtime loop that stops when the command stops:

```sh
.private/venv/bin/python tools/millie_live_upkeep.py --interval 900
```

## Dev IMAP Listener

After importing samples, start the temporary IMAP listener:

```sh
.private/venv/bin/python tools/millie_live_sample_import.py --display-name Geon
.private/venv/bin/python tools/millie_imap_listener.py --host 0.0.0.0 --plain-port 22143 --tls-port 22993 --daemon
```

Credentials are written to ignored `.private/local/millie_ios_mail_credentials.txt`. The listener is a development prototype only; it is not a hardened mail server.

The IMAP listener supports browsing copied messages plus mailbox-copy mutations for client testing: folder create/delete/rename/subscribe, `APPEND` uploads, flag changes, copy/move, and delete/expunge. These operations affect MILLIE's Postgres mailbox facade only. They do not mutate source IMAP accounts, Exchange mailboxes, or PST files.

For mail clients that require an outgoing server during account setup, the temporary SMTP setup shim is available:

```sh
.private/venv/bin/python tools/millie_smtp_listener.py --host 0.0.0.0 --submission-port 22587 --tls-port 22465 --daemon
```

This shim accepts any SMTP username/password or no SMTP authentication so client configuration screens can pass, then discards `DATA`. It does not relay, store, queue, or deliver outbound mail from MILLIE.

For SSL-off client testing, use IMAP port `22143` and, only if needed, SMTP port `22587`. Those plaintext dev ports intentionally do not advertise STARTTLS because some clients auto-upgrade and then reject the local self-signed certificate. The TLS ports remain `22993` for IMAP and `22465` for SMTP setup checks. Sanitized listener diagnostics are written under `.private/local/`.

## Dev Webmail

Start the temporary authenticated webmail view:

```sh
.private/venv/bin/python tools/millie_webmail_server.py --host 0.0.0.0 --port 22001 --daemon
```

It uses Postgres-backed MILLIE identity credentials and opens the signed-in mailbox through the Postgres mailbox facade. For local-only development testing without login, add `--no-auth`. It provides Gmail, Outlook, and Microsoft 365-inspired theme options. It does not include SMTP or compose behavior.

The message list loads only the selected folder and supports `25`, `50`, `100`, `250`, `500`, or `All` messages at a time. The selected size is remembered in browser local storage, folder counts use cheap count queries, and the active list can be refreshed from the webmail toolbar.

The webmail view can search copied mail, review MILLIE brain suggestions and retention-eligible hold messages, batch-review grouped sorting suggestions, inspect/manage learned rules, manage retention policies, inspect operations status, and run dry-run/execute checks for approved internal apply commands. Apply controls are internal-only and still respect `automation_level=auto_internal`. These controls do not write back to source providers. Messages in hold folders show matching retention policy timing and eligibility in the reader.

The webmail **Workbench** button groups proposed sorting suggestions by target, sender domain, current folder, and year. Batch approve/reject/always/never actions write the same feedback and audit rows as individual review.

The webmail **Ops** button shows configured mail account/source status, archive and service mailbox counts, review queue counts, recent automation runs, per-account/folder sync health, and safe one-off controls for live sync, scoped account/folder sync, live upkeep, dedupe reporting, and bounded dedupe backfill. The Ops dashboard does not run remote provider purge or any source-provider write command.

To have webmail check live IMAP/OAuth sources while the webmail process is running, add `--live-sync`. This does not install a macOS service:

```sh
.private/venv/bin/python tools/millie_webmail_server.py \
  --host 0.0.0.0 \
  --port 22001 \
  --live-sync
```

The webmail listener also serves development mail-client discovery XML:

- `GET/POST /autodiscover/autodiscover.xml`
- `GET/POST /autodiscover/autodiscovery.xml`
- `GET /mail/config-v1.1.xml`
- `GET /autoconfig/mail/config-v1.1.xml`
- `GET /.well-known/autoconfig/mail/config-v1.1.xml`

For public Outlook autodiscover, nginx must proxy POST requests for `/autodiscover/autodiscover.xml` to the webmail listener instead of serving a static GET-only file.
