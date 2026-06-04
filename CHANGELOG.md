# Changelog

All notable changes to MILLIE will be documented in this file.

## Unreleased

No unreleased changes.

## [1.3.4] - 2026-06-04

### Added

- Added a dry-run-first empty metadata cleanup tool for internal mailbox folders, source-folder metadata, blank address rows, empty import jobs, and empty source definitions.
- Added an optional live-upkeep empty cleanup report step with guarded empty mailbox-folder execution.

## [1.3.3] - 2026-06-04

### Changed

- Routed trash, spam, and bulk-mail suggestions into separate `Hold/Reevaluate/*` buckets for later review.
- Limited new unsubscribe candidate detection to the last 183 days by default.
- Updated default hold retention policies to cover trash, spam, and bulk reevaluation buckets.

## [1.3.2] - 2026-06-04

### Added

- Added a dry-run-first Postgres search document rebuild tool for recovered MILLIE archives.
- Added tests for search rebuild text helpers and UTF-8 truncation behavior.

### Changed

- Documented controlled search regeneration for recovered archives and the 2026-06-04 runtime recovery outcome.

## [1.3.1] - 2026-06-04

### Added

- Added database recovery and containment documentation for the dedicated MILLIE Postgres recovery cluster.
- Added a runtime Postgres safety guard that refuses the known quarantined main-cluster MILLIE endpoint.

### Changed

- Updated settings, README, and agent guidance to keep MILLIE pointed at `10.0.10.81:55432/millie` and away from `10.0.10.81:5432/millie`.

## [1.3.0] - 2026-06-03

### Added

- Added an aggregate-only manual LLM taxonomy assistant for webmail Metrics.
- Added OpenAI Responses API structured-output support for taxonomy advice using configured `millie.settings` provider tiers.
- Added tests for taxonomy assistant privacy filtering, request shape, response parsing, and unsupported provider handling.

### Security

- Limited taxonomy assistant prompts to aggregate target names, classification fields, evidence counts, confidence, sender domains, source folders, and years; raw bodies, full addresses, attachments, and message samples are not sent.

## [1.2.0] - 2026-06-03

### Added

- Added a dedicated webmail Proposal Review panel for saved rule and taxonomy proposals with status counts, filters, checkbox selection, single-row actions, and bulk activate/disable/retire controls.
- Added `/api/proposals` and `/api/proposals/action` endpoints plus guarded Postgres proposal review and batch-action helpers.
- Added a Proposal Review observe preview that runs the sorter in bounded dry-run mode without persisting suggestions or writing to providers.

### Changed

- Reduced rule candidate action reload scans to the same bounded candidate set used by candidate listing to avoid unnecessary heavy database rescans.

## [1.1.0] - 2026-06-03

### Added

- Added normalized duplicate fingerprints for canonical mail messages.
- Added deduped source UID aliases so exact raw duplicates can be skipped on later incremental syncs.
- Added a dedupe backfill/report tool for exact raw-message, Message-ID, and normalized fingerprint duplicate groups.
- Added incremental IMAP/OAuth live sync helpers that import only newer UIDs while a MILLIE process is running.
- Added `--live-sync` support to the development webmail listener.
- Added iCloud Mail presets/defaults for `icloud.com`, `me.com`, and `mac.com` IMAP retrieval accounts.
- Added webmail per-folder message limits, browser-side list caching, refresh, and cheaper folder counts.
- Added a remote provider cleanup preparation tool that audits live IMAP/OAuth UIDs and tags protected MILLIE copies before any destructive provider-side purge can be considered.
- Added a Gmail label alias reconciliation tool using `X-GM-MSGID` to map label UIDs without fetching duplicate raw messages.
- Added a sync-cutoff remote purge snapshot tool and a manifest-driven provider purge executor that deletes only exact manifest IMAP UIDs.
- Added a dormant Postgres brain schema foundation for safe sorting automation, learned rules, classifications, feedback, retention policies, unsubscribe candidates, automation runs, and audit logging.
- Added an observe-only sorter CLI that proposes categories and unsubscribe candidates without moving, deleting, unsubscribing, or writing to source providers.
- Added webmail review controls for MILLIE brain classification suggestions and unsubscribe candidates.
- Added automation-level guardrail settings and helper checks, including a separate provider-write switch.
- Added a dry-run-first approved-suggestion apply tool for internal MILLIE folder mappings.
- Added proposed no-action retention defaults and a dry-run retention scanner for MILLIE hold folders.
- Added webmail retention policy visibility for messages opened from hold folders.
- Added retention-eligible hold messages to the webmail Review queue with acknowledge and seven-day snooze feedback actions.
- Added a dry-run-first retention apply command for acknowledged active policies, including non-destructive internal hide-from-default-views support.
- Added a dry-run-first retention policy manager for listing, creating, activating, disabling, and editing policies with audit rows.
- Added a live-upkeep runner that combines live sync, dedupe backfill, Gmail label aliasing, observe sorting, retention scan, and safe internal apply steps while MILLIE is running.
- Added a safe unsubscribe review CLI for listing, preparing, ignoring, marking unsafe, and generating manual-assist checklists without clicking provider links.
- Added a webmail unsubscribe queue for global candidate review.
- Added webmail retention policy controls for listing, activating, disabling, and editing hold durations/actions with audit rows.
- Added provider-write guardrails that block remote provider purge execution unless explicit provider-write settings and a manifest id are present.
- Added Postgres-backed webmail login sessions with an explicit `--no-auth` development override.
- Added webmail global search, brain rule management, and internal apply dry-run/execute controls.
- Added a webmail Ops dashboard for live source status, queue counts, automation run history, and bounded one-off sync/upkeep/dedupe commands.
- Added persisted per-account/folder IMAP sync health with stale/failure visibility in webmail Ops and scoped account/folder sync actions.
- Added a webmail sorting Workbench for grouped batch review of proposed classifications by target, sender domain, folder, and year.
- Added active learned-rule matching to the observe sorter so approved rules can propose or suppress future internal suggestions.
- Added a webmail learning Metrics dashboard for classification, feedback, target, and rule health.
- Added review-only rule candidate discovery with bounded evidence previews and seed/dismiss controls.
- Added review-only taxonomy proposals with aggregate LLM-ready context and seed/dismiss controls.
- Added `sync_stale_after_hours` settings support for Ops stale sync thresholds.

