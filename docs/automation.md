# Automation And Learning

MILLIE's automation path starts with observation and review. The first brain layer stores proposed classifications and learning signals, but it does not move mail, delete mail, unsubscribe, or write back to source providers by itself.

## Safety Model

Automation levels are:

- `observe`: create suggestions and audit entries only.
- `review`: require a user decision before an action is applied.
- `auto_internal`: allow approved internal MILLIE mailbox changes only.
- `provider_write`: allow explicitly approved provider-side actions, still blocked by a second switch by default.

Provider cleanup remains separate from sorting. Remote provider cleanup must use the manifest purge flow, which targets exact provider UIDs from a sync cutoff.

The settings database exposes two guardrails:

- `automation_level`: maximum autonomous level, defaulting to `observe`.
- `automation_provider_write_enabled`: second switch for future provider-side automation, defaulting to `false`.

Provider writes require both `automation_level=provider_write` and `automation_provider_write_enabled=true`. Remote provider purge execution additionally requires an explicit manifest id and records provider-write audit rows. Dry-runs remain available without provider-write settings.

## Provider Write Boundary

The only provider-side destructive path currently implemented is the manifest-driven remote provider purge executor. It targets exact source UIDs from `mail_remote_purge_manifest_messages`, checks UIDVALIDITY before deletion, and does not select mail that arrived after the manifest snapshot.

Automatic browser unsubscribe execution remains disabled even when provider-write settings are enabled. Manual-assist unsubscribe checklists are the current safe path for provider unsubscribe links.

## Brain Data

The Postgres brain layer records:

- learned rules
- message classifications
- user feedback events
- retention policies
- unsubscribe candidates
- automation runs
- automation audit log entries

This gives MILLIE a place to learn from user behavior without silently changing source mailboxes.

## Current Status

The brain tables exist in the Postgres schema. An observe-only sorter is available:

```sh
.private/venv/bin/python tools/millie_sort_mail.py --observe --limit 250
```

By default this is a dry run. To store suggestions and audit rows:

```sh
.private/venv/bin/python tools/millie_sort_mail.py --observe --apply --limit 250
```

The sorter can be scoped by account, folder, message id, and date:

```sh
.private/venv/bin/python tools/millie_sort_mail.py \
  --observe \
  --apply \
  --account geoff@example.com \
  --since 2026-01-01 \
  --until 2026-06-03
```

Unsubscribe detection is also scoped by message age. By default, `tools/millie_sort_mail.py` only creates unsubscribe candidates for messages from the last 183 days. Use `--unsubscribe-lookback-days 0` only for a deliberate full-archive unsubscribe review.

Trash, spam, and bulk-mail hints are held in separate reevaluation buckets so they can be reviewed independently:

- `Trash_Hold/Trash`: messages copied from source trash/deleted folders.
- `Trash_Hold/Spam`: messages copied from source spam/junk folders.
- `Trash_Hold/Bulk`: messages with spam or bulk-mail language that need review.

The webmail view includes a **Review** queue and message-level suggestion panels. Classification actions currently persist review decisions only:

- **Approve** marks a suggestion approved.
- **Reject** marks a suggestion rejected.
- **Always** marks it approved and creates active rule evidence.
- **Never** marks it rejected and creates active block-rule evidence.

The webmail **Workbench** groups proposed sorting suggestions by suggested target, sender domain, current folder, and message year. Batch actions use the same feedback and audit semantics as individual review actions, but apply them to the visible group so MILLIE can collect rule evidence faster. The workbench still only changes MILLIE review state and learned rule evidence; it does not move mail or write to source providers.

Large proposed-classification queues can be materialized as internal MILLIE review folders:

```sh
.private/venv/bin/python tools/millie_classification_review_buckets.py
.private/venv/bin/python tools/millie_classification_review_buckets.py --apply --clear-existing
```

The default root is `Review/Classification`. The tool maps proposed messages into `Approve Likely`, `Reject Likely`, and `Needs Skim` roll-ups, then into target/domain subfolders such as `Review/Classification/Approve Likely/Archive/Work/2017/charlestonent.com`. These folders are navigation aids only. The tool does not change classification status, apply suggestions, delete mail, unsubscribe, or write to source providers.

The primary MILLIE taxonomy can be materialized separately:

```sh
.private/venv/bin/python tools/millie_taxonomy_folders.py
.private/venv/bin/python tools/millie_taxonomy_folders.py --apply --clear-existing --retire-legacy
```

This creates and maps internal roll-up folders under `Archive`, `CNB`, `Personal`, `Important`, `Receipts`, and `Trash_Hold`. The `Archive` root has managed `Personal`, `Work`, `Education`, and `Misc` subroots. The tool uses approved/applied classifications, approve-likely proposed classifications, and copied source-folder context, then retires old internal facade mappings such as `Archive/Receipts/*`, `Archive/Taxes/*`, `Archive/Travel/*`, `Hold/Reevaluate/*`, and `trash-hold` when `--retire-legacy` is supplied. It does not approve suggestions, delete messages, unsubscribe, or write to source providers.

