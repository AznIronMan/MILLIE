# API Notes

MILLIE exposes a local versioned API under `/api/v1`.

## Search And Messages

- `GET /api/v1/messages`
- `GET /api/v1/messages?mailbox_id=1`
- `GET /api/v1/messages?q=quarterly`
- `GET /api/v1/search?q=alice@example.com`
- `GET /api/v1/messages/{id}`
- `GET /api/v1/messages/{id}/raw`

Search uses SQLite FTS5 over subject, participants, and text body. Query text is normalized before matching so email addresses and punctuation-heavy terms are safe.

## Import Jobs

- `POST /api/v1/import`
- `GET /api/v1/import-jobs`
- `GET /api/v1/import-jobs/{id}/errors`

Import responses and job rows distinguish:

- `processed`: successfully parsed source items
- `imported` / `new_message_count`: newly created canonical messages
- `duplicates` / `duplicate_count`: source items that matched existing raw MIME content
- `errors` / `error_count`: failed source items

## Other Current Endpoints

- `GET /api/v1/health`
- `GET /api/v1/profiles`
- `POST /api/v1/profiles`
- `POST /api/v1/profiles/active`
- `GET /api/v1/sources`
- `GET /api/v1/mailboxes`
- `GET /api/v1/migrations`
- `POST /api/v1/export`
- `GET /api/v1/export-jobs`
- `GET /api/v1/export-jobs/{id}/items`
