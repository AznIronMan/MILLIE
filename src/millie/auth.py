from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from http import cookies
from typing import Any

from .profiles import ProfileManager


SESSION_COOKIE = "millie_session"
SESSION_TTL_SECONDS = 60 * 60 * 12
PASSWORD_ITERATIONS = 240_000


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


def b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))
