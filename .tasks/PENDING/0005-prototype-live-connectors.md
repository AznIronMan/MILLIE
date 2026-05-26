# Prototype Live Connectors

Status: PENDING

## Goal

Add initial live mail import/sync support.

## Acceptance Criteria

- IMAP connector can import a mailbox.
- POP3 connector can import messages.
- Microsoft Graph / Exchange connector path is selected.
- OAuth/app-password secret handling is designed before storing real credentials.
- Incremental sync state is tracked per source.

## Progress

- Initial read-only IMAP sync MVP is implemented with password/app-password config, active-profile storage, and per-folder UID cursor tracking.
- CLI, API, web controls, and unit coverage exist for the IMAP path.
- IMAP source configs now store credential references instead of raw passwords, with macOS Keychain support and a local development fallback.
- Saved IMAP sources can discover folders, apply selected folder lists, sync, and be deleted through CLI, API, and web controls.
- IMAP hardening now includes generic/Gmail provider presets, Gmail host normalization, one-off selected-folder sync, and capture of IMAP flags/internal dates.
- Initial read-only POP3 support exists with safe probes, secret-backed source configs, UIDL incremental sync, CLI/API/web controls, and no server delete path.
- Microsoft Graph / Exchange is selected as the modern Exchange path. Source configs, provider metadata, PKCE authorization URL generation, OAuth callback/token exchange, secret-backed token storage, token refresh, folder discovery, selected-folder management, delta-backed limited read-only sync, CLI/API endpoints, web controls, and design docs exist.
- Common IMAP/POP provider presets now cover Gmail, Outlook.com / Microsoft 365, Yahoo, AOL, Fastmail, and Zoho, with iCloud covered for IMAP only because Apple does not support POP.

## Remaining

- Harden Graph/IMAP/POP recovery paths for revoked credentials, expired consent, large backfills, and partial sync continuation.
