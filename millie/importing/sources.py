"""Dormant source readers for future mail imports."""

from __future__ import annotations

import base64
import imaplib
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .models import ExtractedMessage


class ImportSourceError(RuntimeError):
    """Raised when a source cannot provide messages."""


class PstPasswordUnsupportedError(ImportSourceError):
    """Raised when a locked PST needs a password-capable backend."""


@dataclass(slots=True)
class PstSource:
    pst_path: Path
    output_dir: Path
    readpst_bin: str = "readpst"
    password_supplied: bool = False

    def extract(self, *, clean: bool = False) -> Path:
        """Extract PST email into MH files and return the message root."""

        readpst_bin = shutil.which(self.readpst_bin) or self.readpst_bin
        if not shutil.which(readpst_bin) and not Path(readpst_bin).exists():
            raise ImportSourceError("readpst is not installed or is not executable.")

        if clean and self.output_dir.exists():
            _safe_rmtree(self.output_dir)
        if self.output_dir.exists() and any(self.output_dir.iterdir()):
            return _message_root(self.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        command = [
            readpst_bin,
            "-q",
            "-M",
            "-te",
            "-o",
            str(self.output_dir),
            str(self.pst_path),
        ]
        result = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip() or "readpst failed"
            if self.password_supplied:
                raise PstPasswordUnsupportedError(
                    "The installed readpst backend cannot receive PST passwords. "
                    "Configure a password-capable PST backend before importing this file. "
                    f"Backend detail: {details}"
                )
            raise ImportSourceError(details)
        return _message_root(self.output_dir)

    def iter_messages(self, *, clean: bool = False) -> Iterator[ExtractedMessage]:
        message_root = self.extract(clean=clean)
        source_uri = str(self.pst_path)
        for path in sorted(message_root.rglob("*")):
            if not path.is_file():
                continue
            relative_path = path.relative_to(message_root)
            yield ExtractedMessage(
                source_type="pst",
                source_uri=source_uri,
                source_message_id=str(relative_path),
                folder=(
                    str(relative_path.parent)
                    if str(relative_path.parent) != "."
                    else None
                ),
                raw_bytes=path.read_bytes(),
                metadata={"pst_extract_path": str(relative_path)},
            )


@dataclass(slots=True)
class ImapSource:
    host: str
    port: int
    username: str
    mailbox: str = "INBOX"
    source_type: str = "imap"
    security: str = "ssl_tls"
    auth_method: str = "password"
    password: str | None = None
    oauth_access_token: str | None = None

    def iter_messages(self) -> Iterator[ExtractedMessage]:
        """Yield RFC822 bytes through IMAP without marking messages read."""

        connection = self._connect()
        try:
            self._authenticate(connection)
            status, _ = connection.select(self.mailbox, readonly=True)
            if status != "OK":
                raise ImportSourceError(f"Could not select IMAP mailbox: {self.mailbox}")
            status, data = connection.uid("SEARCH", None, "ALL")
            if status != "OK":
                raise ImportSourceError("IMAP UID SEARCH failed.")
            for uid in data[0].split():
                status, fetch_data = connection.uid("FETCH", uid, "(BODY.PEEK[])")
                if status != "OK":
                    raise ImportSourceError(f"IMAP UID FETCH failed for UID {uid.decode()}")
                raw_bytes = _extract_imap_fetch_bytes(fetch_data)
                if raw_bytes is None:
                    continue
                yield ExtractedMessage(
                    source_type=self.source_type,
                    source_uri=f"imap://{self.host}:{self.port}/{self.mailbox}",
                    source_message_id=uid.decode("ascii", errors="replace"),
                    folder=self.mailbox,
                    raw_bytes=raw_bytes,
                    metadata={"imap_uid": uid.decode("ascii", errors="replace")},
                )
        finally:
            try:
                connection.close()
            except imaplib.IMAP4.error:
                pass
            connection.logout()

    def _connect(self) -> imaplib.IMAP4:
        if self.security == "ssl_tls":
            return imaplib.IMAP4_SSL(self.host, self.port)
        connection = imaplib.IMAP4(self.host, self.port)
        if self.security == "starttls":
            connection.starttls()
        return connection

    def _authenticate(self, connection: imaplib.IMAP4) -> None:
        if self.auth_method == "oauth":
            if not self.oauth_access_token:
                raise ImportSourceError("OAuth IMAP authentication requires an access token.")
            auth_string = (
                f"user={self.username}\x01"
                f"auth=Bearer {self.oauth_access_token}\x01\x01"
            )
            connection.authenticate("XOAUTH2", lambda _: auth_string.encode("utf-8"))
            return
        if self.auth_method == "password":
            if self.password is None:
                raise ImportSourceError("Password IMAP authentication requires a password.")
            connection.login(self.username, self.password)
            return
        if self.auth_method != "none":
            raise ImportSourceError(f"Unsupported IMAP auth method: {self.auth_method}")


def pst_password_supplied(
    *,
    password_env: str | None = None,
    password_file: Path | None = None,
) -> bool:
    """Check that a PST password is available without returning the secret."""

    if password_env:
        if not os.environ.get(password_env):
            raise ImportSourceError(
                f"PST password env var is empty or missing: {password_env}"
            )
        return True
    if password_file:
        if not password_file.is_file():
            raise ImportSourceError(f"PST password file was not found: {password_file}")
        if not password_file.read_text().strip():
            raise ImportSourceError(f"PST password file is empty: {password_file}")
        return True
    return False


def xoauth2_initial_response(username: str, access_token: str) -> str:
    """Return a base64 XOAUTH2 initial client response for diagnostics/tests."""

    raw = f"user={username}\x01auth=Bearer {access_token}\x01\x01"
    return base64.b64encode(raw.encode("utf-8")).decode("ascii")


def _message_root(output_dir: Path) -> Path:
    children = [child for child in output_dir.iterdir() if child.is_dir()]
    files = [child for child in output_dir.iterdir() if child.is_file()]
    if len(children) == 1 and not files:
        return children[0]
    return output_dir


def _extract_imap_fetch_bytes(
    fetch_data: list[bytes | tuple[bytes, bytes]],
) -> bytes | None:
    for item in fetch_data:
        if isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], bytes):
            return item[1]
    return None


def _safe_rmtree(path: Path) -> None:
    resolved = path.resolve()
    if ".private" not in resolved.parts:
        raise ImportSourceError("Refusing to clean a PST output directory outside .private.")
    if resolved in {Path("/").resolve(), Path.home().resolve()}:
        raise ImportSourceError(f"Refusing to clean unsafe path: {path}")
    shutil.rmtree(resolved)
