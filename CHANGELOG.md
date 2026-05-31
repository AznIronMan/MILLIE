# Changelog

All notable changes to MILLIE will be documented in this file.

## [1.0.0] - 2026-05-31

### Added

- Added root `millie.settings` SQLite3 settings database.
- Added a temporary browser-based settings editor launched by `tmp_settings.sh`.
- Added repeatable IMAP retrieval and SMTP sending account settings in `millie.settings`.
- Added Microsoft Outlook/Exchange OAuth settings for IMAP authorization.
- Added a separate Microsoft OAuth client secret ID setting so the secret value and ID are not conflated.
- Added customer-facing settings documentation under `docs/`.

### Changed

- Stopped tracking local settings and task/private workspace files.
- Updated ignore rules for `.private/`, `.tasks/`, `/data/`, `/logs/`, `*.settings`, and `*.millie`.
- Reset the repository for a fresh 1.0.0 start.
- Archived the previous implementation locally at `.private/archived/version_0.tar.gz`.
- Recreated baseline project documentation, task lanes, and local secret/archive ignore rules.
