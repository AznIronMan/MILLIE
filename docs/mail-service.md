# MILLIE Mail Service Facade

MILLIE is intended to expose copied mail archives like a normal mail service. A user such as `geon@millie.cnbsk.cloud` should be able to sign in and browse imported IMAP, Exchange OAuth, and PST mail from one mailbox without moving or mutating the original sources. Local aliases such as `geon@MILLIE` can remain valid through `millie.settings`.

This layer is an early development prototype. The schema, bootstrap helpers, IMAP listener, and webmail view are present for local/LAN testing, but they are not hardened production services.

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

- `millie_identities`: login identities such as `geon@millie.cnbsk.cloud`.
- `millie_identity_credentials`: password or app-password hashes.
- `millie_auth_sessions`: web/API/IMAP session tokens.
- `millie_protocol_clients`: protocol/client records for IMAP, webmail, and API access.

Passwords use PBKDF2-HMAC-SHA256 hashes in the dormant helper. Plain passwords are never written to generated SQL.

Generate bootstrap SQL for a local identity:

```sh
MILLIE_BOOTSTRAP_PASSWORD='temporary password' \
python3 tools/millie_identity_plan.py \
  --login geon@millie.cnbsk.cloud \
  --display-name Geon \
  --password-env MILLIE_BOOTSTRAP_PASSWORD \
  --output .private/local/geon_identity.sql
```

The tool does not connect to Postgres. Review and apply the generated SQL later when the service is ready.

## Live Sample Import

The temporary live sample importer can copy one message from each configured source into the Postgres-backed MILLIE mailbox:

```sh
.private/venv/bin/python tools/millie_live_sample_import.py --display-name Geon
```

The tool applies the Postgres schema, creates or updates the `geon@millie.cnbsk.cloud` identity from the configured `service_mail_domain`, imports one PST message, one password-IMAP message, and one Exchange OAuth IMAP message, then maps them into `INBOX`, `All Mail`, and source folders. If a local `geon@millie` identity already exists, the importer promotes it to the configured domain and keeps the existing mailbox/folder rows.

Generated dev IMAP credentials are stored under ignored `.private/local/millie_ios_mail_credentials.txt`.

## Dev IMAP Listener

Start the minimal listener:

```sh
.private/venv/bin/python tools/millie_imap_listener.py \
  --host 0.0.0.0 \
  --plain-port 22143 \
  --tls-port 22993 \
  --max-db-connections 8 \
  --imap-folder-mode compact \
  --daemon
```

The listener exposes:

- Plain IMAP on port `22143`.
- IMAP over TLS on port `22993` with a local self-signed certificate.
- Read operations for mailbox navigation, search, fetch, and raw RFC822 retrieval.
- Write operations against the MILLIE mailbox copy: folder create/delete/rename/subscribe, `APPEND`, flag updates, copy/move, and delete/expunge.

IMAP write operations update `millie_*` mailbox facade rows and imported `mail_*` records for messages appended directly into MILLIE. They do not write back to source IMAP accounts, Exchange mailboxes, or PST files.

The development listener limits concurrent Postgres-backed IMAP sessions with `--max-db-connections` so aggressive client indexing does not exhaust the dedicated recovery database. It defaults to `--imap-folder-mode compact`, which exposes only `INBOX`, `Sent`, `Drafts`, `Trash`, and `Junk` to avoid mobile clients indexing the full archive taxonomy. Use `--imap-folder-mode all` only when a desktop/archive client needs every MILLIE folder. If an archived raw MIME row is unreadable because the recovered Postgres payload is corrupt, the listener quarantines that message and returns a small placeholder instead of ending the client sync.

Some mail clients require an outgoing mail server during account setup. Start the temporary SMTP setup shim only when needed:

```sh
.private/venv/bin/python tools/millie_smtp_listener.py \
  --host 0.0.0.0 \
  --submission-port 22587 \
  --tls-port 22465 \
  --daemon
```

The SMTP setup shim accepts any SMTP username/password, or no SMTP authentication at all, so client configuration checks can pass. It discards message `DATA` and never relays, stores, queues, or delivers outbound mail. SMTP from MILLIE is intentionally unsupported for this archive workflow.

