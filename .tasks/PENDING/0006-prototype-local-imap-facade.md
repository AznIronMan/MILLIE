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
- The facade lists folders, selects/examines mailboxes, returns status/search responses, fetches raw MIME, serves common metadata requests, and supports partial/header/text body fetches.
- Local message ids are exposed as stable UIDs.
- Mutating commands are rejected.
- Optional exact username/password auth, a non-loopback safety guard, and direct IMAPS cert/key configuration exist.
- First compatibility notes live in `docs/imap-facade.md`.
- Compatibility helpers now include `ID`, `ENABLE`, `XLIST`, `CHECK`, `UNSELECT`, `IDLE`, and special-use folder hints.

## Remaining

- Test real Thunderbird, Apple Mail, Evolution, and Outlook client setup flows.
- Add client-specific compatibility fixes discovered from real setup flows.
