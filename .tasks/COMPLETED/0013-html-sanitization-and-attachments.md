# Add HTML Sanitization And Attachment Downloads

Status: COMPLETED

## Goal

Make real imported mail safer and more useful to inspect in the web client.

## Result

- Added a conservative dependency-free HTML sanitizer.
- Preserved raw HTML body blobs while storing sanitized HTML body blobs.
- Added lazy sanitized HTML generation for messages imported before this slice.
- Added `GET /api/v1/messages/{id}/html` for sanitized HTML viewing.
- Added `GET /api/v1/attachments/{id}` for attachment downloads.
- Updated the web client to render sanitized HTML in a sandboxed frame.
- Updated the web client to show attachment download links.

## Verification

- `PYTHONWARNINGS=error PYTHONPATH=src python3 -m unittest discover -s tests`

## Notes

The sanitizer intentionally blocks active content and remote image/resource loading by default. Richer rendering can be added later behind explicit safety controls.
