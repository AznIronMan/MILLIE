from __future__ import annotations

import imaplib
import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from .database import MillieDatabase
from .mailparse import parse_raw_message
from .profiles import ProfileManager
from .secrets import SecretManager, backend_from_ref


IMAP_SOURCES_SETTING = "imap.sources.v1"


class ImapClient(Protocol):
    def login(self, user: str, password: str) -> tuple[str, list[bytes]]: ...

    def list(self, directory: str = "", pattern: str = "*") -> tuple[str, list[bytes]]: ...

    def select(self, mailbox: str = "INBOX", readonly: bool = False) -> tuple[str, list[bytes]]: ...

    def response(self, code: str) -> tuple[str, list[bytes | None]]: ...

    def uid(self, command: str, *args: object) -> tuple[str, list[object]]: ...

    def close(self) -> tuple[str, list[bytes]]: ...

    def logout(self) -> tuple[str, list[bytes]]: ...


@dataclass(slots=True)
class ImapSourceConfig:
    id: str
    name: str
    host: str
    port: int
    username: str
    password: str
    use_tls: bool
    folders: list[str]
    sync_limit: int
    auth_ref: str | None = None
    auth_method: str = "password"

    def to_api(self) -> dict[str, Any]:
        secret_backend = backend_from_ref(self.auth_ref)
        return {
            "id": self.id,
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "use_tls": self.use_tls,
            "folders": self.folders,
            "sync_limit": self.sync_limit,
            "auth_method": self.auth_method,
            "password_configured": bool(self.password or self.auth_ref),
            "secret_backend": secret_backend or ("legacy-settings" if self.password else None),
        }

    def source_uri(self) -> str:
        scheme = "imaps" if self.use_tls else "imap"
        return f"{scheme}://{self.username}@{self.host}:{self.port}"


@dataclass(slots=True)
class ImapFolder:
    name: str
    delimiter: str | None
    flags: list[str]
    selectable: bool

    def to_api(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "delimiter": self.delimiter,
            "flags": self.flags,
            "selectable": self.selectable,
            "role": folder_role(self.name),
        }


@dataclass(slots=True)
class ImapSyncResult:
    import_job_id: int
    source_id: int
    processed: int
    imported: int
    duplicates: int
    errors: int
    folders: list[str]

    def to_api(self) -> dict[str, Any]:
        return {
            "import_job_id": self.import_job_id,
            "source_id": self.source_id,
            "processed": self.processed,
            "imported": self.imported,
            "duplicates": self.duplicates,
            "errors": self.errors,
            "folders": self.folders,
            "format": "imap",
        }


def load_imap_sources(profile_manager: ProfileManager) -> list[ImapSourceConfig]:
    payload = load_imap_source_payloads(profile_manager)
    sources: list[ImapSourceConfig] = []
    for item in payload:
        try:
            sources.append(config_from_dict(item))
        except ValueError:
            continue
    return sources


def load_imap_source_payloads(profile_manager: ProfileManager) -> list[dict[str, object]]:
    raw = profile_manager.get_profile_setting(IMAP_SOURCES_SETTING)
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def save_imap_source(
    profile_manager: ProfileManager,
    payload: dict[str, object],
    secret_manager: SecretManager | None = None,
) -> ImapSourceConfig:
    secret_manager = secret_manager or SecretManager(profile_manager)
    migrate_imap_source_secrets(profile_manager, secret_manager)
    existing_sources = load_imap_sources(profile_manager)
    requested_id = str(payload.get("id") or "").strip()
    existing = next((item for item in existing_sources if item.id == requested_id), None)
    source_id = requested_id or unique_imap_source_id(
        str(payload.get("name") or payload.get("host") or "imap"),
        existing_sources,
    )
    config_payload = dict(payload)
    config_payload["id"] = source_id
    incoming_password = str(config_payload.get("password") or "")
    if incoming_password:
        config_payload["auth_ref"] = secret_manager.store_imap_password(source_id, incoming_password)
    elif existing:
        config_payload["auth_ref"] = existing.auth_ref
    config = config_from_dict(config_payload)
    merged = [item for item in existing_sources if item.id != config.id]
    merged.append(config)
    profile_manager.set_profile_setting(
        IMAP_SOURCES_SETTING,
        json.dumps([source_to_settings(item) for item in merged], indent=2, sort_keys=True),
    )
    return config


