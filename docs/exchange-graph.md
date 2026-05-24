# Microsoft Graph / Exchange

MILLIE's planned Exchange path is Microsoft Graph, not legacy Exchange Web Services.

This connector is currently a source/credential skeleton. It can save Microsoft Graph source configs and generate a PKCE authorization URL, but it does not exchange authorization codes, store Graph tokens, or sync mail yet.

## Direction

Use delegated Microsoft Graph permissions for user-owned mailboxes:

- `openid`
- `offline_access`
- `User.Read`
- `Mail.Read`

`offline_access` is required for refresh-token based ongoing access. `Mail.Read` is the read-only mail permission that keeps this connector aligned with MILLIE's current read-only live connector stance.

Current official references:

- [Microsoft identity platform authorization code flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow)
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
- Sync limit for future syncs
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
  --redirect-uri "http://localhost:22013/api/v1/graph/oauth/callback"

PYTHONPATH=src python3 -m millie graph-sources
PYTHONPATH=src python3 -m millie graph-auth-url work-microsoft-365
PYTHONPATH=src python3 -m millie graph-delete work-microsoft-365
```

`graph-auth-url` creates a Microsoft authorization URL and stores the PKCE verifier in the configured secret backend. The callback and token exchange are not implemented yet.

## Current API

- `GET /api/v1/graph-providers`
- `GET /api/v1/graph-sources`
- `POST /api/v1/graph-sources`
- `POST /api/v1/graph-sources/{id}/auth-url`
- `POST /api/v1/graph-sources/{id}/delete`

`POST /api/v1/graph-sources` accepts:

- `name`
- `client_id`
- `tenant_id`
- `redirect_uri`
- `scopes`
- `mailbox`
- `sync_limit`

`POST /api/v1/graph-sources/{id}/auth-url` creates a PKCE authorization URL and stores pending auth state by secret reference.

## Azure App Registration

Before MILLIE can authenticate a real Microsoft account, the user needs a Microsoft Entra app registration.

Recommended first development setup:

- Platform: local/native or web flow compatible with a localhost redirect
- Redirect URI: `http://localhost:22013/api/v1/graph/oauth/callback`
- Supported accounts: choose based on the intended mailbox type
- Delegated Graph permissions: `User.Read`, `Mail.Read`
- Public-client/PKCE posture: no client secret for local development

The redirect URI in Azure must exactly match the redirect URI saved in the MILLIE source.

## Sync Design

Future sync should:

- Probe `/me` or `/me/mailFolders` once token exchange exists
- Enumerate mail folders through Graph
- Use per-folder message delta queries for incremental sync
- Store Graph delta links in `source_sync_states`
- Fetch enough message fields to preserve subject, sender, recipients, timestamps, body, internet message id, conversation id, headers where available, and attachments
- Preserve read-only behavior: no send, update, move, or delete operations without explicit future approval

Message delta is per-folder, so MILLIE should track each selected folder independently.

## Follow-Up

- Add OAuth callback and authorization-code token exchange.
- Store token payloads in the secret backend.
- Add token refresh.
- Add Graph source probe.
- Add folder discovery and selected-folder sync.
- Add Graph delta sync into the canonical raw-message pipeline or a Graph-native normalization path.
