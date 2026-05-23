# Prototype File Importers

Status: PENDING

## Goal

Implement initial file import adapters.

## Acceptance Criteria

- `.eml` import works.
- `mbox` import works.
- `maildir` import works.
- Thunderbird, Evolution, Apple Mail, PST, OLM, and OST support paths are documented.
- Unsupported or partially supported formats produce clear import errors.

## Progress

- `.eml`, `.eml` folder, `mbox`, `maildir`, and PST import paths exist.
- Thunderbird, Evolution, and Apple Mail scanning can discover open mailbox candidates before handing selected paths to the normal import endpoint.
- Apple `.emlx` files are normalized to RFC822 message bytes during import.
- OLM and OST scan as non-importable Outlook candidates with clear workaround notes.
- Direct OLM/OST import attempts now fail with actionable import job errors.
- Remaining desktop-client helper work includes choosing actual OLM/OST adapter toolchains and deeper unsupported-format UX around vendor-specific stores.