def get_imap_source(
    profile_manager: ProfileManager,
    source_id: str,
    secret_manager: SecretManager | None = None,
) -> ImapSourceConfig:
    secret_manager = secret_manager or SecretManager(profile_manager)
    migrate_imap_source_secrets(profile_manager, secret_manager)
    for source in load_imap_sources(profile_manager):
        if source.id == source_id:
            source.password = source.password or secret_manager.read_secret(source.auth_ref) or ""
            return source
    raise KeyError(f"Unknown IMAP source: {source_id}")


def delete_imap_source(
    profile_manager: ProfileManager,
    source_id: str,
    secret_manager: SecretManager | None = None,
) -> bool:
    secret_manager = secret_manager or SecretManager(profile_manager)
    migrate_imap_source_secrets(profile_manager, secret_manager)
    sources = load_imap_sources(profile_manager)
    found = False
    kept: list[ImapSourceConfig] = []
    for source in sources:
        if source.id == source_id:
            found = True
            secret_manager.delete_secret(source.auth_ref)
        else:
            kept.append(source)
    if found:
        profile_manager.set_profile_setting(
            IMAP_SOURCES_SETTING,
            json.dumps([source_to_settings(item) for item in kept], indent=2, sort_keys=True),
        )
    return found


def migrate_imap_source_secrets(
    profile_manager: ProfileManager,
    secret_manager: SecretManager | None = None,
) -> int:
    secret_manager = secret_manager or SecretManager(profile_manager)
    payloads = load_imap_source_payloads(profile_manager)
    migrated = 0
    changed = False
    updated_payloads: list[dict[str, object]] = []
    for item in payloads:
        candidate = dict(item)
        password = str(candidate.get("password") or "")
        auth_ref = str(candidate.get("auth_ref") or "")
        if password and not auth_ref:
            source_id = str(candidate.get("id") or candidate.get("name") or "imap")
            candidate["auth_ref"] = secret_manager.store_imap_password(unique_slug(source_id), password)
            migrated += 1
            changed = True
        if "password" in candidate:
            candidate.pop("password", None)
            changed = True
        updated_payloads.append(candidate)

    if changed:
        profile_manager.set_profile_setting(
            IMAP_SOURCES_SETTING,
            json.dumps(updated_payloads, indent=2, sort_keys=True),
        )
    return migrated


def config_from_dict(payload: dict[str, object]) -> ImapSourceConfig:
    name = str(payload.get("name") or "").strip()
    host = str(payload.get("host") or "").strip()
    username = str(payload.get("username") or "").strip()
    if not name:
        raise ValueError("IMAP source name is required")
    if not host:
        raise ValueError("IMAP host is required")
    if not username:
        raise ValueError("IMAP username is required")

    folders_raw = payload.get("folders") or ["INBOX"]
    if isinstance(folders_raw, str):
        folders = [item.strip() for item in folders_raw.split(",") if item.strip()]
    elif isinstance(folders_raw, list):
        folders = [str(item).strip() for item in folders_raw if str(item).strip()]
    else:
        folders = ["INBOX"]

    use_tls = payload.get("use_tls")
    if isinstance(use_tls, str):
        use_tls_value = use_tls.strip().lower() not in {"0", "false", "no", "off"}
    elif use_tls is None:
        use_tls_value = True
    else:
        use_tls_value = bool(use_tls)

    port = int(payload.get("port") or (993 if use_tls_value else 143))
    sync_limit = max(1, int(payload.get("sync_limit") or payload.get("limit") or 100))
    source_id = str(payload.get("id") or "").strip() or unique_slug(name)
    auth_method = str(payload.get("auth_method") or "password").strip() or "password"
    auth_ref = str(payload.get("auth_ref") or "").strip() or None

    return ImapSourceConfig(
        id=unique_slug(source_id),
        name=name,
        host=host,
        port=port,
        username=username,
        password=str(payload.get("password") or ""),
        use_tls=use_tls_value,
        folders=folders or ["INBOX"],
        sync_limit=sync_limit,
        auth_ref=auth_ref,
        auth_method=auth_method,
    )


def source_to_settings(config: ImapSourceConfig) -> dict[str, Any]:
    return {
        "id": config.id,
        "name": config.name,
        "host": config.host,
        "port": config.port,
        "username": config.username,
        "auth_ref": config.auth_ref,
        "use_tls": config.use_tls,
        "folders": config.folders,
        "sync_limit": config.sync_limit,
        "auth_method": config.auth_method,
    }


