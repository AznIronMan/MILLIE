# Outlook Stores

MILLIE treats Outlook formats as separate adapter paths instead of mixing vendor-specific parsing into the core importer.

## PST

PST import is currently supported through `readpst` from `libpst`.

The importer extracts messages to ignored local temporary storage and then feeds generated `.eml` files into the normal raw-MIME-first import pipeline.

See [pst.md](pst.md) for setup and current smoke-test notes.

## OLM

OLM import is not implemented yet.

Current behavior:

- Source scanning detects `.olm` files.
- Scan candidates are marked `importable: false`.
- Direct import fails with a clear import job error instead of treating the file as MBOX.

Strategy:

- Keep OLM support as an adapter wrapper.
- Prefer an adapter that can emit `.eml`, `mbox`, or another open intermediate format.
- Preserve the original OLM file path, adapter version, and extraction manifest once support is added.
- Avoid promising direct OLM export until a reliable writer is selected.

Practical workaround today: export from Outlook for Mac into an open mailbox format when available, then import that output into MILLIE.

## OST

OST import is not implemented.

OST files are offline Outlook/Exchange caches and may be encrypted, profile-bound, incomplete, or otherwise unusable without the original account context.

Current behavior:

- Source scanning detects `.ost` files.
- Scan candidates are marked `importable: false`.
- Direct import fails with a clear import job error instead of treating the file as MBOX.

Strategy:

- Prefer live sync from Exchange/Microsoft Graph/IMAP when credentials are available.
- Prefer Outlook-exported PST when live sync is unavailable but Outlook can still open the mailbox.
- Treat offline OST extraction as an advanced adapter path only after tool reliability, legal/licensing fit, and data-fidelity behavior are understood.

## Export

MILLIE currently supports Outlook workflow export bundles, not direct PST/OLM writing. Direct native Outlook writers remain under investigation.
