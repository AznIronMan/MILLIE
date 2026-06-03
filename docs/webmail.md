# Webmail

MILLIE currently includes a temporary no-auth webmail view for local development.

Start it from the project root:

```sh
.private/venv/bin/python tools/millie_webmail_server.py --host 0.0.0.0 --port 22001 --daemon
```

Open:

```text
http://127.0.0.1:22001/
```

On the LAN, the current internal URL is:

```text
http://10.0.20.9:22001/
```

The first version opens the current `geon@millie.cnbsk.cloud` mailbox from the configured service mail domain, lists folders and copied messages from the Postgres mailbox facade, and renders message bodies as sanitized plain text. SMTP and compose behavior are intentionally out of scope for this archive view.

The message list loads only the selected folder. Use the **Show** control to choose `25`, `50`, `100`, `250`, `500`, or `All` messages for the active folder. The choice is remembered in browser local storage. Folder counts use cheap count queries, and the message list is cached in the browser until **Refresh** is clicked or the page is reloaded.

Messages with pending MILLIE brain suggestions show a suggestion badge. The reader shows proposed classifications and unsubscribe candidates for the selected message. Messages in hold folders also show matching retention policy status, including hold duration and eligibility timing. The **Review** button opens proposed classifications plus retention-eligible hold messages. Approve/reject/always/never classification actions and acknowledge/snooze retention actions write review feedback, learned rule evidence, and audit rows only; they do not move messages, delete messages, unsubscribe, expire messages, or write to source providers.

Settings are separate from webmail during this temporary phase:

```sh
./tmp_settings.sh
```

Open:

```text
http://127.0.0.1:22011/
```

Use that settings page to add IMAP/iCloud/SMTP account records.
