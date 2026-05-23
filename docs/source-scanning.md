# Source Scanning

Source scanning is a read-only discovery step before import. It helps users point MILLIE at desktop mail-client storage and see which mailbox files can be imported.

## Thunderbird

The first scanner supports Thunderbird profile roots and directories that contain Thunderbird profiles.

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

## API

```http
GET /api/v1/source-scan?path=/path/to/profile&type=thunderbird
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

Selected candidates are imported through the normal `POST /api/v1/import` endpoint. The scanner does not write to the MILLIE database.

## CLI

```sh
PYTHONPATH=src python3 -m millie scan /path/to/profile --type thunderbird
PYTHONPATH=src python3 -m millie scan /path/to/profile --type thunderbird --json
```

`--type auto` falls back to generic file/folder detection when a Thunderbird profile is not found.
