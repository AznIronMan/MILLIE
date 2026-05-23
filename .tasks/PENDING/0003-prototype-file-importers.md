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
- Thunderbird profile scanning can discover extensionless MBOX folders, Maildir folders, and `.eml` folders before handing selected candidates to the normal import endpoint.
- Remaining desktop-client helper work includes Evolution and Apple Mail source scans, OLM/OST path decisions, and clearer unsupported-format UX around vendor-specific stores.
