# Microsoft Graph / Exchange

MILLIE's planned Exchange path is Microsoft Graph, not legacy Exchange Web Services.

This connector can save Microsoft Graph source configs, generate a PKCE authorization URL, receive the local OAuth callback, exchange authorization codes, store token payloads in the configured secret backend, refresh expired access tokens, discover folders, and run limited read-only sync from selected folders.

## Direction

Use delegated Microsoft Graph permissions for user-owned mailboxes:

- `openid`
- `offline_access`
- `User.Read`
- `Mail.Read`

`offline_access` is required for refresh-token based ongoing access. `Mail.Read` is the read-only mail permission that keeps this connector aligned with MILLIE's current read-only live connector stance.

Current official references:

- [Microsoft identity platform authorization code flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow)
- [Microsoft identity platform redirect URI best practices](https://learn.microsoft.com/en-us/entra/identity-platform/reply-url)
- [Microsoft Graph delegated auth on behalf of a user](https://learn.microsoft.com/en-us/graph/auth-v2-user)
- [Microsoft Graph message delta query](https://learn.microsoft.com/en-us/graph/delta-query-messages)
- [Microsoft Graph message delta API](https://learn.microsoft.com/en-us/graph/api/message-delta?view=graph-rest-1.0)

## OAuth Model

MILLIE should use OAuth 2.0 authorization code flow with PKCE.

Saved source config stores:

- Source name
- Provider id
- Microsoft Entra application client id
- Tenant id, such as `common`, `organizations`, `consumers`, tenant GUID, or tenant domain
- Redirect URI
- Requested scopes
- Mailbox selector, currently `me`
- Selected Graph folders
- Sync limit
- Secret references for token payloads and pending PKCE auth state

Saved source config must not store:

- Client secrets
- Access tokens
- Refresh tokens
- PKCE code verifier values

Token payloads and pending PKCE verifier payloads belong in the configured secret backend, which is macOS Keychain by default on macOS and profile-local settings only as a development fallback.

## Current CLI

```sh
PYTHONPATH=src python3 -m millie graph-providers

PYTHONPATH=src python3 -m millie graph-add "Work Microsoft 365" \
  --client-id "<application-client-id>" \
  --tenant-id common \
  --redirect-uri "http://localhost"

PYTHONPATH=src python3 -m millie graph-sources
PYTHONPATH=src python3 -m millie graph-auth-url work-microsoft-365 --redirect-uri "http://localhost:22013"
PYTHONPATH=src python3 -m millie graph-probe work-microsoft-365
PYTHONPATH=src python3 -m millie graph-folders work-microsoft-365
PYTHONPATH=src python3 -m millie graph-set-folders work-microsoft-365 --folder Inbox
PYTHONPATH=src python3 -m millie graph-sync work-microsoft-365 --limit 25
PYTHONPATH=src python3 -m millie graph-delete work-microsoft-365
```

`graph-auth-url` creates a Microsoft authorization URL and stores the PKCE verifier in the configured secret backend. Keep the MILLIE API server running on the same local port used in the redirect URI so the browser can return with the authorization code.

`graph-probe` calls `/me` and `/me/mailFolders` using the stored token payload. It refreshes expired access tokens when a refresh token is available and does not fetch messages.

`graph-folders` discovers the Graph folder tree. `graph-set-folders` saves selected folders by path or id. `graph-sync` imports raw MIME for newly seen messages from the selected folders and never sends, moves, marks, or deletes mail.

## Current API

- `GET /api/v1/graph-providers`
- `GET /api/v1/graph-sources`
- `GET /api/v1/graph/oauth/callback`
- `POST /api/v1/graph-sources`
- `POST /api/v1/graph-sources/{id}/auth-url`
- `POST /api/v1/graph-sources/{id}/probe`
- `POST /api/v1/graph-sources/{id}/folders`
- `POST /api/v1/graph-sources/{id}/sync`
- `POST /api/v1/graph-sources/{id}/delete`

`POST /api/v1/graph-sources` accepts:

- `name`
- `client_id`
- `tenant_id`
- `redirect_uri`
- `scopes`
- `mailbox`
- `folders`
- `sync_limit`

`POST /api/v1/graph-sources/{id}/auth-url` creates a PKCE authorization URL and stores pending auth state by secret reference.

`GET /` and `GET /api/v1/graph/oauth/callback` can complete the OAuth callback when the query contains `code` and `state`.

`POST /api/v1/graph-sources/{id}/probe` refreshes the token if needed, calls read-only Graph metadata endpoints, and returns account and folder summaries.

`POST /api/v1/graph-sources/{id}/folders` returns the discovered folder tree, including folder id, path, role hint, total count, unread count, and child count.

`POST /api/v1/graph-sources/{id}/sync` imports from the source's saved selected folders using the existing raw-MIME parser. It accepts optional `sync_limit` and uses a conservative seen-message-id state until Graph delta sync is implemented.

## How To Set Up Graph OAuth

Before MILLIE can authenticate a real Microsoft account, the user needs a Microsoft Entra app registration.

Recommended first development setup:

- App type: public client/native desktop using authorization code with PKCE
- Platform: `Public client/native (mobile & desktop)`
- Redirect URI: `http://localhost`
- Supported accounts: choose based on the intended mailbox type
- Delegated Graph permissions: `openid`, `offline_access`, `User.Read`, `Mail.Read`
- Public-client/PKCE posture: no client secret for local development

Entra does not call back into localhost from Microsoft's servers. It redirects the signed-in user's browser, and that browser is running on the same workstation as MILLIE.

Microsoft treats localhost loopback redirect URIs specially for native apps: the port can vary, but the path still needs to match. With the current `http://localhost` CNB registration, MILLIE can request `http://localhost:<active-port>` and receive the callback at the app root. If MILLIE later uses a path-based callback such as `/api/v1/graph/oauth/callback`, add a matching redirect URI path in Entra, for example `http://localhost/api/v1/graph/oauth/callback`, and have MILLIE request the same path on its active local port.

### Entra Portal Steps

1. Open [Microsoft Entra app registrations](https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade).
2. Select `New registration`.
3. Enter a reusable app name, such as `cnb_portland_connector`.
4. For a CNB-only connector, choose `Single tenant only - Clark & Burke LLC`.
5. In `Redirect URI`, choose `Public client/native (mobile & desktop)` and enter `http://localhost`.
6. After registration, record the Application/client ID and Directory/tenant ID.
7. Go to `Authentication (Preview)` > `Settings`, enable `Allow public client flows`, and save.
8. Go to `API permissions` > `Add a permission` > `Microsoft Graph` > `Delegated permissions`.
9. Add `openid`, `offline_access`, `User.Read`, and `Mail.Read`.
10. Select `Grant admin consent for <tenant>` when using an admin-controlled tenant and confirm the prompt.

### CNB Development Connector

The current CNB development registration was created as:

- Entra app name: `cnb_portland_connector`
- Application/client ID: `9ba47792-282b-4756-948a-b2d16764934b`
- Directory/tenant ID: `b8803792-461f-475e-b849-9ff62fcd742f`
- Supported accounts: single tenant, Clark & Burke LLC
- Redirect URI: `http://localhost`
- Public client flows: enabled
- Delegated permissions: `openid`, `offline_access`, `User.Read`, `Mail.Read`
- Admin consent: granted for Clark & Burke LLC

Register or update the source in the active MILLIE profile:

```sh
PYTHONPATH=src python3 -m millie graph-add "CNB Portland Connector" \
  --id cnb_portland_connector \
  --client-id 9ba47792-282b-4756-948a-b2d16764934b \
  --tenant-id b8803792-461f-475e-b849-9ff62fcd742f \
  --redirect-uri "http://localhost"
```

MILLIE normalizes the source id to `cnb-portland-connector`.

The redirect URI path in Azure must match the redirect URI path requested by MILLIE. For the current root `http://localhost` setup, MILLIE can add the active local port when generating the auth URL.

To connect this source from the web app, start the API/web server, click `Connect` on the saved Graph source, sign in with the mailbox account, and let Microsoft redirect the browser back to `http://localhost:<active-port>`.

## What OAuth Needs Next

The working Graph connector now has:

- OAuth start flow that generates PKCE state and verifier and opens the Microsoft authorization URL.
- A callback handler that receives `code` and `state`, validates the pending state, and exchanges the code at the tenant token endpoint.
- Secret-backed token storage for access token, refresh token, expiry, tenant, scopes, and mailbox metadata.
- Token refresh before Graph calls when the access token is expired or near expiry.
- A read-only probe endpoint/CLI command that calls `/me` and `/me/mailFolders`.
- Folder discovery and selected-folder management through CLI, API, and web controls.
- A first read-only sync path that imports selected folders/messages into MILLIE's canonical raw-MIME pipeline.

To turn the MVP sync into a production-shaped sync, MILLIE still needs:

- Clear error handling for expired consent, revoked refresh tokens, conditional access, MFA, and permission mismatch.

## Sync Design

Current sync:

- Enumerates mail folders through Graph
- Lists selected folder messages
- Fetches each selected message as MIME with `/$value`
- Imports MIME through the same parser used by file, IMAP, and POP sources
- Tracks seen Graph message ids per folder in `source_sync_states`
- Preserves read-only behavior: no send, update, move, mark-read, or delete operations

Future sync should:

- Use per-folder message delta queries for incremental sync
- Store Graph delta links in `source_sync_states`
- Replace seen-id backfill with durable delta links
- Add richer handling for deleted/moved messages without mutating the remote mailbox

Message delta is per-folder, so MILLIE should track each selected folder independently.

## Follow-Up

- Add Graph delta sync into the canonical raw-message pipeline or a Graph-native normalization path.
