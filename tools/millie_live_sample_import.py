#!/usr/bin/env python3
"""Import one PST, one IMAP, and one Exchange OAuth message into MILLIE."""

from __future__ import annotations

import argparse
import json
import secrets
import string
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from itertools import islice
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from millie.importing.normalize import normalize_email
from millie.importing.sources import ImapSource, PstSource
from millie.service.auth import MillieIdentity, hash_password
from millie.settings_loader import load_local_settings
from millie.storage.postgres_store import PostgresMailStore


DEFAULT_LOGIN = "geon@MILLIE"
DEFAULT_PST = PROJECT_ROOT / "tmp" / "CSU_Archive.pst"
DEFAULT_CREDENTIAL_FILE = PROJECT_ROOT / ".private" / "local" / "millie_ios_mail_credentials.txt"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import one sample message from PST, IMAP, and Exchange OAuth into MILLIE."
    )
    parser.add_argument("--login", default=DEFAULT_LOGIN, help="MILLIE login to create/use.")
    parser.add_argument("--display-name", default="Geon", help="MILLIE mailbox display name.")
    parser.add_argument("--pst", type=Path, default=DEFAULT_PST, help="PST path.")
    parser.add_argument(
        "--credential-file",
        type=Path,
        default=DEFAULT_CREDENTIAL_FILE,
        help="Ignored local file where the generated IMAP password is stored.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_local_settings()
    settings = config["settings"]
    accounts = config["accounts"]

    password = ensure_listener_password(args.credential_file)
    identity = MillieIdentity(args.login, args.display_name)
    store = PostgresMailStore.connect(settings)
    imported: list[dict[str, Any]] = []
    try:
        store.initialize()
        mailbox_id = store.ensure_identity(identity, password_hash=hash_password(password))

        pst_message = first_pst_message(args.pst)
        imported.append(import_message(
            store,
            mailbox_id=mailbox_id,
            source_type="pst",
            source_uri=pst_message.source_uri,
            source_label="PST sample",
            auth_mode=None,
            raw_message=pst_message,
            source_folder="Sources/PST",
        ))

        password_account = first_account(accounts, auth_method="password")
        imap_message = first_imap_message(password_account)
        imported.append(import_message(
            store,
            mailbox_id=mailbox_id,
            source_type="imap",
            source_uri=imap_message.source_uri,
            source_label="IMAP password sample",
            auth_mode="password",
            raw_message=imap_message,
            source_folder="Sources/IMAP",
        ))

        oauth_account = first_account(accounts, auth_method="oauth")
        access_token = oauth_access_token(settings)
        oauth_message = first_imap_message(oauth_account, oauth_access_token=access_token)
        imported.append(import_message(
            store,
            mailbox_id=mailbox_id,
            source_type="exchange_imap_oauth",
            source_uri=oauth_message.source_uri,
            source_label="Exchange OAuth sample",
            auth_mode="oauth",
            raw_message=oauth_message,
            source_folder="Sources/IMAP",
        ))

        store.connection.commit()
    finally:
        store.close()

    write_connection_note(args.credential_file, identity.normalized_login, password)
    print("millie_sample_import=ok")
    print(f"login={identity.normalized_login}")
    print(f"credential_file={relative(args.credential_file)}")
    for item in imported:
        print(
            "imported="
            f"{item['source_type']} message_id={item['message_id']} "
            f"inbox_uid={item['inbox_uid']} all_mail_uid={item['all_mail_uid']}"
        )
    return 0


def ensure_listener_password(path: Path) -> str:
    path = resolve_path(path)
    if path.exists():
        for line in path.read_text().splitlines():
            if line.startswith("password="):
                password = line.split("=", 1)[1].strip()
                if password:
                    return password
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(24))


def write_connection_note(path: Path, login: str, password: str) -> None:
    path = resolve_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join([
            "MILLIE dev IMAP credentials",
            f"login={login}",
            f"password={password}",
            "plaintext_port=22143",
            "tls_port=22993",
            "smtp_submission_port=22587",
            "smtp_tls_port=22465",
            "",
        ])
    )
    try:
        path.chmod(0o600)
    except OSError:
        pass


