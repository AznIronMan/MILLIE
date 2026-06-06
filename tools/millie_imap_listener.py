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
from dataclasses import dataclass
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from millie.service.imap_protocol import body_literal_name, imap_capabilities, summarize_fetch_items
from millie.settings_loader import load_local_settings

try:
    import psycopg
    from millie.storage.postgres_store import PostgresMailStore
except ModuleNotFoundError:
    psycopg = None  # type: ignore[assignment]
    PostgresMailStore = None  # type: ignore[assignment]


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PLAIN_PORT = 22143
DEFAULT_TLS_PORT = 22993
CERT_DIR = PROJECT_ROOT / ".private" / "local" / "imap_tls"
DEFAULT_PID_FILE = PROJECT_ROOT / ".private" / "local" / "millie_imap_listener.pid"
DEFAULT_LOG_FILE = PROJECT_ROOT / ".private" / "local" / "millie_imap_listener.log"
DEFAULT_MAX_DB_CONNECTIONS = 8
DEFAULT_DB_SLOT_TIMEOUT_SECONDS = 10


class MillieImapHandler(socketserver.StreamRequestHandler):
    server: "MillieImapServer"

    def setup(self) -> None:
        super().setup()
        self.connection_slot_acquired = False
        self.store = None
        self.identity_id: str | None = None
        self.mailbox_id: str | None = None
        self.selected_folder = "INBOX"
        self.client = f"{self.client_address[0]}:{self.client_address[1]}"
        if PostgresMailStore is None or psycopg is None:
            raise RuntimeError("psycopg is required to run the MILLIE IMAP listener")
        if not self.server.connection_slots.acquire(timeout=self.server.db_slot_timeout_seconds):
            self.wfile.write(b"* BYE MILLIE IMAP connection limit reached\r\n")
            self.wfile.flush()
            self.log_event("connection_rejected", reason="db_connection_limit")
            raise RuntimeError("MILLIE IMAP database connection limit reached")
        self.connection_slot_acquired = True
        try:
            self.store = PostgresMailStore.connect(self.server.settings)
        except Exception:
            self.server.connection_slots.release()
            self.connection_slot_acquired = False
            raise
        self.log_event("connect", mode="tls" if self.server.implicit_tls else "plain")

    def finish(self) -> None:
        try:
            self.log_event("disconnect")
            store = getattr(self, "store", None)
            if store:
                store.close()
            if getattr(self, "connection_slot_acquired", False):
                self.server.connection_slots.release()
                self.connection_slot_acquired = False
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
            literal = self.read_command_literal(text)
            if not self.handle_command(text, literal=literal):
                break

    def read_command_literal(self, text: str) -> bytes | None:
        marker = re.search(r"\{(\d+)(\+)?\}$", text)
        if not marker:
            return None
        size = int(marker.group(1))
        if not marker.group(2):
            self.send_line("+ Ready for literal data")
        literal = self.rfile.read(size)
        self.rfile.readline(1024)
        return literal

    def handle_command(self, text: str, *, literal: bytes | None = None) -> bool:
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
        elif upper == "CREATE":
            self.require_auth(tag) and self.create_folder(tag, rest)
        elif upper == "DELETE":
            self.require_auth(tag) and self.delete_folder(tag, rest)
        elif upper == "RENAME":
            self.require_auth(tag) and self.rename_folder(tag, rest)
        elif upper == "SUBSCRIBE":
            self.require_auth(tag) and self.subscribe_folder(tag, rest, subscribed=True)
        elif upper == "UNSUBSCRIBE":
            self.require_auth(tag) and self.subscribe_folder(tag, rest, subscribed=False)
        elif upper == "APPEND":
            self.require_auth(tag) and self.append_message(tag, rest, literal)
        elif upper == "COPY":
            self.require_selected(tag) and self.copy_messages(tag, rest, uid_mode=False)
        elif upper == "MOVE":
            self.require_selected(tag) and self.move_messages(tag, rest, uid_mode=False)
        elif upper == "SEARCH":
            self.require_selected(tag) and self.search(tag, rest, uid_mode=False)
        elif upper == "FETCH":
            self.require_selected(tag) and self.fetch(tag, rest, uid_mode=False)
        elif upper == "EXPUNGE":
            self.require_selected(tag) and self.expunge(tag, respond=True)
        elif upper == "CLOSE":
            self.require_selected(tag) and self.close_folder(tag)
        elif upper == "STORE":
            self.require_selected(tag) and self.store_flags(tag, rest, uid_mode=False)
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
            self.require_selected(tag) and self.store_flags(tag, rest, uid_mode=True)
        elif subcommand == "COPY":
            self.require_selected(tag) and self.copy_messages(tag, rest, uid_mode=True)
        elif subcommand == "MOVE":
            self.require_selected(tag) and self.move_messages(tag, rest, uid_mode=True)
        elif subcommand == "EXPUNGE":
            self.require_selected(tag) and self.uid_expunge(tag, rest)
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

    def create_folder(self, tag: str, rest: str) -> None:
        folder = normalize_folder_name(rest)
        if not folder:
            self.send_bad(tag, "CREATE requires a folder name")
            return
        self.store.ensure_mailbox_folder(self.mailbox_id, folder)
        self.commit("create_folder", folder=folder)
        self.send_ok(tag, "CREATE completed")

    def delete_folder(self, tag: str, rest: str) -> None:
        folder = normalize_folder_name(rest)
        result = self.store.delete_mailbox_folder(self.mailbox_id, folder)
        if result == "deleted":
            self.commit("delete_folder", folder=folder)
            self.send_ok(tag, "DELETE completed")
        elif result == "protected":
            self.rollback("delete_protected", folder=folder)
            self.send_no(tag, "Cannot delete a protected MILLIE folder")
        else:
            self.rollback("delete_missing", folder=folder)
            self.send_no(tag, "Folder not found")

    def rename_folder(self, tag: str, rest: str) -> None:
        try:
            old_folder, new_folder = shlex.split(rest)[:2]
        except ValueError:
            self.send_bad(tag, "RENAME requires old and new folder names")
            return
        result = self.store.rename_mailbox_folder(self.mailbox_id, old_folder, new_folder)
        if result == "renamed":
            if self.selected_folder == normalize_folder_name(old_folder):
                self.selected_folder = normalize_folder_name(new_folder)
            self.commit("rename_folder", old_folder=old_folder, new_folder=new_folder)
            self.send_ok(tag, "RENAME completed")
        else:
            self.rollback("rename_failed", old_folder=old_folder, new_folder=new_folder, reason=result)
            self.send_no(tag, f"RENAME failed: {result}")

    def subscribe_folder(self, tag: str, rest: str, *, subscribed: bool) -> None:
        folder = normalize_folder_name(rest)
        if self.store.set_folder_subscription(self.mailbox_id, folder, subscribed):
            self.commit("subscription", folder=folder, subscribed=subscribed)
            self.send_ok(tag, "SUBSCRIBE completed" if subscribed else "UNSUBSCRIBE completed")
        else:
            self.rollback("subscription_missing", folder=folder, subscribed=subscribed)
            self.send_no(tag, "Folder not found")

    def select_folder(self, tag: str, rest: str, *, readonly: bool) -> None:
        folder = normalize_folder_name(rest)
        messages = self.messages(folder)
        uid_next = max([int(message["uid"]) for message in messages], default=0) + 1
        self.selected_folder = folder
        self.log_event("select", folder=folder, messages=len(messages), readonly=readonly)
        self.send_line("* FLAGS (\\Answered \\Flagged \\Deleted \\Seen \\Draft)")
        self.send_line("* OK [PERMANENTFLAGS (\\Answered \\Flagged \\Deleted \\Seen \\Draft \\*)]")
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

    def store_flags(self, tag: str, rest: str, *, uid_mode: bool) -> None:
        operation = parse_store_operation(rest)
        if not operation:
            self.send_bad(tag, "STORE requires a message set, operation, and flags")
            return
        messages = self.messages(self.selected_folder)
        selected = select_messages(messages, operation.set_spec, uid_mode=uid_mode)
        uids = [int(message["uid"]) for _, message in selected]
        updates = self.store.update_message_flags(
            mailbox_id=self.mailbox_id,
            folder_path=self.selected_folder,
            uids=uids,
            mode=operation.mode,
            flags=operation.flags,
        )
        self.commit("store_flags", folder=self.selected_folder, uid_mode=uid_mode, count=len(updates))
        if not operation.silent:
            sequence_by_uid = {int(message["uid"]): sequence for sequence, message in selected}
            for update in updates:
                sequence = sequence_by_uid.get(int(update["uid"]))
                if sequence is None:
                    continue
                attrs = [f"FLAGS ({' '.join(update['flags'])})"]
                if uid_mode:
                    attrs.append(f"UID {update['uid']}")
                self.send_line(f"* {sequence} FETCH ({' '.join(attrs)})")
        self.send_ok(tag, "UID STORE completed" if uid_mode else "STORE completed")

    def copy_messages(self, tag: str, rest: str, *, uid_mode: bool) -> None:
        parsed = split_set_and_folder(rest)
        if not parsed:
            self.send_bad(tag, "COPY requires a message set and destination folder")
            return
        set_spec, target_folder = parsed
        messages = self.messages(self.selected_folder)
        selected = select_messages(messages, set_spec, uid_mode=uid_mode)
        source_uids = [int(message["uid"]) for _, message in selected]
        copied = self.store.copy_messages(
            mailbox_id=self.mailbox_id,
            source_folder_path=self.selected_folder,
            target_folder_path=target_folder,
            uids=source_uids,
        )
        if source_uids and not copied:
            self.rollback("copy_failed", target_folder=target_folder)
            self.send_no(tag, "[TRYCREATE] Destination folder not found")
            return
        self.commit("copy_messages", source=self.selected_folder, target=target_folder, count=len(copied))
        self.send_ok(tag, copy_uid_response("COPY", copied, uid_mode=uid_mode))

    def move_messages(self, tag: str, rest: str, *, uid_mode: bool) -> None:
        parsed = split_set_and_folder(rest)
        if not parsed:
            self.send_bad(tag, "MOVE requires a message set and destination folder")
            return
        set_spec, target_folder = parsed
        messages = self.messages(self.selected_folder)
        selected = select_messages(messages, set_spec, uid_mode=uid_mode)
        source_uids = [int(message["uid"]) for _, message in selected]
        copied = self.store.move_messages(
            mailbox_id=self.mailbox_id,
            source_folder_path=self.selected_folder,
            target_folder_path=target_folder,
            uids=source_uids,
        )
        if source_uids and not copied:
            self.rollback("move_failed", target_folder=target_folder)
            self.send_no(tag, "[TRYCREATE] Destination folder not found")
            return
        for sequence in expunge_sequence_numbers(messages, source_uids):
            self.send_line(f"* {sequence} EXPUNGE")
        self.commit("move_messages", source=self.selected_folder, target=target_folder, count=len(copied))
        self.send_ok(tag, copy_uid_response("MOVE", copied, uid_mode=uid_mode))

    def append_message(self, tag: str, rest: str, literal: bytes | None) -> None:
        if literal is None:
            self.send_bad(tag, "APPEND requires a message literal")
            return
        request = parse_append_request(rest, literal)
        if not request:
            self.send_bad(tag, "Could not parse APPEND")
            return
        uid = self.store.append_raw_message_to_mailbox(
            mailbox_id=self.mailbox_id,
            folder_path=request.folder,
            raw_bytes=request.literal,
            flags=request.flags,
            internal_date=request.internal_date,
        )
        self.commit("append_message", folder=request.folder, uid=uid, size=len(request.literal))
        self.send_ok(tag, f"[APPENDUID 1 {uid}] APPEND completed")

    def expunge(self, tag: str, *, respond: bool) -> None:
        count = self.expunge_selected_folder(respond=respond)
        self.send_ok(tag, "EXPUNGE completed")

    def expunge_selected_folder(self, *, respond: bool) -> int:
        messages = self.messages(self.selected_folder)
        deleted_uids = [
            int(message["uid"])
            for message in messages
            if "\\Deleted" in message["flags"]
        ]
        expunged = self.store.expunge_deleted(
            mailbox_id=self.mailbox_id,
            folder_path=self.selected_folder,
        )
        if respond:
            for sequence in expunge_sequence_numbers(messages, expunged):
                self.send_line(f"* {sequence} EXPUNGE")
        self.commit("expunge", folder=self.selected_folder, requested=len(deleted_uids), count=len(expunged))
        return len(expunged)

    def uid_expunge(self, tag: str, rest: str) -> None:
        messages = self.messages(self.selected_folder)
        selected = select_messages(messages, rest, uid_mode=True)
        selected_uids = [int(message["uid"]) for _, message in selected]
        expunged = self.store.expunge_uids(
            mailbox_id=self.mailbox_id,
            folder_path=self.selected_folder,
            uids=selected_uids,
            require_deleted=True,
        )
        for sequence in expunge_sequence_numbers(messages, expunged):
            self.send_line(f"* {sequence} EXPUNGE")
        self.commit("uid_expunge", folder=self.selected_folder, count=len(expunged))
        self.send_ok(tag, "UID EXPUNGE completed")

    def close_folder(self, tag: str) -> None:
        self.expunge_selected_folder(respond=False)
        self.selected_folder = "INBOX"
        self.send_ok(tag, "CLOSE completed")

    def fetch_one(self, sequence: int, message: dict[str, Any], item_text: str, *, uid_mode: bool) -> None:
        upper = item_text.upper()
        raw = self.raw_mime_for_fetch(message)
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

    def raw_mime_for_fetch(self, message: dict[str, Any]) -> bytes:
        if bool(message.get("raw_mime_quarantined")):
            return quarantined_raw_mime(message, reason="raw MIME is quarantined")
        try:
            return self.store.get_raw_mime_by_uid(
                mailbox_id=self.mailbox_id,
                folder_path=self.selected_folder,
                uid=int(message["uid"]),
            ) or b""
        except psycopg.errors.DataCorrupted as exc:
            message_id = str(message.get("message_id") or "")
            self.store.connection.rollback()
            if message_id:
                self.store.mark_raw_mime_quarantined(
                    message_id,
                    reason="postgres_data_corrupted",
                    source="imap_fetch",
                    details={
                        "folder_path": self.selected_folder,
                        "uid": int(message["uid"]),
                        "error": str(exc),
                    },
                )
                self.store.connection.commit()
                message["raw_mime_quarantined"] = True
            self.log_event(
                "raw_mime_quarantined",
                folder=self.selected_folder,
                uid=message.get("uid"),
                message_id=message_id or "unknown",
                reason="postgres_data_corrupted",
            )
            return quarantined_raw_mime(message, reason="raw MIME storage is damaged")

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

    def commit(self, event: str, **fields: object) -> None:
        self.store.connection.commit()
        self.log_event(event, **fields)

    def rollback(self, event: str, **fields: object) -> None:
        self.store.connection.rollback()
        self.log_event(event, **fields)

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
        max_db_connections: int = DEFAULT_MAX_DB_CONNECTIONS,
        db_slot_timeout_seconds: int = DEFAULT_DB_SLOT_TIMEOUT_SECONDS,
    ) -> None:
        self.settings = settings
        self.implicit_tls = implicit_tls
        self.ssl_context = ssl_context
        self.connection_slots = threading.BoundedSemaphore(max(1, max_db_connections))
        self.db_slot_timeout_seconds = max(1, db_slot_timeout_seconds)
        super().__init__(server_address, handler_class)

    def get_request(self):
        request, client_address = super().get_request()
        if self.implicit_tls:
            request = self.ssl_context.wrap_socket(request, server_side=True)
        return request, client_address


