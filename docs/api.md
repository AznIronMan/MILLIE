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

## Sync State

- `GET /api/v1/sync-states`
- `GET /api/v1/background-jobs`
- `POST /api/v1/background-jobs/sync`
- `POST /api/v1/background-jobs/{id}/cancel`

`GET /api/v1/sync-states` returns parsed connector cursor/recovery state for IMAP, POP, and Graph scopes. Each row includes source identity, scope, `updated_at`, redacted `state_json`, parsed `state`, latest import job metadata, and a `source_config_id` when it can be resolved from job options. Provider cursor links such as Graph delta/next URLs are redacted while preserving configured/not-configured signals.

`GET /api/v1/background-jobs` returns in-process background sync jobs for the current server run. `POST /api/v1/background-jobs/sync` queues an IMAP, POP, or Graph sync without blocking the web request. It accepts `connector`, `sourceId`, optional `folders`, and optional `sync_limit`. `POST /api/v1/background-jobs/{id}/cancel` cancels a queued job or marks a running job as cancel-requested. Running connector calls are not interrupted mid-request in the first worker implementation.

## API Tokens

- `GET /api/v1/api-tokens`
- `POST /api/v1/api-tokens`
- `POST /api/v1/api-tokens/{id}/revoke`

API token management requires a browser/session login or development bypass. Created tokens are shown once, stored as hashes in `millie.settings`, and can be used as `Authorization: Bearer <token>` for API calls. Scopes are currently coarse: `read` for `GET` API calls and `write` for `POST` API calls.

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

`POST /api/v1/imap-sources/{id}/sync` performs a read-only sync. It accepts optional `folders` and `sync_limit` overrides for a one-off run. It imports newly discovered UIDs through the same raw-MIME pipeline as file imports, captures IMAP flags/internal dates when the server provides them, creates an import job, and returns processed/new/duplicate/error counts plus the effective `sync_limit`.

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

`POST /api/v1/pop-sources/{id}/sync` performs a read-only sync with optional `sync_limit` override. It imports new `UIDL` values through the raw-MIME pipeline, returns the effective `sync_limit`, and never issues `DELE`.

`POST /api/v1/pop-sources/{id}/delete` removes the saved source and deletes its secret reference.

## Microsoft Graph Sources

- `GET /api/v1/graph-providers`
- `GET /api/v1/graph-sources`
- `GET /api/v1/graph/oauth/callback`
- `POST /api/v1/graph-sources`
- `POST /api/v1/graph-sources/{id}/auth-url`
- `POST /api/v1/graph-sources/{id}/probe`
- `POST /api/v1/graph-sources/{id}/folders`
- `POST /api/v1/graph-sources/{id}/sync`
- `POST /api/v1/graph-sources/{id}/delete`

Microsoft Graph source configs are stored per active profile. Source configs store client id, tenant id, redirect URI, scopes, mailbox selector, selected folders, and secret references for token payloads or pending PKCE auth state.

`GET /api/v1/graph-providers` returns the Microsoft Graph provider preset and default read-only delegated scopes.

`POST /api/v1/graph-sources` accepts `name`, `client_id`, `tenant_id`, `redirect_uri`, `scopes`, `mailbox`, `folders`, and `sync_limit`.

`POST /api/v1/graph-sources/{id}/auth-url` creates an OAuth authorization URL using authorization code flow with PKCE. MILLIE stores the PKCE code verifier in the configured secret backend and returns the authorization URL, state, scopes, and code challenge metadata. For localhost redirect URIs, the API uses the active local server port so the browser returns to the running MILLIE instance.

`GET /` and `GET /api/v1/graph/oauth/callback` complete the OAuth callback when the browser returns with `code` and `state`. MILLIE exchanges the code, stores token payloads in the configured secret backend, clears pending auth state, and never writes raw tokens into the source config.

`POST /api/v1/graph-sources/{id}/probe` refreshes an expired access token when possible, then calls read-only Graph metadata endpoints for account and mail-folder summaries.

`POST /api/v1/graph-sources/{id}/folders` returns the discovered Graph folder tree with stable folder ids, display names, folder paths, count metadata, and role hints.

`POST /api/v1/graph-sources/{id}/sync` performs a limited read-only sync from the saved selected folders. It uses per-folder Microsoft Graph delta links, fetches changed message MIME with `/$value`, imports through the same raw-MIME pipeline as file/IMAP/POP imports, tracks `delta_link`/`next_link` state per folder, and never sends, moves, marks, or deletes remote mail. The sync response includes `processed`, `imported`, `duplicates`, `removed`, `errors`, and `sync_limit`.

## Export Jobs

- `GET /api/v1/export-profiles`
- `POST /api/v1/export`
- `POST /api/v1/export/verify`
- `GET /api/v1/export-jobs`
- `GET /api/v1/export-jobs/{id}/items`

Export profiles include a target ID, display name, supported formats, recommended format, import instructions, and known limitations. `POST /api/v1/export` accepts `targetProfile` and `format`; use `format: "auto"` to select the profile recommendation.

`POST /api/v1/export/verify` accepts `manifestPath` and validates output files against the export manifest hashes.

## Backups

- `POST /api/v1/backup`
- `POST /api/v1/restore-backup`

`POST /api/v1/backup` accepts `outputPath` and `includeSecrets`. It creates a portable active-profile ZIP backup and redacts secret-bearing settings unless `includeSecrets` is true.

`POST /api/v1/restore-backup` accepts `path`, optional `name`, optional `profileId`, and optional `switch`. It validates the backup manifest hashes, restores into a new profile, and switches to that profile by default.

## Other Current Endpoints

- `GET /api/v1/health`
- `GET /api/v1/profiles`
- `POST /api/v1/profiles`
- `POST /api/v1/profiles/active`
- `GET /api/v1/sources`
- `GET /api/v1/sync-states`
- `GET /api/v1/mailboxes`
- `GET /api/v1/migrations`