Active learned rules are now used by the observe sorter. **Always** rules can create future proposed classifications when their message context matches. **Never** rules can suppress matching proposed classifications. Rule conditions can include the suggested target, sender domain, current source folder, and message year. This is still observe-only behavior: rule matches write proposed classifications and audit data only.

Unsubscribe candidates can be approved or ignored. Approval does not click links, submit forms, send mail, or contact providers by itself.

The webmail **Rules** panel lists learned brain rules and can activate, disable, retire, or lightly edit rule names and priorities. Rule changes write audit rows and do not apply mail movements by themselves.

The webmail **Metrics** panel shows read-only learning health: proposed/approved/rejected suggestion counts, active and attention-needed rule counts, feedback event totals, target buckets, and top active rules. It does not run sorting, apply rules, or write to providers.

Metrics also shows **Rule candidates** discovered from current classification evidence. Each candidate includes a bounded evidence preview, matching samples, and conflict counts from MILLIE's review data. **Seed proposal** stores the candidate as a proposed brain rule. **Dismiss** stores a retired rule marker so the same candidate does not keep returning. Neither action moves mail.

Metrics shows **Taxonomy proposals** built from aggregate targets, sender domains, source folders, and message years. These proposals include LLM-ready aggregate context for review. Seeding a taxonomy proposal creates a proposed custom brain rule for manual review.

The **Ask LLM** control in the Metrics taxonomy section is a manual-only assistant. It sends aggregate proposal context only: target names, classification kinds/values, evidence counts, confidence, sender domains, source folders, and years. It does not send raw email bodies, full addresses, attachments, or message samples. The current implemented provider path is OpenAI via the configured provider tier in `millie.settings`; other provider settings are rejected until their APIs are implemented deliberately. LLM output is advisory JSON displayed in webmail only and does not activate rules or apply mail changes.

The webmail **Proposals** panel reviews saved proposals from rule candidates and taxonomy proposals. It shows proposal status counts, filters by open/proposed/active/disabled/retired/all, supports checkbox selection, and can activate, disable, or retire one or many proposals. These actions only update `millie_brain_rules` status and write audit rows. They do not move messages, apply retention, unsubscribe, or write to source providers.

The **Proposals** panel also has an **Observe** preview. It runs `tools/millie_sort_mail.py --observe` without `--apply` and prints a bounded dry-run summary. Use it after activating proposal rules to see what the current active brain rules would suggest before any suggestions are persisted or applied.

## Search Rebuild

`mail_search_documents` is derived data. If a recovery or clean rebuild leaves it empty, rebuild it from recovered message, address, and metadata rows:

```sh
.private/venv/bin/python tools/millie_rebuild_search_documents.py
.private/venv/bin/python tools/millie_rebuild_search_documents.py --apply --batch-size 2000
```

The command is dry-run by default. Apply mode writes only `mail_search_documents`, commits in batches, and skips damaged recovered rows that fail during search regeneration. It does not read source providers, move mail, write provider state, or touch raw MIME.

Reviewed unsubscribe candidates can be listed and prepared with a dry-run-first command:

```sh
.private/venv/bin/python tools/millie_unsubscribe_review.py list --status approved --include-browser
.private/venv/bin/python tools/millie_unsubscribe_review.py prepare --execute
```

Preparation records `attempting` or `unsafe` state plus `unsubscribe_attempt` audit rows only. It does not load unsubscribe URLs or submit forms. Browser-required or body-derived candidates are marked unsafe unless explicitly prepared with manual browser assist:

```sh
.private/venv/bin/python tools/millie_unsubscribe_review.py prepare \
  --allow-browser-manual \
  --execute
```

Generate a local manual-assist checklist for approved or prepared candidates:

```sh
.private/venv/bin/python tools/millie_unsubscribe_review.py assist
```

The checklist is written under ignored `.private/local/` and contains reviewed links/mailto targets for human follow-up. MILLIE still does not click or submit anything.

Messages opened from hold folders with matching retention policies show a read-only retention panel in webmail. The panel shows the policy status, hold duration, target action, review requirement, copied date, and eligibility date. It does not hide, expire, delete, or otherwise change messages.

Retention-eligible hold-folder messages also appear in the webmail **Review** queue. **Acknowledge** records that the item was reviewed. **Snooze 7d** records a short deferral before it appears in the queue again. Both actions write `retention_override` feedback and `retention_evaluate` audit rows only; they do not perform the policy action.

Approved folder/spam/trash suggestions can be applied to the internal MILLIE mailbox facade with a dry-run-first command:

```sh
.private/venv/bin/python tools/millie_apply_suggestions.py --limit 100
```

Execution requires `automation_level=auto_internal` or higher:

```sh
.private/venv/bin/python tools/millie_apply_suggestions.py --execute --limit 100
```

The apply command creates missing MILLIE folders and maps approved messages into those folders. It does not expunge existing MILLIE folder mappings and does not write to source providers.

The webmail **Apply** panel can run the same approved-suggestion command in dry-run mode or execute mode. Execute mode is blocked unless `automation_level` allows `auto_internal`.

Reviewed retention decisions can be applied with a dry-run-first command:

