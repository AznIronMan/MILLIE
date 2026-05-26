from __future__ import annotations

import json
import poplib
import re
import ssl
from dataclasses import dataclass
from typing import Any, Protocol

from .database import MillieDatabase, utc_now
from .mailparse import parse_raw_message
from .profiles import ProfileManager
from .secrets import SecretManager, backend_from_ref


POP_SOURCES_SETTING = "pop.sources.v1"


@dataclass(frozen=True, slots=True)
class PopProviderPreset:
    id: str
    display_name: str
    host: str
    port: int
    use_ssl: bool
    host_aliases: tuple[str, ...] = ()

    def to_api(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "host": self.host,
            "port": self.port,
            "use_ssl": self.use_ssl,
            "host_aliases": list(self.host_aliases),
        }


POP_PROVIDER_PRESETS = {
    "generic": PopProviderPreset(
        id="generic",
        display_name="Generic POP3",
        host="",
        port=995,
        use_ssl=True,
    ),
    "gmail": PopProviderPreset(
        id="gmail",
        display_name="Gmail / Google Workspace",
        host="pop.gmail.com",
        port=995,
        use_ssl=True,
    ),
    "outlook": PopProviderPreset(
        id="outlook",
        display_name="Outlook.com / Microsoft 365",
        host="outlook.office365.com",
        port=995,
        use_ssl=True,
    ),
    "yahoo": PopProviderPreset(
        id="yahoo",
        display_name="Yahoo Mail",
        host="pop.mail.yahoo.com",
        port=995,
        use_ssl=True,
    ),
    "aol": PopProviderPreset(
        id="aol",
        display_name="AOL Mail",
        host="pop.aol.com",
        port=995,
        use_ssl=True,
    ),
    "fastmail": PopProviderPreset(
        id="fastmail",
        display_name="Fastmail",
        host="pop.fastmail.com",
        port=995,
        use_ssl=True,
    ),
    "zoho": PopProviderPreset(
        id="zoho",
        display_name="Zoho Mail",
        host="pop.zoho.com",
        port=995,
        use_ssl=True,
    ),
}


class PopClient(Protocol):
    def user(self, user: str) -> bytes: ...

    def pass_(self, password: str) -> bytes: ...

    def stat(self) -> tuple[int, int]: ...

    def uidl(self, which: int | None = None) -> tuple[bytes, list[bytes], int] | bytes: ...

    def retr(self, which: int) -> tuple[bytes, list[bytes], int]: ...

    def quit(self) -> bytes: ...

    def close(self) -> None: ...


@dataclass(slots=True)
class PopSourceConfig:
    id: str
    name: str
    host: str
    port: int
    username: str
    password: str
    use_ssl: bool
    sync_limit: int
    auth_ref: str | None = None
    auth_method: str = "password"
    provider: str = "generic"

    def to_api(self) -> dict[str, Any]:
        secret_backend = backend_from_ref(self.auth_ref)
        return {
            "id": self.id,
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "use_ssl": self.use_ssl,
            "sync_limit": self.sync_limit,
            "auth_method": self.auth_method,
            "provider": self.provider,
            "password_configured": bool(self.password or self.auth_ref),
            "secret_backend": secret_backend or ("legacy-settings" if self.password else None),
        }

    def source_uri(self) -> str:
        scheme = "pop3s" if self.use_ssl else "pop3"
        return f"{scheme}://{self.username}@{self.host}:{self.port}"


@dataclass(slots=True)
class PopProbeResult:
    message_count: int
    maildrop_size_bytes: int
    uidl_available: bool
    uidl_sample_count: int
    capabilities: list[str]

    def to_api(self) -> dict[str, Any]:
        return {
            "message_count": self.message_count,
            "maildrop_size_bytes": self.maildrop_size_bytes,
            "uidl_available": self.uidl_available,
            "uidl_sample_count": self.uidl_sample_count,
            "capabilities": self.capabilities,
            "commands_not_used": ["RETR", "DELE"],
        }


@dataclass(slots=True)
class PopSyncResult:
    import_job_id: int
    source_id: int
    processed: int
    imported: int
    duplicates: int
    errors: int

    def to_api(self) -> dict[str, Any]:
        return {
            "import_job_id": self.import_job_id,
            "source_id": self.source_id,
            "processed": self.processed,
            "imported": self.imported,
            "duplicates": self.duplicates,
            "errors": self.errors,
            "format": "pop3",
        }


