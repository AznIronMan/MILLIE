# Add Deduplication Accounting And Search Hardening

Status: COMPLETED

## Goal

Make repeated imports safe and make search reliable enough for real archives.

## Result

- Added import job accounting for processed, new, and duplicate messages.
- Made `insert_message` return whether a canonical message was newly created or already existed.
- Kept raw content hash as the canonical stable ID for exact-message deduplication.
- Rebuilt FTS rows idempotently when a message is imported again.
- Normalized FTS queries so punctuation-heavy searches, including email addresses, do not break search.
- Added `/api/v1/search` alongside the existing `q` support on `/api/v1/messages`.
- Updated the web Operations panel and import status text with duplicate counts.

## Verification

- `PYTHONWARNINGS=error PYTHONPATH=src python3 -m unittest discover -s tests`

## Notes

This is exact raw-MIME deduplication. Fuzzy duplicate detection for messages that differ only in transport headers remains future work.
