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
- `POST /api/v1/import`
- `GET /api/v1/import-jobs`
- `GET /api/v1/import-jobs/{id}/errors`

Source scanning is read-only. It returns local candidate paths, source type, detected import format, mailbox path, size, message estimate, confidence, and notes. The web app can send a selected candidate to `POST /api/v1/import`.

Import responses and job rows distinguish:

- `processed`: successfully parsed source items
- `imported` / `new_message_count`: newly created canonical messages
- `duplicates` / `duplicate_count`: source items that matched existing raw MIME content
- `errors` / `error_count`: failed source items

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