def first_pst_message(pst_path: Path):
    pst = resolve_path(pst_path)
    output_dir = PROJECT_ROOT / ".private" / "local" / "pst-live-sample" / pst.stem
    source = PstSource(pst_path=pst, output_dir=output_dir)
    return next(iter(source.iter_messages(clean=True)))


def first_account(accounts: list[dict[str, Any]], *, auth_method: str) -> dict[str, Any]:
    for account in accounts:
        if (
            account.get("enabled")
            and account.get("account_type") == "imap"
            and account.get("auth_method") == auth_method
        ):
            return account
    raise SystemExit(f"No enabled IMAP account found for auth method: {auth_method}")


def first_imap_message(account: dict[str, Any], *, oauth_access_token: str | None = None):
    source = ImapSource(
        host=account["host"],
        port=int(account["port"] or 993),
        username=account["username"],
        mailbox="INBOX",
        source_type="exchange_imap_oauth" if account["auth_method"] == "oauth" else "imap",
        security=account.get("security") or "ssl_tls",
        auth_method=account["auth_method"],
        password=account.get("password") or None,
        oauth_access_token=oauth_access_token,
    )
    return next(islice(source.iter_messages(), 1))


def oauth_access_token(settings: dict[str, str]) -> str:
    token = settings.get("microsoft_oauth_access_token") or ""
    expires_at = parse_datetime(settings.get("microsoft_oauth_expires_at") or "")
    if token and expires_at and expires_at > datetime.now(timezone.utc) + timedelta(minutes=5):
        return token
    return refresh_oauth_access_token(settings)


def refresh_oauth_access_token(settings: dict[str, str]) -> str:
    refresh_token = settings.get("microsoft_oauth_refresh_token") or ""
    if not refresh_token:
        raise SystemExit("Microsoft OAuth refresh token is missing.")
    tenant = settings.get("microsoft_oauth_tenant") or "organizations"
    body = {
        "client_id": settings["microsoft_oauth_client_id"],
        "scope": settings["microsoft_oauth_scopes"],
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    if settings.get("microsoft_oauth_client_secret"):
        body["client_secret"] = settings["microsoft_oauth_client_secret"]
    request = urllib.request.Request(
        f"https://login.microsoftonline.com/{urllib.parse.quote(tenant)}/oauth2/v2.0/token",
        data=urllib.parse.urlencode(body).encode("utf-8"),
        headers={"content-type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    token = payload.get("access_token")
    if not token:
        raise SystemExit("Microsoft OAuth refresh did not return an access token.")
    return token


def import_message(
    store: PostgresMailStore,
    *,
    mailbox_id: str,
    source_type: str,
    source_uri: str,
    source_label: str,
    auth_mode: str | None,
    raw_message,
    source_folder: str,
) -> dict[str, Any]:
    source_id = store.upsert_source(
        source_type=source_type,
        source_uri=source_uri,
        display_name=source_label,
        auth_mode=auth_mode,
        is_active=False,
    )
    job_id = store.create_import_job(
        source_id=source_id,
        metadata={"sample": True, "source_label": source_label},
    )
    normalized = normalize_email(
        raw_message.raw_bytes,
        source_message_id=raw_message.source_message_id,
        source_uri=source_uri,
        folder=raw_message.folder,
        metadata={**raw_message.metadata, "sample_import": True},
    )
    store.store_message(
        source_id=source_id,
        import_job_id=job_id,
        message=normalized,
        folder=raw_message.folder,
    )
    inbox_uid = store.map_message_to_mailbox(
        mailbox_id=mailbox_id,
        folder_path="INBOX",
        message_id=normalized.id,
    )
    all_mail_uid = store.map_message_to_mailbox(
        mailbox_id=mailbox_id,
        folder_path="All Mail",
        message_id=normalized.id,
    )
    store.map_message_to_mailbox(
        mailbox_id=mailbox_id,
        folder_path=source_folder,
        message_id=normalized.id,
    )
    return {
        "source_type": source_type,
        "message_id": normalized.id,
        "inbox_uid": inbox_uid,
        "all_mail_uid": all_mail_uid,
    }


def parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def resolve_path(path: Path) -> Path:
    path = path.expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path


def relative(path: Path) -> str:
    path = resolve_path(path)
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
