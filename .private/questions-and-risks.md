# MILLIE Questions And Risks

## Key Questions

- Should the first implementation stack be Python/FastAPI with a TypeScript web client, or another stack?
- Should local auth be required even in dev once real mail is imported?
- Should attachment binary content live inside SQLite for the first MVP, or in a content-addressed file store referenced by SQLite?
- What export fidelity level is acceptable for each target client: exact raw MIME, equivalent importable mail, or best-effort metadata preservation?
- Which direct Outlook export path is acceptable: PST writer, EML bundles, local IMAP handoff, or another bridge?
- Should the local IMAP facade be read-only forever, or only read-only for the first version?
- Should MILLIE optimize first for personal forensic/archive use or for day-to-day active mailbox viewing?

## Risks And Concerns

- OST is harder than PST. Some OST files are cache-bound, encrypted, incomplete, or dependent on account credentials.
- Email HTML is a major XSS surface and needs strict sanitization.
- Binding to `0.0.0.0` is useful for LAN/dev access, but it can expose private mail if auth is not ready.
- A single-message row is not enough for a faithful model. The app can expose a flattened object while storing normalized child records.
- Message deduplication is tricky. `Message-ID` alone is not reliable.
- Attachments can quickly make SQLite databases huge. A content-addressed attachment store may be cleaner even for the SQLite MVP.
- Gmail labels, IMAP folders, Exchange categories, and desktop client folders do not map perfectly.
- Exporting open formats like EML, MBOX, and Maildir is realistic early. Writing PST or OLM directly is more fragile and may require third-party tools or a different strategy.
- Reconstructing MIME from normalized fields can lose original boundaries, header ordering, signatures, or client-specific metadata. Raw MIME preservation should be prioritized during import.
- OAuth token storage, refresh, revocation, and provider scopes need to be designed before live connectors handle real accounts.
- Local IMAP write support can create difficult sync semantics. Read-only should come first.
