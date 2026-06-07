# Mail Import Pipeline

MILLIE has an import pipeline for turning mail sources into connected database records. Current live tools can copy PST files and configured IMAP accounts into the Postgres-backed MILLIE mailbox facade.

## Supported Source Shapes

- PST files through `readpst`, staged under `.private/local/pst-extract/`.
- IMAP accounts with password authentication.
- iCloud Mail / me.com / mac.com IMAP using Apple app-specific passwords.
- Exchange/Outlook IMAP with OAuth using XOAUTH2 access tokens.

IMAP extraction uses read-only mailbox selection and `BODY.PEEK[]` fetches by UID so messages are not marked read during import. Imported source messages are copied into MILLIE's canonical `mail_*` tables and can then be presented through the Postgres-backed `millie_*` mailbox facade.

## PST Passwords

The PST probe and future PST source path accept password input by environment variable, password file, or prompt. The password value is validated but not printed.

Current limitation: the installed `readpst` backend has no password parameter. If a password-protected PST cannot be opened by this backend, MILLIE fails early with an explicit password-backend error instead of pretending the password was used.

Preferred secret handling:

```sh
MILLIE_PST_PASSWORD='value goes here' \
python3 tools/pst_probe.py tmp/your-archive.pst --password-env MILLIE_PST_PASSWORD
```

For local files, keep password files under ignored `.private/secrets/`.

## Bulk PST Planning And Import

Use the bulk PST tool to plan a directory of PST files. The default mode is a dry run:

```sh
.private/venv/bin/python tools/millie_pst_bulk_import.py "/Users/ironman/HomeDrive/Outlook Files"
```

The bulk importer keeps PST files separated in the mailbox facade. A PST named `CSU_Archive.pst` maps to `Sources/PST/CSU_Archive`, and original folders inside that PST are nested below that root. Imported messages are also mapped into `All Mail` by default.

Run the actual import only when ready:

```sh
.private/venv/bin/python tools/millie_pst_bulk_import.py "/Users/ironman/HomeDrive/Outlook Files" --apply
```

The importer skips existing source messages by source URI plus source message ID unless `--replace-existing` is explicitly used. PostgreSQL search text is capped before indexing so oversized HTML messages still keep their raw MIME and normalized body fields while avoiding `tsvector` size failures.

MILLIE stores duplicate fingerprints on each canonical message:

- `raw_mime_sha256` for exact raw RFC822 duplicates.
- `normalized_body_sha256` for whitespace/case-normalized body text.
- `attachment_set_sha256` for the attachment filename/size/content-hash set.
- `normalized_message_fingerprint` for conservative same-message candidate grouping across IDs, dates, addresses, subject, body, and attachments.

These fingerprints are non-destructive. They support reporting and future review workflows without deleting raw MIME or merging messages automatically.

When an exact raw duplicate is found from another source UID, MILLIE stores the source UID in `mail_source_message_aliases` and maps the existing canonical message into the relevant folders. That lets later incremental syncs skip the same source UID without storing a second raw email body.

## Bulk IMAP Import

Configured IMAP retrieval accounts live in `millie.settings`. The bulk IMAP importer supports password IMAP and Microsoft OAuth/XOAUTH2 accounts:

```sh
.private/venv/bin/python tools/millie_imap_bulk_import.py --apply
```

To import one configured account:

```sh
.private/venv/bin/python tools/millie_imap_bulk_import.py --apply --account geoff@clarktribe.com
```

The tool lists every selectable folder, fetches messages read-only, and preserves the source tree under `Sources/IMAP/<account>/...`. Messages also appear in `All Mail`; common source folders such as `INBOX`, Sent, Drafts, Trash, Junk, and Archive are also mapped to MILLIE's top-level special folders. Existing source UIDs are skipped, and already imported raw MIME hashes are mapped into the requested folders instead of creating duplicate canonical messages.

When exact `--folder` values are supplied, the importer trusts those names and skips the broad provider `LIST` call. Use this for targeted catch-up when a known folder is incomplete or a provider-wide folder listing is slow:

```sh
.private/venv/bin/python tools/millie_imap_bulk_import.py \
  --apply \
  --account geoff@cnb.llc \
  --folder INBOX \
  --folder 'Deleted Items'
```

