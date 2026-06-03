# Webmail

MILLIE currently includes a temporary authenticated webmail/admin view for local development.

Start it from the project root:

```sh
.private/venv/bin/python tools/millie_webmail_server.py --host 0.0.0.0 --port 22001 --daemon
```

The page uses the same Postgres-backed MILLIE identity password used by the development IMAP listener. For local-only development testing without login, add `--no-auth`.

Open:

```text
http://127.0.0.1:22001/
```

On the LAN, the current internal URL is:

```text
http://10.0.20.9:22001/
```

The first version opens the signed-in mailbox from the configured service mail domain, lists folders and copied messages from the Postgres mailbox facade, and renders message bodies as sanitized plain text. SMTP and compose behavior are intentionally out of scope for this archive view.

The message list loads only the selected folder. Use the **Show** control to choose `25`, `50`, `100`, `250`, `500`, or `All` messages for the active folder. The choice is remembered in browser local storage. Folder counts use cheap count queries, and the message list is cached in the browser until **Refresh** is clicked or the page is reloaded.

Use **Search** for global indexed search across copied messages. Messages with pending MILLIE brain suggestions show a suggestion badge. The reader shows proposed classifications and unsubscribe candidates for the selected message. Messages in hold folders also show matching retention policy status, including hold duration and eligibility timing. The **Review** button opens proposed classifications plus retention-eligible hold messages. The **Workbench** button groups proposed sorting suggestions by target, sender domain, folder, and year so a batch can be approved, rejected, or turned into always/never rule evidence. The **Unsub** button opens a global unsubscribe candidate queue. The **Policies** button opens retention policy management for names, hold durations, review requirements, internal actions, and active/disabled states. The **Rules** button opens learned brain rule management. The **Apply** button opens dry-run and guarded execute controls for internal suggestion/retention apply commands. The **Ops** button opens source/account status, archive counts, queue counts, recent automation runs, sync health, and bounded one-off controls for live sync, scoped account/folder sync, live upkeep, dedupe reporting, and dedupe backfill. Classification, workbench, retention, rule, apply, and Ops controls do not run remote provider purge or write back to source providers.

Settings are separate from webmail during this temporary phase:

```sh
./tmp_settings.sh
```

Open:

```text
http://127.0.0.1:22011/
```

Use that settings page to add IMAP/iCloud/SMTP account records.
