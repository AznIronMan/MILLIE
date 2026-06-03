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
from xml.sax.saxutils import escape as xml_escape

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from millie.settings_loader import load_local_settings
from millie.service.auth import default_service_login
from millie.storage.postgres_store import PostgresMailStore
from millie.sync.live_mail import LiveSyncConfig, start_live_sync_thread


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 22001
DEFAULT_PID_FILE = PROJECT_ROOT / ".private" / "local" / "millie_webmail_server.pid"
DEFAULT_LOG_FILE = PROJECT_ROOT / ".private" / "local" / "millie_webmail_server.log"
AUTODISCOVER_PATHS = {
    "/autodiscover/autodiscover.xml",
    "/autodiscover/autodiscovery.xml",
}
AUTOCONFIG_PATHS = {
    "/mail/config-v1.1.xml",
    "/autoconfig/mail/config-v1.1.xml",
    "/.well-known/autoconfig/mail/config-v1.1.xml",
}
MESSAGE_LIMIT_OPTIONS = {"25", "50", "100", "250", "500", "all"}
DEFAULT_MESSAGE_LIMIT = "50"


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
        if parsed.path == "/api/review":
            self.send_json(self.review_payload(parsed))
            return
        if parsed.path == "/api/unsubscribe":
            self.send_json(self.unsubscribe_payload(parsed))
            return
        if parsed.path == "/api/retention/policies":
            self.send_json(self.retention_policies_payload(parsed))
            return
        if parsed.path in AUTODISCOVER_PATHS:
            self.send_xml(autodiscover_xml(self.server.settings, self.server.mailbox_address))
            return
        if parsed.path in AUTOCONFIG_PATHS:
            self.send_xml(autoconfig_xml(self.server.settings, self.server.mailbox_address))
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in AUTODISCOVER_PATHS:
            body = self.read_request_body()
            requested_email = autodiscover_request_email(body) or self.server.mailbox_address
            self.send_xml(autodiscover_xml(self.server.settings, requested_email))
            return
        if parsed.path == "/api/classifications/action":
            self.send_json(self.classification_action_payload())
            return
        if parsed.path == "/api/unsubscribe/action":
            self.send_json(self.unsubscribe_action_payload())
            return
        if parsed.path == "/api/retention/policies/action":
            self.send_json(self.retention_policy_action_payload())
            return
        if parsed.path == "/api/retention/action":
            self.send_json(self.retention_action_payload())
            return
        self.send_error(HTTPStatus.METHOD_NOT_ALLOWED, "Method not allowed")

    def log_message(self, format: str, *args: object) -> None:
        print(
            f"WEBMAIL client={self.client_address[0]}:{self.client_address[1]} "
            f"request={self.requestline!r} status={args[1] if len(args) > 1 else '-'}",
            flush=True,
        )

    def bootstrap_payload(self, parsed: urllib.parse.ParseResult) -> dict[str, object]:
        query = urllib.parse.parse_qs(parsed.query)
        folder = query.get("folder", ["INBOX"])[0]
        requested_limit = parse_message_limit(query.get("limit", [DEFAULT_MESSAGE_LIMIT])[0])
        with self.store() as store:
            mailbox = store.mailbox_by_address(self.server.mailbox_address)
            if mailbox is None:
                raise NotFoundError(f"Mailbox not found: {self.server.mailbox_address}")
            folders = store.list_folders(str(mailbox["id"]))
            folder_counts = store.webmail_folder_counts(mailbox_id=str(mailbox["id"]))
            selected_folder = folder if folder in folder_counts else "INBOX"
            messages = store.list_webmail_messages(
                mailbox_id=str(mailbox["id"]),
                folder_path=selected_folder,
                limit=None if requested_limit == "all" else int(requested_limit),
            )
        return {
            "mailbox": mailbox,
            "folders": [decorate_folder(folder, folder_counts) for folder in folders],
            "selected_folder": selected_folder,
            "message_limit": requested_limit,
            "folder_count": folder_counts.get(selected_folder, 0),
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
            "classifications": detail["classifications"],
            "unsubscribe_candidates": detail["unsubscribe_candidates"],
            "retention_status": detail["retention_status"],
        }

    def review_payload(self, parsed: urllib.parse.ParseResult) -> dict[str, object]:
        query = urllib.parse.parse_qs(parsed.query)
        limit_text = query.get("limit", ["50"])[0]
        try:
            limit = min(max(int(limit_text), 1), 250)
        except ValueError:
            limit = 50
        with self.store() as store:
            mailbox = store.mailbox_by_address(self.server.mailbox_address)
            if mailbox is None:
                raise NotFoundError(f"Mailbox not found: {self.server.mailbox_address}")
            suggestions = store.list_review_suggestions(limit=limit)
            retention = store.list_retention_review_items(
                mailbox_id=str(mailbox["id"]),
                limit=limit,
            )
        return {"suggestions": suggestions, "retention": retention, "limit": limit}

    def unsubscribe_payload(self, parsed: urllib.parse.ParseResult) -> dict[str, object]:
        query = urllib.parse.parse_qs(parsed.query)
        limit_text = query.get("limit", ["100"])[0]
        try:
            limit = min(max(int(limit_text), 1), 250)
        except ValueError:
            limit = 100
        statuses = [
            status
            for value in query.get("status", [])
            for status in value.split(",")
            if status
        ]
        with self.store() as store:
            candidates = store.list_unsubscribe_review_items(
                limit=limit,
                statuses=statuses or None,
            )
        return {"candidates": candidates, "limit": limit, "statuses": statuses}

    def retention_policies_payload(self, parsed: urllib.parse.ParseResult) -> dict[str, object]:
        query = urllib.parse.parse_qs(parsed.query)
        statuses = [
            status
            for value in query.get("status", [])
            for status in value.split(",")
            if status
        ]
        with self.store() as store:
            policies = store.list_retention_policies(statuses=statuses or None)
        return {"policies": policies, "statuses": statuses}

    def classification_action_payload(self) -> dict[str, object]:
        payload = self.read_json_body()
        classification_id = str(payload.get("classification_id") or "")
        action = str(payload.get("action") or "")
        if not classification_id or action not in {"approve", "reject", "always", "never"}:
            raise BadRequestError("classification_id and valid action are required")
        with self.store() as store:
            mailbox = store.mailbox_by_address(self.server.mailbox_address)
            identity_id = str(mailbox["owner_identity_id"]) if mailbox else None
            try:
                result = store.record_classification_feedback(
                    classification_id=classification_id,
                    action=action,
                    identity_id=identity_id,
                )
            except KeyError as exc:
                raise NotFoundError(str(exc)) from exc
            store.connection.commit()
        return {"ok": True, "classification": result}

    def unsubscribe_action_payload(self) -> dict[str, object]:
        payload = self.read_json_body()
        candidate_id = str(payload.get("candidate_id") or "")
        action = str(payload.get("action") or "")
        if not candidate_id or action not in {"approve", "reject"}:
            raise BadRequestError("candidate_id and valid action are required")
        with self.store() as store:
            mailbox = store.mailbox_by_address(self.server.mailbox_address)
            identity_id = str(mailbox["owner_identity_id"]) if mailbox else None
            try:
                result = store.record_unsubscribe_feedback(
                    candidate_id=candidate_id,
                    action=action,
                    identity_id=identity_id,
                )
            except KeyError as exc:
                raise NotFoundError(str(exc)) from exc
            store.connection.commit()
        return {"ok": True, "unsubscribe_candidate": result}

    def retention_action_payload(self) -> dict[str, object]:
        payload = self.read_json_body()
        policy_id = str(payload.get("policy_id") or "")
        mailbox_message_id = str(payload.get("mailbox_message_id") or "")
        action = str(payload.get("action") or "")
        if not policy_id or not mailbox_message_id or action not in {"acknowledge", "defer"}:
            raise BadRequestError("policy_id, mailbox_message_id, and valid action are required")
        with self.store() as store:
            mailbox = store.mailbox_by_address(self.server.mailbox_address)
            if mailbox is None:
                raise NotFoundError(f"Mailbox not found: {self.server.mailbox_address}")
            identity_id = str(mailbox["owner_identity_id"])
            try:
                result = store.record_retention_feedback(
                    mailbox_id=str(mailbox["id"]),
                    policy_id=policy_id,
                    mailbox_message_id=mailbox_message_id,
                    action=action,
                    identity_id=identity_id,
                )
            except KeyError as exc:
                raise NotFoundError(str(exc)) from exc
            except ValueError as exc:
                raise BadRequestError(str(exc)) from exc
            store.connection.commit()
        return {"ok": True, "retention": result}

    def retention_policy_action_payload(self) -> dict[str, object]:
        payload = self.read_json_body()
        policy_id = str(payload.get("policy_id") or "")
        action = str(payload.get("action") or "").strip().lower()
        if not policy_id or action not in {"activate", "disable", "update"}:
            raise BadRequestError("policy_id and valid action are required")
        updates: dict[str, object] = {}
        if action == "update":
            if "policy_name" in payload:
                updates["policy_name"] = str(payload.get("policy_name") or "")
            if "status" in payload:
                updates["status"] = str(payload.get("status") or "")
            if "policy_action" in payload:
                updates["policy_action"] = str(payload.get("policy_action") or "")
            if "requires_review" in payload:
                updates["requires_review"] = bool(payload.get("requires_review"))
            if "hold_duration_seconds" in payload:
                try:
                    updates["hold_duration_seconds"] = int(str(payload.get("hold_duration_seconds") or "0"))
                except ValueError as exc:
                    raise BadRequestError("hold_duration_seconds must be a number") from exc
        with self.store() as store:
            mailbox = store.mailbox_by_address(self.server.mailbox_address)
            identity_id = str(mailbox["owner_identity_id"]) if mailbox else None
            try:
                policy = store.record_retention_policy_action(
                    policy_id=policy_id,
                    action=action,
                    updates=updates,
                    identity_id=identity_id,
                )
            except KeyError as exc:
                raise NotFoundError(str(exc)) from exc
            except ValueError as exc:
                raise BadRequestError(str(exc)) from exc
            store.connection.commit()
        return {"ok": True, "policy": policy}

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

    def send_xml(self, value: str) -> None:
        payload = value.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/xml; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def read_request_body(self) -> bytes:
        length_text = self.headers.get("Content-Length") or "0"
        try:
            length = int(length_text)
        except ValueError:
            length = 0
        return self.rfile.read(max(length, 0)) if length else b""

    def read_json_body(self) -> dict[str, object]:
        body = self.read_request_body()
        if not body:
            raise BadRequestError("JSON body is required")
        try:
            value = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise BadRequestError("Invalid JSON body") from exc
        if not isinstance(value, dict):
            raise BadRequestError("JSON object body is required")
        return value

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