@dataclass(slots=True)
class StoreOperation:
    set_spec: str
    mode: str
    flags: list[str]
    silent: bool


@dataclass(slots=True)
class AppendRequest:
    folder: str
    flags: list[str]
    internal_date: datetime | None
    literal: bytes


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


def split_set_and_folder(rest: str) -> tuple[str, str] | None:
    parts = rest.split(" ", 1)
    if len(parts) != 2:
        return None
    return parts[0].strip(), normalize_folder_name(parts[1])


def parse_store_operation(rest: str) -> StoreOperation | None:
    parts = rest.split(" ", 2)
    if len(parts) != 3:
        return None
    set_spec, operation_text, flags_text = parts
    operation = operation_text.upper()
    silent = operation.endswith(".SILENT")
    operation = operation.removesuffix(".SILENT")
    if operation == "FLAGS":
        mode = "replace"
    elif operation == "+FLAGS":
        mode = "add"
    elif operation == "-FLAGS":
        mode = "remove"
    else:
        return None
    return StoreOperation(
        set_spec=set_spec,
        mode=mode,
        flags=parse_flag_list(flags_text),
        silent=silent,
    )


def parse_flag_list(value: str) -> list[str]:
    text = value.strip()
    if text.startswith("("):
        end = text.find(")")
        if end >= 0:
            text = text[1:end]
    return [flag for flag in re.split(r"\s+", text.strip()) if flag]


