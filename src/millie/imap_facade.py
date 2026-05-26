from __future__ import annotations

import base64
import json
import re
import shlex
import socketserver
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import getaddresses
from typing import Any

from .database import MillieDatabase


MUTATING_COMMANDS = {"APPEND", "COPY", "CREATE", "DELETE", "EXPUNGE", "MOVE", "RENAME", "STORE", "SUBSCRIBE"}
SYSTEM_FLAGS = ("\\Seen", "\\Answered", "\\Flagged", "\\Deleted", "\\Draft")


@dataclass(frozen=True, slots=True)
class ImapMailbox:
    id: int
    path: str
    display_name: str
    message_count: int
    role: str | None = None


@dataclass(frozen=True, slots=True)
class ImapMessage:
    seq: int
    uid: int
    message_id: int
    size: int
    flags: tuple[str, ...]
    internal_date: str


@dataclass(frozen=True, slots=True)
class ImapFacadeAuth:
    username: str | None = None
    password: str | None = None
    allow_dev_login: bool = True

    def authenticate(self, username: str, password: str) -> bool:
        if self.allow_dev_login and self.username is None and self.password is None:
            return True
        return bool(self.username) and self.username == username and self.password == password


class ReadOnlyImapStore:
    def __init__(self, db: MillieDatabase):
        self.db = db

    def mailboxes(self) -> list[ImapMailbox]:
        return [
            ImapMailbox(
                id=int(row["id"]),
                path=str(row["path"]),
                display_name=str(row["display_name"] or row["path"]),
                message_count=int(row["message_count"] or 0),
                role=str(row["role"] or "") or None,
            )
            for row in self.db.list_mailboxes()
        ]

    def get_mailbox(self, name: str) -> ImapMailbox | None:
        clean_name = normalize_mailbox_name(name)
        for mailbox in self.mailboxes():
            if mailbox.path.lower() == clean_name.lower() or mailbox.display_name.lower() == clean_name.lower():
                return mailbox
        if clean_name.upper() == "INBOX":
            return next((mailbox for mailbox in self.mailboxes() if mailbox.path.lower().endswith("inbox")), None)
        return None

    def messages(self, mailbox_id: int) -> list[ImapMessage]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT msg.id, msg.size_bytes, msg.internal_date, msg.received_at,
                       msg.sent_at, msg.created_at, mm.flags_json
                FROM message_mailboxes mm
                JOIN messages msg ON msg.id = mm.message_id
                WHERE mm.mailbox_id = ?
                GROUP BY msg.id
                ORDER BY COALESCE(msg.internal_date, msg.received_at, msg.sent_at, msg.created_at), msg.id
                """,
                (mailbox_id,),
            ).fetchall()
        messages: list[ImapMessage] = []
        for index, row in enumerate(rows, start=1):
            messages.append(
                ImapMessage(
                    seq=index,
                    uid=int(row["id"]),
                    message_id=int(row["id"]),
                    size=int(row["size_bytes"] or 0),
                    flags=parse_flags(row["flags_json"]),
                    internal_date=format_internal_date(
                        row["internal_date"] or row["received_at"] or row["sent_at"] or row["created_at"]
                    ),
                )
            )
        return messages

    def raw_message(self, message_id: int) -> bytes:
        return self.db.get_raw_message(message_id) or b""


class MillieIMAPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        db: MillieDatabase,
        auth: ImapFacadeAuth | None = None,
    ):
        super().__init__(server_address, MillieIMAPHandler)
        self.store = ReadOnlyImapStore(db)
        self.auth = auth or ImapFacadeAuth()


class MillieIMAPHandler(socketserver.StreamRequestHandler):
    selected_mailbox: ImapMailbox | None = None
    authenticated = False

    @property
    def imap_server(self) -> MillieIMAPServer:
        return self.server  # type: ignore[return-value]

    def handle(self) -> None:
        self.write_line("* OK MILLIE read-only IMAP facade ready")
        while True:
            raw_line = self.rfile.readline(65536)
            if not raw_line:
                return
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            should_continue = self.dispatch(line)
            if not should_continue:
                return

    def dispatch(self, line: str) -> bool:
        tag, command, args = split_command(line)
        if not tag or not command:
            self.write_line("* BAD Malformed command")
            return True
        command = command.upper()
        try:
            if command == "CAPABILITY":
                self.write_line("* CAPABILITY IMAP4rev1 NAMESPACE UIDPLUS LITERAL+ AUTH=PLAIN ID ENABLE IDLE SPECIAL-USE")
                self.write_ok(tag, "CAPABILITY completed")
            elif command == "NOOP":
                self.write_ok(tag, "NOOP completed")
            elif command == "ID":
                self.write_line('* ID ("name" "MILLIE" "vendor" "MILLIE" "support-url" "https://github.com/AznIronMan/MILLIE")')
                self.write_ok(tag, "ID completed")
            elif command == "ENABLE":
                requested = [item.upper() for item in args]
                enabled = [item for item in requested if item in {"UTF8=ACCEPT"}]
                if enabled:
                    self.write_line(f"* ENABLED {' '.join(enabled)}")
                self.write_ok(tag, "ENABLE completed")
            elif command == "LOGOUT":
                self.write_line("* BYE MILLIE closing connection")
                self.write_ok(tag, "LOGOUT completed")
                return False
            elif command == "LOGIN":
                self.handle_login(tag, args)
            elif command == "AUTHENTICATE":
                self.handle_authenticate(tag, args)
            elif not self.authenticated:
                self.write_line(f"{tag} NO Authentication required")
            elif command == "NAMESPACE":
                self.write_line('* NAMESPACE (("" "/")) NIL NIL')
                self.write_ok(tag, "NAMESPACE completed")
            elif command in {"LIST", "LSUB", "XLIST"}:
                self.handle_list(tag, command)
            elif command in {"SELECT", "EXAMINE"}:
                self.handle_select(tag, args, read_only=True)
            elif command == "STATUS":
                self.handle_status(tag, args)
            elif command == "SEARCH":
                self.handle_search(tag, args, uid_mode=False)
            elif command == "UID":
                self.handle_uid(tag, args)
            elif command == "FETCH":
                self.handle_fetch(tag, args, uid_mode=False)
            elif command in {"CLOSE", "UNSELECT"}:
                self.selected_mailbox = None
                self.write_ok(tag, f"{command} completed")
            elif command == "CHECK":
                self.write_ok(tag, "CHECK completed")
            elif command == "IDLE":
                self.handle_idle(tag)
            elif command in MUTATING_COMMANDS:
                self.write_line(f"{tag} NO MILLIE IMAP facade is read-only")
            else:
                self.write_line(f"{tag} BAD Unsupported command")
        except Exception as exc:  # noqa: BLE001
            self.write_line(f"{tag} NO {sanitize_response_text(str(exc))}")
        return True

    def handle_login(self, tag: str, args: list[str]) -> None:
        if len(args) < 2:
            self.write_line(f"{tag} BAD LOGIN requires username and password")
            return
        username = args[0]
        password = args[1]
        if not self.imap_server.auth.authenticate(username, password):
            self.write_line(f"{tag} NO Invalid username or password")
            return
        self.authenticated = True
        self.write_ok(tag, "LOGIN completed")

    def handle_authenticate(self, tag: str, args: list[str]) -> None:
        if not args or args[0].upper() != "PLAIN":
            self.write_line(f"{tag} NO Unsupported authentication mechanism")
            return
        if len(args) >= 2:
            encoded = args[1]
        else:
            self.write_line("+")
            raw_line = self.rfile.readline(65536)
            if not raw_line:
                self.write_line(f"{tag} NO Authentication cancelled")
                return
            encoded = raw_line.decode("utf-8", errors="replace").strip()
        try:
            decoded = base64.b64decode(encoded).decode("utf-8", errors="replace")
        except ValueError:
            self.write_line(f"{tag} NO Invalid authentication payload")
            return
        parts = decoded.split("\x00")
        username = parts[-2] if len(parts) >= 2 else ""
        password = parts[-1] if parts else ""
        if not self.imap_server.auth.authenticate(username, password):
            self.write_line(f"{tag} NO Invalid username or password")
            return
        self.authenticated = True
        self.write_ok(tag, "AUTHENTICATE completed")

    def handle_list(self, tag: str, command: str) -> None:
        for mailbox in self.imap_server.store.mailboxes():
            flags = ["\\HasNoChildren", *mailbox_special_use_flags(mailbox)]
            self.write_line(f"* {command} ({' '.join(flags)}) \"/\" {quote_imap_string(mailbox.path)}")
        self.write_ok(tag, f"{command} completed")

    def handle_idle(self, tag: str) -> None:
        self.write_line("+ idling")
        raw_line = self.rfile.readline(65536)
        if raw_line and raw_line.decode("utf-8", errors="replace").strip().upper() == "DONE":
            self.write_ok(tag, "IDLE completed")
            return
        self.write_line(f"{tag} BAD IDLE terminated unexpectedly")

    def handle_select(self, tag: str, args: list[str], read_only: bool) -> None:
        if not args:
            self.write_line(f"{tag} BAD Mailbox name is required")
            return
        mailbox = self.imap_server.store.get_mailbox(args[0])
        if mailbox is None:
            self.write_line(f"{tag} NO Mailbox not found")
            return
        messages = self.imap_server.store.messages(mailbox.id)
        uid_next = max((message.uid for message in messages), default=0) + 1
        unseen = sum(1 for message in messages if "\\Seen" not in message.flags)
        self.selected_mailbox = mailbox
        self.write_line(f"* {len(messages)} EXISTS")
        self.write_line("* 0 RECENT")
        self.write_line(f"* FLAGS ({' '.join(SYSTEM_FLAGS)})")
        self.write_line("* OK [PERMANENTFLAGS ()] Read-only mailbox")
        self.write_line(f"* OK [UIDVALIDITY {mailbox.id}] UIDs valid")
        self.write_line(f"* OK [UIDNEXT {uid_next}] Predicted next UID")
        if unseen:
            self.write_line(f"* OK [UNSEEN {unseen}] First unseen message")
        code = "[READ-ONLY] " if read_only else ""
        self.write_ok(tag, f"{code}SELECT completed")

    def handle_status(self, tag: str, args: list[str]) -> None:
        if not args:
            self.write_line(f"{tag} BAD Mailbox name is required")
            return
        mailbox = self.imap_server.store.get_mailbox(args[0])
        if mailbox is None:
            self.write_line(f"{tag} NO Mailbox not found")
            return
        messages = self.imap_server.store.messages(mailbox.id)
        uid_next = max((message.uid for message in messages), default=0) + 1
        unseen = sum(1 for message in messages if "\\Seen" not in message.flags)
        self.write_line(
            f"* STATUS {quote_imap_string(mailbox.path)} "
            f"(MESSAGES {len(messages)} RECENT 0 UIDNEXT {uid_next} UIDVALIDITY {mailbox.id} UNSEEN {unseen})"
        )
        self.write_ok(tag, "STATUS completed")

    def handle_search(self, tag: str, args: list[str], uid_mode: bool) -> None:
        messages = self.selected_messages()
        criteria = " ".join(args).upper()
        if criteria and criteria not in {"ALL", "(ALL)"}:
            self.write_line(f"{tag} BAD Only SEARCH ALL is supported")
            return
        values = [str(message.uid if uid_mode else message.seq) for message in messages]
        self.write_line(f"* SEARCH {' '.join(values)}")
        self.write_ok(tag, "SEARCH completed")

    def handle_uid(self, tag: str, args: list[str]) -> None:
        if not args:
            self.write_line(f"{tag} BAD UID subcommand is required")
            return
        subcommand = args[0].upper()
        if subcommand == "FETCH":
            self.handle_fetch(tag, args[1:], uid_mode=True)
        elif subcommand == "SEARCH":
            self.handle_search(tag, args[1:], uid_mode=True)
        else:
            self.write_line(f"{tag} BAD Unsupported UID subcommand")

    def handle_fetch(self, tag: str, args: list[str], uid_mode: bool) -> None:
        if len(args) < 2:
            self.write_line(f"{tag} BAD FETCH requires a message set and item list")
            return
        messages = self.selected_messages()
        selected = select_messages(messages, args[0], uid_mode)
        fetch_items = " ".join(args[1:])
        for message in selected:
            self.write_fetch(message, fetch_items, include_uid=uid_mode or "UID" in fetch_items.upper())
        self.write_ok(tag, "FETCH completed")

    def selected_messages(self) -> list[ImapMessage]:
        if self.selected_mailbox is None:
            raise ValueError("Select a mailbox first")
        return self.imap_server.store.messages(self.selected_mailbox.id)

    def write_fetch(self, message: ImapMessage, fetch_items: str, include_uid: bool) -> None:
        requested = fetch_items.upper()
        parts: list[str] = []
        if include_uid:
            parts.append(f"UID {message.uid}")
        if "FLAGS" in requested or not parts:
            parts.append(f"FLAGS ({' '.join(message.flags)})")
        if "INTERNALDATE" in requested:
            parts.append(f'INTERNALDATE "{message.internal_date}"')
        if "RFC822.SIZE" in requested:
            parts.append(f"RFC822.SIZE {message.size}")
        raw: bytes | None = None
        parsed: Message | None = None
        needs_parsed = "ENVELOPE" in requested or "BODYSTRUCTURE" in requested
        literal_request = fetch_literal_request(fetch_items)
        if needs_parsed or literal_request is not None:
            raw = self.imap_server.store.raw_message(message.message_id)
        if needs_parsed and raw is not None:
            parsed = BytesParser(policy=policy.default).parsebytes(raw)
        if "ENVELOPE" in requested and parsed is not None:
            parts.append(f"ENVELOPE {imap_envelope(parsed)}")
        if "BODYSTRUCTURE" in requested and parsed is not None and raw is not None:
            parts.append(f"BODYSTRUCTURE {imap_body_structure(parsed, raw)}")
        if "RFC822.SIZE" not in requested and literal_request is not None and raw is not None:
            parts.append(f"RFC822.SIZE {len(raw)}")

        if literal_request is None or raw is None:
            self.write_line(f"* {message.seq} FETCH ({' '.join(parts)})")
            return

        literal_name, literal_value = resolve_fetch_literal(raw, literal_request)
        prefix_parts = " ".join(parts)
        prefix_space = " " if prefix_parts else ""
        prefix = f"* {message.seq} FETCH ({prefix_parts}{prefix_space}{literal_name} {{{len(literal_value)}}}\r\n"
        self.wfile.write(prefix.encode("utf-8"))
        self.wfile.write(literal_value)
        self.wfile.write(b")\r\n")
        self.wfile.flush()

    def write_ok(self, tag: str, message: str) -> None:
        self.write_line(f"{tag} OK {message}")

    def write_line(self, line: str) -> None:
        self.wfile.write(f"{line}\r\n".encode("utf-8"))
        self.wfile.flush()


def run_imap_facade(
    db: MillieDatabase,
    host: str,
    port: int,
    *,
    username: str | None = None,
    password: str | None = None,
    allow_dev_login: bool | None = None,
    tls_cert: str | None = None,
    tls_key: str | None = None,
) -> None:
    db.init()
    if allow_dev_login is None:
        allow_dev_login = username is None and password is None and is_loopback_host(host)
    if not allow_dev_login and (not username or password is None):
        raise ValueError("Non-development IMAP facade mode requires both username and password")
    server = MillieIMAPServer(
        (host, port),
        db,
        ImapFacadeAuth(username=username, password=password, allow_dev_login=allow_dev_login),
    )
    scheme = "imap"
    if tls_cert or tls_key:
        if not tls_cert or not tls_key:
            raise ValueError("Both tls_cert and tls_key are required to enable IMAPS")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(tls_cert), str(tls_key))
        server.socket = context.wrap_socket(server.socket, server_side=True)
        scheme = "imaps"
    print(f"MILLIE read-only IMAP facade listening on {scheme}://{host}:{port}")
    if allow_dev_login:
        print("Development login is accepted; all mailbox operations are read-only.")
    else:
        print(f"Login requires username {username!r}; all mailbox operations are read-only.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def split_command(line: str) -> tuple[str, str, list[str]]:
    try:
        parts = shlex.split(line)
    except ValueError:
        parts = line.split()
    if len(parts) < 2:
        return "", "", []
    return parts[0], parts[1], parts[2:]


def normalize_mailbox_name(name: str) -> str:
    return name.strip().strip('"').strip("'").strip("/") or "INBOX"


def quote_imap_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def imap_nstring(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "NIL"
    return quote_imap_string(text)


def imap_astring(value: Any) -> str:
    text = str(value or "")
    return quote_imap_string(text)


def imap_address_list(values: list[str]) -> str:
    addresses = getaddresses(values)
    if not addresses:
        return "NIL"
    rendered = [imap_address(display_name, email) for display_name, email in addresses if display_name or email]
    return f"({' '.join(rendered)})" if rendered else "NIL"


def imap_address(display_name: str, email: str) -> str:
    if "@" in email:
        mailbox, host = email.rsplit("@", 1)
    else:
        mailbox = email or display_name or "unknown"
        host = "millie.local"
    return f"({imap_nstring(display_name)} NIL {imap_nstring(mailbox)} {imap_nstring(host)})"


def imap_envelope(message: Message) -> str:
    from_values = message.get_all("from", [])
    sender_values = message.get_all("sender", []) or from_values
    reply_to_values = message.get_all("reply-to", []) or from_values
    return (
        f"({imap_nstring(message.get('date'))} "
        f"{imap_nstring(message.get('subject'))} "
        f"{imap_address_list(from_values)} "
        f"{imap_address_list(sender_values)} "
        f"{imap_address_list(reply_to_values)} "
        f"{imap_address_list(message.get_all('to', []))} "
        f"{imap_address_list(message.get_all('cc', []))} "
        f"{imap_address_list(message.get_all('bcc', []))} "
        f"{imap_nstring(message.get('in-reply-to'))} "
        f"{imap_nstring(message.get('message-id'))})"
    )


def imap_body_structure(message: Message, raw: bytes) -> str:
    if message.is_multipart():
        children = []
        for part in message.iter_parts() if hasattr(message, "iter_parts") else []:
            part_bytes = part.as_bytes(policy=policy.SMTP)
            children.append(imap_body_structure(part, part_bytes))
        if not children:
            children.append(f'("TEXT" "PLAIN" NIL NIL NIL "7BIT" 0 0 NIL NIL NIL NIL)')
        subtype = message.get_content_subtype().upper() or "MIXED"
        return f"({' '.join(children)} {imap_astring(subtype)} NIL NIL NIL)"

    content_type = message.get_content_type()
    if "/" in content_type:
        maintype, subtype = content_type.split("/", 1)
    else:
        maintype, subtype = "text", "plain"
    encoding = str(message.get("content-transfer-encoding") or "7bit").upper()
    params = imap_body_params(message)
    lines = raw.count(b"\n") if maintype.lower() == "text" else None
    base = (
        f"({imap_astring(maintype.upper())} {imap_astring(subtype.upper())} {params} "
        f"{imap_nstring(message.get('content-id'))} {imap_nstring(message.get('content-description'))} "
        f"{imap_astring(encoding)} {len(raw)}"
    )
    if lines is not None:
        base += f" {lines}"
    return base + " NIL NIL NIL NIL)"


def imap_body_params(message: Message) -> str:
    params = []
    raw_params = message.get_params() or []
    for key, value in raw_params[1:]:
        params.append(imap_astring(str(key).upper()))
        params.append(imap_astring(value))
    return f"({' '.join(params)})" if params else "NIL"


@dataclass(frozen=True, slots=True)
class FetchLiteralRequest:
    item_name: str
    section: str
    partial_start: int | None = None
    partial_length: int | None = None


def fetch_literal_request(fetch_items: str) -> FetchLiteralRequest | None:
    raw = fetch_items.strip().strip("()")
    upper = raw.upper()
    if "RFC822.HEADER" in upper:
        return FetchLiteralRequest("RFC822.HEADER", "RFC822.HEADER")
    if "RFC822.TEXT" in upper:
        return FetchLiteralRequest("RFC822.TEXT", "RFC822.TEXT")
    if re.search(r"\bRFC822\b", upper) and "RFC822.SIZE" not in upper:
        return FetchLiteralRequest("RFC822", "RFC822")

    match = re.search(r"BODY(?:\.PEEK)?\[(?P<section>[^\]]*)\](?:<(?P<start>\d+)\.(?P<length>\d+)>)?", raw, re.IGNORECASE)
    if not match:
        return None
    section = match.group("section").strip()
    item_name = f"BODY[{section}]"
    start = int(match.group("start")) if match.group("start") else None
    length = int(match.group("length")) if match.group("length") else None
    if start is not None:
        item_name = f"{item_name}<{start}>"
    return FetchLiteralRequest(item_name, section or "RFC822", start, length)


def resolve_fetch_literal(raw: bytes, request: FetchLiteralRequest) -> tuple[str, bytes]:
    section = request.section.upper()
    if section in {"RFC822", ""}:
        body = raw
    elif section in {"RFC822.HEADER", "HEADER"}:
        body = raw_header_bytes(raw)
    elif section in {"RFC822.TEXT", "TEXT"}:
        body = raw_body_bytes(raw)
    elif section.startswith("HEADER.FIELDS.NOT"):
        body = header_fields_bytes(raw, parse_header_field_names(section), invert=True)
    elif section.startswith("HEADER.FIELDS"):
        body = header_fields_bytes(raw, parse_header_field_names(section), invert=False)
    else:
        body = raw
    if request.partial_start is not None:
        end = request.partial_start + (request.partial_length or len(body))
        body = body[request.partial_start:end]
    return request.item_name, body


def raw_header_bytes(raw: bytes) -> bytes:
    header, _, _ = split_raw_message(raw)
    return ensure_crlf_suffix(header)


def raw_body_bytes(raw: bytes) -> bytes:
    _, _, body = split_raw_message(raw)
    return body


def split_raw_message(raw: bytes) -> tuple[bytes, bytes, bytes]:
    for separator in (b"\r\n\r\n", b"\n\n"):
        if separator in raw:
            header, body = raw.split(separator, 1)
            return header + separator, separator, body
    return raw, b"\r\n\r\n", b""


def parse_header_field_names(section: str) -> list[str]:
    match = re.search(r"\((?P<fields>[^)]*)\)", section)
    if not match:
        return []
    return [item.strip().lower() for item in match.group("fields").split() if item.strip()]


def header_fields_bytes(raw: bytes, field_names: list[str], invert: bool) -> bytes:
    parsed = BytesParser(policy=policy.default).parsebytes(raw)
    wanted = set(field_names)
    lines: list[str] = []
    for name, value in parsed.items():
        include = name.lower() in wanted
        if invert:
            include = not include
        if include:
            lines.append(f"{name}: {value}\r\n")
    lines.append("\r\n")
    return "".join(lines).encode("utf-8")


def ensure_crlf_suffix(value: bytes) -> bytes:
    if value.endswith(b"\r\n\r\n") or value.endswith(b"\n\n"):
        return value
    if value.endswith(b"\r\n") or value.endswith(b"\n"):
        return value + b"\r\n"
    return value + b"\r\n\r\n"


def parse_flags(value: Any) -> tuple[str, ...]:
    try:
        raw = json.loads(str(value or "[]"))
    except json.JSONDecodeError:
        raw = []
    if not isinstance(raw, list):
        raw = []
    flags = [str(item) for item in raw if str(item).startswith("\\")]
    return tuple(flag for flag in SYSTEM_FLAGS if flag in flags)


def mailbox_special_use_flags(mailbox: ImapMailbox) -> list[str]:
    role = (mailbox.role or "").lower()
    normalized = mailbox.path.strip().lower().replace("\\", "/")
    if normalized.startswith("[gmail]/"):
        normalized = normalized.split("/", 1)[1]
    if role == "inbox" or normalized == "inbox":
        return ["\\Inbox"]
    if role == "sent" or normalized in {"sent", "sent mail", "sent items"}:
        return ["\\Sent"]
    if role == "drafts" or normalized == "drafts":
        return ["\\Drafts"]
    if role == "trash" or normalized in {"trash", "deleted", "deleted items"}:
        return ["\\Trash"]
    if role == "junk" or normalized in {"junk", "spam"}:
        return ["\\Junk"]
    if role == "archive" or normalized in {"archive", "all mail"}:
        return ["\\Archive"]
    if role == "flagged" or normalized in {"flagged", "starred"}:
        return ["\\Flagged"]
    return []


def format_internal_date(value: Any) -> str:
    raw = str(value or "")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.now(UTC).replace(microsecond=0)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.strftime("%d-%b-%Y %H:%M:%S %z")


def select_messages(messages: list[ImapMessage], message_set: str, uid_mode: bool) -> list[ImapMessage]:
    selected: list[ImapMessage] = []
    if not messages:
        return []
    max_value = max((message.uid if uid_mode else message.seq) for message in messages)
    for part in message_set.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            start_raw, end_raw = part.split(":", 1)
            start = parse_bound(start_raw, max_value)
            end = parse_bound(end_raw, max_value)
            low, high = sorted((start, end))
            selected.extend(
                message
                for message in messages
                if low <= (message.uid if uid_mode else message.seq) <= high
            )
        else:
            value = parse_bound(part, max_value)
            selected.extend(
                message
                for message in messages
                if (message.uid if uid_mode else message.seq) == value
            )
    seen: set[int] = set()
    unique: list[ImapMessage] = []
    for message in selected:
        key = message.uid if uid_mode else message.seq
        if key in seen:
            continue
        seen.add(key)
        unique.append(message)
    return unique


def parse_bound(value: str, max_value: int) -> int:
    if value.strip() == "*":
        return max_value
    return int(value)


def sanitize_response_text(value: str) -> str:
    return re.sub(r"[\r\n]+", " ", value)[:240]


def is_loopback_host(host: str) -> bool:
    normalized = host.strip().lower()
    return normalized in {"", "localhost", "::1"} or normalized.startswith("127.")
