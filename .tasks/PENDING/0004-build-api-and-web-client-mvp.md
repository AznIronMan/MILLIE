# Build API And Web Client MVP

Status: PENDING

## Goal

Create a useful first webmail-style viewer over imported mail.

## Acceptance Criteria

- API exposes sources, mailboxes, message list, message detail, search, attachments, and import jobs.
- Web client shows source/folder navigation, message list, message detail, and search.
- HTML message rendering is sanitized.
- App can run in dev over HTTP.
- HTTPS/TLS configuration path is documented.

## Progress

- Sources, mailboxes, messages, search, import jobs, export jobs, sanitized HTML, and attachment download endpoints exist.
- Web client has source/folder navigation, message list/detail, search, sanitized HTML viewing, attachment links, import/export controls, profile switching, and job history.
- Local admin/session auth exists, with `auth.dev_bypass` enabled by default for development.
- Import/export job drill-down views exist for errors and generated export items.
- Remaining MVP hardening includes richer attachment browsing, conversation/thread views, dev-bypass-off real-mail testing, and TLS/deployment documentation.
