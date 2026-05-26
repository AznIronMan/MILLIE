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
- Authentication: development login accepted
- Mutations: refused

Override the bind address only for controlled testing:

```sh
PYTHONPATH=src python3 -m millie imap-facade --host 127.0.0.1 --port 22143
```

## Current Protocol Surface

Supported enough for first compatibility testing:

- `CAPABILITY`
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
- `NOOP`
- `CLOSE`
- `LOGOUT`

Mutating commands such as `APPEND`, `COPY`, `STORE`, `DELETE`, `EXPUNGE`, `MOVE`, and `RENAME` return `NO`.

## Mapping

- MILLIE mailboxes become IMAP folders using the stored mailbox path and `/` delimiter.
- Local canonical message ids are exposed as stable IMAP UIDs.
- Raw MIME from the blob store is returned for `RFC822`, `BODY[]`, and `BODY.PEEK[]` fetches.
- Stored flags are exposed when available, but the facade currently has no writeback path.

## Client Notes

This is a protocol MVP, not a fully certified mail server.

Expected near-term tests:

- Thunderbird can connect to `127.0.0.1:22143`, list folders, and fetch messages.
- Apple Mail can add a manual IMAP account pointed at localhost and browse imported folders.
- Evolution can connect to the local account and browse/fetch messages.
- Outlook compatibility needs extra attention because account setup may expect TLS, stronger auth, or server capability combinations.

Do not bind this service to `0.0.0.0` with real mail until authentication and optional TLS for the facade are designed.
