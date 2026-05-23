# PST Import

MILLIE uses `readpst` from `libpst` as the first PST adapter path.

## Prerequisite

```sh
brew install libpst
```

Check availability:

```sh
PYTHONPATH=src python3 -m millie doctor
```

The doctor command reports `readpst/libpst` as optional. PST import is disabled when `readpst` is unavailable.

## Import

```sh
PYTHONPATH=src python3 -m millie import /path/to/archive.pst --format pst
```

The importer runs `readpst` into an ignored temporary extraction directory, then imports the generated `.eml` files through the normal raw-MIME-first pipeline.

## Current Smoke Test

The local ignored fixture in `.private/local/fixtures/` was copied from a user-provided source path. `lspst` can read it, and `readpst` can extract `.eml` files from it.

The first isolated import smoke test produced:

- 193 imported messages
- 6 mailboxes
- 327 attachments
- 0 import errors

Do not commit PST files, extracted messages, or generated metadata from real mail.

## Limitations

- PST support depends on `readpst` behavior.
- Non-email PST items are not imported yet.
- Folder and message counts can differ between `lspst` listing and generated `.eml` output.
- This is a file import path, not Outlook profile sync.