def list_pop_provider_presets() -> list[PopProviderPreset]:
    return list(POP_PROVIDER_PRESETS.values())


def load_pop_sources(profile_manager: ProfileManager) -> list[PopSourceConfig]:
    sources: list[PopSourceConfig] = []
    for item in load_pop_source_payloads(profile_manager):
        try:
            sources.append(config_from_dict(item))
        except ValueError:
            continue
    return sources


def load_pop_source_payloads(profile_manager: ProfileManager) -> list[dict[str, object]]:
    raw = profile_manager.get_profile_setting(POP_SOURCES_SETTING)
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def save_pop_source(
    profile_manager: ProfileManager,
    payload: dict[str, object],
    secret_manager: SecretManager | None = None,
) -> PopSourceConfig:
    secret_manager = secret_manager or SecretManager(profile_manager)
    migrate_pop_source_secrets(profile_manager, secret_manager)
    existing_sources = load_pop_sources(profile_manager)
    requested_id = str(payload.get("id") or "").strip()
    existing = next((item for item in existing_sources if item.id == requested_id), None)
    source_id = requested_id or unique_pop_source_id(
        str(payload.get("name") or payload.get("host") or "pop"),
        existing_sources,
    )
    config_payload = dict(payload)
    config_payload["id"] = source_id
    incoming_password = str(config_payload.get("password") or "")
    if incoming_password:
        config_payload["auth_ref"] = secret_manager.store_pop_password(source_id, incoming_password)
    elif existing:
        config_payload["auth_ref"] = existing.auth_ref
    config = config_from_dict(config_payload)
    merged = [item for item in existing_sources if item.id != config.id]
    merged.append(config)
    profile_manager.set_profile_setting(
        POP_SOURCES_SETTING,
        json.dumps([source_to_settings(item) for item in merged], indent=2, sort_keys=True),
    )
    return config


def get_pop_source(
    profile_manager: ProfileManager,
    source_id: str,
    secret_manager: SecretManager | None = None,
) -> PopSourceConfig:
    secret_manager = secret_manager or SecretManager(profile_manager)
    migrate_pop_source_secrets(profile_manager, secret_manager)
    for source in load_pop_sources(profile_manager):
        if source.id == source_id:
            source.password = source.password or secret_manager.read_secret(source.auth_ref) or ""
            return source
    raise KeyError(f"Unknown POP source: {source_id}")


def delete_pop_source(
    profile_manager: ProfileManager,
    source_id: str,
    secret_manager: SecretManager | None = None,
) -> bool:
    secret_manager = secret_manager or SecretManager(profile_manager)
    migrate_pop_source_secrets(profile_manager, secret_manager)
    sources = load_pop_sources(profile_manager)
    found = False
    kept: list[PopSourceConfig] = []
    for source in sources:
        if source.id == source_id:
            found = True
            secret_manager.delete_secret(source.auth_ref)
        else:
            kept.append(source)
    if found:
        profile_manager.set_profile_setting(
            POP_SOURCES_SETTING,
            json.dumps([source_to_settings(item) for item in kept], indent=2, sort_keys=True),
        )
    return found


def migrate_pop_source_secrets(
    profile_manager: ProfileManager,
    secret_manager: SecretManager | None = None,
) -> int:
    secret_manager = secret_manager or SecretManager(profile_manager)
    payloads = load_pop_source_payloads(profile_manager)
    migrated = 0
    changed = False
    updated_payloads: list[dict[str, object]] = []
    for item in payloads:
        candidate = dict(item)
        password = str(candidate.get("password") or "")
        auth_ref = str(candidate.get("auth_ref") or "")
        if password and not auth_ref:
            source_id = str(candidate.get("id") or candidate.get("name") or "pop")
            candidate["auth_ref"] = secret_manager.store_pop_password(unique_slug(source_id), password)
            migrated += 1
            changed = True
        if "password" in candidate:
            candidate.pop("password", None)
            changed = True
        updated_payloads.append(candidate)

    if changed:
        profile_manager.set_profile_setting(
            POP_SOURCES_SETTING,
            json.dumps(updated_payloads, indent=2, sort_keys=True),
        )
    return migrated


