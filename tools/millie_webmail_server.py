#!/usr/bin/env python3
"""No-auth development webmail view for the current MILLIE mailbox."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
from datetime import date, datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from html.parser import HTMLParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from millie.settings_loader import load_local_settings
from millie.storage.postgres_store import PostgresMailStore


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 22001
DEFAULT_MAILBOX = "geon@millie"
DEFAULT_PID_FILE = PROJECT_ROOT / ".private" / "local" / "millie_webmail_server.pid"
DEFAULT_LOG_FILE = PROJECT_ROOT / ".private" / "local" / "millie_webmail_server.log"


class MillieWebmailHandler(BaseHTTPRequestHandler):
    server: "MillieWebmailServer"

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self.send_html(INDEX_HTML)
            return
        if parsed.path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return
        if parsed.path == "/api/bootstrap":
            self.send_json(self.bootstrap_payload(parsed))
            return
        if parsed.path == "/api/messages":
            self.send_json(self.message_payload(parsed))
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def log_message(self, format: str, *args: object) -> None:
        print(
            f"WEBMAIL client={self.client_address[0]}:{self.client_address[1]} "
            f"request={self.requestline!r} status={args[1] if len(args) > 1 else '-'}",
            flush=True,
        )

    def bootstrap_payload(self, parsed: urllib.parse.ParseResult) -> dict[str, object]:
        folder = urllib.parse.parse_qs(parsed.query).get("folder", ["INBOX"])[0]
        with self.store() as store:
            mailbox = store.mailbox_by_address(self.server.mailbox_address)
            if mailbox is None:
                raise NotFoundError(f"Mailbox not found: {self.server.mailbox_address}")
            folders = store.list_folders(str(mailbox["id"]))
            folder_counts = {
                str(item["path"]): len(
                    store.list_webmail_messages(
                        mailbox_id=str(mailbox["id"]),
                        folder_path=str(item["path"]),
                        limit=1000,
                    )
                )
                for item in folders
                if item.get("selectable")
            }
            selected_folder = folder if folder in folder_counts else "INBOX"
            messages = store.list_webmail_messages(
                mailbox_id=str(mailbox["id"]),
                folder_path=selected_folder,
                limit=100,
            )
        return {
            "mailbox": mailbox,
            "folders": [decorate_folder(folder, folder_counts) for folder in folders],
            "selected_folder": selected_folder,
            "messages": messages,
        }

    def message_payload(self, parsed: urllib.parse.ParseResult) -> dict[str, object]:
        query = urllib.parse.parse_qs(parsed.query)
        folder = query.get("folder", ["INBOX"])[0]
        uid_text = query.get("uid", [""])[0]
        if not uid_text.isdigit():
            raise BadRequestError("uid is required")
        with self.store() as store:
            mailbox = store.mailbox_by_address(self.server.mailbox_address)
            if mailbox is None:
                raise NotFoundError(f"Mailbox not found: {self.server.mailbox_address}")
            detail = store.get_webmail_message_by_uid(
                mailbox_id=str(mailbox["id"]),
                folder_path=folder,
                uid=int(uid_text),
            )
        if detail is None:
            raise NotFoundError("Message not found")
        body = display_body(detail)
        return {
            "uid": detail["uid"],
            "folder": folder,
            "message_id": detail["message_id"],
            "internet_message_id": detail["internet_message_id"],
            "subject": detail["subject"] or "(no subject)",
            "message_date": detail["message_date"],
            "body": body,
            "body_preview": detail["body_preview"],
            "addresses": group_addresses(detail["addresses"]),
            "attachments": detail["attachments"],
            "has_attachments": detail["has_attachments"],
            "size": detail["size"],
        }

    def store(self) -> PostgresMailStore:
        return PostgresMailStore.connect(self.server.settings)

    def send_html(self, value: str) -> None:
        payload = value.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_json(self, value: object) -> None:
        payload = json.dumps(value, default=json_default, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        if self.path.startswith("/api/"):
            payload = json.dumps({"error": message or HTTPStatus(code).phrase}).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        super().send_error(code, message, explain)

    def handle_one_request(self) -> None:
        try:
            super().handle_one_request()
        except BadRequestError as exc:
            self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
        except NotFoundError as exc:
            self.send_error(HTTPStatus.NOT_FOUND, str(exc))
        except Exception as exc:  # noqa: BLE001 - dev server should surface request failures.
            print(f"WEBMAIL error={exc!r}", flush=True)
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Webmail request failed")


class MillieWebmailServer(ThreadingHTTPServer):
    def __init__(self, server_address, handler_class, *, settings: dict[str, str], mailbox_address: str):
        self.settings = settings
        self.mailbox_address = mailbox_address
        super().__init__(server_address, handler_class)


class BadRequestError(Exception):
    pass


class NotFoundError(Exception):
    pass


class BodyTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.parts.append(text)

    def get_text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", "\n".join(self.parts)).strip()


def decorate_folder(folder: dict[str, object], counts: dict[str, int]) -> dict[str, object]:
    path = str(folder["path"])
    return {
        "path": path,
        "display_name": folder.get("display_name") or path.rsplit("/", 1)[-1],
        "role": folder.get("role"),
        "selectable": bool(folder.get("selectable")),
        "subscribed": bool(folder.get("subscribed")),
        "count": counts.get(path, 0),
    }


def group_addresses(addresses: list[dict[str, object]]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for address in addresses:
        grouped.setdefault(str(address["role"]), []).append(str(address["display"]))
    return grouped


def display_body(detail: dict[str, Any]) -> str:
    if detail.get("body_text"):
        return str(detail["body_text"]).strip()
    if detail.get("body_html"):
        return html_to_text(str(detail["body_html"]))
    raw = detail.get("raw_mime")
    if raw:
        message = BytesParser(policy=policy.default).parsebytes(raw)
        text = message_text(message)
        if text:
            return text
    return detail.get("body_preview") or ""


def message_text(message: EmailMessage) -> str:
    if message.is_multipart():
        html_fallback = ""
        for part in message.walk():
            if part.get_content_disposition() == "attachment":
                continue
            content_type = part.get_content_type()
            if content_type == "text/plain":
                return str(part.get_content()).strip()
            if content_type == "text/html" and not html_fallback:
                html_fallback = html_to_text(str(part.get_content()))
        return html_fallback
    if message.get_content_type() == "text/html":
        return html_to_text(str(message.get_content()))
    return str(message.get_content()).strip()


def html_to_text(value: str) -> str:
    extractor = BodyTextExtractor()
    extractor.feed(value)
    return extractor.get_text()


def json_default(value: object) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start MILLIE's no-auth development webmail.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--mailbox", default=DEFAULT_MAILBOX)
    parser.add_argument("--daemon", action="store_true", help="Detach into the background.")
    parser.add_argument("--pid-file", type=Path, default=DEFAULT_PID_FILE)
    parser.add_argument("--log-file", type=Path, default=DEFAULT_LOG_FILE)
    return parser


def daemonize(*, pid_file: Path, log_file: Path) -> None:
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    first_pid = os.fork()
    if first_pid > 0:
        raise SystemExit(0)
    os.setsid()
    second_pid = os.fork()
    if second_pid > 0:
        pid_file.write_text(f"{second_pid}\n")
        raise SystemExit(0)
    os.chdir(PROJECT_ROOT)
    os.umask(0o077)
    log_fd = os.open(log_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.dup2(log_fd, 1)
    os.dup2(log_fd, 2)
    os.close(log_fd)
    null_fd = os.open("/dev/null", os.O_RDONLY)
    os.dup2(null_fd, 0)
    os.close(null_fd)


def serve(args: argparse.Namespace) -> None:
    settings = load_local_settings()["settings"]
    server = MillieWebmailServer(
        (args.host, args.port),
        MillieWebmailHandler,
        settings=settings,
        mailbox_address=args.mailbox,
    )
    print(f"MILLIE webmail listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


INDEX_HTML = r"""<!doctype html>
<html lang="en" data-theme="gmail">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MILLIE Mail</title>
  <style>
    :root {
      --bg: #f6f8fb;
      --surface: #ffffff;
      --surface-2: #eef2f7;
      --line: #d7dde7;
      --text: #17202f;
      --muted: #657084;
      --accent: #c5221f;
      --accent-soft: #fce8e6;
      --selected: #eaf1fb;
      --shadow: 0 1px 2px rgba(18, 28, 45, .08);
    }
    html[data-theme="outlook"] {
      --bg: #f3f7fb;
      --surface: #ffffff;
      --surface-2: #e8f1fb;
      --line: #d1dcea;
      --text: #102033;
      --muted: #52647a;
      --accent: #0078d4;
      --accent-soft: #dff0ff;
      --selected: #deecf9;
    }
    html[data-theme="m365"] {
      --bg: #f6f7fb;
      --surface: #ffffff;
      --surface-2: #eef1f6;
      --line: #d8deea;
      --text: #1c2434;
      --muted: #626f82;
      --accent: #6264a7;
      --accent-soft: #ecebff;
      --selected: #e8f5f3;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    button, input { font: inherit; }
    .app {
      display: grid;
      grid-template-rows: 56px minmax(0, 1fr);
      min-height: 100vh;
    }
    .topbar {
      display: grid;
      grid-template-columns: 220px minmax(160px, 1fr) auto auto;
      align-items: center;
      gap: 14px;
      padding: 0 18px;
      background: var(--surface);
      border-bottom: 1px solid var(--line);
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 10px;
      font-weight: 700;
      font-size: 16px;
      letter-spacing: 0;
      min-width: 0;
    }
    .brand-mark {
      display: grid;
      place-items: center;
      width: 30px;
      height: 30px;
      border-radius: 7px;
      color: #fff;
      background: var(--accent);
      box-shadow: var(--shadow);
    }
    .search {
      min-width: 0;
      height: 36px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface-2);
      color: var(--text);
      padding: 0 12px;
      outline: none;
    }
    .search:focus { border-color: var(--accent); background: var(--surface); }
    .themes {
      display: grid;
      grid-template-columns: repeat(3, minmax(70px, 1fr));
      gap: 4px;
      padding: 3px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface-2);
    }
    .themes button {
      height: 28px;
      border: 0;
      border-radius: 6px;
      background: transparent;
      color: var(--muted);
      cursor: pointer;
    }
    .themes button.active {
      color: var(--text);
      background: var(--surface);
      box-shadow: var(--shadow);
    }
    .account {
      color: var(--muted);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      max-width: 180px;
    }
    .main {
      min-height: 0;
      display: grid;
      grid-template-columns: 228px minmax(300px, 390px) minmax(360px, 1fr);
    }
    .folders, .messages, .reader {
      min-height: 0;
      overflow: auto;
      border-right: 1px solid var(--line);
      background: var(--surface);
    }
    .folders {
      padding: 12px 10px;
      background: color-mix(in srgb, var(--surface) 84%, var(--surface-2));
    }
    .folder {
      width: 100%;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: center;
      gap: 8px;
      height: 34px;
      margin: 2px 0;
      padding: 0 10px;
      border: 0;
      border-radius: 7px;
      background: transparent;
      color: var(--text);
      text-align: left;
      cursor: pointer;
    }
    .folder .name {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .folder .count { color: var(--muted); font-size: 12px; }
    .folder.active {
      background: var(--accent-soft);
      color: var(--accent);
      font-weight: 650;
    }
    .messages {
      background: var(--surface);
    }
    .list-head {
      position: sticky;
      top: 0;
      z-index: 1;
      display: flex;
      align-items: center;
      justify-content: space-between;
      height: 44px;
      padding: 0 14px;
      background: var(--surface);
      border-bottom: 1px solid var(--line);
      font-weight: 650;
    }
    .list-count { color: var(--muted); font-weight: 500; }
    .message-row {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 8px 10px;
      width: 100%;
      min-height: 94px;
      padding: 12px 14px;
      border: 0;
      border-bottom: 1px solid var(--line);
      background: var(--surface);
      color: var(--text);
      text-align: left;
      cursor: pointer;
    }
    .message-row:hover { background: color-mix(in srgb, var(--selected) 55%, var(--surface)); }
    .message-row.active {
      background: var(--selected);
      border-left: 3px solid var(--accent);
      padding-left: 11px;
    }
    .sender, .subject, .preview {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .sender { font-weight: 650; }
    .date { color: var(--muted); font-size: 12px; white-space: nowrap; }
    .subject { grid-column: 1 / -1; font-weight: 600; }
    .preview { grid-column: 1 / -1; color: var(--muted); }
    .reader {
      border-right: 0;
      background: var(--surface);
    }
    .reader-inner {
      max-width: 980px;
      margin: 0 auto;
      padding: 24px 30px 48px;
    }
    .reader-subject {
      margin: 0 0 14px;
      font-size: 24px;
      line-height: 1.2;
      letter-spacing: 0;
      overflow-wrap: anywhere;
    }
    .meta {
      display: grid;
      gap: 5px;
      padding-bottom: 18px;
      border-bottom: 1px solid var(--line);
      color: var(--muted);
    }
    .meta-row {
      display: grid;
      grid-template-columns: 54px minmax(0, 1fr);
      gap: 10px;
      align-items: baseline;
    }
    .meta-label { color: var(--muted); }
    .meta-value {
      color: var(--text);
      min-width: 0;
      overflow-wrap: anywhere;
    }
    .body {
      margin-top: 24px;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font-size: 15px;
      line-height: 1.58;
    }
    .attachments {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 22px;
    }
    .attachment {
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 8px 10px;
      background: var(--surface-2);
      color: var(--text);
      max-width: 260px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .empty, .error {
      padding: 30px;
      color: var(--muted);
    }
    @media (max-width: 980px) {
      .topbar { grid-template-columns: 160px minmax(120px, 1fr); grid-auto-flow: row; height: auto; padding: 10px 12px; }
      .themes, .account { grid-column: span 1; }
      .main { grid-template-columns: 76px minmax(260px, 38vw) minmax(320px, 1fr); }
      .folder { grid-template-columns: 1fr; justify-items: center; padding: 0 6px; }
      .folder .name { max-width: 54px; }
      .folder .count { display: none; }
    }
  </style>
</head>
<body>
  <div class="app">
    <header class="topbar">
      <div class="brand"><span class="brand-mark">M</span><span>MILLIE Mail</span></div>
      <input id="search" class="search" type="search" placeholder="Search mail" autocomplete="off">
      <div class="themes" id="themes">
        <button type="button" data-theme="gmail">Gmail</button>
        <button type="button" data-theme="outlook">Outlook</button>
        <button type="button" data-theme="m365">365</button>
      </div>
      <div class="account" id="account"></div>
    </header>
    <main class="main">
      <nav class="folders" id="folders"></nav>
      <section class="messages">
        <div class="list-head"><span id="folderTitle">INBOX</span><span class="list-count" id="messageCount">0</span></div>
        <div id="messageList"></div>
      </section>
      <section class="reader" id="reader"><div class="empty">Loading</div></section>
    </main>
  </div>
  <script>
    const state = { mailbox: null, folders: [], folder: "INBOX", messages: [], selectedUid: null, query: "" };
    const $ = (id) => document.getElementById(id);

    function setTheme(theme) {
      document.documentElement.dataset.theme = theme;
      localStorage.setItem("millie.webmail.theme", theme);
      document.querySelectorAll("#themes button").forEach((button) => {
        button.classList.toggle("active", button.dataset.theme === theme);
      });
    }

    function text(value, fallback = "") {
      return value === null || value === undefined || value === "" ? fallback : String(value);
    }

    function formatDate(value) {
      if (!value) return "";
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return "";
      return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(date);
    }

    function formatSize(value) {
      const size = Number(value || 0);
      if (size >= 1048576) return `${(size / 1048576).toFixed(1)} MB`;
      if (size >= 1024) return `${Math.round(size / 1024)} KB`;
      return `${size} B`;
    }

    async function api(path) {
      const response = await fetch(path, { cache: "no-store" });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.error || response.statusText);
      }
      return response.json();
    }

    async function loadBootstrap(folder = "INBOX") {
      const data = await api(`/api/bootstrap?folder=${encodeURIComponent(folder)}`);
      state.mailbox = data.mailbox;
      state.folders = data.folders;
      state.folder = data.selected_folder;
      state.messages = data.messages;
      state.selectedUid = state.messages[0]?.uid || null;
      render();
      if (state.selectedUid) await openMessage(state.selectedUid);
    }

    async function loadFolder(folder) {
      const data = await api(`/api/bootstrap?folder=${encodeURIComponent(folder)}`);
      state.folder = data.selected_folder;
      state.folders = data.folders;
      state.messages = data.messages;
      state.selectedUid = state.messages[0]?.uid || null;
      render();
      if (state.selectedUid) {
        await openMessage(state.selectedUid);
      } else {
        $("reader").innerHTML = `<div class="empty">No messages</div>`;
      }
    }

    async function openMessage(uid) {
      state.selectedUid = uid;
      renderMessages();
      const detail = await api(`/api/messages?folder=${encodeURIComponent(state.folder)}&uid=${encodeURIComponent(uid)}`);
      renderReader(detail);
    }

    function render() {
      $("account").textContent = state.mailbox?.mailbox_address || "";
      renderFolders();
      renderMessages();
    }

    function renderFolders() {
      const nav = $("folders");
      nav.innerHTML = "";
      state.folders.forEach((folder) => {
        if (!folder.selectable) return;
        const button = document.createElement("button");
        button.className = `folder${folder.path === state.folder ? " active" : ""}`;
        button.type = "button";
        button.innerHTML = `<span class="name"></span><span class="count"></span>`;
        button.querySelector(".name").textContent = folder.display_name;
        button.querySelector(".count").textContent = folder.count;
        button.addEventListener("click", () => loadFolder(folder.path).catch(showError));
        nav.appendChild(button);
      });
    }

    function filteredMessages() {
      const q = state.query.trim().toLowerCase();
      if (!q) return state.messages;
      return state.messages.filter((message) => [message.from, message.to, message.subject, message.body_preview]
        .some((value) => text(value).toLowerCase().includes(q)));
    }

    function renderMessages() {
      const messages = filteredMessages();
      $("folderTitle").textContent = state.folder;
      $("messageCount").textContent = messages.length;
      const list = $("messageList");
      list.innerHTML = "";
      if (!messages.length) {
        list.innerHTML = `<div class="empty">No messages</div>`;
        return;
      }
      messages.forEach((message) => {
        const row = document.createElement("button");
        row.className = `message-row${message.uid === state.selectedUid ? " active" : ""}`;
        row.type = "button";
        row.innerHTML = `
          <div class="sender"></div>
          <div class="date"></div>
          <div class="subject"></div>
          <div class="preview"></div>
        `;
        row.querySelector(".sender").textContent = text(message.from, "(unknown)");
        row.querySelector(".date").textContent = formatDate(message.message_date);
        row.querySelector(".subject").textContent = text(message.subject, "(no subject)");
        row.querySelector(".preview").textContent = text(message.body_preview, formatSize(message.size));
        row.addEventListener("click", () => openMessage(message.uid).catch(showError));
        list.appendChild(row);
      });
    }

    function renderReader(message) {
      const from = (message.addresses.from || []).join(", ");
      const to = (message.addresses.to || []).join(", ");
      const cc = (message.addresses.cc || []).join(", ");
      const attachments = message.attachments || [];
      $("reader").innerHTML = `
        <div class="reader-inner">
          <h1 class="reader-subject"></h1>
          <div class="meta">
            <div class="meta-row"><span class="meta-label">From</span><span class="meta-value" data-field="from"></span></div>
            <div class="meta-row"><span class="meta-label">To</span><span class="meta-value" data-field="to"></span></div>
            ${cc ? `<div class="meta-row"><span class="meta-label">Cc</span><span class="meta-value" data-field="cc"></span></div>` : ""}
            <div class="meta-row"><span class="meta-label">Date</span><span class="meta-value" data-field="date"></span></div>
          </div>
          <div class="body"></div>
          <div class="attachments"></div>
        </div>
      `;
      $("reader").querySelector(".reader-subject").textContent = text(message.subject, "(no subject)");
      $("reader").querySelector('[data-field="from"]').textContent = text(from, "(unknown)");
      $("reader").querySelector('[data-field="to"]').textContent = text(to, "(none)");
      const ccNode = $("reader").querySelector('[data-field="cc"]');
      if (ccNode) ccNode.textContent = cc;
      $("reader").querySelector('[data-field="date"]').textContent = formatDate(message.message_date);
      $("reader").querySelector(".body").textContent = text(message.body, "");
      const container = $("reader").querySelector(".attachments");
      attachments.forEach((attachment) => {
        const item = document.createElement("div");
        item.className = "attachment";
        item.textContent = `${attachment.filename} · ${formatSize(attachment.size)}`;
        container.appendChild(item);
      });
    }

    function showError(error) {
      $("reader").innerHTML = `<div class="error"></div>`;
      $("reader").querySelector(".error").textContent = error.message || String(error);
    }

    $("themes").addEventListener("click", (event) => {
      const button = event.target.closest("button[data-theme]");
      if (button) setTheme(button.dataset.theme);
    });
    $("search").addEventListener("input", (event) => {
      state.query = event.target.value;
      renderMessages();
    });

    setTheme(localStorage.getItem("millie.webmail.theme") || "gmail");
    loadBootstrap().catch(showError);
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    parsed_args = build_parser().parse_args()
    if parsed_args.daemon:
        daemonize(pid_file=parsed_args.pid_file, log_file=parsed_args.log_file)
    serve(parsed_args)