def parse_append_request(rest: str, literal: bytes) -> AppendRequest | None:
    text = strip_literal_marker(rest).strip()
    folder, index = read_imap_astring(text)
    if folder is None:
        return None
    remainder = text[index:].strip()
    flags: list[str] = []
    internal_date: datetime | None = None
    if remainder.startswith("("):
        end = remainder.find(")")
        if end < 0:
            return None
        flags = parse_flag_list(remainder[: end + 1])
        remainder = remainder[end + 1 :].strip()
    if remainder:
        date_value, index = read_imap_astring(remainder)
        if date_value is not None:
            internal_date = parse_internal_date(date_value)
            remainder = remainder[index:].strip()
    return AppendRequest(
        folder=normalize_folder_name(folder),
        flags=flags,
        internal_date=internal_date,
        literal=literal,
    )


def strip_literal_marker(value: str) -> str:
    return re.sub(r"\s*\{\d+\+?\}\s*$", "", value)


def read_imap_astring(value: str) -> tuple[str | None, int]:
    text = value.lstrip()
    offset = len(value) - len(text)
    if not text:
        return None, offset
    if text[0] != '"':
        match = re.match(r"[^\s]+", text)
        if not match:
            return None, offset
        return match.group(0), offset + match.end()
    result: list[str] = []
    escaped = False
    for index, char in enumerate(text[1:], start=1):
        if escaped:
            result.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            return "".join(result), offset + index + 1
        else:
            result.append(char)
    return None, len(value)


