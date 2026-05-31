# MILLIE Mail Service Facade

MILLIE is intended to expose copied mail archives like a normal mail service. A user such as `geon@MILLIE` should be able to sign in and browse imported IMAP, Exchange OAuth, and PST mail from one mailbox without moving or mutating the original sources.

This layer is dormant for now. The schema and bootstrap helpers are present, but no IMAP listener, webmail server, or live authentication service is started.

## Core Model

MILLIE has two storage layers:

- `mail_*`: canonical copied archive data from sources. These tables preserve source provenance, normalized headers/addresses/body/parts, raw MIME, attachments, metadata, and search text.
- `millie_*`: service-facing identity and mailbox data. These tables decide who can sign in, which mailbox they see, which folders exist, and how copied messages appear to IMAP/webmail clients.

Source mail should flow one way into MILLIE:

```text
IMAP / Exchange OAuth / PST -> MILLIE copy/archive -> IMAP facade / API / webmail
```

The source account or PST remains the source. MILLIE stores a copy and presents that copy as a mailbox.

## Postgres-Backed Authentication

Authentication state is stored in Postgres under `millie_*` tables:

- `millie_identities`: login identities such as `geon@millie`.
- `millie_identity_credentials`: password or app-password hashes.
- `millie_auth_sessions`: web/API/IMAP session tokens.
- `millie_protocol_clients`: protocol/client records for IMAP, webmail, and API access.

Passwords use PBKDF2-HMAC-SHA256 hashes in the dormant helper. Plain passwords are never written to generated SQL.

Generate bootstrap SQL for a local identity:

```sh
MILLIE_BOOTSTRAP_PASSWORD='temporary password' \
python3 tools/millie_identity_plan.py \
  --login geon@MILLIE \
  --display-name Geon \
  --password-env MILLIE_BOOTSTRAP_PASSWORD \
  --output .private/local/geon_identity.sql
```

The tool does not connect to Postgres. Review and apply the generated SQL later when the service is ready.

## Live Sample Import

The temporary live sample importer can copy one message from each configured source into the Postgres-backed MILLIE mailbox:

```sh
.private/venv/bin/python tools/millie_live_sample_import.py --login geon@MILLIE --display-name Geon
```

The tool applies the Postgres schema, creates or updates the `geon@millie` identity, imports one PST message, one password-IMAP message, and one Exchange OAuth IMAP message, then maps them into `INBOX`, `All Mail`, and source folders.

Generated dev IMAP credentials are stored under ignored `.private/local/millie_ios_mail_credentials.txt`.

## Dev IMAP Listener

Start the minimal listener:

```sh
.private/venv/bin/python tools/millie_imap_listener.py \
  --host 0.0.0.0 \
  --plain-port 22143 \
  --tls-port 22993 \
  --daemon
```

The listener exposes:

- Plain IMAP on port `22143`.
- IMAP over TLS on port `22993` with a local self-signed certificate.

iOS Mail normally requires an outgoing mail server during account setup. Start the temporary authenticated SMTP companion:

```sh
.private/venv/bin/python tools/millie_smtp_listener.py \
  --host 0.0.0.0 \
  --submission-port 22587 \
  --tls-port 22465 \
  --daemon
```

The SMTP listener authenticates with the same `geon@millie` credential and accepts test messages for dev discard. It is only there so mail clients can complete account setup; sending and archival of outgoing mail are future workflows.

Stop the listener:

```sh
kill "$(cat .private/local/millie_imap_listener.pid)"
kill "$(cat .private/local/millie_smtp_listener.pid)"
```

This is a development listener, not a production mail server. It is intended to verify mailbox navigation from clients such as Apple Mail, Outlook, and iOS Mail.

## Mailbox Facade

Mailbox state is stored in:

- `millie_mailboxes`: service-visible mailbox addresses.
- `millie_mailbox_folders`: IMAP/webmail folders such as `INBOX`, `All Mail`, `Archive`, `Sent`, `Trash`, and source-preserving folders under `Sources/`.
- `millie_source_mailbox_bindings`: one-way source-to-mailbox mappings.
- `millie_mailbox_messages`: folder membership, IMAP UID, flags, keywords, internal date, and the pointer back to canonical `mail_messages`.

Default folders are:

- `INBOX`
- `All Mail`
- `Archive`
- `Sent`
- `Drafts`
- `Trash`
- `Junk`
- `Sources`
- `Sources/IMAP`
- `Sources/PST`

An imported source can preserve its original folder structure under `Sources/<type>/...` while also appearing in `All Mail` or other service folders.

## Client/Server Shape

The schema is intended to support multiple frontends over the same mailbox:

- IMAP facade for Apple Mail, Outlook, iOS Mail, and other IMAP clients.
- Webmail frontend with Gmail/Outlook-like search, thread list, message view, and attachment download.
- API server for automation and local apps.

Expected read path:

1. Authenticate `geon@MILLIE` against `millie_identities` and `millie_identity_credentials`.
2. Resolve the user's `millie_mailboxes` row.
3. List folders from `millie_mailbox_folders`.
4. List message summaries from `millie_v_mailbox_messages`.
5. Fetch complete raw messages from `mail_raw_mime` when an IMAP client asks for RFC822 content.
6. Fetch normalized parts and search text from `mail_message_parts` and `mail_search_documents` for webmail/API views.

Expected write path is limited at first:

- IMAP flags and keywords update `millie_mailbox_messages`.
- Deletes should mark the mailbox copy as deleted or expunged, not delete the canonical `mail_*` record by default.
- Sending mail, drafts, labels, server-side rules, and source write-back are future workflows.

## Views

`millie_v_mailbox_messages` joins service mailbox state to canonical message metadata for IMAP and webmail message lists.

`millie_v_webmail_threads` groups visible messages into thread-like rows for future webmail views.

These views are query surfaces. Raw MIME remains the source of truth for exact email reconstruction.
