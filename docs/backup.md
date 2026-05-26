# Backup

MILLIE can package the active profile into a portable ZIP archive.

## Create A Backup

```sh
PYTHONPATH=src python3 -m millie backup --output .private/local/backups
```

The web app also has a backup control in the message detail toolbar.

If `--output` is a directory or has no `.zip` suffix, MILLIE creates a file named `millie-backup-<profile-id>.zip` inside that directory.

The archive contains:

- `manifest.json`
- `profile/millie.sqlite`
- `profile/data/...`
- `settings/millie.settings`
- `settings/profile.settings`

The manifest includes the MILLIE version, creation time, active profile metadata, warning list, and file hashes.

## Secret Redaction

Backups redact secret-bearing settings by default. This removes known local secret keys such as session secrets, password hashes, and profile-local secret stores from the copied settings databases before packaging.

Use this only for controlled local moves where preserving local fallback secrets is required:

```sh
PYTHONPATH=src python3 -m millie backup --output .private/local/backups --include-secrets
```

`--include-secrets` can package sensitive local material. Treat that ZIP like a password vault.

## Restore

Restore a backup into a new profile:

```sh
PYTHONPATH=src python3 -m millie restore-backup .private/local/backups/default/millie-backup-default.zip \
  --name "Restored Archive" \
  --id restored-archive
```

MILLIE validates every manifest file hash before copying data. Restore creates a new profile and switches to it by default. Use `--no-switch` to leave the current profile active.

The web app can also restore a backup ZIP from the message detail toolbar.
