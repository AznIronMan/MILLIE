# Local IMAP Facade

MILLIE includes an early read-only IMAP facade so external mail clients can browse imported mail from the active profile.

## Run

```sh
PYTHONPATH=src python3 -m millie imap-facade
```

Defaults:

- Host: `127.0.0.1`
- Port: `22143`
- Profile: previously selected active profile
- Authentication: development login accepted on loopback unless exact credentials are supplied
- Mutations: refused

Override the bind address only for controlled testing:

```sh
PYTHONPATH=src python3 -m millie imap-facade --host 127.0.0.1 --port 22143
```

Require exact credentials:

```sh
PYTHONPATH=src python3 -m millie imap-facade --username archive
```

MILLIE prompts for the password when `--username` is provided without `--password`.

Enable direct IMAPS with a certificate and key:

```sh
PYTHONPATH=src python3 -m millie imap-facade \
  --port 22993 \
  --username archive \
  --tls-cert .private/local/tls/dev.crt \
  --tls-key .private/local/tls/dev.key
```

Binding to a non-loopback host requires exact credentials unless `--allow-dev-login` is explicitly set.

## Current Protocol Surface

Supported enough for first compatibility testing:

- `CAPABILITY`
- `AUTHENTICATE PLAIN`
- `LOGIN`
- `NAMESPACE`
- `LIST`
- `LSUB`
- `SELECT`
- `EXAMINE`
- `STATUS`
- `SEARCH ALL`
- `UID SEARCH ALL`
- `FETCH`
- `UID FETCH`
- `ENVELOPE`
- `BODYSTRUCTURE`
- `RFC822`, `RFC822.HEADER`, and `RFC822.TEXT`
- `BODY[]`, `BODY.PEEK[]`, `BODY[HEADER]`, `BODY[TEXT]`, and `BODY[HEADER.FIELDS (...)]`
- Partial body literals such as `BODY.PEEK[TEXT]<0.1024>`
- `NOOP`
- `CLOSE`
- `LOGOUT`

Mutating commands such as `APPEND`, `COPY`, `STORE`, `DELETE`, `EXPUNGE`, `MOVE`, and `RENAME` return `NO`.

## Mapping

- MILLIE mailboxes become IMAP folders using the stored mailbox path and `/` delimiter.
- Local canonical message ids are exposed as stable IMAP UIDs.
- Raw MIME from the blob store is returned for full-message fetches.
- Header/text/partial fetches are served from the preserved raw MIME content.
- `ENVELOPE` and `BODYSTRUCTURE` are derived from parsed raw MIME metadata.
- Stored flags are exposed when available, but the facade currently has no writeback path.

## Client Notes

This is a protocol MVP, not a fully certified mail server.

Expected near-term tests:

- Thunderbird can connect to `127.0.0.1:22143`, list folders, and fetch messages.
- Apple Mail can add a manual IMAP account pointed at localhost and browse imported folders.
- Evolution can connect to the local account and browse/fetch messages.
- Outlook compatibility needs extra attention because account setup may expect specific TLS/certificate and capability combinations.

Do not bind this service to `0.0.0.0` with real mail unless exact credentials are configured and the network is trusted. Prefer loopback-only testing until the facade has broader client compatibility coverage.