def parse_message_limit(value: str) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in MESSAGE_LIMIT_OPTIONS else DEFAULT_MESSAGE_LIMIT


def autodiscover_request_email(body: bytes) -> str | None:
    text = body.decode("utf-8", errors="ignore")
    match = re.search(r"<(?:[A-Za-z0-9_]+:)?E?MailAddress>\s*([^<]+?)\s*</", text, re.I)
    if not match:
        return None
    value = match.group(1).strip()
    return value if "@" in value else None


def autodiscover_xml(settings: dict[str, str], login_name: str) -> str:
    domain = settings.get("service_mail_domain") or "localhost"
    login = login_name or default_service_login(settings, "geon")
    return f"""<?xml version="1.0" encoding="utf-8"?>
<Autodiscover xmlns="http://schemas.microsoft.com/exchange/autodiscover/responseschema/2006">
  <Response xmlns="http://schemas.microsoft.com/exchange/autodiscover/outlook/responseschema/2006a">
    <Account>
      <AccountType>email</AccountType>
      <Action>settings</Action>
      <Protocol>
        <Type>IMAP</Type>
        <Server>{xml_escape(domain)}</Server>
        <Port>993</Port>
        <LoginName>{xml_escape(login)}</LoginName>
        <SSL>on</SSL>
        <AuthRequired>on</AuthRequired>
      </Protocol>
      <Protocol>
        <Type>SMTP</Type>
        <Server>{xml_escape(domain)}</Server>
        <Port>465</Port>
        <LoginName>{xml_escape(login)}</LoginName>
        <SSL>on</SSL>
        <AuthRequired>on</AuthRequired>
      </Protocol>
    </Account>
  </Response>
</Autodiscover>
"""