By default, obvious non-mail folders exposed by some servers, such as Calendar, Contacts, Tasks, Journal, Notes, RSS Feeds, Outbox, and Sync Issues, are skipped. Use `--include-non-mail-folders` only when those folders should be copied as raw IMAP items too.

For routine live checks after a full import, use the incremental UID mode. It searches only UIDs newer than the highest already imported UID for each folder:

```sh
.private/venv/bin/python tools/millie_imap_bulk_import.py \
  --apply \
  --newer-than-existing
```

The runtime sync helper wraps that mode:

```sh
.private/venv/bin/python tools/millie_live_sync.py --once
```

To keep checking only while the command is running:

```sh
.private/venv/bin/python tools/millie_live_sync.py --interval 900
```

This is a foreground/runtime process, not a macOS service.

Each live IMAP/OAuth folder sync records status, counts, UID state, and errors in Postgres so webmail Ops can show ok, stale, running, failed, or unknown health per account/folder.

### Gmail Label Alias Reconciliation

Gmail labels such as `[Gmail]/All Mail` and `[Gmail]/Important` can expose the same message under different folder UIDs. To avoid fetching thousands of duplicate raw messages only to prove the same hash, use the Gmail label alias tool:

```sh
.private/venv/bin/python tools/millie_gmail_label_alias_sync.py \
  --apply \
  --account geoff@clarktribe.com \
  --folder '[Gmail]/All Mail' \
  --folder '[Gmail]/Important'
```

The tool reads Gmail's `X-GM-MSGID` values and creates `mail_source_message_aliases` rows only when Gmail proves a label UID is the same message as an already copied MILLIE canonical message. It never mutates Gmail. Any unmatched UID remains for the normal raw importer.

## Provider Cleanup Preparation

Provider-side deletion is a separate destructive workflow and should not run until copied messages are protected inside MILLIE. Use the remote purge prep tool first:

```sh
.private/venv/bin/python tools/millie_remote_purge_prep.py
```

The default mode is a dry-run audit. It connects to configured live IMAP/OAuth accounts, lists provider UIDs by folder, and checks those UIDs against canonical `mail_messages` plus deduped `mail_source_message_aliases`.

When the audit reports zero missing provider UIDs, write the MILLIE-side manifest and tags:

```sh
.private/venv/bin/python tools/millie_remote_purge_prep.py --apply
```

This creates `mail_remote_purge_manifests` and `mail_remote_purge_manifest_messages` rows and tags each protected canonical message in `mail_message_metadata` with `remote_purge_protected=true`, `millie_archive_tag=remote-provider-purge-prepared`, and the manifest id. It does not delete, move, expunge, or archive anything on Gmail, Exchange, iCloud, or other source providers.

For a cleanup that should exclude anything arriving after a sync pass, create a DB snapshot manifest immediately after the sync completes:

```sh
.private/venv/bin/python tools/millie_remote_purge_snapshot.py \
  --account geoff@clarktribe.com \
  --account geoff@cnb.llc \
  --account aznblusuazn@me.com \
  --action delete
```

Then dry-run and execute provider-side cleanup from that exact manifest:

```sh
.private/venv/bin/python tools/millie_remote_provider_purge.py \
  --manifest-id remote-purge-snapshot-YYYYMMDDTHHMMSSZ

.private/venv/bin/python tools/millie_remote_provider_purge.py \
  --execute \
  --manifest-id remote-purge-snapshot-YYYYMMDDTHHMMSSZ
```

Dry-run mode does not require provider-write settings. Execute mode is blocked unless `automation_level=provider_write`, `automation_provider_write_enabled=true`, and `--manifest-id` are all present. Blocked and executed attempts are written to `millie_automation_audit_log`.

The executor sends IMAP `UID STORE +FLAGS.SILENT (\Deleted)` plus `UID EXPUNGE` only for source UIDs listed in the manifest. It requires UIDPLUS by default and checks each folder's UIDVALIDITY before deleting, which prevents the same numeric UID from being applied after a provider UID reset. It does not search for or delete provider mail that arrived after the snapshot.

For Gmail `[Gmail]/All Mail`, the executor uses Gmail `UID MOVE` into `[Gmail]/Trash` and then performs a final Trash delete by `X-GM-MSGID`. That removes the manifest messages from Gmail's archive without selecting unrelated messages that may have arrived after the snapshot.

