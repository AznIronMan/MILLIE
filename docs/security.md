# Security And Networking Notes

MILLIE handles personal and organizational email. Treat every imported mailbox, generated database, attachment, token, and log as sensitive.

## Development

- Non-secure HTTP is acceptable for local development.
- The default backend server path remains HTTP for current testing.
- Direct HTTPS is enabled only when both `MILLIE_TLS_CERT`/`MILLIE_TLS_KEY` or `serve --tls-cert`/`--tls-key` are provided.
- Dev mode should be explicit in configuration.
- `auth.dev_bypass` currently defaults to `true` while the app is being built.
- Sample data should be synthetic unless the user knowingly imports real mail.
- Logs should avoid full message bodies, tokens, passwords, and attachment content.

## Production Or Real Mail Use

- HTTPS/TLS/SSL must be easy to enable by configuration.
- Reverse proxy TLS should be supported.
- Direct app TLS can be supported as a second path.
- Authentication should be required before exposing real mail.
- Disable `auth.dev_bypass` before exposing real mail beyond trusted local development.
- Binding to `0.0.0.0` must be paired with clear warnings and access controls.

## Local IMAP Facade

The local IMAP facade is read-only and intended for local client testing. It binds to `127.0.0.1:22143` by default, accepts development logins only for loopback/default testing, can require exact username/password credentials, can run direct IMAPS with cert/key paths, and refuses mutating IMAP commands such as `APPEND`, `COPY`, `STORE`, `DELETE`, and `EXPUNGE`.

Do not bind the IMAP facade to a network interface with real mail unless exact credentials are configured and the network is trusted.

## Local Authentication

The first auth path stores admin configuration in `millie.settings`, hashes passwords with PBKDF2-SHA256, and uses an HTTP-only `millie_session` cookie. This is a local-app baseline, not a complete production identity system.

## Webmail Rendering

HTML email is untrusted content.

The viewer must:

- Sanitize HTML before rendering.
- Block active script content and event attributes.
- Block embedded remote image/resource loading by default.
- Avoid leaking local file paths.
- Avoid executing embedded content.

The first implemented renderer stores both raw HTML and sanitized HTML. The web client loads sanitized HTML in a sandboxed frame and downloads attachments through API responses with `Content-Disposition: attachment`.

## Secrets

OAuth tokens, refresh tokens, app passwords, private keys, and API keys should not be stored directly in ordinary app tables.

Use secret references in app data and keep secret values in a protected store.

IMAP source configs now store `auth_ref` values instead of raw passwords. The default secret backend uses macOS Keychain when available. Non-macOS or explicitly local development runs can use a profile-local settings fallback, which should still be treated as sensitive and limited to development/test credentials.

## Backups

`millie backup` redacts known secret-bearing settings by default, including session secrets, password hashes, and profile-local secret stores. Backups created with `--include-secrets` can contain local fallback secrets and should be protected like credentials.
