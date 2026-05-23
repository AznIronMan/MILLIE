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
- Remaining MVP hardening includes local authentication, richer attachment browsing, import job drill-down views, and TLS/deployment documentation.
