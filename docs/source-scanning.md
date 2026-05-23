# Source Scanning

Source scanning is a read-only discovery step before import. It helps users point MILLIE at desktop mail-client storage and see which mailbox files can be imported.

## Thunderbird

The Thunderbird scanner supports Thunderbird profile roots and directories that contain Thunderbird profiles.

It looks for profile markers such as:

- `prefs.js`
- `Mail/`
- `ImapMail/`
- `global-messages-db.sqlite`

Within `Mail/` and `ImapMail/`, it detects:

- Thunderbird MBOX mailbox files, including extensionless files such as `Inbox`, `Sent`, and nested `.sbd` folders
- Maildir-style folders with `cur/` and `new/`
- Direct folders of `.eml` or `.emlx` files

It ignores Thunderbird metadata files such as `.msf` indexes, `panacea.dat`, `folderTree.json`, `global-messages-db.sqlite`, `msgFilterRules.dat`, and related SQLite or JSON files.

## Evolution

The Evolution scanner supports local mail store roots and account/local folders.

It detects:

- MBOX mailbox files, including extensionless files that begin with an MBOX `From ` separator
- Maildir-style folders with `cur/` and `new/`
- Direct folders of `.eml` or `.emlx` files

It ignores common metadata and index files such as `folders.db`, `*.cmeta`, `*.ibex.index`, `*.ev-summary`, and related SQLite or JSON files.

## Apple Mail

The Apple Mail scanner supports `~/Library/Mail` style roots, version folders such as `V10`, and exported `.mbox` packages.

It detects:

- `.mbox` packages that contain a raw `mbox` file
- `.mbox` packages that contain `Messages/*.emlx`
- Standalone Apple Mail MBOX files where present

When importing `.emlx` files, MILLIE strips the Apple wrapper line and trailing plist metadata before parsing the RFC822 message content.

## Outlook Vendor Stores

The scanner detects `.pst`, `.olm`, and `.ost` files.

- PST candidates are importable when `readpst/libpst` is installed.
- OLM candidates are currently marked `importable: false`.
- OST candidates are currently marked `importable: false`.

OLM and OST candidates include notes with the current workaround. Direct import attempts for OLM/OST produce clear failed import jobs instead of treating the files as MBOX.

## API

```http
GET /api/v1/source-scan?path=/path/to/profile&type=thunderbird
GET /api/v1/source-scan?path=/path/to/store&type=evolution
GET /api/v1/source-scan?path=/path/to/Mail&type=apple-mail
GET /api/v1/source-scan?path=/path/to/archive.olm&type=auto
```

Each candidate includes:

- stable local scan ID
- source type
- import format
- absolute path
- display name
- mailbox path
- byte size
- message estimate when available
- confidence
- notes
- `importable`, so clients can disable unsupported candidates

Selected candidates are imported through the normal `POST /api/v1/import` endpoint. Candidate imports can pass `mailboxPath` so directory-based sources keep the discovered mailbox path. The scanner does not write to the MILLIE database.

## CLI

```sh
PYTHONPATH=src python3 -m millie scan /path/to/profile --type thunderbird
PYTHONPATH=src python3 -m millie scan /path/to/evolution/mail --type evolution
PYTHONPATH=src python3 -m millie scan ~/Library/Mail --type apple-mail
PYTHONPATH=src python3 -m millie scan /path/to/archive.olm --type auto
PYTHONPATH=src python3 -m millie scan /path/to/profile --type thunderbird --json
```

`--type auto` tries known desktop-client layouts before falling back to generic file/folder detection.
