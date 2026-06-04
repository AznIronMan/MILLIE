# Database Recovery And Containment

MILLIE currently runs from a recovered Postgres archive. The recovery is operationally contained, but the original source data was physically damaged. A small number of old archived rows may still fail during deep scans.

## Current Endpoint

Use the dedicated MILLIE recovery cluster only:

```text
10.0.10.81:55432/millie
```

Do not use Phoebe/Jazmine's main Postgres port for MILLIE:

```text
10.0.10.81:5432
```

The old main-cluster `millie` database is quarantined with connections disabled. It must stay disabled and must not be imported back into the main Jazmine database cluster.

## Operating Rules

- Keep MILLIE clients, importers, sync jobs, webmail, IMAP, automation, and maintenance scripts pointed at `10.0.10.81:55432/millie`.
- Treat the recovered archive as read-mostly until a clean successor database exists.
- Leave autovacuum disabled on the dedicated recovery cluster for now.
- Avoid `VACUUM FULL`, broad `ANALYZE`, `pg_amcheck`, large index rebuilds, and aggressive autovacuum against large MILLIE mail tables unless there is a staged rebuild or maintenance plan.
- Before any maintenance, take a fresh backup or snapshot and use `/data/backup/millie/` or `/data/backups/` as the work area.

MILLIE also has a runtime guard that refuses the known quarantined endpoint `10.0.10.81:5432/millie`.

## Current Data State

The recovered archive contains the live canonical mail data, mailbox facade data, and raw MIME records. Derived search data may need a controlled rebuild later.

Known recovery counts from the containment event:

- `mail_messages`: 161014
- `mail_message_parts`: 688748
- `mail_raw_mime`: 161013
- `mail_search_documents`: 0

The original loaded message count was about 161061, so the known loss is about 47 records, roughly 0.03 percent.

## Clean Successor Plan

The safer long-term fix is a staged clean rebuild:

1. Create a fresh MILLIE database on an isolated cluster.
2. Copy readable rows in bounded batches.
3. Skip or quarantine records that fail reads.
4. Rebuild derived search tables from the clean message set.
5. Validate counts, source mappings, mailbox facade rows, and raw MIME recall.
6. Switch MILLIE settings to the clean successor endpoint.

Until that rebuild is complete, containment is the protection: the damaged archive must not be allowed to destabilize the main Jazmine/Phoebe Postgres cluster.
