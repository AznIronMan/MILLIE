# IMAP Facade Compatibility Hardening

Status: COMPLETED

## Goal

Make the local read-only IMAP facade more likely to work with real desktop mail clients.

## Completed

- Added exact username/password mode while preserving loopback development login by default.
- Added a non-loopback guard so network binds require credentials unless dev login is explicitly allowed.
- Added direct IMAPS configuration through certificate/key paths.
- Added `AUTHENTICATE PLAIN` support in addition to `LOGIN`.
- Added common client fetch metadata: `ENVELOPE`, `BODYSTRUCTURE`, `RFC822.HEADER`, `RFC822.TEXT`, header field literals, text literals, and partial body literals.
- Expanded unit coverage for metadata fetches, partial fetches, wrong-password rejection, and non-loopback guard behavior.

## Verification

- `PYTHONPATH=src python3 -m unittest discover -s tests`
