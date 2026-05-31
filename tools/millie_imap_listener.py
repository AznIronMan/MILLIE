#!/usr/bin/env python3
"""Minimal dev IMAP listener backed by MILLIE's Postgres mailbox facade."""

from __future__ import annotations

import argparse
import base64
import email
import os
import re
import shlex
import socket
import socketserver
import ssl
import subprocess
import sys
import threading
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from millie.service.imap_protocol import body_literal_name, imap_capabilities, summarize_fetch_items
from millie.settings_loader import load_local_settings
from millie.storage.postgres_store import PostgresMailStore


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PLAIN_PORT = 22143
DEFAULT_TLS_PORT = 22993
CERT_DIR = PROJECT_ROOT / ".private" / "local" / "imap_tls"
DEFAULT_PID_FILE = PROJECT_ROOT / ".private" / "local" / "millie_imap_listener.pid"
DEFAULT_LOG_FILE = PROJECT_ROOT / ".private" / "local" / "millie_imap_listener.log"


class MillieImapHandler(socketserver.StreamRequestHandler):
    server: "MillieImapServer"

    def setup(self) -> None:
        super().setup()
        self.store = PostgresMailStore.connect(self.server.settings)
        self.identity_id: str | None = None
        self.mailbox_id: str | None = None
        self.selected_folder = "INBOX"
        self.client = f"{self.client_address[0]}:{self.client_address[1]}"
        self.log_event("connect", mode="tls" if self.server.implicit_tls else "plain")

    def finish(self) -> None:
        try:
            self.log_event("disconnect")
            self.store.close()
        finally:
            super().finish()

    def handle(self) -> None:
        self.send_line("* OK MILLIE dev IMAP ready")
        while True:
            line = self.rfile.readline(1024 * 1024)
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip("\r\n")
            if not text:
                continue
            if not self.handle_command(text):
                break

    def handle_command(self, text: str) -> bool:
        tag, command, rest = split_command(text)
        upper = command.upper()
        if upper == "UID":
            subcommand, subrest = split_subcommand(rest)
            self.log_event("command", command=f"UID {subcommand.upper()}")
            return self.handle_uid_command(tag, subcommand.upper(), subrest)
        self.log_event("command", command=upper)
        if upper == "CAPABILITY":
            self.send_capability()
            self.send_ok(tag, "CAPABILITY completed")
        elif upper == "NOOP":
            self.send_ok(tag, "NOOP completed")
        elif upper == "ID":
            self.send_line('* ID ("name" "MILLIE" "version" "1.0.0")')
            self.send_ok(tag, "ID completed")
        elif upper == "LOGOUT":
            self.send_line("* BYE MILLIE dev IMAP closing connection")
            self.send_ok(tag, "LOGOUT completed")
            return False
        elif upper == "STARTTLS":
            self.send_no(tag, "STARTTLS disabled on this dev plaintext port")
        elif upper == "LOGIN":
            self.login(tag, rest)
        elif upper == "AUTHENTICATE":
            self.authenticate(tag, rest)
        elif upper == "NAMESPACE":
            self.require_auth(tag) and self.namespace(tag)
        elif upper in {"LIST", "LSUB"}:
            self.require_auth(tag) and self.list_folders(tag, upper, rest)
        elif upper in {"SELECT", "EXAMINE"}:
            self.require_auth(tag) and self.select_folder(tag, rest, readonly=(upper == "EXAMINE"))
        elif upper == "STATUS":
            self.require_auth(tag) and self.status(tag, rest)
        elif upper == "SEARCH":
            self.require_selected(tag) and self.search(tag, rest, uid_mode=False)
        elif upper == "FETCH":
            self.require_selected(tag) and self.fetch(tag, rest, uid_mode=False)
        elif upper in {"CLOSE", "EXPUNGE"}:
            self.require_selected(tag) and self.send_ok(tag, f"{upper} completed")
        elif upper in {"SUBSCRIBE", "UNSUBSCRIBE", "STORE"}:
            self.require_auth(tag) and self.send_ok(tag, f"{upper} completed")
        else:
            self.log_event("unsupported", command=command)
            self.send_bad(tag, f"Unsupported command: {command}")
        return True

    def handle_uid_command(self, tag: str, subcommand: str, rest: str) -> bool:
        if subcommand == "SEARCH":
            self.require_selected(tag) and self.search(tag, rest, uid_mode=True)
        elif subcommand == "FETCH":
            self.require_selected(tag) and self.fetch(tag, rest, uid_mode=True)
        elif subcommand == "STORE":
            self.require_selected(tag) and self.send_ok(tag, "UID STORE completed")
        else:
            self.send_bad(tag, f"Unsupported UID command: {subcommand}")
        return True

    def send_capability(self) -> None:
        self.send_line("* CAPABILITY " + " ".join(imap_capabilities()))

    def login(self, tag: str, rest: str) -> None:
        try:
            username, password = shlex.split(rest)[:2]
        except ValueError:
            self.log_event("login_parse_failed")
            self.send_bad(tag, "LOGIN requires username and password")
            return
        self.complete_login(tag, username, password)

    def authenticate(self, tag: str, rest: str) -> None:
        parts = rest.split()
        mechanism = parts[0].upper() if parts else ""
        if mechanism != "PLAIN":
            self.log_event("auth_unsupported", mechanism=mechanism or "unknown")
            self.send_no(tag, "Only AUTHENTICATE PLAIN is supported")
            return
        if len(parts) > 1:
            payload = parts[1]
        else:
            self.wfile.write(b"+ \r\n")
            self.wfile.flush()
            payload = self.rfile.readline(1024 * 64).decode("ascii", errors="replace").strip()
        try:
            decoded = base64.b64decode(payload).decode("utf-8", errors="replace")
            authzid, username, password = decoded.split("\x00", 2)
        except (ValueError, base64.binascii.Error):
            self.log_event("auth_parse_failed", mechanism=mechanism)
            self.send_bad(tag, "Invalid AUTHENTICATE PLAIN payload")
            return
        self.complete_login(tag, username or authzid, password)

    def complete_login(self, tag: str, username: str, password: str) -> None:
        identity_id = self.store.authenticate(username, password)
        if not identity_id:
            self.log_event("auth_failed", username=username)
            self.send_no(tag, "Authentication failed")
            return
        mailbox_id = self.store.primary_mailbox_for_identity(identity_id)
        if not mailbox_id:
            self.log_event("auth_no_mailbox", username=username)
            self.send_no(tag, "No primary mailbox")
            return
        self.identity_id = identity_id
        self.mailbox_id = mailbox_id
        self.log_event("auth_ok", username=username)
        self.send_ok(tag, "LOGIN completed")

    def namespace(self, tag: str) -> None:
        self.send_line('* NAMESPACE (("" "/")) NIL NIL')
        self.send_ok(tag, "NAMESPACE completed")

    def list_folders(self, tag: str, command: str, rest: str) -> None:
        count = 0
        for folder in self.store.list_folders(self.mailbox_id):
            count += 1
            attributes = "\\HasNoChildren" if folder["selectable"] else "\\Noselect"
            self.send_line(
                f'* {command} ({attributes}) "/" {imap_quote(str(folder["path"]))}'
            )
        self.log_event("list_folders", count=count)
        self.send_ok(tag, f"{command} completed")

    def select_folder(self, tag: str, rest: str, *, readonly: bool) -> None:
        folder = normalize_folder_name(rest)
        messages = self.messages(folder)
        uid_next = max([int(message["uid"]) for message in messages], default=0) + 1
        self.selected_folder = folder
        self.log_event("select", folder=folder, messages=len(messages), readonly=readonly)
        self.send_line("* FLAGS (\\Answered \\Flagged \\Deleted \\Seen \\Draft)")
        self.send_line("* OK [PERMANENTFLAGS (\\Answered \\Flagged \\Deleted \\Seen \\Draft)]")
        self.send_line(f"* {len(messages)} EXISTS")
        self.send_line("* 0 RECENT")
        self.send_line("* OK [UIDVALIDITY 1] UIDs valid")
        self.send_line(f"* OK [UIDNEXT {uid_next}] Predicted next UID")
        mode = "READ-ONLY" if readonly else "READ-WRITE"
        self.send_ok(tag, f"[{mode}] SELECT completed")

    def status(self, tag: str, rest: str) -> None:
        parts = shlex.split(rest)
        folder = normalize_folder_name(parts[0] if parts else "INBOX")
        messages = self.messages(folder)
        uid_next = max([int(message["uid"]) for message in messages], default=0) + 1
        unseen = sum(1 for message in messages if "\\Seen" not in message["flags"])
        self.log_event("status", folder=folder, messages=len(messages), unseen=unseen)
        self.send_line(
            f'* STATUS {imap_quote(folder)} '
            f"(MESSAGES {len(messages)} RECENT 0 UIDNEXT {uid_next} UIDVALIDITY 1 UNSEEN {unseen})"
        )
        self.send_ok(tag, "STATUS completed")

    def search(self, tag: str, rest: str, *, uid_mode: bool) -> None:
        messages = self.messages(self.selected_folder)
        if uid_mode:
            values = [str(message["uid"]) for message in messages]
        else:
            values = [str(index) for index, _ in enumerate(messages, start=1)]
        self.log_event("search", folder=self.selected_folder, uid_mode=uid_mode, count=len(values))
        self.send_line("* SEARCH " + " ".join(values))
        self.send_ok(tag, "SEARCH completed")

    def fetch(self, tag: str, rest: str, *, uid_mode: bool) -> None:
        set_spec, item_text = split_fetch(rest)
        messages = self.messages(self.selected_folder)
        selected = select_messages(messages, set_spec, uid_mode=uid_mode)
        self.log_event(
            "fetch",
            folder=self.selected_folder,
            uid_mode=uid_mode,
            set=set_spec,
            count=len(selected),
            items=summarize_fetch_items(item_text),
            request=item_text,
        )
        for sequence, message in selected:
            self.fetch_one(sequence, message, item_text, uid_mode=uid_mode)
        self.send_ok(tag, "FETCH completed")

    def fetch_one(self, sequence: int, message: dict[str, Any], item_text: str, *, uid_mode: bool) -> None:
        upper = item_text.upper()
        raw = self.store.get_raw_mime_by_uid(
            mailbox_id=self.mailbox_id,
            folder_path=self.selected_folder,
            uid=int(message["uid"]),
        ) or b""
        attrs: list[str] = []
        if uid_mode or "UID" in upper:
            attrs.append(f"UID {message['uid']}")
        if "FLAGS" in upper:
            attrs.append(f"FLAGS ({' '.join(message['flags'])})")
        if "INTERNALDATE" in upper:
            attrs.append(f'INTERNALDATE "{imap_date(message["internal_date"])}"')
        if "RFC822.SIZE" in upper:
            attrs.append(f"RFC822.SIZE {len(raw)}")
        if "ENVELOPE" in upper:
            attrs.append("ENVELOPE " + envelope(raw))
        if "BODYSTRUCTURE" in upper:
            attrs.append("BODYSTRUCTURE " + bodystructure(raw))

        literal_name, literal = fetch_literal(item_text, raw)
        if literal_name:
            prefix = f"* {sequence} FETCH (" + " ".join(attrs + [f"{literal_name} {{{len(literal)}}}"])
            self.wfile.write(prefix.encode("utf-8") + b"\r\n")
            self.wfile.write(literal)
            self.wfile.write(b")\r\n")
            self.wfile.flush()
            return

        if "RFC822" in upper and "RFC822.SIZE" not in upper:
            prefix = f"* {sequence} FETCH (" + " ".join(attrs + [f"RFC822 {{{len(raw)}}}"])
            self.wfile.write(prefix.encode("utf-8") + b"\r\n")
            self.wfile.write(raw)
            self.wfile.write(b")\r\n")
            self.wfile.flush()
            return

        self.send_line(f"* {sequence} FETCH ({' '.join(attrs)})")

    def messages(self, folder: str) -> list[dict[str, Any]]:
        return self.store.list_imap_messages(self.mailbox_id, folder)

    def require_auth(self, tag: str) -> bool:
        if self.mailbox_id:
            return True
        self.send_no(tag, "Authenticate first")
        return False

    def require_selected(self, tag: str) -> bool:
        if self.require_auth(tag):
            return True
        return False

    def start_tls(self) -> None:
        context = self.server.ssl_context
        self.request = context.wrap_socket(self.request, server_side=True)
        self.rfile = self.request.makefile("rb")
        self.wfile = self.request.makefile("wb")

    def send_line(self, value: str) -> None:
        self.wfile.write(value.encode("utf-8") + b"\r\n")
        self.wfile.flush()

    def send_ok(self, tag: str, message: str) -> None:
        self.send_line(f"{tag} OK {message}")

    def send_no(self, tag: str, message: str) -> None:
        self.send_line(f"{tag} NO {message}")

    def send_bad(self, tag: str, message: str) -> None:
        self.send_line(f"{tag} BAD {message}")

    def log_event(self, event: str, **fields: object) -> None:
        values = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "client": getattr(self, "client", "unknown"),
            "event": event,
        }
        values.update(fields)
        print("IMAP " + " ".join(f"{key}={log_value(value)}" for key, value in values.items()), flush=True)


class MillieImapServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class,
        *,
        settings: dict[str, str],
        implicit_tls: bool,
        ssl_context: ssl.SSLContext,
    ) -> None:
        self.settings = settings
        self.implicit_tls = implicit_tls
        self.ssl_context = ssl_context
        super().__init__(server_address, handler_class)

    def get_request(self):
        request, client_address = super().get_request()
        if self.implicit_tls:
            request = self.ssl_context.wrap_socket(request, server_side=True)
        return request, client_address


def split_command(text: str) -> tuple[str, str, str]:
    parts = text.split(" ", 2)
    if len(parts) == 1:
        return parts[0], "", ""
    if len(parts) == 2:
        return parts[0], parts[1], ""
    return parts[0], parts[1], parts[2]


def split_subcommand(text: str) -> tuple[str, str]:
    parts = text.split(" ", 1)
    return (parts[0], parts[1] if len(parts) > 1 else "") if parts else ("", "")


def split_fetch(rest: str) -> tuple[str, str]:
    parts = rest.split(" ", 1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


def log_value(value: object) -> str:
    text = str(value).replace("\n", "\\n").replace("\r", "\\r").replace(" ", "_")
    return text[:160]


def normalize_folder_name(value: str) -> str:
    text = value.strip()
    if " " in text and not text.startswith('"'):
        text = text.split(" ", 1)[0]
    try:
        parts = shlex.split(text)
        if parts:
            text = parts[0]
    except ValueError:
        text = text.strip('"')
    if text.upper() == "INBOX":
        return "INBOX"
    return text


def select_messages(
    messages: list[dict[str, Any]],
    set_spec: str,
    *,
    uid_mode: bool,
) -> list[tuple[int, dict[str, Any]]]:
    selected: list[tuple[int, dict[str, Any]]] = []
    if set_spec.strip() == "*":
        wanted = {len(messages) if not uid_mode else int(messages[-1]["uid"])} if messages else set()
    else:
        wanted = expand_set(set_spec, max_value=(int(messages[-1]["uid"]) if uid_mode and messages else len(messages)))
    for sequence, message in enumerate(messages, start=1):
        value = int(message["uid"]) if uid_mode else sequence
        if value in wanted:
            selected.append((sequence, message))
    return selected


def expand_set(value: str, *, max_value: int) -> set[int]:
    selected: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            left, right = part.split(":", 1)
            start = max_value if left == "*" else int(left)
            end = max_value if right == "*" else int(right)
            if start > end:
                start, end = end, start
            selected.update(range(start, end + 1))
        else:
            selected.add(max_value if part == "*" else int(part))
    return selected


def fetch_literal(item_text: str, raw: bytes) -> tuple[str | None, bytes]:
    upper = item_text.upper()
    if "BODY.PEEK[HEADER.FIELDS" in upper or "BODY[HEADER.FIELDS" in upper:
        return body_literal_name(item_text), selected_headers(raw, item_text)
    if "BODY.PEEK[HEADER]" in upper or "BODY[HEADER]" in upper:
        return body_literal_name(item_text), header_bytes(raw)
    if "BODY.PEEK[TEXT]" in upper or "BODY[TEXT]" in upper:
        return body_literal_name(item_text), text_bytes(raw)
    if "BODY.PEEK[]" in upper or "BODY[]" in upper:
        return body_literal_name(item_text), raw
    return None, b""


def selected_headers(raw: bytes, item_text: str) -> bytes:
    match = re.search(r"HEADER\.FIELDS\s*\(([^)]*)\)", item_text, flags=re.I)
    names = [name.lower() for name in re.split(r"\s+", match.group(1).strip())] if match else []
    message = BytesParser(policy=policy.default).parsebytes(raw)
    lines: list[str] = []
    for name in names:
        for value in message.get_all(name, []):
            lines.append(f"{name}: {value}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8")


def header_bytes(raw: bytes) -> bytes:
    marker = b"\r\n\r\n"
    if marker in raw:
        return raw.split(marker, 1)[0] + marker
    marker = b"\n\n"
    if marker in raw:
        return raw.split(marker, 1)[0].replace(b"\n", b"\r\n") + b"\r\n\r\n"
    return b"\r\n\r\n"


def text_bytes(raw: bytes) -> bytes:
    marker = b"\r\n\r\n"
    if marker in raw:
        return raw.split(marker, 1)[1]
    marker = b"\n\n"
    if marker in raw:
        return raw.split(marker, 1)[1]
    return b""


def envelope(raw: bytes) -> str:
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    date = nstring(msg.get("Date"))
    subject = nstring(msg.get("Subject"))
    from_addr = address_list(msg.get("From"))
    sender = address_list(msg.get("Sender") or msg.get("From"))
    reply_to = address_list(msg.get("Reply-To") or msg.get("From"))
    to_addr = address_list(msg.get("To"))
    cc_addr = address_list(msg.get("Cc"))
    bcc_addr = address_list(msg.get("Bcc"))
    in_reply_to = nstring(msg.get("In-Reply-To"))
    message_id = nstring(msg.get("Message-ID"))
    return f"({date} {subject} {from_addr} {sender} {reply_to} {to_addr} {cc_addr} {bcc_addr} {in_reply_to} {message_id})"


def address_list(value: str | None) -> str:
    if not value:
        return "NIL"
    parsed = email.utils.getaddresses([value])
    addresses = []
    for display, addr in parsed:
        local, _, domain = addr.partition("@")
        addresses.append(f"({nstring(display)} NIL {nstring(local)} {nstring(domain)})")
    return "(" + " ".join(addresses) + ")" if addresses else "NIL"


def bodystructure(raw: bytes) -> str:
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    content_type = msg.get_content_type()
    maintype, _, subtype = content_type.partition("/")
    return (
        f"({nstring(maintype.upper())} {nstring(subtype.upper())} "
        "NIL NIL NIL \"7BIT\" "
        f"{len(raw)} 1 NIL NIL NIL NIL)"
    )


def nstring(value: object) -> str:
    if value is None or value == "":
        return "NIL"
    return imap_quote(str(value))


def imap_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def imap_date(value: object) -> str:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%d-%b-%Y %H:%M:%S %z")


def ensure_certificates() -> tuple[Path, Path]:
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    cert = CERT_DIR / "cert.pem"
    key = CERT_DIR / "key.pem"
    if cert.exists() and key.exists():
        return cert, key
    command = [
        "openssl",
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-days",
        "365",
        "-keyout",
        str(key),
        "-out",
        str(cert),
        "-subj",
        "/CN=MILLIE",
        "-addext",
        "subjectAltName=DNS:localhost,IP:127.0.0.1",
    ]
    result = subprocess.run(command, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or "openssl failed to create IMAP TLS certificate")
    try:
        key.chmod(0o600)
        cert.chmod(0o644)
    except OSError:
        pass
    return cert, key


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start MILLIE's minimal dev IMAP listener.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--plain-port", type=int, default=DEFAULT_PLAIN_PORT)
    parser.add_argument("--tls-port", type=int, default=DEFAULT_TLS_PORT)
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
    config = load_local_settings()
    settings = config["settings"]
    cert, key = ensure_certificates()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=cert, keyfile=key)

    plain_server = MillieImapServer(
        (args.host, args.plain_port),
        MillieImapHandler,
        settings=settings,
        implicit_tls=False,
        ssl_context=context,
    )
    tls_server = MillieImapServer(
        (args.host, args.tls_port),
        MillieImapHandler,
        settings=settings,
        implicit_tls=True,
        ssl_context=context,
    )
    threads = [
        threading.Thread(target=plain_server.serve_forever, daemon=True),
        threading.Thread(target=tls_server.serve_forever, daemon=True),
    ]
    for thread in threads:
        thread.start()
    print(f"MILLIE IMAP plaintext listening on {args.host}:{args.plain_port}", flush=True)
    print(f"MILLIE IMAP TLS listening on {args.host}:{args.tls_port}", flush=True)
    try:
        for thread in threads:
            thread.join()
    except KeyboardInterrupt:
        plain_server.shutdown()
        tls_server.shutdown()


if __name__ == "__main__":
    parsed_args = build_parser().parse_args()
    if parsed_args.daemon:
        daemonize(pid_file=parsed_args.pid_file, log_file=parsed_args.log_file)
    serve(parsed_args)