```sh
.private/venv/bin/python tools/millie_apply_retention.py --limit 100
```

Execution requires `automation_level=auto_internal` or higher:

```sh
.private/venv/bin/python tools/millie_apply_retention.py --execute --limit 100
```

The retention apply command only considers acknowledged decisions for active policies. It supports `no_action` audit application and non-destructive `hide_from_default_views`, which marks matching `INBOX` and `All Mail` facade rows hidden while leaving hold/source folders and provider mail intact. `expire_internal_copy` and `delete_internal_copy` are not executed yet.

The webmail **Apply** panel can also run the retention apply command in dry-run mode or execute mode. This path remains internal-only and never writes to source providers.

Automatic unsubscribe execution is planned follow-up work. Manual-assist preparation is available, but browser automation and provider form submission are not enabled.

## Empty Metadata Cleanup

MILLIE can report empty internal metadata without contacting source providers:

```sh
.private/venv/bin/python tools/millie_cleanup_empty.py
```

The cleanup command reports:

- empty custom MILLIE mailbox leaf folders
- empty canonical source-folder metadata leaves
- blank address rows
- non-running import jobs with no attached messages
- source definitions with no messages, folders, aliases, cursors, jobs, or bindings
- optional report-only derived MIME part containers with `--include-derived-parts-report`

Execution is separated by category and blocked unless `automation_level` allows `auto_internal`:

```sh
.private/venv/bin/python tools/millie_cleanup_empty.py --execute-mailbox-folders
```

Mailbox folder execution only deletes custom leaf folders with no message mappings and no child folders. It does not delete canonical messages, source provider mail, source accounts, source folders, addresses, import jobs, or MIME parts unless their specific execute flags are supplied. Derived MIME part containers are report-only because raw MIME remains the canonical recall source.

## Retention Holds

MILLIE can seed proposed no-action retention policies for hold folders:

```sh
.private/venv/bin/python tools/millie_retention_scan.py --seed-defaults
```

Defaults:

- `Trash_Hold/Trash`: review after 30 days, `no_action`, review required.
- `Trash_Hold/Spam`: review after 14 days, `no_action`, review required.
- `Trash_Hold/Bulk`: review after 14 days, `no_action`, review required.

Retention policies can be listed and edited with a dry-run-first policy manager:

```sh
.private/venv/bin/python tools/millie_retention_policies.py list
.private/venv/bin/python tools/millie_retention_policies.py activate --default-holds
.private/venv/bin/python tools/millie_retention_policies.py activate --default-holds --execute
```

Create or update folder policies:

```sh
.private/venv/bin/python tools/millie_retention_policies.py create \
  --name "Hide reviewed trash from default views" \
  --folder Trash_Hold/Trash \
  --duration 30d \
  --action hide_from_default_views
```

Mutating policy commands require `--execute` and write audit rows. Supported policy actions are `no_action`, `hide_from_default_views`, `expire_internal_copy`, and `delete_internal_copy`, but only `no_action` and `hide_from_default_views` are currently executable by `tools/millie_apply_retention.py`.

The webmail **Policies** button can also list, activate, disable, and edit retention policy names, hold durations, review requirements, statuses, and internal actions. These controls write audit rows and do not touch source providers.

Run a dry scan:

```sh
.private/venv/bin/python tools/millie_retention_scan.py --limit 100
```

The scanner reports held messages and retention-eligible messages. It does not hide, expire, delete, unsubscribe, or write to source providers. With `--record-scan`, it records a `retention_scan` run and `retention_evaluate` audit rows only.

## Live Upkeep

Run one live upkeep pass:

```sh
.private/venv/bin/python tools/millie_live_upkeep.py --once
```

By default, one upkeep pass runs live sync, duplicate fingerprint backfill, observe sorting with persisted suggestions, retention scanning, and safe internal apply commands. Internal apply commands execute only when `automation_level` allows `auto_internal`; otherwise they run as dry-run reports. Gmail label aliasing is available when exact folders are supplied:

```sh
.private/venv/bin/python tools/millie_live_upkeep.py \
  --once \
  --gmail-label-folder "[Gmail]/All Mail"
```

Add an empty metadata report to a runtime upkeep pass with:

```sh
.private/venv/bin/python tools/millie_live_upkeep.py --once --empty-cleanup
```

With `automation_level=auto_internal`, `--empty-cleanup --empty-cleanup-execute` also deletes empty custom mailbox leaf folders.

For a runtime loop that stops when the command stops:

```sh
.private/venv/bin/python tools/millie_live_upkeep.py --interval 900
```

Each upkeep pass records a `live_upkeep` automation run with step return codes and timings. This is runtime behavior only; it does not install a macOS service.

The webmail **Ops** dashboard shows recent `live_upkeep` runs, live source cursor status, per-account/folder sync health, queue counts, and bounded one-off buttons for sync, scoped account/folder sync, upkeep, dedupe report, and dedupe backfill. A folder becomes stale after `sync_stale_after_hours` in `millie.settings`, defaulting to 24 hours. The dashboard is for local MILLIE maintenance only and does not run remote provider purge.
