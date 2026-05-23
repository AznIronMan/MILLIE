# Build Export MVP

Status: COMPLETED

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

## Result

- Implemented raw-MIME-first EML, MBOX, and Maildir exports.
- Added export jobs, export items, and generated JSON manifests.
- Added profile-aware export with `generic-eml`, `generic-mbox`, `generic-maildir`, `thunderbird`, `evolution`, `apple-mail`, and `outlook-workflow`.
- Added `format=auto` to select each profile's recommended format.
- Added manifest metadata for profile instructions, limitations, source filters, message counts, folder counts, attachment counts, source IDs, hashes, and per-item warnings.
- Added web export profile controls and export job drill-down integration.

## Verification

- `PYTHONWARNINGS=error PYTHONPATH=src python3 -m unittest discover -s tests`
- `npm run build` from `web/`

## Notes

Preserving raw MIME during import is required for the best export fidelity.