### Changed

- Changed exact-folder IMAP imports to skip broad provider folder listing and trust supplied `--folder` names for targeted catch-up runs.
- Extended the observe sorter with account, folder, message id, and date filters.
- Changed Always/Never rule evidence to include sender domain, source folder, and message year when available.

## [1.0.0] - 2026-05-31

### Added

- Added root `millie.settings` SQLite3 settings database.
- Added a temporary browser-based settings editor launched by `tmp_settings.sh`.
- Added repeatable IMAP retrieval and SMTP sending account settings in `millie.settings`.
- Added Microsoft Outlook/Exchange OAuth settings for IMAP authorization.
- Added a separate Microsoft OAuth client secret ID setting so the secret value and ID are not conflated.
- Added a temporary Microsoft OAuth callback helper for saving Outlook authorization tokens locally.
- Added a read-only PST probe using libpst/readpst with metadata-only reporting.
- Added dormant mail import pipeline models for PST, IMAP, and Exchange OAuth IMAP sources.
- Added SQLite and PostgreSQL canonical mail storage schemas.
- Added SQLite storage writer coverage for normalized message graphs.
- Added PST password input handling with explicit backend capability reporting.
- Added a dry-run import planner that does not connect, extract, or write data.
- Added AES-256-GCM protection for secret values stored in `millie.settings`.
- Added automatic migration of existing plaintext settings secrets and mail account passwords.
- Updated the Microsoft OAuth helper to read and write encrypted settings secrets.
- Added dormant Postgres identity/authentication tables for MILLIE logins.
- Added dormant Postgres mailbox facade tables and views for IMAP/webmail clients.
- Added a bootstrap SQL planner for identities such as `geon@MILLIE`.
- Added a live sample importer for copying one PST, one IMAP, and one Exchange OAuth message into MILLIE.
- Added a minimal development IMAP listener backed by the Postgres mailbox facade.
- Added a minimal authenticated development SMTP listener for mail client account setup.
- Added customer-facing settings documentation under `docs/`.
- Added IMAP mailbox-copy mutation support for folder changes, `APPEND`, flag updates, copy/move, delete, and expunge.
- Added a dry-run-first bulk PST import tool that separates archives under `Sources/PST/<archive name>/...`.
- Added a duplicate-safe bulk IMAP importer for configured password and Microsoft OAuth accounts.
- Added development autoconfig/autodiscover XML endpoints to the webmail listener, including Outlook-style POST support on `/autodiscover/autodiscover.xml`.

### Changed

- Stopped tracking local settings and task/private workspace files.
- Updated ignore rules for `.private/`, `.tasks/`, `/data/`, `/logs/`, `*.settings`, and `*.millie`.
- Reset the repository for a fresh 1.0.0 start.
- Archived the previous implementation locally at `.private/archived/version_0.tar.gz`.
- Recreated baseline project documentation, task lanes, and local secret/archive ignore rules.
- Updated the dev IMAP/SMTP listeners so SSL-off ports do not advertise STARTTLS, added sanitized listener diagnostics, and corrected IMAP `BODY.PEEK` fetch responses for stricter mail clients.
- Added a no-auth development webmail view for browsing the current MILLIE mailbox with Gmail, Outlook, and Microsoft 365-inspired themes.
- Added configurable service mailbox domains in `millie.settings`, defaulting to `millie.cnbsk.cloud` with local `MILLIE` aliases for identities such as `geon@millie.cnbsk.cloud`.
- Changed the temporary SMTP listener into a setup-only blackhole shim that accepts any or no SMTP authentication, discards submitted message data, and never relays, stores, queues, or delivers outbound mail from MILLIE.
- Clarified that IMAP client edits mutate only MILLIE's copied mailbox facade and do not write back to original IMAP, Exchange, or PST sources.
- Capped search-index text before PostgreSQL `to_tsvector` indexing so oversized HTML messages do not abort imports.
- Cached mailbox folder and UID lookups during bulk PST imports to reduce repeated Postgres work.