def unique_imap_source_id(value: str, existing_sources: list[ImapSourceConfig]) -> str:
    existing = {item.id for item in existing_sources}
    base = unique_slug(value)
    candidate = base
    counter = 2
    while candidate in existing:
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


def unique_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "imap"


def sync_imap_source(
    db: MillieDatabase,
    config: ImapSourceConfig,
    imap_factory: Any | None = None,
) -> ImapSyncResult:
    db.init()
    if not config.password:
        raise ValueError("IMAP password/app password is not configured")
    source_id = db.get_or_create_source("imap", config.name, config.source_uri())
    job_id = db.start_import_job(
        source_id,
        "imap",
        {
            "source_config_id": config.id,
            "host": config.host,
            "port": config.port,
            "use_tls": config.use_tls,
            "folders": config.folders,
            "sync_limit": config.sync_limit,
            "username": config.username,
        },
    )
    processed = 0
    imported = 0
    duplicates = 0
    errors = 0
    attempted = 0
    synced_folders: list[str] = []
    client: ImapClient | None = None

    try:
        client = imap_factory(config) if imap_factory else open_imap_client(config)
        login_status, _ = client.login(config.username, config.password)
        ensure_ok(login_status, "IMAP login failed")

        for folder in config.folders:
            if attempted >= config.sync_limit:
                break
            try:
                synced = sync_imap_folder(
                    db,
                    client,
                    source_id,
                    job_id,
                    folder,
                    config.sync_limit - attempted,
                )
                attempted += synced["attempted"]
                processed += synced["processed"]
                imported += synced["imported"]
                duplicates += synced["duplicates"]
                errors += synced["errors"]
                synced_folders.append(folder)
            except Exception as exc:  # noqa: BLE001
                errors += 1
                db.record_import_error(job_id, folder, "error", str(exc), {"folder": folder})
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
        close_imap_client(client)

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
    return ImapSyncResult(job_id, source_id, processed, imported, duplicates, errors, synced_folders)


def discover_imap_folders(
    config: ImapSourceConfig,
    imap_factory: Any | None = None,
) -> list[ImapFolder]:
    if not config.password:
        raise ValueError("IMAP password/app password is not configured")
    client: ImapClient | None = None
    try:
        client = imap_factory(config) if imap_factory else open_imap_client(config)
        login_status, _ = client.login(config.username, config.password)
        ensure_ok(login_status, "IMAP login failed")
        list_status, list_data = client.list("", "*")
        ensure_ok(list_status, "Could not list IMAP folders")
        folders = [folder for item in list_data if (folder := parse_list_response(item)) is not None]
        return sorted(folders, key=lambda item: item.name.lower())
    finally:
        close_imap_client(client)


def sync_imap_folder(
    db: MillieDatabase,
    client: ImapClient,
    source_id: int,
    job_id: int,
    folder: str,
    limit: int,
) -> dict[str, int]:
    select_status, _ = client.select(quote_mailbox(folder), readonly=True)
    ensure_ok(select_status, f"Could not select IMAP folder {folder}")

    uidvalidity = read_uidvalidity(client)
    scope = f"folder:{folder}"
    state = db.get_source_sync_state(source_id, scope)
    last_uid = int(state.get("last_uid") or 0)
    if uidvalidity and state.get("uidvalidity") and str(state.get("uidvalidity")) != uidvalidity:
        last_uid = 0

    search_status, search_data = search_uids(client, last_uid)
    ensure_ok(search_status, f"Could not search IMAP folder {folder}")
    uids = parse_uid_list(search_data)

    mailbox_id = db.get_or_create_mailbox(source_id, folder, role=folder_role(folder))
    attempted = 0
    processed = 0
    imported = 0
    duplicates = 0
    errors = 0
    highest_uid = last_uid

    for uid in uids:
        if attempted >= limit:
            break
        attempted += 1
        uid_int = int(uid)
        try:
            fetch_status, fetch_data = client.uid("FETCH", uid, "(RFC822)")
            ensure_ok(fetch_status, f"Could not fetch UID {uid} from {folder}")
            raw = extract_fetch_raw(fetch_data)
            parsed = parse_raw_message(db, raw)
            result = db.insert_message(
                source_id=source_id,
                mailbox_id=mailbox_id,
                source_uid=f"{folder}:{uid}",
                fields=parsed["fields"],
                headers=parsed["headers"],
                addresses=parsed["addresses"],
                attachments=parsed["attachments"],
                participants_text=parsed["participants_text"],
            )
            processed += 1
            if result.created:
                imported += 1
            else:
                duplicates += 1
        except Exception as exc:  # noqa: BLE001
            errors += 1
            db.record_import_error(
                job_id,
                f"{folder}:{uid}",
                "error",
                str(exc),
                {"folder": folder, "uid": uid},
            )
        highest_uid = max(highest_uid, uid_int)

    db.set_source_sync_state(
        source_id,
        scope,
        {
            "uidvalidity": uidvalidity,
            "last_uid": highest_uid,
        },
    )
    return {
        "attempted": attempted,
        "processed": processed,
        "imported": imported,
        "duplicates": duplicates,
        "errors": errors,
    }


