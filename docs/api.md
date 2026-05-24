# API Notes

MILLIE exposes a local versioned API under `/api/v1`.

## Authentication

- `GET /api/v1/auth/status`
- `POST /api/v1/auth/setup`
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/logout`

Auth state is stored in the global SQLite settings file. `auth.dev_bypass` currently defaults to `true` for development, so API requests are accepted without a session until that setting is changed to `false`.

When bypass is off, protected API endpoints require the `millie_session` cookie. Static web assets and auth endpoints remain available so the browser can load the login/setup UI.

## Search And Messages

- `GET /api/v1/messages`
- `GET /api/v1/messages?mailbox_id=1`
- `GET /api/v1/messages?q=quarterly`
- `GET /api/v1/search?q=alice@example.com`
- `GET /api/v1/messages/{id}`
- `GET /api/v1/messages/{id}/html`
- `GET /api/v1/messages/{id}/raw`
- `GET /api/v1/attachments/{id}`

Search uses SQLite FTS5 over subject, participants, and text body. Query text is normalized before matching so email addresses and punctuation-heavy terms are safe.

HTML message viewing uses sanitized stored HTML. Raw HTML remains preserved in blob storage, but `/api/v1/messages/{id}/html` serves the sanitized representation with a restrictive content security policy. Attachments are downloaded through `Content-Disposition: attachment` responses.

## Import Jobs

- `GET /api/v1/source-scan?path=/path/to/profile&type=thunderbird`
- `GET /api/v1/source-scan?path=/path/to/store&type=evolution`
- `GET /api/v1/source-scan?path=/path/to/Mail&type=apple-mail`
- `POST /api/v1/import`
- `GET /api/v1/import-jobs`
- `GET /api/v1/import-jobs/{id}/errors`

Source scanning is read-only. It returns local candidate paths, source type, detected import format, mailbox path, size, message estimate, confidence, notes, and whether the candidate is currently importable. The web app can send importable candidates to `POST /api/v1/import`.

`POST /api/v1/import` accepts an optional `mailboxPath` value. Source-scan candidate imports use it so directory-based sources such as Apple Mail `Messages/*.emlx` keep the discovered mailbox path.

Unsupported vendor formats such as OLM and OST produce non-importable scan candidates. Direct import attempts fail with a clear import job error.

Import responses and job rows distinguish:

- `processed`: successfully parsed source items
- `imported` / `new_message_count`: newly created canonical messages
- `duplicates` / `duplicate_count`: source items that matched existing raw MIME content
- `errors` / `error_count`: failed source items

## IMAP Sources

- `GET /api/v1/imap-sources`
- `GET /api/v1/imap-providers`
- `POST /api/v1/imap-sources`
- `POST /api/v1/imap-sources/{id}/folders`
- `POST /api/v1/imap-sources/{id}/sync`
- `POST /api/v1/imap-sources/{id}/delete`
- `POST /api/v1/imap-sources/migrate-secrets`

IMAP source configs are stored per active profile. Saved configs store `auth_ref` secret references instead of raw password/app-password values.

`GET /api/v1/imap-sources` returns saved sources with the password redacted, `password_configured` set to `true` or `false`, and `secret_backend` showing where the secret reference resolves.

`GET /api/v1/imap-providers` returns provider presets such as generic IMAP and Gmail / Google Workspace. Presets include host, port, TLS, default folders, and host aliases.

`POST /api/v1/imap-sources` accepts `name`, `provider`, `host`, `port`, `username`, `password`, `use_tls`, `folders`, and `sync_limit`. `folders` can be a string or list. TLS defaults to on.

`POST /api/v1/imap-sources/{id}/folders` returns selectable and non-selectable folders discovered from the remote account using IMAP `LIST`.

`POST /api/v1/imap-sources/{id}/sync` performs a read-only sync. It accepts optional `folders` and `sync_limit` overrides for a one-off run. It imports newly discovered UIDs through the same raw-MIME pipeline as file imports, captures IMAP flags/internal dates when the server provides them, creates an import job, and returns processed/new/duplicate/error counts.

`POST /api/v1/imap-sources/{id}/delete` removes the saved source and deletes its secret reference.

`POST /api/v1/imap-sources/migrate-secrets` migrates legacy raw IMAP passwords from `imap.sources.v1` into the configured secret backend.

## POP Sources

- `GET /api/v1/pop-sources`
- `GET /api/v1/pop-providers`
- `POST /api/v1/pop-sources`
- `POST /api/v1/pop-sources/{id}/probe`
- `POST /api/v1/pop-sources/{id}/sync`
- `POST /api/v1/pop-sources/{id}/delete`
- `POST /api/v1/pop-sources/migrate-secrets`

POP source configs are stored per active profile. Saved configs store `auth_ref` secret references instead of raw password/app-password values.

`GET /api/v1/pop-providers` returns provider presets such as generic POP3 and Gmail / Google Workspace.

`POST /api/v1/pop-sources` accepts `name`, `provider`, `host`, `port`, `username`, `password`, `use_ssl`, and `sync_limit`.

`POST /api/v1/pop-sources/{id}/probe` logs in and checks capabilities, message count, maildrop size, and `UIDL` availability without using `RETR` or `DELE`.

`POST /api/v1/pop-sources/{id}/sync` performs a read-only sync with optional `sync_limit` override. It imports new `UIDL` values through the raw-MIME pipeline and never issues `DELE`.

`POST /api/v1/pop-sources/{id}/delete` removes the saved source and deletes its secret reference.

## Microsoft Graph Sources

- `GET /api/v1/graph-providers`
- `GET /api/v1/graph-sources`
- `POST /api/v1/graph-sources`
- `POST /api/v1/graph-sources/{id}/auth-url`
- `POST /api/v1/graph-sources/{id}/delete`

Microsoft Graph source configs are stored per active profile. Source configs store client id, tenant id, redirect URI, scopes, mailbox selector, and secret references for future token payloads or pending PKCE auth state.

`GET /api/v1/graph-providers` returns the Microsoft Graph provider preset and default read-only delegated scopes.

`POST /api/v1/graph-sources` accepts `name`, `client_id`, `tenant_id`, `redirect_uri`, `scopes`, `mailbox`, and `sync_limit`.

`POST /api/v1/graph-sources/{id}/auth-url` creates an OAuth authorization URL using authorization code flow with PKCE. MILLIE stores the PKCE code verifier in the configured secret backend and returns the authorization URL, state, scopes, and code challenge metadata.

Token exchange and Graph mail sync are not implemented yet.

## Export Jobs

- `GET /api/v1/export-profiles`
- `POST /api/v1/export`
- `GET /api/v1/export-jobs`
- `GET /api/v1/export-jobs/{id}/items`

Export profiles include a target ID, display name, supported formats, recommended format, import instructions, and known limitations. `POST /api/v1/export` accepts `targetProfile` and `format`; use `format: "auto"` to select the profile recommendation.

## Other Current Endpoints

- `GET /api/v1/health`
- `GET /api/v1/profiles`
- `POST /api/v1/profiles`
- `POST /api/v1/profiles/active`
- `GET /api/v1/sources`
- `GET /api/v1/mailboxes`
- `GET /api/v1/migrations`
