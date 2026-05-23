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

Export profiles should define target-specific behavior without changing the canonical model.

Potential profiles:

- Generic EML bundle
- Generic MBOX folder tree
- Generic Maildir folder tree
- Thunderbird import profile
- Evolution import profile
- Apple Mail import profile
- Outlook workflow profile

Each profile should include:

- Output format
- Folder path mapping
- Filename rules
- Flag mapping
- Label/category mapping
- Timestamp handling
- Import instructions
- Known limitations

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

## Non-Goals For The First Export MVP

- Perfect proprietary PST/OLM round-tripping
- Guaranteed preservation of every client-specific flag
- Writeback sync to live accounts
- Editing messages during export
