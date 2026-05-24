from __future__ import annotations

import json
import platform
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Protocol

from .profiles import ProfileManager


LOCAL_SECRETS_SETTING = "secrets.local.v1"
SECRET_BACKEND_SETTING = "secrets.backend"
KEYCHAIN_SERVICE = "MILLIE"


class SecretStore(Protocol):
    backend: str

    def put(self, key: str, value: str) -> str: ...

    def get(self, key: str) -> str | None: ...

    def delete(self, key: str) -> None: ...


@dataclass(slots=True)
class LocalProfileSecretStore:
    profile_manager: ProfileManager
    backend: str = "local-settings"

    def put(self, key: str, value: str) -> str:
        secrets = self.load()
        secrets[key] = value
        self.profile_manager.set_profile_setting(
            LOCAL_SECRETS_SETTING,
            json.dumps(secrets, indent=2, sort_keys=True),
        )
        return secret_ref(self.backend, key)

    def get(self, key: str) -> str | None:
        return self.load().get(key)

    def delete(self, key: str) -> None:
        secrets = self.load()
        if key not in secrets:
            return
        del secrets[key]
        self.profile_manager.set_profile_setting(
            LOCAL_SECRETS_SETTING,
            json.dumps(secrets, indent=2, sort_keys=True),
        )

    def load(self) -> dict[str, str]:
        raw = self.profile_manager.get_profile_setting(LOCAL_SECRETS_SETTING)
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if not isinstance(payload, dict):
            return {}
        return {str(key): str(value) for key, value in payload.items()}


@dataclass(slots=True)
class MacOSKeychainSecretStore:
    backend: str = "keychain"

    @classmethod
    def is_available(cls) -> bool:
        return platform.system() == "Darwin" and shutil.which("security") is not None

    def put(self, key: str, value: str) -> str:
        self.run_security(
            [
                "add-generic-password",
                "-a",
                key,
                "-s",
                KEYCHAIN_SERVICE,
                "-w",
                value,
                "-U",
            ]
        )
        return secret_ref(self.backend, key)

    def get(self, key: str) -> str | None:
        completed = self.run_security(
            [
                "find-generic-password",
                "-a",
                key,
                "-s",
                KEYCHAIN_SERVICE,
                "-w",
            ],
            check=False,
        )
        if completed.returncode != 0:
            return None
        return completed.stdout.rstrip("\n")

    def delete(self, key: str) -> None:
        self.run_security(
            [
                "delete-generic-password",
                "-a",
                key,
                "-s",
                KEYCHAIN_SERVICE,
            ],
            check=False,
        )

    def run_security(self, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            ["security", *args],
            capture_output=True,
            text=True,
            check=False,
        )
        if check and completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "security command failed"
            raise RuntimeError(detail)
        return completed


class SecretManager:
    def __init__(self, profile_manager: ProfileManager, backend: str | None = None):
        self.profile_manager = profile_manager
        configured = backend or profile_manager.get_app_setting(SECRET_BACKEND_SETTING) or "auto"
        self.backend = normalize_backend(configured)
        self.local_store = LocalProfileSecretStore(profile_manager)
        self.keychain_store = MacOSKeychainSecretStore()

    def status(self) -> dict[str, Any]:
        error = None
        try:
            preferred = self.preferred_store().backend
        except RuntimeError as exc:
            preferred = None
            error = str(exc)
        return {
            "configured_backend": self.backend,
            "active_backend": preferred,
            "keychain_available": self.keychain_store.is_available(),
            "local_available": True,
            "error": error,
        }

    def store_imap_password(self, source_id: str, value: str) -> str:
        key = make_secret_key(
            self.profile_manager.active_profile_id,
            "imap",
            source_id,
            "password",
        )
        return self.preferred_store().put(key, value)

    def store_pop_password(self, source_id: str, value: str) -> str:
        key = make_secret_key(
            self.profile_manager.active_profile_id,
            "pop",
            source_id,
            "password",
        )
        return self.preferred_store().put(key, value)

    def store_graph_token_payload(self, source_id: str, value: str) -> str:
        key = make_secret_key(
            self.profile_manager.active_profile_id,
            "graph",
            source_id,
            "token",
        )
        return self.preferred_store().put(key, value)

    def store_graph_pending_auth(self, source_id: str, state: str, value: str) -> str:
        key = make_secret_key(
            self.profile_manager.active_profile_id,
            "graph",
            source_id,
            f"pending/{state}",
        )
        return self.preferred_store().put(key, value)

    def read_secret(self, ref: str | None) -> str | None:
        if not ref:
            return None
        backend, key = parse_secret_ref(ref)
        return self.store_for_backend(backend).get(key)

    def delete_secret(self, ref: str | None) -> None:
        if not ref:
            return
        backend, key = parse_secret_ref(ref)
        self.store_for_backend(backend).delete(key)

    def preferred_store(self) -> SecretStore:
        if self.backend == "keychain":
            if not self.keychain_store.is_available():
                raise RuntimeError("macOS Keychain secret backend is not available")
            return self.keychain_store
        if self.backend == "local-settings":
            return self.local_store
        if self.keychain_store.is_available():
            return self.keychain_store
        return self.local_store

    def store_for_backend(self, backend: str) -> SecretStore:
        if backend == "keychain":
            if not self.keychain_store.is_available():
                raise RuntimeError("macOS Keychain secret backend is not available")
            return self.keychain_store
        if backend == "local-settings":
            return self.local_store
        raise ValueError(f"Unknown secret backend: {backend}")


def normalize_backend(value: str) -> str:
    cleaned = value.strip().lower()
    if cleaned in {"", "auto"}:
        return "auto"
    if cleaned in {"local", "local-settings", "settings", "profile"}:
        return "local-settings"
    if cleaned in {"keychain", "macos-keychain", "macos"}:
        return "keychain"
    raise ValueError(f"Unknown secret backend: {value}")


def make_secret_key(profile_id: str, kind: str, item_id: str, field: str) -> str:
    return f"profile/{profile_id}/{kind}/{item_id}/{field}"


def secret_ref(backend: str, key: str) -> str:
    return f"{backend}://{key}"


def parse_secret_ref(ref: str) -> tuple[str, str]:
    if "://" not in ref:
        raise ValueError(f"Invalid secret reference: {ref}")
    backend, key = ref.split("://", 1)
    return normalize_backend(backend), key


def backend_from_ref(ref: str | None) -> str | None:
    if not ref or "://" not in ref:
        return None
    backend, _ = ref.split("://", 1)
    return normalize_backend(backend)
