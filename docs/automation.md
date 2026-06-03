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

The webmail view includes a **Review** queue and message-level suggestion panels. Classification actions currently persist review decisions only:

- **Approve** marks a suggestion approved.
- **Reject** marks a suggestion rejected.
- **Always** marks it approved and creates active rule evidence.
- **Never** marks it rejected and creates active block-rule evidence.

The webmail **Workbench** groups proposed sorting suggestions by suggested target, sender domain, current folder, and message year. Batch actions use the same feedback and audit semantics as individual review actions, but apply them to the visible group so MILLIE can collect rule evidence faster. The workbench still only changes MILLIE review state and learned rule evidence; it does not move mail or write to source providers.

Unsubscribe candidates can be approved or ignored. Approval does not click links, submit forms, send mail, or contact providers by itself.

The webmail **Rules** panel lists learned brain rules and can activate, disable, retire, or lightly edit rule names and priorities. Rule changes write audit rows and do not apply mail movements by themselves.

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

## Retention Holds

MILLIE can seed proposed no-action retention policies for hold folders:

```sh
.private/venv/bin/python tools/millie_retention_scan.py --seed-defaults
```

Defaults:

- `Hold/Trash`: review after 30 days, `no_action`, review required.
- `Hold/Spam`: review after 14 days, `no_action`, review required.

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
  --folder Hold/Trash \
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

For a runtime loop that stops when the command stops:

```sh
.private/venv/bin/python tools/millie_live_upkeep.py --interval 900
```

Each upkeep pass records a `live_upkeep` automation run with step return codes and timings. This is runtime behavior only; it does not install a macOS service.

The webmail **Ops** dashboard shows recent `live_upkeep` runs, live source cursor status, per-account/folder sync health, queue counts, and bounded one-off buttons for sync, scoped account/folder sync, upkeep, dedupe report, and dedupe backfill. A folder becomes stale after `sync_stale_after_hours` in `millie.settings`, defaulting to 24 hours. The dashboard is for local MILLIE maintenance only and does not run remote provider purge.
