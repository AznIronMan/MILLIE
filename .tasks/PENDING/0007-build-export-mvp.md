# Build Export MVP

Status: PENDING

## Goal

Export imported mail into common mailbox formats that can be imported by major mail clients while preserving as much original content as possible.

## Context

The export path should be raw-message-first. If original MIME content was stored during import, export should reuse it rather than reconstructing from normalized fields.

## Acceptance Criteria

- `.eml` export works for selected messages.
- `mbox` export works for selected folders.
- `maildir` export works for selected folders.
- Export manifests include counts, hashes, warnings, errors, source IDs, and unsupported metadata.
- Export profiles exist for generic EML, generic MBOX, generic Maildir, Thunderbird, Evolution, Apple Mail, and Outlook workflow paths.
- PST/OLM direct export remains documented as advanced until a reliable writer/toolchain is approved.

## Notes

Preserving raw MIME during import is required for the best export fidelity.
