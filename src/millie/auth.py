from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from http import cookies
from typing import Any

from .profiles import ProfileManager


SESSION_COOKIE = "millie_session"
SESSION_TTL_SECONDS = 60 * 60 * 12
PASSWORD_ITERATIONS = 240_000
API_TOKENS_SETTING = "auth.api_tokens.v1"


@dataclass(slots=True)
class AuthStatus:
    authenticated: bool
    dev_bypass: bool
    setup_required: bool
    username: str | None

    def to_api(self) -> dict[str, Any]:
        return {
            "authenticated": self.authenticated,
            "dev_bypass": self.dev_bypass,
            "setup_required": self.setup_required,
            "username": self.username,
        }


class AuthManager:
    def __init__(self, profile_manager: ProfileManager):
        self.profile_manager = profile_manager
        self.ensure_defaults()

    def ensure_defaults(self) -> None:
        if self.profile_manager.get_app_setting("auth.dev_bypass") is None:
            self.profile_manager.set_app_setting("auth.dev_bypass", "true")
        if self.profile_manager.get_app_setting("auth.session_secret") is None:
            self.profile_manager.set_app_setting("auth.session_secret", secrets.token_urlsafe(48))

    def status(self, cookie_header: str | None = None) -> AuthStatus:
        username = self.session_username(cookie_header)
        dev_bypass = self.dev_bypass_enabled()
        if dev_bypass and username is None:
            username = "dev-bypass"
        return AuthStatus(
            authenticated=dev_bypass or username is not None,
            dev_bypass=dev_bypass,
            setup_required=not self.admin_configured(),
            username=username,
        )

    def dev_bypass_enabled(self) -> bool:
        value = self.profile_manager.get_app_setting("auth.dev_bypass")
        return str(value if value is not None else "true").strip().lower() in {"1", "true", "yes", "on"}

    def admin_configured(self) -> bool:
        return bool(self.profile_manager.get_app_setting("auth.admin.username")) and bool(
            self.profile_manager.get_app_setting("auth.admin.password_hash")
        )

    def setup_admin(self, username: str, password: str) -> str:
        if self.admin_configured():
            raise ValueError("Admin user is already configured")
        cleaned_username = username.strip()
        if not cleaned_username:
            raise ValueError("Username is required")
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters")
        self.profile_manager.set_app_setting("auth.admin.username", cleaned_username)
        self.profile_manager.set_app_setting("auth.admin.password_hash", hash_password(password))
        return self.create_session(cleaned_username)

    def login(self, username: str, password: str) -> str:
        configured_username = self.profile_manager.get_app_setting("auth.admin.username")
        password_hash = self.profile_manager.get_app_setting("auth.admin.password_hash")
        if not configured_username or not password_hash:
            raise ValueError("Admin user is not configured")
        if username.strip() != configured_username or not verify_password(password, password_hash):
            raise ValueError("Invalid username or password")
        return self.create_session(configured_username)

    def create_session(self, username: str) -> str:
        payload = {
            "username": username,
            "expires": int(time.time()) + SESSION_TTL_SECONDS,
            "nonce": secrets.token_urlsafe(16),
        }
        payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        encoded_payload = b64encode(payload_bytes)
        signature = sign(encoded_payload.encode("ascii"), self.session_secret())
        return f"{encoded_payload}.{signature}"

    def session_username(self, cookie_header: str | None) -> str | None:
        token = session_cookie_value(cookie_header)
        if not token:
            return None
        try:
            encoded_payload, signature = token.split(".", 1)
        except ValueError:
            return None
        expected = sign(encoded_payload.encode("ascii"), self.session_secret())
        if not hmac.compare_digest(signature, expected):
            return None
        try:
            payload = json.loads(b64decode(encoded_payload).decode("utf-8"))
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            return None
        if int(payload.get("expires") or 0) < int(time.time()):
            return None
        configured_username = self.profile_manager.get_app_setting("auth.admin.username")
        username = str(payload.get("username") or "")
        if not configured_username or username != configured_username:
            return None
        return username

    def session_secret(self) -> str:
        value = self.profile_manager.get_app_setting("auth.session_secret")
        if value:
            return value
        secret = secrets.token_urlsafe(48)
        self.profile_manager.set_app_setting("auth.session_secret", secret)
        return secret

    def list_api_tokens(self) -> list[dict[str, Any]]:
        return [redact_api_token_record(record) for record in self.api_token_records()]

    def create_api_token(self, name: str, scopes: list[str] | None = None) -> dict[str, Any]:
        cleaned_name = name.strip()
        if not cleaned_name:
            raise ValueError("API token name is required")
        cleaned_scopes = normalize_scopes(scopes or ["read"])
        raw_token = f"millie_{secrets.token_urlsafe(32)}"
        record = {
            "id": secrets.token_hex(8),
            "name": cleaned_name,
            "token_prefix": raw_token[:14],
            "token_hash": hash_api_token(raw_token),
            "scopes": cleaned_scopes,
            "created_at": utc_now(),
            "last_used_at": None,
            "revoked_at": None,
        }
        records = self.api_token_records()
        records.append(record)
        self.save_api_token_records(records)
        return {"token": raw_token, "record": redact_api_token_record(record)}

    def revoke_api_token(self, token_id: str) -> bool:
        records = self.api_token_records()
        revoked = False
        for record in records:
            if record.get("id") == token_id and not record.get("revoked_at"):
                record["revoked_at"] = utc_now()
                revoked = True
        if revoked:
            self.save_api_token_records(records)
        return revoked

    def authenticate_api_token(self, authorization_header: str | None, required_scope: str | None = None) -> dict[str, Any] | None:
        token = bearer_token(authorization_header)
        if not token:
            return None
        token_hash = hash_api_token(token)
        records = self.api_token_records()
        matched: dict[str, Any] | None = None
        for record in records:
            if record.get("revoked_at"):
                continue
            if hmac.compare_digest(str(record.get("token_hash") or ""), token_hash):
                if required_scope and not token_allows(record, required_scope):
                    return None
                record["last_used_at"] = utc_now()
                matched = record
                break
        if matched is not None:
            self.save_api_token_records(records)
            return redact_api_token_record(matched)
        return None

    def api_token_records(self) -> list[dict[str, Any]]:
        raw = self.profile_manager.get_app_setting(API_TOKENS_SETTING)
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        return [item for item in parsed if isinstance(item, dict)]

    def save_api_token_records(self, records: list[dict[str, Any]]) -> None:
        self.profile_manager.set_app_setting(API_TOKENS_SETTING, json.dumps(records, sort_keys=True))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations_text, salt_hex, digest_hex = stored_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_text)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, TypeError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def session_cookie_value(cookie_header: str | None) -> str | None:
    if not cookie_header:
        return None
    parsed = cookies.SimpleCookie()
    try:
        parsed.load(cookie_header)
    except cookies.CookieError:
        return None
    morsel = parsed.get(SESSION_COOKIE)
    return morsel.value if morsel else None


def session_cookie(token: str) -> str:
    return (
        f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; "
        f"Max-Age={SESSION_TTL_SECONDS}"
    )


def expired_session_cookie() -> str:
    return f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"


def sign(payload: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    return b64encode(digest)


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def hash_api_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def bearer_token(authorization_header: str | None) -> str | None:
    if not authorization_header:
        return None
    scheme, _, token = authorization_header.strip().partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def normalize_scopes(scopes: list[str]) -> list[str]:
    cleaned = []
    for scope in scopes:
        value = str(scope).strip().lower()
        if not value:
            continue
        if value not in cleaned:
            cleaned.append(value)
    return cleaned or ["read"]


def token_allows(record: dict[str, Any], required_scope: str) -> bool:
    scopes = [str(scope).lower() for scope in record.get("scopes") or []]
    return "*" in scopes or "admin" in scopes or required_scope.lower() in scopes


def redact_api_token_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record.get("id"),
        "name": record.get("name"),
        "token_prefix": record.get("token_prefix"),
        "scopes": list(record.get("scopes") or []),
        "created_at": record.get("created_at"),
        "last_used_at": record.get("last_used_at"),
        "revoked_at": record.get("revoked_at"),
        "active": not bool(record.get("revoked_at")),
    }


def b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))
