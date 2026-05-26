# POP Sync

MILLIE includes an initial read-only POP3 connector for importing messages from accounts that expose POP access.

## Scope

Current POP support is intentionally narrow:

- Password or app-password login through Python `poplib`
- Secret references for stored credentials
- SSL by default, with plain POP3 available only for local/dev testing
- Provider presets for generic POP3, Gmail / Google Workspace, Outlook.com / Microsoft 365, Yahoo Mail, AOL Mail, Fastmail, and Zoho Mail
- Safe account probe using `USER`, `PASS`, `CAPA`, `STAT`, and `UIDL`
- Incremental sync using POP `UIDL`
- Raw message retrieval through `RETR` only during explicit sync
- No `DELE` command path; POP sync never deletes server mail
- Existing raw-MIME preservation, dedupe, search, HTML sanitization, attachment capture, and export support after import

POP does not expose folders. MILLIE stores POP-imported messages in a `POP` mailbox for the source.

## Settings

Saved POP source configs live in the active profile SQLite settings file under the `pop.sources.v1` key.

The source config stores an `auth_ref`, not the password/app password. It uses the same secret backend as IMAP: macOS Keychain by default on macOS when available, with a local profile-settings fallback for development.

Incremental sync state lives in the profile mail database in `source_sync_states`, keyed by source and the `maildrop` scope. The state stores seen `UIDL` values and a `delete_policy` of `never`.

## CLI

```sh
PYTHONPATH=src python3 -m millie pop-add "Gmail POP" \
  --host pop.gmail.com \
  --username user@example.com \
  --limit 100

PYTHONPATH=src python3 -m millie pop-sources
PYTHONPATH=src python3 -m millie pop-probe gmail-pop
PYTHONPATH=src python3 -m millie pop-sync gmail-pop --limit 5
PYTHONPATH=src python3 -m millie pop-migrate-secrets
```

Use `--no-ssl` only for trusted local/dev servers.

Common preset defaults:

| Provider | POP host | Port | Notes |
| --- | --- | ---: | --- |
| Gmail / Google Workspace | `pop.gmail.com` | 995 | Gmail POP must be enabled in Gmail settings before MILLIE can probe or sync it. |
| Outlook.com / Microsoft 365 | `outlook.office365.com` | 995 | Microsoft requires Modern Auth/OAuth2 for Outlook.com; use Graph for the main Exchange Online path. |
| Yahoo Mail | `pop.mail.yahoo.com` | 995 | Yahoo requires SSL and usually an app password for third-party clients. |
| AOL Mail | `pop.aol.com` | 995 | AOL requires SSL and the full email address as the username. |
| Fastmail | `pop.fastmail.com` | 995 | Fastmail requires an app password and SSL/TLS. |
| Zoho Mail | `pop.zoho.com` | 995 | Enable POP in Zoho first; some paid/data-center accounts may need account-specific server details. |

iCloud Mail does not support POP, so MILLIE only provides an iCloud IMAP preset.

`pop-probe` does not retrieve message contents and does not delete anything. It checks POP login, capabilities, maildrop size, and `UIDL` support.

`pop-sync` retrieves message bodies for new UIDLs and imports them. It does not call `DELE`.

`pop-delete` removes a saved POP source and deletes its secret reference from the configured secret backend.

## API

- `GET /api/v1/pop-sources`
- `GET /api/v1/pop-providers`
- `POST /api/v1/pop-sources`
- `POST /api/v1/pop-sources/{id}/probe`
- `POST /api/v1/pop-sources/{id}/sync`
- `POST /api/v1/pop-sources/{id}/delete`
- `POST /api/v1/pop-sources/migrate-secrets`

`GET /api/v1/pop-sources` redacts the stored password and only reports whether one is configured.

`GET /api/v1/pop-providers` returns provider presets for the web UI and other clients.

`POST /api/v1/pop-sources` accepts:

- `name`
- `provider`
- `host`
- `port`
- `username`
- `password`
- `use_ssl`
- `sync_limit`

`POST /api/v1/pop-sources/{id}/probe` logs in, checks capabilities, runs `STAT` and `UIDL`, and reports that `RETR` and `DELE` were not used.

`POST /api/v1/pop-sources/{id}/sync` runs a read-only sync immediately and creates an import job. Optional `sync_limit` can override the saved config for a single run.

`POST /api/v1/pop-sources/{id}/delete` removes the saved source and deletes its secret reference.

## Follow-Up

- Add OAuth/provider credential flows where POP providers support them.
- Harden recovery paths for revoked credentials, large maildrops, and partial sync continuation.

## References

- [Gmail IMAP, POP, and SMTP](https://developers.google.com/gmail/imap/imap-smtp)
- [Outlook.com POP, IMAP, and SMTP settings](https://support.microsoft.com/en-gb/office/pop-imap-and-smtp-settings-for-outlook-com-d088b986-291d-42b8-9564-9c414e2aa040)
- [Yahoo POP settings](https://help.yahoo.com/kb/mail/review-adjust-settings-sln7754.html)
- [AOL POP and IMAP settings](https://help.aol.com/articles/how-do-i-use-other-email-applications-to-send-and-receive-my-aol-mail)
- [Fastmail server names and ports](https://www.fastmail.help/hc/en-us/articles/1500000278342-Server-names-and-ports)
- [Zoho Mail POP access](https://www.zoho.com/mail/help/pop-access.html)
