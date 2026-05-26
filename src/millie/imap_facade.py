from __future__ import annotations

import json
import re
import shlex
import socketserver
from dataclasses import dataclass
from datetime import UTC, datetime
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


@dataclass(frozen=True, slots=True)
class ImapMessage:
    seq: int
    uid: int
    message_id: int
    size: int
    flags: tuple[str, ...]
    internal_date: str


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

    def __init__(self, server_address: tuple[str, int], db: MillieDatabase):
        super().__init__(server_address, MillieIMAPHandler)
        self.store = ReadOnlyImapStore(db)


class MillieIMAPHandler(socketserver.StreamRequestHandler):
    selected_mailbox: ImapMailbox | None = None

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
                self.write_line("* CAPABILITY IMAP4rev1 NAMESPACE UIDPLUS")
                self.write_ok(tag, "CAPABILITY completed")
            elif command == "NOOP":
                self.write_ok(tag, "NOOP completed")
            elif command == "LOGOUT":
                self.write_line("* BYE MILLIE closing connection")
                self.write_ok(tag, "LOGOUT completed")
                return False
            elif command == "LOGIN":
                self.write_ok(tag, "LOGIN completed")
            elif command == "NAMESPACE":
                self.write_line('* NAMESPACE (("" "/")) NIL NIL')
                self.write_ok(tag, "NAMESPACE completed")
            elif command in {"LIST", "LSUB"}:
                self.handle_list(tag)
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
            elif command == "CLOSE":
                self.selected_mailbox = None
                self.write_ok(tag, "CLOSE completed")
            elif command in MUTATING_COMMANDS:
                self.write_line(f"{tag} NO MILLIE IMAP facade is read-only")
            else:
                self.write_line(f"{tag} BAD Unsupported command")
        except Exception as exc:  # noqa: BLE001
            self.write_line(f"{tag} NO {sanitize_response_text(str(exc))}")
        return True

    def handle_list(self, tag: str) -> None:
        for mailbox in self.imap_server.store.mailboxes():
            self.write_line(f'* LIST (\\HasNoChildren) "/" {quote_imap_string(mailbox.path)}')
        self.write_ok(tag, "LIST completed")

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
        include_literal = (
            "BODY[]" in requested
            or "BODY.PEEK[]" in requested
            or "BODY.PEEK[" in requested
            or re.search(r"\bRFC822\b", requested) is not None
        )
        parts: list[str] = []
        if include_uid:
            parts.append(f"UID {message.uid}")
        if "FLAGS" in requested or not parts:
            parts.append(f"FLAGS ({' '.join(message.flags)})")
        if "INTERNALDATE" in requested:
            parts.append(f'INTERNALDATE "{message.internal_date}"')
        if "RFC822.SIZE" in requested or include_literal:
            parts.append(f"RFC822.SIZE {message.size}")

        if not include_literal:
            self.write_line(f"* {message.seq} FETCH ({' '.join(parts)})")
            return

        raw = self.imap_server.store.raw_message(message.message_id)
        literal_name = "RFC822" if re.search(r"\bRFC822\b", requested) and "BODY" not in requested else "BODY[]"
        prefix = f"* {message.seq} FETCH ({' '.join(parts)} {literal_name} {{{len(raw)}}}\r\n"
        self.wfile.write(prefix.encode("utf-8"))
        self.wfile.write(raw)
        self.wfile.write(b")\r\n")
        self.wfile.flush()

    def write_ok(self, tag: str, message: str) -> None:
        self.write_line(f"{tag} OK {message}")

    def write_line(self, line: str) -> None:
        self.wfile.write(f"{line}\r\n".encode("utf-8"))
        self.wfile.flush()


def run_imap_facade(db: MillieDatabase, host: str, port: int) -> None:
    db.init()
    server = MillieIMAPServer((host, port), db)
    print(f"MILLIE read-only IMAP facade listening on imap://{host}:{port}")
    print("Login is accepted for local development; all mailbox operations are read-only.")
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


def parse_flags(value: Any) -> tuple[str, ...]:
    try:
        raw = json.loads(str(value or "[]"))
    except json.JSONDecodeError:
        raw = []
    if not isinstance(raw, list):
        raw = []
    flags = [str(item) for item in raw if str(item).startswith("\\")]
    return tuple(flag for flag in SYSTEM_FLAGS if flag in flags)


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