Stop the listener:

```sh
kill "$(cat .private/local/millie_imap_listener.pid)"
kill "$(cat .private/local/millie_smtp_listener.pid)"
```

This is a development listener, not a production mail server. It is intended to verify mailbox navigation and mailbox-copy edits from clients such as Apple Mail, Outlook, and iOS Mail.

## Autodiscover And Autoconfig

The temporary webmail listener on port `22001` serves mail-client discovery XML:

- `GET/POST /autodiscover/autodiscover.xml`
- `GET/POST /autodiscover/autodiscovery.xml`
- `GET /mail/config-v1.1.xml`
- `GET /autoconfig/mail/config-v1.1.xml`
- `GET /.well-known/autoconfig/mail/config-v1.1.xml`

The XML advertises IMAPS on `millie.cnbsk.cloud:993` and SMTP submissions on `millie.cnbsk.cloud:465`. SMTP remains a setup-only placeholder; MILLIE does not send outbound mail.

For public Outlook auto-setup, nginx must forward POST requests for `/autodiscover/autodiscover.xml` to the webmail listener. If nginx serves that path from a static location, Outlook receives `405 Not Allowed` even though GET works. The required nginx shape is:

```nginx
location = /autodiscover/autodiscover.xml {
    proxy_pass http://10.0.20.9:22001/autodiscover/autodiscover.xml;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

The same proxy rule can be duplicated for `/autodiscover/autodiscovery.xml` if clients or tests use that spelling.

## Runtime Live Sync

Live source checks run only while a MILLIE process is active. They are not installed as a macOS service.

To start webmail and have it check configured IMAP/OAuth accounts while webmail is running:

```sh
.private/venv/bin/python tools/millie_webmail_server.py \
  --host 0.0.0.0 \
  --port 22001 \
  --live-sync \
  --sync-fetch-batch-size 5 \
  --sync-interval 900
```

`--sync-account` can be repeated to limit the runtime sync to specific configured accounts. Without it, all enabled IMAP accounts in `millie.settings` are checked. The sync path imports new UIDs into MILLIE and does not write back to the source accounts.

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

An imported source can preserve its original folder structure under `Sources/<type>/...` while also appearing in `All Mail` or other service folders. Multiple PST imports stay separated under `Sources/PST/<archive name>/...`.

## Client/Server Shape

The schema is intended to support multiple frontends over the same mailbox:

- IMAP facade for Apple Mail, Outlook, iOS Mail, and other IMAP clients.
- Webmail frontend with Gmail/Outlook-like search, thread list, message view, and attachment download.
- API server for automation and local apps.

Expected read path:

1. Authenticate `geon@millie.cnbsk.cloud` or configured aliases such as `geon@MILLIE` against `millie_identities` and `millie_identity_credentials`.
2. Resolve the user's `millie_mailboxes` row.
3. List folders from `millie_mailbox_folders`.
4. List message summaries from `millie_v_mailbox_messages`.
5. Fetch complete raw messages from `mail_raw_mime` when an IMAP client asks for RFC822 content.
6. Fetch normalized parts and search text from `mail_message_parts` and `mail_search_documents` for webmail/API views.

Expected write path is limited to the MILLIE copy:

- IMAP flags and keywords update `millie_mailbox_messages`.
- Folder create/delete/rename/subscribe updates `millie_mailbox_folders`.
- IMAP copy/move changes folder membership in `millie_mailbox_messages`.
- IMAP `APPEND` stores a new canonical `mail_messages` record with raw MIME and maps it into the target MILLIE folder.
- Deletes mark the mailbox copy as deleted or expunged. Expunge hides the message from that folder view; it does not delete the original source account or PST.
- Sending mail, drafts workflows, labels, server-side rules, and source write-back are future workflows.

## Views

`millie_v_mailbox_messages` joins service mailbox state to canonical message metadata for IMAP and webmail message lists.

`millie_v_webmail_threads` groups visible messages into thread-like rows for future webmail views.

These views are query surfaces. Raw MIME remains the source of truth for exact email reconstruction.
