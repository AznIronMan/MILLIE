# Security And Networking Notes

MILLIE handles personal and organizational email. Treat every imported mailbox, generated database, attachment, token, and log as sensitive.

## Development

- Non-secure HTTP is acceptable for local development.
- Dev mode should be explicit in configuration.
- Sample data should be synthetic unless the user knowingly imports real mail.
- Logs should avoid full message bodies, tokens, passwords, and attachment content.

## Production Or Real Mail Use

- HTTPS/TLS/SSL must be easy to enable by configuration.
- Reverse proxy TLS should be supported.
- Direct app TLS can be supported as a second path.
- Authentication should be required before exposing real mail.
- Binding to `0.0.0.0` must be paired with clear warnings and access controls.

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
