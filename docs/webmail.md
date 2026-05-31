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

The first version is read-only. It opens the current `geon@millie` mailbox, lists folders and copied messages from the Postgres mailbox facade, and renders message bodies as sanitized plain text. SMTP and compose behavior are intentionally out of scope for this archive view.
