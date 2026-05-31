# PST Probe

MILLIE has an early read-only PST probe for validating that a local Outlook PST can be opened and converted into a format the future importer can consume.

The current probe is not the final import workflow. It extracts email messages to an ignored local working directory and prints a metadata-only report. It does not print message bodies, subjects, senders, or recipients.

## Requirements

- Python 3.
- `readpst` from libpst.

On macOS with Homebrew:

```sh
brew install libpst
```

## Run The Probe

From the project root:

```sh
python3 tools/pst_probe.py tmp/your-archive.pst --clean
```

The probe:

- Reads the source PST without intentionally writing to it.
- Computes the PST SHA-256 before and after extraction and reports whether it stayed unchanged.
- Uses `readpst -M -te` to extract email-only MH files.
- Writes derived files under `.private/local/pst-extract/<pst-name>-<hash>/`.
- Writes `pst_probe_manifest.json` in the extraction directory.
- Prints counts, date range, header presence counts, folder counts, parse errors, and attachment MIME part counts.

## Password Input

Do not put PST passwords directly on the command line. Use one of:

```sh
MILLIE_PST_PASSWORD='value goes here' \
python3 tools/pst_probe.py tmp/your-archive.pst --password-env MILLIE_PST_PASSWORD
```

```sh
python3 tools/pst_probe.py tmp/your-archive.pst --password-file .private/secrets/pst-password.txt
```

```sh
python3 tools/pst_probe.py tmp/your-archive.pst --password-prompt
```

The current installed `readpst` backend has no password parameter. MILLIE validates that password input exists and records that a password was supplied, but cannot pass the password to `readpst`. If this backend cannot open a locked PST, the probe fails with an explicit password-backend error.

## Report Shape

The compact report includes:

- PST path.
- PST SHA-256.
- `readpst` binary path.
- Extraction directory.
- Manifest path.
- Parsed message count and parse error count.
- Attachment-bearing message count and total attachment MIME part count.
- Header presence counts for `Message-ID`, `From`, and `To`.
- Message date range when dates are available.
- Source unchanged status.
- Folder message counts.

## Notes And Limits

- Generated extraction output contains real email data and must stay ignored.
- The source PST belongs in `tmp/`, `.private/`, or another ignored location.
- The probe currently handles email items only. Contacts, calendars, journals, and other Outlook item types are not part of the first pass.
- MH mode is used because mbox-style recursive extraction can hit Outlook folder/file naming collisions.