def config_from_dict(payload: dict[str, object]) -> PopSourceConfig:
    name = str(payload.get("name") or "").strip()
    host = normalize_pop_host(str(payload.get("host") or "").strip())
    username = str(payload.get("username") or "").strip()
    raw_provider = str(payload.get("provider") or "").strip().lower()
    provider = normalize_pop_provider(raw_provider) if raw_provider and raw_provider != "generic" else detect_pop_provider(host)
    if not name:
        raise ValueError("POP source name is required")
    if not host:
        raise ValueError("POP host is required")
    if not username:
        raise ValueError("POP username is required")

    use_ssl = payload.get("use_ssl")
    if isinstance(use_ssl, str):
        use_ssl_value = use_ssl.strip().lower() not in {"0", "false", "no", "off"}
    elif use_ssl is None:
        use_ssl_value = True
    else:
        use_ssl_value = bool(use_ssl)

    port = int(payload.get("port") or (995 if use_ssl_value else 110))
    sync_limit = max(1, int(payload.get("sync_limit") or payload.get("limit") or 100))
    source_id = str(payload.get("id") or "").strip() or unique_slug(name)
    auth_method = str(payload.get("auth_method") or "password").strip() or "password"
    auth_ref = str(payload.get("auth_ref") or "").strip() or None

    return PopSourceConfig(
        id=unique_slug(source_id),
        name=name,
        host=host,
        port=port,
        username=username,
        password=str(payload.get("password") or ""),
        use_ssl=use_ssl_value,
        sync_limit=sync_limit,
        auth_ref=auth_ref,
        auth_method=auth_method,
        provider=provider,
    )


def source_to_settings(config: PopSourceConfig) -> dict[str, Any]:
    return {
        "id": config.id,
        "name": config.name,
        "host": config.host,
        "port": config.port,
        "username": config.username,
        "auth_ref": config.auth_ref,
        "use_ssl": config.use_ssl,
        "sync_limit": config.sync_limit,
        "auth_method": config.auth_method,
        "provider": config.provider,
    }


def unique_pop_source_id(value: str, existing_sources: list[PopSourceConfig]) -> str:
    existing = {item.id for item in existing_sources}
    base = unique_slug(value)
    candidate = base
    counter = 2
    while candidate in existing:
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


def unique_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "pop"


def normalize_pop_host(host: str) -> str:
    lowered = host.lower()
    for preset in POP_PROVIDER_PRESETS.values():
        if lowered in {alias.lower() for alias in preset.host_aliases}:
            return preset.host
    return host


def normalize_pop_provider(provider: str) -> str:
    lowered = provider.strip().lower()
    return lowered if lowered in POP_PROVIDER_PRESETS else "generic"


def detect_pop_provider(host: str) -> str:
    lowered = host.strip().lower()
    for preset in POP_PROVIDER_PRESETS.values():
        candidates = {preset.host.lower(), *(alias.lower() for alias in preset.host_aliases)}
        if lowered and lowered in candidates:
            return preset.id
    return "generic"


def probe_pop_source(
    config: PopSourceConfig,
    pop_factory: Any | None = None,
) -> PopProbeResult:
    if not config.password:
        raise ValueError("POP password/app password is not configured")
    client: PopClient | None = None
    try:
        client = pop_factory(config) if pop_factory else open_pop_client(config)
        login_pop_client(client, config)
        capabilities = read_capabilities(client)
        count, size = client.stat()
        uidls = read_uidls(client)
        return PopProbeResult(count, size, bool(uidls), min(len(uidls), 5), capabilities)
    finally:
        close_pop_client(client)


