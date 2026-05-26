# Prototype Local IMAP Facade

Status: PENDING

## Goal

Expose imported mail to external mail clients through a local read-only IMAP server.

## Acceptance Criteria

- Read-only IMAP server can list folders.
- Read-only IMAP server can list and fetch messages.
- UID mapping is stable.
- Apple Mail, Outlook, Thunderbird, and Evolution compatibility notes are captured.
- Write support is explicitly deferred unless approved.

## Progress

- Initial read-only server command exists as `millie imap-facade`.
- The facade lists folders, selects/examines mailboxes, returns status/search responses, and fetches raw MIME.
- Local message ids are exposed as stable UIDs.
- Mutating commands are rejected.
- First compatibility notes live in `docs/imap-facade.md`.

## Remaining

- Test real Thunderbird, Apple Mail, Evolution, and Outlook client setup flows.
- Add stronger auth before any non-local bind.
- Decide whether the facade needs direct TLS or should stay behind local-only/plaintext for this phase.