def parse_internal_date(value: str) -> datetime | None:
    try:
        parsed = datetime.strptime(value, "%d-%b-%Y %H:%M:%S %z")
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def copy_uid_response(command: str, copied: list[dict[str, int]], *, uid_mode: bool) -> str:
    if not copied:
        return f"{'UID ' if uid_mode else ''}{command} completed"
    source = ",".join(str(item["source_uid"]) for item in copied)
    target = ",".join(str(item["target_uid"]) for item in copied)
    return f"[COPYUID 1 {source} {target}] {'UID ' if uid_mode else ''}{command} completed"


def expunge_sequence_numbers(messages: list[dict[str, Any]], expunged_uids: list[int]) -> list[int]:
    expunged = set(expunged_uids)
    deleted_before = 0
    sequences: list[int] = []
    for sequence, message in enumerate(messages, start=1):
        if int(message["uid"]) in expunged:
            sequences.append(sequence - deleted_before)
            deleted_before += 1
    return sequences


def log_value(value: object) -> str:
    text = str(value).replace("\n", "\\n").replace("\r", "\\r").replace(" ", "_")
    return text[:160]


def normalize_folder_name(value: str) -> str:
    text = value.strip()
    try:
        parts = shlex.split(text)
        if parts and (text.startswith('"') or len(parts) == 1):
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