For retention cleanup, create the manifest from provider-visible mail instead of old DB source rows:

```sh
.private/venv/bin/python tools/millie_remote_purge_visible_snapshot.py \
  --cutoff-utc 2026-06-06T00:00:00+00:00 \
  --account geoff@clarktribe.com \
  --account geoff@cnb.llc \
  --action delete
```

The provider-visible snapshot searches live IMAP folders, fetches provider `INTERNALDATE`, applies the UTC cutoff, and includes only source UIDs that already exist in canonical `mail_messages` or deduped `mail_source_message_aliases`. Provider-visible UIDs that are not verified in MILLIE are skipped, not deleted.

### Hourly Provider Cleanup

Production can run a guarded hourly cleanup wrapper:

```sh
.private/venv/bin/python tools/millie_hourly_provider_purge.py --execute
```

The wrapper defaults to the configured online accounts for Gmail/clarktribe, CNB, iCloud, and Gmail/gclark82. Each run:

- leaves provider messages with `INTERNALDATE` inside the last 24 hours untouched;
- creates a manifest from source UIDs that are still visible online and already represented by canonical `mail_messages` or `mail_source_message_aliases` rows;
- skips provider-visible UIDs that are not verified in MILLIE;
- dry-runs the exact manifest first;
- executes provider-side deletion only when `automation_level=provider_write`, `automation_provider_write_enabled=true`, and the manifest id is explicit.

The default hourly cap is 5,000 verified source UIDs per run so old online mail drains gradually instead of issuing one very large provider delete pass.

## Dry-Run Planning

Use the planner to inspect how a source would flow through the dormant pipeline:

```sh
python3 tools/mail_import_plan.py --source pst --database postgres --pst tmp/your-archive.pst
```

Exchange OAuth IMAP plan:

```sh
python3 tools/mail_import_plan.py \
  --source exchange-oauth \
  --database sqlite \
  --host outlook.office365.com \
  --port 993 \
  --username user@example.com \
  --oauth-token-env MILLIE_MICROSOFT_ACCESS_TOKEN
```

The planner does not connect to IMAP, extract PST data, or write database rows.

## Canonical Database Records

Schemas live in:

- `db/schema/sqlite.sql`
- `db/schema/postgres.sql`

The schema is organized around these tables:

- `mail_sources`: PST, IMAP, and Exchange OAuth source definitions.
- `mail_import_jobs`: import job status, mode, errors, and metadata.
- `mail_folders` and `mail_message_folders`: source folder membership.
- `mail_source_message_aliases`: deduped source UIDs that point to an existing canonical message.
- `mail_messages`: subject, dates, body projections, IDs, hashes, and message-level metadata.
- `mail_message_addresses`: `from`, `sender`, `reply_to`, `to`, `cc`, `bcc`, and resent address roles.
- `mail_message_headers`: ordered raw headers.
- `mail_raw_mime`: complete original RFC822 bytes for high-fidelity recall/export.
- `mail_message_parts`: MIME tree records, including body parts, attachments, inline content, embedded message parts, content IDs, filenames, hashes, text, and binary data.
- `mail_message_metadata`: searchable key/value metadata.
- `mail_source_cursors`: per-source UID/cursor checkpoints for future incremental imports.
- `mail_remote_purge_manifests` and `mail_remote_purge_manifest_messages`: provider cleanup preparation manifests that prove copied source UIDs and tag protected MILLIE copies.
- `mail_search_documents`: flattened search text.

SQLite uses FTS5 through `mail_search_fts`. PostgreSQL uses a GIN index over `to_tsvector('simple', search_text)`.

## Recall Model

MILLIE should recall an email from two connected layers:

- `mail_raw_mime.content_blob` keeps the original message intact for faithful rehydration and export.
- Normalized tables make the message searchable, filterable, inspectable, and callable by API clients without reparsing the whole MIME message every time.

The original raw MIME should remain the source of truth whenever exact email reconstruction matters.

## Service Facade

The Postgres schema also includes a dormant `millie_*` service layer for identities, credentials, mailbox folders, source bindings, IMAP flags, webmail views, and protocol clients. See [Mail Service Facade](mail-service.md).