def open_imap_client(config: ImapSourceConfig) -> ImapClient:
    if config.use_tls:
        return imaplib.IMAP4_SSL(config.host, config.port)
    return imaplib.IMAP4(config.host, config.port)


def ensure_ok(status: object, message: str) -> None:
    if str(status).upper() != "OK":
        raise RuntimeError(message)


def search_uids(client: ImapClient, last_uid: int) -> tuple[str, list[object]]:
    if last_uid > 0:
        return client.uid("SEARCH", None, "UID", f"{last_uid + 1}:*")
    return client.uid("SEARCH", None, "ALL")


def parse_uid_list(data: list[object]) -> list[str]:
    raw_parts: list[bytes] = []
    for item in data:
        if isinstance(item, bytes):
            raw_parts.append(item)
        elif item is not None:
            raw_parts.append(str(item).encode("ascii", errors="ignore"))
    raw = b" ".join(raw_parts)
    values = [item.decode("ascii") for item in raw.split() if item.isdigit()]
    return sorted(values, key=int)


def read_uidvalidity(client: ImapClient) -> str | None:
    try:
        _, values = client.response("UIDVALIDITY")
    except Exception:  # noqa: BLE001
        return None
    raw = b" ".join(
        item
        for item in values
        if isinstance(item, bytes)
    )
    match = re.search(rb"\d+", raw)
    return match.group(0).decode("ascii") if match else None


def parse_list_response(item: bytes) -> ImapFolder | None:
    text = item.decode("utf-8", errors="replace")
    match = re.match(r"\((?P<flags>.*?)\)\s+(?P<delimiter>NIL|\"(?:\\.|[^\"])*\"|\S+)\s+(?P<name>.+)$", text)
    if not match:
        return None
    flags = [flag.strip() for flag in match.group("flags").split() if flag.strip()]
    delimiter = unquote_imap_atom(match.group("delimiter"))
    name = unquote_imap_atom(match.group("name").strip())
    selectable = "\\noselect" not in {flag.lower() for flag in flags}
    return ImapFolder(name=name, delimiter=delimiter, flags=flags, selectable=selectable)


def unquote_imap_atom(value: str) -> str | None:
    if value.upper() == "NIL":
        return None
    raw = value.strip()
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        return bytes(raw[1:-1], "utf-8").decode("unicode_escape")
    return raw


def extract_fetch_raw(data: list[object]) -> bytes:
    candidates: list[bytes] = []
    for item in data:
        if isinstance(item, tuple):
            candidates.extend(part for part in item if isinstance(part, bytes))
        elif isinstance(item, bytes):
            candidates.append(item)
    message_candidates = [item for item in candidates if b"\n" in item or b"\r" in item]
    if message_candidates:
        return max(message_candidates, key=len)
    if candidates:
        return max(candidates, key=len)
    raise ValueError("IMAP fetch did not include RFC822 message bytes")


def quote_mailbox(folder: str) -> str:
    if folder.upper() == "INBOX" or re.fullmatch(r"[A-Za-z0-9._/\-]+", folder):
        return folder
    escaped = folder.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def folder_role(folder: str) -> str | None:
    normalized = folder.strip().lower().replace("\\", "/")
    if normalized in {"inbox"}:
        return "inbox"
    if normalized in {"sent", "sent mail", "sent items"}:
        return "sent"
    if normalized in {"drafts"}:
        return "drafts"
    if normalized in {"trash", "deleted", "deleted items"}:
        return "trash"
    if normalized in {"junk", "spam"}:
        return "junk"
    if normalized in {"archive", "all mail"}:
        return "archive"
    return None


def close_imap_client(client: ImapClient | None) -> None:
    if client is None:
        return
    try:
        client.close()
    except Exception:  # noqa: BLE001
        pass
    try:
        client.logout()
    except Exception:  # noqa: BLE001
        pass