def quarantined_raw_mime(message: dict[str, Any], *, reason: str) -> bytes:
    subject = safe_header_value(str(message.get("subject") or "MILLIE quarantined message"))
    message_id = safe_message_id_token(str(message.get("message_id") or "unknown"))
    uid = str(message.get("uid") or "unknown")
    body = (
        "MILLIE could not read this message's original raw MIME bytes from the "
        "recovered Postgres archive.\r\n\r\n"
        f"Reason: {reason}\r\n"
        f"MILLIE message id: {message_id}\r\n"
        f"IMAP UID: {uid}\r\n\r\n"
        "The message metadata remains available, but the damaged raw payload has "
        "been quarantined so IMAP clients can continue syncing.\r\n"
    )
    return (
        "From: MILLIE Archive <postmaster@millie.local>\r\n"
        "To: geon@millie.cnbsk.cloud\r\n"
        f"Subject: [MILLIE quarantined] {subject}\r\n"
        f"Date: {email.utils.format_datetime(datetime.now(timezone.utc))}\r\n"
        f"Message-ID: <millie-quarantine-{message_id}@millie.local>\r\n"
        "MIME-Version: 1.0\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "Content-Transfer-Encoding: 8bit\r\n"
        "\r\n"
        f"{body}"
    ).encode("utf-8", errors="replace")


def safe_header_value(value: str) -> str:
    return re.sub(r"[\r\n]+", " ", value).strip()[:180] or "MILLIE quarantined message"


def safe_message_id_token(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "-", value).strip(".-")[:120] or "unknown"


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
    parser.add_argument(
        "--max-db-connections",
        type=int,
        default=DEFAULT_MAX_DB_CONNECTIONS,
        help="Maximum concurrent Postgres connections used by IMAP client sessions.",
    )
    parser.add_argument(
        "--db-slot-timeout",
        type=int,
        default=DEFAULT_DB_SLOT_TIMEOUT_SECONDS,
        help="Seconds an IMAP client waits for a database connection slot before being rejected.",
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
        max_db_connections=args.max_db_connections,
        db_slot_timeout_seconds=args.db_slot_timeout,
    )
    tls_server = MillieImapServer(
        (args.host, args.tls_port),
        MillieImapHandler,
        settings=settings,
        implicit_tls=True,
        ssl_context=context,
        max_db_connections=args.max_db_connections,
        db_slot_timeout_seconds=args.db_slot_timeout,
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
