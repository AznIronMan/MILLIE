# Export And Round-Trip Strategy

MILLIE should export mail into formats that common mail clients can import while keeping as much original content as possible.

## Fidelity Goal

The preferred export path is raw-message-first:

1. If the original raw MIME message is available, write it back out unchanged for message-level exports.
2. Preserve folder paths and message ordering through the export layout.
3. Preserve flags, labels, and read/unread state where the target format supports them.
4. Emit a manifest that documents anything that could not be represented.
5. Reconstruct MIME only when the original raw message was not stored.

This helps preserve:

- Original headers
- `Message-ID`, `In-Reply-To`, and `References`
- Dates and received paths
- Text and HTML bodies
- MIME structure and boundaries
- Inline images
- Attachments
- S/MIME or PGP payloads, when kept as original MIME

## Initial Export Formats

### EML

Best for individual messages, portable archives, and import workflows that accept message files.

### MBOX

Best for folder-based import into Thunderbird, Apple Mail, Evolution, and many migration tools.

### Maildir

Best for Unix-style mail stores, Evolution workflows, Dovecot/testing scenarios, and deterministic folder exports.

## Client Profiles

Export profiles define target-specific behavior without changing the canonical model. The first profiles are:

- `generic-eml`: EML bundle, one file per message, grouped by mailbox path.
- `generic-mbox`: MBOX files, one file per mailbox path.
- `generic-maildir`: Maildir folder tree with `tmp`, `new`, and `cur`.
- `thunderbird`: Thunderbird-friendly MBOX by default, with EML available.
- `evolution`: Evolution-oriented Maildir by default, with MBOX available.
- `apple-mail`: Apple Mail-oriented MBOX by default, with EML available.
- `outlook-workflow`: EML workflow placeholder until a reliable PST writer or local IMAP copy path is selected.

Use `format=auto` to select a profile's recommended format.

## Current Client Workflows

### Thunderbird

Export with the `thunderbird` profile. MILLIE recommends MBOX output. Import the generated `.mbox` files through Thunderbird's mailbox import workflow, then compare the imported folder/message counts with the manifest.

### Evolution

Export with the `evolution` profile. MILLIE recommends Maildir output, with MBOX available for import workflows that prefer it. Review manifest warnings for unsupported flag or label mappings.

### Apple Mail

Export with the `apple-mail` profile. MILLIE recommends MBOX output. Use Apple Mail's import mailbox workflow and select the generated `.mbox` files. Compare folder/message counts with the manifest after import.

### Outlook Workflow

Export with the `outlook-workflow` profile. MILLIE currently recommends EML bundles for near-term migration workflows. Direct PST/OLM writing remains advanced until a reliable writer/toolchain is approved.

## Outlook Notes

Outlook-native PST export is desirable but should be treated as an advanced feature until a reliable writer or toolchain is selected.

Near-term Outlook-compatible paths may include:

- EML bundle export where supported by the user's Outlook workflow
- Local read-only IMAP facade so Outlook can copy mail into a mailbox
- PST generation through a vetted external library or tool

## Export Manifest

Every export job should produce a manifest.

Manifest fields should include:

- MILLIE version
- Export job ID
- Target profile
- Output format
- Created timestamp
- Source filters
- Message count
- Folder count
- Attachment count
- Output file list and hashes
- Per-message source IDs
- Warnings
- Errors
- Unsupported metadata

The current manifest includes:

- MILLIE version
- Export job ID
- Target profile ID and display name
- Profile metadata, import instructions, and known limitations
- Output format
- Created timestamp
- Source filters
- Message, unique message, folder, attachment, error, and warning counts
- Source IDs
- Per-output item paths and hashes
- Per-message source IDs, mailbox IDs, mailbox paths, subjects, hashes, and warnings

## Non-Goals For The First Export MVP

- Perfect proprietary PST/OLM round-tripping
- Guaranteed preservation of every client-specific flag
- Writeback sync to live accounts
- Editing messages during export