def autoconfig_xml(settings: dict[str, str], login_name: str) -> str:
    domain = settings.get("service_mail_domain") or "localhost"
    login = login_name or default_service_login(settings, "geon")
    return f"""<?xml version="1.0" encoding="utf-8"?>
<clientConfig version="1.1">
  <emailProvider id="{xml_escape(domain)}">
    <domain>{xml_escape(domain)}</domain>
    <displayName>MILLIE Mail</displayName>
    <displayShortName>MILLIE</displayShortName>
    <incomingServer type="imap">
      <hostname>{xml_escape(domain)}</hostname>
      <port>993</port>
      <socketType>SSL</socketType>
      <authentication>password-cleartext</authentication>
      <username>{xml_escape(login)}</username>
    </incomingServer>
    <outgoingServer type="smtp">
      <hostname>{xml_escape(domain)}</hostname>
      <port>465</port>
      <socketType>SSL</socketType>
      <authentication>password-cleartext</authentication>
      <username>{xml_escape(login)}</username>
    </outgoingServer>
  </emailProvider>
</clientConfig>
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start MILLIE's no-auth development webmail.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--mailbox",
        default="",
        help="Mailbox address to open. Defaults to geon@<service_mail_domain> from millie.settings.",
    )
    parser.add_argument("--daemon", action="store_true", help="Detach into the background.")
    parser.add_argument("--pid-file", type=Path, default=DEFAULT_PID_FILE)
    parser.add_argument("--log-file", type=Path, default=DEFAULT_LOG_FILE)
    parser.add_argument(
        "--live-sync",
        action="store_true",
        help="Sync enabled IMAP/OAuth accounts while this webmail process is running.",
    )
    parser.add_argument(
        "--sync-account",
        action="append",
        default=[],
        help="Account email/id/display name to sync. May be repeated. Defaults to all enabled IMAP accounts.",
    )
    parser.add_argument("--sync-interval", type=int, default=900, help="Seconds between live sync passes.")
    parser.add_argument("--sync-fetch-batch-size", type=int, default=10)
    parser.add_argument("--sync-commit-every", type=int, default=50)
    parser.add_argument("--sync-imap-timeout", type=int, default=120)
    parser.add_argument(
        "--no-sync-on-start",
        action="store_true",
        help="Wait one interval before the first live sync pass.",
    )
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
    mailbox_address = args.mailbox or default_service_login(settings, "geon")
    sync_thread = None
    sync_stop = None
    if args.live_sync:
        sync_config = LiveSyncConfig(
            accounts=tuple(args.sync_account),
            interval_seconds=args.sync_interval,
            fetch_batch_size=args.sync_fetch_batch_size,
            commit_every=args.sync_commit_every,
            imap_timeout_seconds=args.sync_imap_timeout,
        )
        sync_thread, sync_stop = start_live_sync_thread(
            sync_config,
            run_immediately=not args.no_sync_on_start,
            log=lambda value: print(value, flush=True),
        )
    server = MillieWebmailServer(
        (args.host, args.port),
        MillieWebmailHandler,
        settings=settings,
        mailbox_address=mailbox_address,
    )
    print(f"MILLIE webmail listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
    finally:
        if sync_stop is not None:
            sync_stop.set()
        if sync_thread is not None:
            sync_thread.join(timeout=5)


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
    button, input, select { font: inherit; }
    .app {
      display: grid;
      grid-template-rows: 56px minmax(0, 1fr);
      min-height: 100vh;
    }
    .topbar {
      display: grid;
      grid-template-columns: 220px minmax(160px, 1fr) auto auto auto auto auto;
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
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: center;
      gap: 10px;
      height: 44px;
      padding: 0 14px;
      background: var(--surface);
      border-bottom: 1px solid var(--line);
      font-weight: 650;
    }
    .list-title {
      display: flex;
      align-items: baseline;
      gap: 8px;
      min-width: 0;
    }
    #folderTitle {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .list-count { color: var(--muted); font-weight: 500; }
    .list-controls {
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .limit-label {
      display: flex;
      align-items: center;
      gap: 5px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 600;
      white-space: nowrap;
    }
    .limit-select {
      width: 66px;
      height: 30px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--surface);
      color: var(--text);
      font: inherit;
      font-size: 12px;
    }
    .refresh-button {
      height: 30px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--surface);
      color: var(--text);
      padding: 0 9px;
      font: inherit;
      font-size: 12px;
      font-weight: 650;
      cursor: pointer;
    }
    .refresh-button:hover { border-color: var(--accent); color: var(--accent); }
    .review-button {
      height: 32px;
      border: 1px solid var(--line);
      border-radius: 7px;
      background: var(--surface);
      color: var(--text);
      padding: 0 11px;
      font: inherit;
      font-size: 12px;
      font-weight: 700;
      cursor: pointer;
      white-space: nowrap;
    }
    .review-button:hover { border-color: var(--accent); color: var(--accent); }
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
    .subject-line {
      grid-column: 1 / -1;
      display: flex;
      align-items: center;
      gap: 7px;
      min-width: 0;
    }
    .subject { font-weight: 600; }
    .suggestion-badge {
      flex: 0 0 auto;
      display: inline-flex;
      align-items: center;
      height: 19px;
      padding: 0 6px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent);
      font-size: 11px;
      font-weight: 700;
    }
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
    .review-panel {
      display: grid;
      gap: 10px;
      margin-top: 18px;
      padding: 14px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface-2);
    }
    .review-panel h2 {
      margin: 0;
      font-size: 15px;
      letter-spacing: 0;
    }
    .suggestion-card {
      display: grid;
      gap: 8px;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface);
    }
    .suggestion-top {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
    }
    .suggestion-title { font-weight: 700; overflow-wrap: anywhere; }
    .suggestion-meta { color: var(--muted); font-size: 12px; }
    .suggestion-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }
    .suggestion-actions button {
      height: 28px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--surface);
      color: var(--text);
      padding: 0 9px;
      font: inherit;
      font-size: 12px;
      font-weight: 650;
      cursor: pointer;
    }
    .suggestion-actions button:hover { border-color: var(--accent); color: var(--accent); }
    .policy-edit {
      display: grid;
      grid-template-columns: minmax(180px, 1fr) repeat(4, auto);
      gap: 8px;
      align-items: end;
    }
    .policy-edit label {
      display: grid;
      gap: 4px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }
    .policy-edit input,
    .policy-edit select {
      height: 30px;
      min-width: 0;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--surface);
      color: var(--text);
      padding: 0 8px;
      font-size: 12px;
    }
    .policy-edit .check-label {
      display: flex;
      align-items: center;
      gap: 6px;
      height: 30px;
    }
    .policy-edit .check-label input {
      width: 16px;
      height: 16px;
      padding: 0;
    }
    .policy-edit .duration-group {
      display: grid;
      grid-template-columns: 70px 82px;
      gap: 6px;
    }
    .review-list {
      display: grid;
      gap: 10px;
      padding: 24px 30px 48px;
    }
    .review-list h1 {
      margin: 0 0 4px;
      font-size: 24px;
      letter-spacing: 0;
    }
    .empty, .error {
      padding: 30px;
      color: var(--muted);
    }
    @media (max-width: 980px) {
      .topbar { grid-template-columns: 160px minmax(120px, 1fr); grid-auto-flow: row; height: auto; padding: 10px 12px; }
      .themes, .account { grid-column: span 1; }
      .policy-edit { grid-template-columns: 1fr 1fr; align-items: stretch; }
      .main { grid-template-columns: 76px minmax(260px, 38vw) minmax(320px, 1fr); }
      .folder { grid-template-columns: 1fr; justify-items: center; padding: 0 6px; }
      .folder .name { max-width: 54px; }
      .folder .count { display: none; }
      .list-head { height: auto; min-height: 44px; align-items: stretch; padding: 8px 10px; }
      .list-title { flex-direction: column; gap: 0; }
      .list-controls { align-self: center; }
    }
  </style>
</head>
<body>
  <div class="app">
    <header class="topbar">
      <div class="brand"><span class="brand-mark">M</span><span>MILLIE Mail</span></div>
      <input id="search" class="search" type="search" placeholder="Search mail" autocomplete="off">
      <button id="reviewButton" class="review-button" type="button">Review</button>
      <button id="unsubscribeButton" class="review-button" type="button">Unsub</button>
      <button id="policiesButton" class="review-button" type="button">Policies</button>
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
        <div class="list-head">
          <div class="list-title">
            <span id="folderTitle">INBOX</span>
            <span class="list-count" id="messageCount">0</span>
          </div>
          <div class="list-controls">
            <label class="limit-label">Show
              <select id="messageLimit" class="limit-select">
                <option value="25">25</option>
                <option value="50">50</option>
                <option value="100">100</option>
                <option value="250">250</option>
                <option value="500">500</option>
                <option value="all">All</option>
              </select>
            </label>
            <button id="refreshFolder" class="refresh-button" type="button">Refresh</button>
          </div>
        </div>
        <div id="messageList"></div>
      </section>
      <section class="reader" id="reader"><div class="empty">Loading</div></section>
    </main>
  </div>
  <script>
    const limitStorageKey = "millie.webmail.messageLimit";
    const validMessageLimits = new Set(["25", "50", "100", "250", "500", "all"]);
    const savedLimit = localStorage.getItem(limitStorageKey) || "50";
    const state = {
      mailbox: null,
      folders: [],
      folder: "INBOX",
      folderCount: 0,
      messages: [],
      selectedUid: null,
      query: "",
      limit: validMessageLimits.has(savedLimit) ? savedLimit : "50",
      cache: new Map(),
    };
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

    async function postJson(path, payload) {
      const response = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || response.statusText);
      }
      return response.json();
    }

    function cacheKey(folder, limit = state.limit) {
      return `${limit}::${folder}`;
    }

    function bootstrapUrl(folder) {
      return `/api/bootstrap?folder=${encodeURIComponent(folder)}&limit=${encodeURIComponent(state.limit)}`;
    }

    function applyBootstrap(data) {
      state.mailbox = data.mailbox;
      state.folders = data.folders;
      state.folder = data.selected_folder;
      state.folderCount = Number(data.folder_count || 0);
      state.limit = data.message_limit || state.limit;
      state.messages = data.messages;
      state.selectedUid = state.messages[0]?.uid || null;
    }

    async function loadBootstrap(folder = "INBOX", options = {}) {
      const data = await loadFolderData(folder, options);
      applyBootstrap(data);
      render();
      if (state.selectedUid) await openMessage(state.selectedUid);
    }

    async function loadFolderData(folder, options = {}) {
      const key = cacheKey(folder);
      if (!options.force && state.cache.has(key)) {
        return state.cache.get(key);
      }
      const data = await api(bootstrapUrl(folder));
      state.cache.set(key, data);
      return data;
    }

    async function loadFolder(folder, options = {}) {
      if (options.force) {
        state.cache.delete(cacheKey(folder));
      }
      const data = await loadFolderData(folder, options);
      applyBootstrap(data);
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
      $("messageLimit").value = state.limit;
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
      $("messageCount").textContent = messageCountText(messages.length);
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
          <div class="subject-line"><span class="subject"></span><span class="suggestion-badge"></span></div>
          <div class="preview"></div>
        `;
        row.querySelector(".sender").textContent = text(message.from, "(unknown)");
        row.querySelector(".date").textContent = formatDate(message.message_date);
        row.querySelector(".subject").textContent = text(message.subject, "(no subject)");
        const badge = row.querySelector(".suggestion-badge");
        const suggestionCount = Number(message.proposed_classifications || 0);
        if (suggestionCount > 0) {
          badge.textContent = `${suggestionCount} suggested`;
        } else {
          badge.remove();
        }
        row.querySelector(".preview").textContent = text(message.body_preview, formatSize(message.size));
        row.addEventListener("click", () => openMessage(message.uid).catch(showError));
        list.appendChild(row);
      });
    }

    function messageCountText(filteredCount) {
      const loaded = state.messages.length;
      const total = state.folderCount || loaded;
      if (state.query.trim()) {
        return `${filteredCount} / ${loaded} loaded`;
      }
      if (loaded < total) {
        return `${loaded} / ${total}`;
      }
      return String(total);
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
          <div class="review-panel" data-panel="classifications" hidden>
            <h2>MILLIE Suggestions</h2>
            <div data-list="classifications"></div>
          </div>
          <div class="review-panel" data-panel="unsubscribe" hidden>
            <h2>Unsubscribe Candidates</h2>
            <div data-list="unsubscribe"></div>
          </div>
          <div class="review-panel" data-panel="retention" hidden>
            <h2>Retention</h2>
            <div data-list="retention"></div>
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
      renderClassificationPanel(message.classifications || []);
      renderUnsubscribePanel(message.unsubscribe_candidates || []);
      renderRetentionPanel(message.retention_status || []);
    }

    function renderClassificationPanel(classifications) {
      const panel = $("reader").querySelector('[data-panel="classifications"]');
      const list = $("reader").querySelector('[data-list="classifications"]');
      if (!panel || !list || !classifications.length) return;
      panel.hidden = false;
      list.innerHTML = "";
      classifications.forEach((item) => {
        const card = document.createElement("div");
        card.className = "suggestion-card";
        const target = item.target_folder_path || (item.target_tags || []).join(", ");
        card.innerHTML = `
          <div class="suggestion-top">
            <div>
              <div class="suggestion-title"></div>
              <div class="suggestion-meta"></div>
            </div>
            <span class="suggestion-badge"></span>
          </div>
          <div class="suggestion-meta" data-field="reason"></div>
          <div class="suggestion-actions"></div>
        `;
        card.querySelector(".suggestion-title").textContent = `${item.kind}:${item.value} -> ${target}`;
        card.querySelector(".suggestion-meta").textContent = `confidence ${Number(item.confidence || 0).toFixed(2)}`;
        card.querySelector(".suggestion-badge").textContent = item.status;
        card.querySelector('[data-field="reason"]').textContent = item.reason || "";
        const actions = card.querySelector(".suggestion-actions");
        if (item.status === "proposed") {
          [
            ["approve", "Approve"],
            ["reject", "Reject"],
            ["always", "Always"],
            ["never", "Never"],
          ].forEach(([action, label]) => {
            const button = document.createElement("button");
            button.type = "button";
            button.textContent = label;
            button.addEventListener("click", () => applyClassificationAction(item.id, action).catch(showError));
            actions.appendChild(button);
          });
        }
        list.appendChild(card);
      });
    }

    function renderUnsubscribePanel(candidates) {
      const panel = $("reader").querySelector('[data-panel="unsubscribe"]');
      const list = $("reader").querySelector('[data-list="unsubscribe"]');
      if (!panel || !list || !candidates.length) return;
      panel.hidden = false;
      list.innerHTML = "";
      candidates.forEach((item) => {
        const card = document.createElement("div");
        card.className = "suggestion-card";
        const target = item.unsubscribe_mailto || item.unsubscribe_url || item.candidate_type;
        card.innerHTML = `
          <div class="suggestion-top">
            <div>
              <div class="suggestion-title"></div>
              <div class="suggestion-meta"></div>
            </div>
            <span class="suggestion-badge"></span>
          </div>
          <div class="suggestion-actions"></div>
        `;
        card.querySelector(".suggestion-title").textContent = target;
        card.querySelector(".suggestion-meta").textContent = `${item.candidate_type} · confidence ${Number(item.confidence || 0).toFixed(2)}`;
        card.querySelector(".suggestion-badge").textContent = item.status;
        const actions = card.querySelector(".suggestion-actions");
        if (["detected", "review_required"].includes(item.status)) {
          [
            ["approve", "Approve"],
            ["reject", "Ignore"],
          ].forEach(([action, label]) => {
            const button = document.createElement("button");
            button.type = "button";
            button.textContent = label;
            button.addEventListener("click", () => applyUnsubscribeAction(item.id, action).catch(showError));
            actions.appendChild(button);
          });
        }
        list.appendChild(card);
      });
    }

    function renderRetentionPanel(policies) {
      const panel = $("reader").querySelector('[data-panel="retention"]');
      const list = $("reader").querySelector('[data-list="retention"]');
      if (!panel || !list || !policies.length) return;
      panel.hidden = false;
      list.innerHTML = "";
      policies.forEach((item) => {
        const card = document.createElement("div");
        card.className = "suggestion-card";
        const due = item.eligible_at ? `eligible ${formatDate(item.eligible_at)}` : "no eligibility date";
        const review = item.requires_review ? "review required" : "review not required";
        card.innerHTML = `
          <div class="suggestion-top">
            <div>
              <div class="suggestion-title"></div>
              <div class="suggestion-meta"></div>
            </div>
            <span class="suggestion-badge"></span>
          </div>
          <div class="suggestion-meta" data-field="timing"></div>
        `;
        card.querySelector(".suggestion-title").textContent = `${item.policy_name} · ${item.target_value}`;
        card.querySelector(".suggestion-meta").textContent =
          `${item.hold_duration_text} hold · ${item.action} · ${review}`;
        card.querySelector(".suggestion-badge").textContent = item.is_eligible ? "eligible" : item.status;
        card.querySelector('[data-field="timing"]').textContent =
          `${due} · copied ${formatDate(item.copied_at)}`;
        list.appendChild(card);
      });
    }

    async function applyClassificationAction(classificationId, action) {
      await postJson("/api/classifications/action", { classification_id: classificationId, action });
      state.cache.delete(cacheKey(state.folder));
      if (state.selectedUid) await openMessage(state.selectedUid);
    }

    async function applyUnsubscribeAction(candidateId, action) {
      await postJson("/api/unsubscribe/action", { candidate_id: candidateId, action });
      if (state.selectedUid) await openMessage(state.selectedUid);
    }

    async function applyRetentionAction(policyId, mailboxMessageId, action) {
      await postJson("/api/retention/action", {
        policy_id: policyId,
        mailbox_message_id: mailboxMessageId,
        action,
      });
    }

    async function openReview() {
      const data = await api("/api/review?limit=50");
      renderReviewList(data.suggestions || [], data.retention || []);
    }

    async function openUnsubscribeQueue() {
      const data = await api("/api/unsubscribe?limit=100");
      renderUnsubscribeQueue(data.candidates || []);
    }

    async function openPolicies() {
      const data = await api("/api/retention/policies");
      renderPolicyList(data.policies || []);
    }

    function durationParts(seconds) {
      const normalized = Math.max(1, Number(seconds || 86400));
      if (normalized % 604800 === 0) return { value: normalized / 604800, unit: "weeks" };
      if (normalized % 86400 === 0) return { value: normalized / 86400, unit: "days" };
      if (normalized % 3600 === 0) return { value: normalized / 3600, unit: "hours" };
      return { value: normalized, unit: "seconds" };
    }

    function durationSeconds(value, unit) {
      const amount = Math.max(1, Number(value || 1));
      const multipliers = { seconds: 1, hours: 3600, days: 86400, weeks: 604800 };
      return Math.round(amount * (multipliers[unit] || 86400));
    }

    function renderReviewList(suggestions, retentionItems) {
      $("reader").innerHTML = `
        <div class="review-list">
          <h1>Review Queue</h1>
          <div class="suggestion-meta"></div>
          <div data-list="review-classifications"></div>
          <div data-list="review-retention"></div>
        </div>
      `;
      $("reader").querySelector(".suggestion-meta").textContent =
        `${suggestions.length} proposed classifications · ${retentionItems.length} retention items`;
      const list = $("reader").querySelector('[data-list="review-classifications"]');
      const retentionList = $("reader").querySelector('[data-list="review-retention"]');
      suggestions.forEach((item) => {
        const card = document.createElement("div");
        card.className = "suggestion-card";
        const target = item.target_folder_path || (item.target_tags || []).join(", ");
        card.innerHTML = `
          <div class="suggestion-top">
            <div>
              <div class="suggestion-title"></div>
              <div class="suggestion-meta"></div>
            </div>
            <span class="suggestion-badge"></span>
          </div>
          <div class="suggestion-meta" data-field="reason"></div>
          <div class="suggestion-actions"></div>
        `;
        card.querySelector(".suggestion-title").textContent = `${item.subject} · ${item.kind}:${item.value} -> ${target}`;
        card.querySelector(".suggestion-meta").textContent = `${item.from || "(unknown)"} · ${formatDate(item.message_date)}`;
        card.querySelector(".suggestion-badge").textContent = Number(item.confidence || 0).toFixed(2);
        card.querySelector('[data-field="reason"]').textContent = item.reason || "";
        const actions = card.querySelector(".suggestion-actions");
        [
          ["approve", "Approve"],
          ["reject", "Reject"],
          ["always", "Always"],
          ["never", "Never"],
        ].forEach(([action, label]) => {
          const button = document.createElement("button");
          button.type = "button";
          button.textContent = label;
          button.addEventListener("click", async () => {
            await postJson("/api/classifications/action", { classification_id: item.classification_id, action });
            await openReview();
          });
          actions.appendChild(button);
        });
        if (item.folder_path && item.uid) {
          const openButton = document.createElement("button");
          openButton.type = "button";
          openButton.textContent = "Open";
          openButton.addEventListener("click", async () => {
            await loadFolder(item.folder_path);
            await openMessage(item.uid);
          });
          actions.appendChild(openButton);
        }
        list.appendChild(card);
      });
      retentionItems.forEach((item) => {
        const card = document.createElement("div");
        card.className = "suggestion-card";
        const review = item.requires_review ? "review required" : "review optional";
        card.innerHTML = `
          <div class="suggestion-top">
            <div>
              <div class="suggestion-title"></div>
              <div class="suggestion-meta"></div>
            </div>
            <span class="suggestion-badge"></span>
          </div>
          <div class="suggestion-meta" data-field="retention"></div>
          <div class="suggestion-actions"></div>
        `;
        card.querySelector(".suggestion-title").textContent = `${item.subject} · ${item.policy_name}`;
        card.querySelector(".suggestion-meta").textContent =
          `${item.from || "(unknown)"} · ${formatDate(item.message_date)}`;
        card.querySelector(".suggestion-badge").textContent = "retention";
        card.querySelector('[data-field="retention"]').textContent =
          `${item.folder_path} · ${item.hold_duration_text} hold · ${item.policy_action} · ${review} · eligible ${formatDate(item.eligible_at)}`;
        const actions = card.querySelector(".suggestion-actions");
        [
          ["acknowledge", "Acknowledge"],
          ["defer", "Snooze 7d"],
        ].forEach(([action, label]) => {
          const button = document.createElement("button");
          button.type = "button";
          button.textContent = label;
          button.addEventListener("click", async () => {
            await applyRetentionAction(item.policy_id, item.mailbox_message_id, action);
            await openReview();
          });
          actions.appendChild(button);
        });
        if (item.folder_path && item.uid) {
          const openButton = document.createElement("button");
          openButton.type = "button";
          openButton.textContent = "Open";
          openButton.addEventListener("click", async () => {
            await loadFolder(item.folder_path);
            await openMessage(item.uid);
          });
          actions.appendChild(openButton);
        }
        retentionList.appendChild(card);
      });
      if (!suggestions.length && !retentionItems.length) {
        list.innerHTML = `<div class="empty">No items waiting for review</div>`;
      }
    }

    function renderPolicyList(policies) {
      $("reader").innerHTML = `
        <div class="review-list">
          <h1>Retention Policies</h1>
          <div class="suggestion-meta"></div>
          <div data-list="retention-policies"></div>
        </div>
      `;
      $("reader").querySelector(".suggestion-meta").textContent =
        `${policies.length} policies · provider mail is not changed by these controls`;
      const list = $("reader").querySelector('[data-list="retention-policies"]');
      if (!policies.length) {
        list.innerHTML = `<div class="empty">No retention policies</div>`;
        return;
      }
      policies.forEach((item) => {
        const card = document.createElement("div");
        card.className = "suggestion-card";
        const parts = durationParts(item.hold_duration_seconds);
        card.innerHTML = `
          <div class="suggestion-top">
            <div>
              <div class="suggestion-title"></div>
              <div class="suggestion-meta" data-field="policy-meta"></div>
            </div>
            <span class="suggestion-badge"></span>
          </div>
          <div class="policy-edit">
            <label>Name
              <input data-field="policy-name" type="text">
            </label>
            <label>Hold
              <span class="duration-group">
                <input data-field="policy-duration" type="number" min="1" step="1">
                <select data-field="policy-duration-unit">
                  <option value="hours">hours</option>
                  <option value="days">days</option>
                  <option value="weeks">weeks</option>
                  <option value="seconds">seconds</option>
                </select>
              </span>
            </label>
            <label>Action
              <select data-field="policy-action">
                <option value="no_action">no action</option>
                <option value="hide_from_default_views">hide from defaults</option>
                <option value="expire_internal_copy">expire internal copy</option>
                <option value="delete_internal_copy">delete internal copy</option>
              </select>
            </label>
            <label>Status
              <select data-field="policy-status">
                <option value="proposed">proposed</option>
                <option value="active">active</option>
                <option value="disabled">disabled</option>
                <option value="retired">retired</option>
              </select>
            </label>
            <label class="check-label">
              <input data-field="policy-review" type="checkbox">
              review
            </label>
          </div>
          <div class="suggestion-actions"></div>
        `;
        card.querySelector(".suggestion-title").textContent = item.policy_name || item.id;
        card.querySelector('[data-field="policy-meta"]').textContent =
          `${item.target_kind}:${item.target_value} · ${item.hold_duration_text} · ${item.policy_action}`;
        card.querySelector(".suggestion-badge").textContent = item.status;
        card.querySelector('[data-field="policy-name"]').value = item.policy_name || "";
        card.querySelector('[data-field="policy-duration"]').value = parts.value;
        card.querySelector('[data-field="policy-duration-unit"]').value = parts.unit;
        card.querySelector('[data-field="policy-action"]').value = item.policy_action || "no_action";
        card.querySelector('[data-field="policy-status"]').value = item.status || "proposed";
        card.querySelector('[data-field="policy-review"]').checked = Boolean(item.requires_review);
        const actions = card.querySelector(".suggestion-actions");
        const saveButton = document.createElement("button");
        saveButton.type = "button";
        saveButton.textContent = "Save";
        saveButton.addEventListener("click", async () => {
          await savePolicy(card, item.id);
          await openPolicies();
        });
        actions.appendChild(saveButton);
        if (item.status !== "active") {
          const activateButton = document.createElement("button");
          activateButton.type = "button";
          activateButton.textContent = "Activate";
          activateButton.addEventListener("click", async () => {
            await applyPolicyAction(item.id, "activate");
            await openPolicies();
          });
          actions.appendChild(activateButton);
        }
        if (item.status !== "disabled") {
          const disableButton = document.createElement("button");
          disableButton.type = "button";
          disableButton.textContent = "Disable";
          disableButton.addEventListener("click", async () => {
            await applyPolicyAction(item.id, "disable");
            await openPolicies();
          });
          actions.appendChild(disableButton);
        }
        list.appendChild(card);
      });
    }

    function renderUnsubscribeQueue(candidates) {
      $("reader").innerHTML = `
        <div class="review-list">
          <h1>Unsubscribe Queue</h1>
          <div class="suggestion-meta"></div>
          <div data-list="unsubscribe-queue"></div>
        </div>
      `;
      $("reader").querySelector(".suggestion-meta").textContent = `${candidates.length} candidates`;
      const list = $("reader").querySelector('[data-list="unsubscribe-queue"]');
      if (!candidates.length) {
        list.innerHTML = `<div class="empty">No unsubscribe candidates</div>`;
        return;
      }
      candidates.forEach((item) => {
        const card = document.createElement("div");
        card.className = "suggestion-card";
        const target = item.unsubscribe_mailto || item.unsubscribe_url || item.candidate_type;
        const browser = item.requires_browser ? "browser/manual assist" : "manual assist";
        card.innerHTML = `
          <div class="suggestion-top">
            <div>
              <div class="suggestion-title"></div>
              <div class="suggestion-meta"></div>
            </div>
            <span class="suggestion-badge"></span>
          </div>
          <div class="suggestion-meta" data-field="target"></div>
          <div class="suggestion-actions"></div>
        `;
        card.querySelector(".suggestion-title").textContent = `${item.subject} · ${item.candidate_type}`;
        card.querySelector(".suggestion-meta").textContent =
          `${item.from || "(unknown)"} · ${formatDate(item.message_date)} · confidence ${Number(item.confidence || 0).toFixed(2)}`;
        card.querySelector(".suggestion-badge").textContent = item.status;
        card.querySelector('[data-field="target"]').textContent = `${browser} · ${target}`;
        const actions = card.querySelector(".suggestion-actions");
        if (["detected", "review_required"].includes(item.status)) {
          [
            ["approve", "Approve"],
            ["reject", "Ignore"],
          ].forEach(([action, label]) => {
            const button = document.createElement("button");
            button.type = "button";
            button.textContent = label;
            button.addEventListener("click", async () => {
              await applyUnsubscribeAction(item.id, action);
              await openUnsubscribeQueue();
            });
            actions.appendChild(button);
          });
        }
        if (item.folder_path && item.uid) {
          const openButton = document.createElement("button");
          openButton.type = "button";
          openButton.textContent = "Open";
          openButton.addEventListener("click", async () => {
            await loadFolder(item.folder_path);
            await openMessage(item.uid);
          });
          actions.appendChild(openButton);
        }
        list.appendChild(card);
      });
    }

    async function applyPolicyAction(policyId, action) {
      await postJson("/api/retention/policies/action", { policy_id: policyId, action });
    }

    async function savePolicy(card, policyId) {
      const durationValue = card.querySelector('[data-field="policy-duration"]').value;
      const durationUnit = card.querySelector('[data-field="policy-duration-unit"]').value;
      await postJson("/api/retention/policies/action", {
        policy_id: policyId,
        action: "update",
        policy_name: card.querySelector('[data-field="policy-name"]').value,
        hold_duration_seconds: durationSeconds(durationValue, durationUnit),
        policy_action: card.querySelector('[data-field="policy-action"]').value,
        status: card.querySelector('[data-field="policy-status"]').value,
        requires_review: card.querySelector('[data-field="policy-review"]').checked,
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
    $("messageLimit").addEventListener("change", (event) => {
      state.limit = validMessageLimits.has(event.target.value) ? event.target.value : "50";
      localStorage.setItem(limitStorageKey, state.limit);
      loadFolder(state.folder).catch(showError);
    });
    $("refreshFolder").addEventListener("click", () => {
      loadFolder(state.folder, { force: true }).catch(showError);
    });
    $("reviewButton").addEventListener("click", () => {
      openReview().catch(showError);
    });
    $("unsubscribeButton").addEventListener("click", () => {
      openUnsubscribeQueue().catch(showError);
    });
    $("policiesButton").addEventListener("click", () => {
      openPolicies().catch(showError);
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
