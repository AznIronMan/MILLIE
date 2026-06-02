# Mail Import Pipeline

MILLIE has an import pipeline for turning mail sources into connected database records. Current live tools can copy PST files and configured IMAP accounts into the Postgres-backed MILLIE mailbox facade.

## Supported Source Shapes

- PST files through `readpst`, staged under `.private/local/pst-extract/`.
- IMAP accounts with password authentication.
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

By default, obvious non-mail folders exposed by some servers, such as Calendar, Contacts, Tasks, Journal, Notes, RSS Feeds, Outbox, and Sync Issues, are skipped. Use `--include-non-mail-folders` only when those folders should be copied as raw IMAP items too.

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
- `mail_messages`: subject, dates, body projections, IDs, hashes, and message-level metadata.
- `mail_message_addresses`: `from`, `sender`, `reply_to`, `to`, `cc`, `bcc`, and resent address roles.
- `mail_message_headers`: ordered raw headers.
- `mail_raw_mime`: complete original RFC822 bytes for high-fidelity recall/export.
- `mail_message_parts`: MIME tree records, including body parts, attachments, inline content, embedded message parts, content IDs, filenames, hashes, text, and binary data.
- `mail_message_metadata`: searchable key/value metadata.
- `mail_source_cursors`: per-source UID/cursor checkpoints for future incremental imports.
- `mail_search_documents`: flattened search text.

SQLite uses FTS5 through `mail_search_fts`. PostgreSQL uses a GIN index over `to_tsvector('simple', search_text)`.

## Recall Model

MILLIE should recall an email from two connected layers:

- `mail_raw_mime.content_blob` keeps the original message intact for faithful rehydration and export.
- Normalized tables make the message searchable, filterable, inspectable, and callable by API clients without reparsing the whole MIME message every time.

The original raw MIME should remain the source of truth whenever exact email reconstruction matters.

## Service Facade

The Postgres schema also includes a dormant `millie_*` service layer for identities, credentials, mailbox folders, source bindings, IMAP flags, webmail views, and protocol clients. See [Mail Service Facade](mail-service.md).
