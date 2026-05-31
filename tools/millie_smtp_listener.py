#!/usr/bin/env python3
"""Minimal authenticated dev SMTP listener for iOS Mail account setup."""

from __future__ import annotations

import argparse
import base64
import os
import socketserver
import ssl
import subprocess
import sys
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from millie.settings_loader import load_local_settings
from millie.storage.postgres_store import PostgresMailStore


DEFAULT_HOST = "0.0.0.0"
DEFAULT_SUBMISSION_PORT = 22587
DEFAULT_TLS_PORT = 22465
CERT_DIR = PROJECT_ROOT / ".private" / "local" / "imap_tls"
DEFAULT_PID_FILE = PROJECT_ROOT / ".private" / "local" / "millie_smtp_listener.pid"
DEFAULT_LOG_FILE = PROJECT_ROOT / ".private" / "local" / "millie_smtp_listener.log"


class MillieSmtpHandler(socketserver.StreamRequestHandler):
    server: "MillieSmtpServer"

    def setup(self) -> None:
        super().setup()
        self.store = PostgresMailStore.connect(self.server.settings)
        self.authenticated = False
        self.identity_id: str | None = None
        self.in_data = False
        self.data_lines: list[bytes] = []

    def finish(self) -> None:
        try:
            self.store.close()
        finally:
            super().finish()

    def handle(self) -> None:
        self.send_line("220 MILLIE dev SMTP ready")
        while True:
            raw = self.rfile.readline(1024 * 1024)
            if not raw:
                break
            if self.in_data:
                if raw.rstrip(b"\r\n") == b".":
                    self.in_data = False
                    self.data_lines.clear()
                    self.send_line("250 Message accepted for dev discard")
                else:
                    self.data_lines.append(raw)
                continue
            text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if not self.handle_command(text):
                break

    def handle_command(self, text: str) -> bool:
        command, _, rest = text.partition(" ")
        upper = command.upper()
        if upper in {"EHLO", "HELO"}:
            self.ehlo()
        elif upper == "STARTTLS":
            if self.server.implicit_tls:
                self.send_line("503 Already using TLS")
            else:
                self.send_line("220 Begin TLS")
                self.start_tls()
        elif upper == "AUTH":
            self.auth(rest)
        elif upper == "MAIL":
            self.require_auth_or_reject() and self.send_line("250 Sender OK")
        elif upper == "RCPT":
            self.require_auth_or_reject() and self.send_line("250 Recipient OK")
        elif upper == "DATA":
            if self.require_auth_or_reject():
                self.in_data = True
                self.send_line("354 End data with <CR><LF>.<CR><LF>")
        elif upper == "RSET":
            self.data_lines.clear()
            self.send_line("250 Reset OK")
        elif upper == "NOOP":
            self.send_line("250 OK")
        elif upper == "QUIT":
            self.send_line("221 Bye")
            return False
        else:
            self.send_line("502 Command not implemented")
        return True

    def ehlo(self) -> None:
        self.send_line("250-MILLIE")
        if not self.server.implicit_tls:
            self.send_line("250-STARTTLS")
        self.send_line("250-AUTH PLAIN LOGIN")
        self.send_line("250 8BITMIME")

    def auth(self, rest: str) -> None:
        parts = rest.split()
        mechanism = parts[0].upper() if parts else ""
        if mechanism == "PLAIN":
            payload = parts[1] if len(parts) > 1 else self.prompt("334 ")
            try:
                decoded = base64.b64decode(payload).decode("utf-8", errors="replace")
                authzid, username, password = decoded.split("\x00", 2)
            except (ValueError, base64.binascii.Error):
                self.send_line("501 Invalid AUTH PLAIN payload")
                return
            self.complete_auth(username or authzid, password)
            return
        if mechanism == "LOGIN":
            username_payload = parts[1] if len(parts) > 1 else self.prompt("334 VXNlcm5hbWU6")
            password_payload = self.prompt("334 UGFzc3dvcmQ6")
            try:
                username = base64.b64decode(username_payload).decode("utf-8", errors="replace")
                password = base64.b64decode(password_payload).decode("utf-8", errors="replace")
            except base64.binascii.Error:
                self.send_line("501 Invalid AUTH LOGIN payload")
                return
            self.complete_auth(username, password)
            return
        self.send_line("504 Unsupported authentication mechanism")

    def complete_auth(self, username: str, password: str) -> None:
        identity_id = self.store.authenticate(username, password)
        if not identity_id:
            self.send_line("535 Authentication failed")
            return
        self.identity_id = identity_id
        self.authenticated = True
        self.send_line("235 Authentication successful")

    def require_auth_or_reject(self) -> bool:
        if self.authenticated:
            return True
        self.send_line("530 Authentication required")
        return False

    def prompt(self, value: str) -> str:
        self.send_line(value)
        return self.rfile.readline(1024 * 64).decode("ascii", errors="replace").strip()

    def start_tls(self) -> None:
        self.request = self.server.ssl_context.wrap_socket(self.request, server_side=True)
        self.rfile = self.request.makefile("rb")
        self.wfile = self.request.makefile("wb")

    def send_line(self, value: str) -> None:
        self.wfile.write(value.encode("utf-8") + b"\r\n")
        self.wfile.flush()


class MillieSmtpServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address, handler_class, *, settings, implicit_tls, ssl_context):
        self.settings = settings
        self.implicit_tls = implicit_tls
        self.ssl_context = ssl_context
        super().__init__(server_address, handler_class)

    def get_request(self):
        request, client_address = super().get_request()
        if self.implicit_tls:
            request = self.ssl_context.wrap_socket(request, server_side=True)
        return request, client_address


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
        raise SystemExit(result.stderr.strip() or "openssl failed to create SMTP TLS certificate")
    try:
        key.chmod(0o600)
        cert.chmod(0o644)
    except OSError:
        pass
    return cert, key


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start MILLIE's minimal dev SMTP listener.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--submission-port", type=int, default=DEFAULT_SUBMISSION_PORT)
    parser.add_argument("--tls-port", type=int, default=DEFAULT_TLS_PORT)
    parser.add_argument("--daemon", action="store_true")
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
    submission_server = MillieSmtpServer(
        (args.host, args.submission_port),
        MillieSmtpHandler,
        settings=settings,
        implicit_tls=False,
        ssl_context=context,
    )
    tls_server = MillieSmtpServer(
        (args.host, args.tls_port),
        MillieSmtpHandler,
        settings=settings,
        implicit_tls=True,
        ssl_context=context,
    )
    threads = [
        threading.Thread(target=submission_server.serve_forever, daemon=True),
        threading.Thread(target=tls_server.serve_forever, daemon=True),
    ]
    for thread in threads:
        thread.start()
    print(f"MILLIE SMTP submission listening on {args.host}:{args.submission_port}", flush=True)
    print(f"MILLIE SMTP TLS listening on {args.host}:{args.tls_port}", flush=True)
    try:
        for thread in threads:
            thread.join()
    except KeyboardInterrupt:
        submission_server.shutdown()
        tls_server.shutdown()


if __name__ == "__main__":
    parsed_args = build_parser().parse_args()
    if parsed_args.daemon:
        daemonize(pid_file=parsed_args.pid_file, log_file=parsed_args.log_file)
    serve(parsed_args)