def sync_pop_source(
    db: MillieDatabase,
    config: PopSourceConfig,
    pop_factory: Any | None = None,
    sync_limit: int | None = None,
) -> PopSyncResult:
    db.init()
    if not config.password:
        raise ValueError("POP password/app password is not configured")
    effective_limit = max(1, int(sync_limit or config.sync_limit))
    source_id = db.get_or_create_source("pop3", config.name, config.source_uri())
    job_id = db.start_import_job(
        source_id,
        "pop3",
        {
            "source_config_id": config.id,
            "host": config.host,
            "port": config.port,
            "use_ssl": config.use_ssl,
            "sync_limit": effective_limit,
            "username": config.username,
            "provider": config.provider,
            "delete_policy": "never",
        },
    )
    processed = 0
    imported = 0
    duplicates = 0
    errors = 0
    seen_uidls: set[str] = set()
    client: PopClient | None = None

    try:
        client = pop_factory(config) if pop_factory else open_pop_client(config)
        login_pop_client(client, config)
        uidls = read_uidls(client)
        state = db.get_source_sync_state(source_id, "maildrop")
        seen_uidls = set(str(item) for item in state.get("seen_uidls", []) if str(item))
        mailbox_id = db.get_or_create_mailbox(source_id, "POP", role="inbox")
        synced_uidls: list[str] = []
        attempted_uidls: list[str] = []
        failed_uidls: list[str] = []
        last_error: str | None = None

        for message_number, uidl in uidls:
            if len(attempted_uidls) >= effective_limit:
                break
            if uidl in seen_uidls:
                continue
            attempted_uidls.append(uidl)
            try:
                _, lines, _ = client.retr(message_number)
                raw = pop_lines_to_message_bytes(lines)
                parsed = parse_raw_message(db, raw)
                result = db.insert_message(
                    source_id=source_id,
                    mailbox_id=mailbox_id,
                    source_uid=f"POP:{uidl}",
                    fields=parsed["fields"],
                    headers=parsed["headers"],
                    addresses=parsed["addresses"],
                    attachments=parsed["attachments"],
                    participants_text=parsed["participants_text"],
                    labels=["pop3"],
                )
                processed += 1
                if result.created:
                    imported += 1
                else:
                    duplicates += 1
                synced_uidls.append(uidl)
            except Exception as exc:  # noqa: BLE001
                errors += 1
                failed_uidls.append(uidl)
                last_error = str(exc)
                db.record_import_error(
                    job_id,
                    f"POP:{uidl}",
                    "error",
                    str(exc),
                    {"message_number": message_number, "uidl": uidl},
                )

        seen_uidls.update(synced_uidls)
        db.set_source_sync_state(
            source_id,
            "maildrop",
            {
                "seen_uidls": sorted(seen_uidls),
                "last_uidl_count": len(uidls),
                "delete_policy": "never",
                "last_attempted_uidls": attempted_uidls[-20:],
                "last_synced_uidls": synced_uidls[-20:],
                "last_failed_uidls": failed_uidls[-20:],
                "last_error": last_error,
                "last_status": "partial" if errors else "ok",
                "last_job_id": job_id,
                "last_processed_count": processed,
                "last_imported_count": imported,
                "last_duplicate_count": duplicates,
                "last_error_count": errors,
                "last_sync_limit": effective_limit,
                "last_synced_at": utc_now(),
            },
        )
    except Exception as exc:
        errors += 1
        db.record_import_error(
            job_id,
            config.source_uri(),
            "error",
            str(exc),
            {"source_config_id": config.id, "host": config.host},
        )
        db.finish_import_job(
            job_id,
            "failed",
            processed,
            errors,
            new_message_count=imported,
            duplicate_count=duplicates,
        )
        raise
    finally:
        close_pop_client(client)

    status = "completed_with_errors" if errors else "completed"
    db.touch_source_sync(source_id)
    db.finish_import_job(
        job_id,
        status,
        processed,
        errors,
        new_message_count=imported,
        duplicate_count=duplicates,
    )
    return PopSyncResult(job_id, source_id, processed, imported, duplicates, errors)


def open_pop_client(config: PopSourceConfig) -> PopClient:
    if config.use_ssl:
        return poplib.POP3_SSL(config.host, config.port, timeout=30, context=ssl.create_default_context())
    return poplib.POP3(config.host, config.port, timeout=30)


def login_pop_client(client: PopClient, config: PopSourceConfig) -> None:
    client.user(config.username)
    client.pass_(config.password)


def read_capabilities(client: PopClient) -> list[str]:
    capa = getattr(client, "capa", None)
    if not callable(capa):
        return []
    try:
        raw = capa()
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(raw, dict):
        return []
    return sorted(str(key.decode("ascii", errors="replace") if isinstance(key, bytes) else key) for key in raw)


def read_uidls(client: PopClient) -> list[tuple[int, str]]:
    response = client.uidl()
    lines: list[bytes]
    if isinstance(response, tuple):
        lines = response[1]
    else:
        lines = [response]
    uidls: list[tuple[int, str]] = []
    for line in lines:
        text = line.decode("utf-8", errors="replace") if isinstance(line, bytes) else str(line)
        parts = text.strip().split(maxsplit=1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        uidls.append((int(parts[0]), parts[1]))
    return uidls


def pop_lines_to_message_bytes(lines: list[bytes]) -> bytes:
    body = b"\r\n".join(lines)
    if not body.endswith(b"\r\n"):
        body += b"\r\n"
    return body


def close_pop_client(client: PopClient | None) -> None:
    if client is None:
        return
    try:
        client.quit()
    except Exception:  # noqa: BLE001
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass
