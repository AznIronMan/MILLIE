# Automation And Learning

MILLIE's automation path starts with observation and review. The first brain layer stores proposed classifications and learning signals, but it does not move mail, delete mail, unsubscribe, or write back to source providers by itself.

## Safety Model

Automation levels are:

- `observe`: create suggestions and audit entries only.
- `review`: require a user decision before an action is applied.
- `auto_internal`: allow approved internal MILLIE mailbox changes only.
- `provider_write`: reserved for future provider-side actions and disabled by default.

Provider cleanup remains separate from sorting. Remote provider cleanup must use the manifest purge flow, which targets exact provider UIDs from a sync cutoff.

The settings database exposes two guardrails:

- `automation_level`: maximum autonomous level, defaulting to `observe`.
- `automation_provider_write_enabled`: second switch for future provider-side automation, defaulting to `false`.

Future provider writes require both `automation_level=provider_write` and `automation_provider_write_enabled=true`. Manifest-driven purge tools remain a separate explicit workflow.

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

Unsubscribe candidates can be approved or ignored, but approval does not execute an unsubscribe yet.

Approved folder/spam/trash suggestions can be applied to the internal MILLIE mailbox facade with a dry-run-first command:

```sh
.private/venv/bin/python tools/millie_apply_suggestions.py --limit 100
```

Execution requires `automation_level=auto_internal` or higher:

```sh
.private/venv/bin/python tools/millie_apply_suggestions.py --execute --limit 100
```

The apply command creates missing MILLIE folders and maps approved messages into those folders. It does not expunge existing MILLIE folder mappings and does not write to source providers.

Retention execution and unsubscribe execution are planned follow-up work.
