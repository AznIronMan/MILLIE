# Automation And Learning

MILLIE's automation path starts with observation and review. The first brain layer stores proposed classifications and learning signals, but it does not move mail, delete mail, unsubscribe, or write back to source providers by itself.

## Safety Model

Automation levels are:

- `observe`: create suggestions and audit entries only.
- `review`: require a user decision before an action is applied.
- `auto_internal`: allow approved internal MILLIE mailbox changes only.
- `provider_write`: reserved for future provider-side actions and disabled by default.

Provider cleanup remains separate from sorting. Remote provider cleanup must use the manifest purge flow, which targets exact provider UIDs from a sync cutoff.

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

The applied observe mode only writes to the brain tables. Review UI, automatic internal moves/tags, retention execution, and unsubscribe execution are planned follow-up work.
