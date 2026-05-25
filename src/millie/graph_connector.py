from __future__ import annotations

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .database import MillieDatabase
from .mailparse import parse_raw_message
from .profiles import ProfileManager
from .secrets import SecretManager, backend_from_ref


GRAPH_SOURCES_SETTING = "graph.sources.v1"
GRAPH_AUTHORIZE_BASE = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"
GRAPH_TOKEN_BASE = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"
DEFAULT_GRAPH_SCOPES = ("openid", "offline_access", "User.Read", "Mail.Read")
DEFAULT_GRAPH_REDIRECT_URI = "http://localhost:22013/api/v1/graph/oauth/callback"
TOKEN_REFRESH_SKEW = timedelta(minutes=5)


class GraphHttpClient(Protocol):
    def post_form(self, url: str, data: Mapping[str, str]) -> dict[str, Any]: ...

    def get_json(self, url: str, access_token: str) -> dict[str, Any]: ...

    def get_bytes(self, url: str, access_token: str) -> bytes: ...


@dataclass(frozen=True, slots=True)
class GraphProviderPreset:
    id: str
    display_name: str
    authority_host: str
    api_base_url: str
    default_tenant: str
    default_scopes: tuple[str, ...]

    def to_api(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "authority_host": self.authority_host,
            "api_base_url": self.api_base_url,
            "default_tenant": self.default_tenant,
            "default_scopes": list(self.default_scopes),
            "auth_flow": "authorization_code_pkce",
        }


GRAPH_PROVIDER_PRESETS = {
    "microsoft-graph": GraphProviderPreset(
        id="microsoft-graph",
        display_name="Microsoft Graph / Exchange Online",
        authority_host="https://login.microsoftonline.com",
        api_base_url=GRAPH_API_BASE,
        default_tenant="common",
        default_scopes=DEFAULT_GRAPH_SCOPES,
    )
}


@dataclass(frozen=True, slots=True)
class GraphFolderSelection:
    id: str
    display_name: str
    path: str

    def to_api(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "path": self.path,
        }


@dataclass(slots=True)
class GraphSourceConfig:
    id: str
    name: str
    client_id: str
    tenant_id: str
    redirect_uri: str
    scopes: list[str]
    mailbox: str
    folders: list[GraphFolderSelection]
    sync_limit: int
    token_ref: str | None = None
    pending_auth_ref: str | None = None
    auth_method: str = "authorization_code_pkce"
    provider: str = "microsoft-graph"

    def to_api(self) -> dict[str, Any]:
        token_backend = backend_from_ref(self.token_ref)
        return {
            "id": self.id,
            "name": self.name,
            "client_id": self.client_id,
            "tenant_id": self.tenant_id,
            "redirect_uri": self.redirect_uri,
            "scopes": self.scopes,
            "mailbox": self.mailbox,
            "folders": [folder.to_api() for folder in self.folders],
            "sync_limit": self.sync_limit,
            "auth_method": self.auth_method,
            "provider": self.provider,
            "token_configured": bool(self.token_ref),
            "pending_auth_configured": bool(self.pending_auth_ref),
            "secret_backend": token_backend,
        }


@dataclass(frozen=True, slots=True)
class GraphAuthRequest:
    authorization_url: str
    tenant_id: str
    client_id: str
    redirect_uri: str
    scopes: list[str]
    state: str
    code_challenge: str
    code_challenge_method: str = "S256"

    def to_api(self) -> dict[str, Any]:
        return {
            "authorization_url": self.authorization_url,
            "tenant_id": self.tenant_id,
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scopes": self.scopes,
            "state": self.state,
            "code_challenge": self.code_challenge,
            "code_challenge_method": self.code_challenge_method,
        }


@dataclass(frozen=True, slots=True)
class GraphAuthCompletion:
    source: GraphSourceConfig
    token_type: str
    scopes: list[str]
    expires_at: str | None

    def to_api(self) -> dict[str, Any]:
        return {
            "source": self.source.to_api(),
            "token_type": self.token_type,
            "scopes": self.scopes,
            "expires_at": self.expires_at,
            "token_configured": bool(self.source.token_ref),
        }


@dataclass(frozen=True, slots=True)
class GraphFolderSummary:
    id: str
    display_name: str
    path: str
    parent_folder_id: str | None
    total_item_count: int | None
    unread_item_count: int | None
    child_folder_count: int | None

    def to_api(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "path": self.path,
            "parent_folder_id": self.parent_folder_id,
            "total_item_count": self.total_item_count,
            "unread_item_count": self.unread_item_count,
            "child_folder_count": self.child_folder_count,
            "role": graph_folder_role(self.path),
        }

    def to_selection(self) -> GraphFolderSelection:
        return GraphFolderSelection(id=self.id, display_name=self.display_name, path=self.path)


@dataclass(frozen=True, slots=True)
class GraphProbeResult:
    source_id: str
    mailbox: str
    display_name: str | None
    user_principal_name: str | None
    mail: str | None
    folder_count: int
    folders: list[GraphFolderSummary]
    token_refreshed: bool

    def to_api(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "mailbox": self.mailbox,
            "display_name": self.display_name,
            "user_principal_name": self.user_principal_name,
            "mail": self.mail,
            "folder_count": self.folder_count,
            "folders": [folder.to_api() for folder in self.folders],
            "token_refreshed": self.token_refreshed,
            "read_only": True,
        }


@dataclass(frozen=True, slots=True)
class GraphSyncResult:
    import_job_id: int
    source_id: int
    processed: int
    imported: int
    duplicates: int
    errors: int
    folders: list[str]
    token_refreshed: bool

    def to_api(self) -> dict[str, Any]:
        return {
            "import_job_id": self.import_job_id,
            "source_id": self.source_id,
            "processed": self.processed,
            "imported": self.imported,
            "duplicates": self.duplicates,
            "errors": self.errors,
            "folders": self.folders,
            "token_refreshed": self.token_refreshed,
            "format": "graph",
            "read_only": True,
        }


@dataclass(slots=True)
class UrllibGraphHttpClient:
    timeout_seconds: int = 30

    def post_form(self, url: str, data: Mapping[str, str]) -> dict[str, Any]:
        request = Request(
            url,
            data=urlencode(data).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        return self.open_json(request)

    def get_json(self, url: str, access_token: str) -> dict[str, Any]:
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {access_token}",
            },
            method="GET",
        )
        return self.open_json(request)

    def get_bytes(self, url: str, access_token: str) -> bytes:
        request = Request(
            url,
            headers={
                "Accept": "message/rfc822",
                "Authorization": f"Bearer {access_token}",
            },
            method="GET",
        )
        return self.open_bytes(request)

    def open_bytes(self, request: Request) -> bytes:
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
                return response.read()
        except HTTPError as exc:
            raw = exc.read()
            detail = graph_error_detail(raw) or exc.reason or "request failed"
            raise RuntimeError(f"Microsoft Graph request failed ({exc.code}): {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"Microsoft Graph request failed: {exc.reason}") from exc

    def open_json(self, request: Request) -> dict[str, Any]:
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
                raw = response.read()
        except HTTPError as exc:
            raw = exc.read()
            detail = graph_error_detail(raw) or exc.reason or "request failed"
            raise RuntimeError(f"Microsoft Graph request failed ({exc.code}): {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"Microsoft Graph request failed: {exc.reason}") from exc
        if not raw:
            return {}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError("Microsoft Graph returned a non-JSON response") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Microsoft Graph returned an unexpected JSON response")
        return payload


def list_graph_provider_presets() -> list[GraphProviderPreset]:
    return list(GRAPH_PROVIDER_PRESETS.values())


def load_graph_sources(profile_manager: ProfileManager) -> list[GraphSourceConfig]:
    sources: list[GraphSourceConfig] = []
    for item in load_graph_source_payloads(profile_manager):
        try:
            sources.append(config_from_dict(item))
        except ValueError:
            continue
    return sources


def load_graph_source_payloads(profile_manager: ProfileManager) -> list[dict[str, object]]:
    raw = profile_manager.get_profile_setting(GRAPH_SOURCES_SETTING)
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def save_graph_source(
    profile_manager: ProfileManager,
    payload: dict[str, object],
) -> GraphSourceConfig:
    existing_sources = load_graph_sources(profile_manager)
    requested_id = str(payload.get("id") or "").strip()
    existing = next((item for item in existing_sources if item.id == requested_id), None)
    source_id = requested_id or unique_graph_source_id(
        str(payload.get("name") or payload.get("mailbox") or "graph"),
        existing_sources,
    )
    config_payload = dict(payload)
    config_payload["id"] = source_id
    if existing:
        if "token_ref" not in config_payload:
            config_payload["token_ref"] = existing.token_ref or ""
        if "pending_auth_ref" not in config_payload:
            config_payload["pending_auth_ref"] = existing.pending_auth_ref or ""
    config = config_from_dict(config_payload)
    merged = [item for item in existing_sources if item.id != config.id]
    merged.append(config)
    profile_manager.set_profile_setting(
        GRAPH_SOURCES_SETTING,
        json.dumps([source_to_settings(item) for item in merged], indent=2, sort_keys=True),
    )
    return config


def get_graph_source(profile_manager: ProfileManager, source_id: str) -> GraphSourceConfig:
    for source in load_graph_sources(profile_manager):
        if source.id == source_id:
            return source
    raise KeyError(f"Unknown Microsoft Graph source: {source_id}")


def delete_graph_source(
    profile_manager: ProfileManager,
    source_id: str,
    secret_manager: SecretManager | None = None,
) -> bool:
    secret_manager = secret_manager or SecretManager(profile_manager)
    sources = load_graph_sources(profile_manager)
    found = False
    kept: list[GraphSourceConfig] = []
    for source in sources:
        if source.id == source_id:
            found = True
            secret_manager.delete_secret(source.token_ref)
            secret_manager.delete_secret(source.pending_auth_ref)
        else:
            kept.append(source)
    if found:
        profile_manager.set_profile_setting(
            GRAPH_SOURCES_SETTING,
            json.dumps([source_to_settings(item) for item in kept], indent=2, sort_keys=True),
        )
    return found


def create_graph_authorization_request(
    profile_manager: ProfileManager,
    source_id: str,
    secret_manager: SecretManager | None = None,
    redirect_uri: str | None = None,
) -> GraphAuthRequest:
    secret_manager = secret_manager or SecretManager(profile_manager)
    source = get_graph_source(profile_manager, source_id)
    effective_redirect_uri = (redirect_uri or source.redirect_uri).strip()
    if not effective_redirect_uri:
        raise ValueError("Microsoft Graph redirect_uri is required")
    state = secrets.token_urlsafe(24)
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = pkce_challenge(code_verifier)
    secret_manager.delete_secret(source.pending_auth_ref)
    pending_payload = {
        "state": state,
        "code_verifier": code_verifier,
        "created_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "source_id": source.id,
        "redirect_uri": effective_redirect_uri,
        "scopes": source.scopes,
    }
    pending_ref = secret_manager.store_graph_pending_auth(
        source.id,
        state,
        json.dumps(pending_payload, sort_keys=True),
    )
    updated = save_graph_source(
        profile_manager,
        {
            **source_to_settings(source),
            "pending_auth_ref": pending_ref,
        },
    )
    return GraphAuthRequest(
        authorization_url=authorization_url(updated, state, code_challenge, effective_redirect_uri),
        tenant_id=updated.tenant_id,
        client_id=updated.client_id,
        redirect_uri=effective_redirect_uri,
        scopes=updated.scopes,
        state=state,
        code_challenge=code_challenge,
    )


def complete_graph_authorization(
    profile_manager: ProfileManager,
    code: str,
    state: str,
    secret_manager: SecretManager | None = None,
    http_client: GraphHttpClient | None = None,
) -> GraphAuthCompletion:
    if not code.strip():
        raise ValueError("Microsoft Graph authorization code is required")
    if not state.strip():
        raise ValueError("Microsoft Graph state is required")
    secret_manager = secret_manager or SecretManager(profile_manager)
    http_client = http_client or UrllibGraphHttpClient()
    source, pending = find_pending_graph_authorization(profile_manager, state, secret_manager)
    code_verifier = str(pending.get("code_verifier") or "")
    redirect_uri = str(pending.get("redirect_uri") or source.redirect_uri)
    scopes = parse_scope_list(pending.get("scopes"), source.scopes)
    if not code_verifier:
        raise ValueError("Stored Microsoft Graph PKCE verifier is missing")

    token_response = http_client.post_form(
        token_endpoint(source.tenant_id),
        {
            "client_id": source.client_id,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
            "scope": " ".join(scopes),
        },
    )
    token_payload = token_response_to_secret_payload(source, token_response, scopes)
    token_ref = secret_manager.store_graph_token_payload(
        source.id,
        json.dumps(token_payload, sort_keys=True),
    )
    secret_manager.delete_secret(source.pending_auth_ref)
    updated = save_graph_source(
        profile_manager,
        {
            **source_to_settings(source),
            "token_ref": token_ref,
            "pending_auth_ref": "",
        },
    )
    return GraphAuthCompletion(
        source=updated,
        token_type=str(token_payload.get("token_type") or "Bearer"),
        scopes=parse_scope_list(token_payload.get("scope"), scopes),
        expires_at=string_or_none(token_payload.get("expires_at")),
    )


def probe_graph_source(
    profile_manager: ProfileManager,
    source_id: str,
    secret_manager: SecretManager | None = None,
    http_client: GraphHttpClient | None = None,
) -> GraphProbeResult:
    secret_manager = secret_manager or SecretManager(profile_manager)
    http_client = http_client or UrllibGraphHttpClient()
    source = get_graph_source(profile_manager, source_id)
    access_token, token_refreshed = graph_access_token(
        profile_manager,
        source,
        secret_manager,
        http_client,
    )
    mailbox_path = graph_mailbox_path(source.mailbox)
    me = http_client.get_json(
        f"{GRAPH_API_BASE}/{mailbox_path}?{urlencode({'$select': 'id,displayName,userPrincipalName,mail'})}",
        access_token,
    )
    folder_payload = http_client.get_json(
        f"{GRAPH_API_BASE}/{mailbox_path}/mailFolders?"
        f"{urlencode({'$top': '10', '$select': 'id,displayName,totalItemCount,unreadItemCount,childFolderCount'})}",
        access_token,
    )
    folders_raw = folder_payload.get("value") if isinstance(folder_payload.get("value"), list) else []
    folders = [graph_folder_from_payload(item) for item in folders_raw if isinstance(item, dict)]
    return GraphProbeResult(
        source_id=source.id,
        mailbox=source.mailbox,
        display_name=string_or_none(me.get("displayName")),
        user_principal_name=string_or_none(me.get("userPrincipalName")),
        mail=string_or_none(me.get("mail")),
        folder_count=len(folders),
        folders=folders,
        token_refreshed=token_refreshed,
    )


def discover_graph_folders(
    profile_manager: ProfileManager,
    source_id: str,
    secret_manager: SecretManager | None = None,
    http_client: GraphHttpClient | None = None,
) -> list[GraphFolderSummary]:
    secret_manager = secret_manager or SecretManager(profile_manager)
    http_client = http_client or UrllibGraphHttpClient()
    source = get_graph_source(profile_manager, source_id)
    access_token, _ = graph_access_token(profile_manager, source, secret_manager, http_client)
    mailbox_path = graph_mailbox_path(source.mailbox)
    root_url = (
        f"{GRAPH_API_BASE}/{mailbox_path}/mailFolders?"
        f"{urlencode({'$top': '100', '$select': 'id,parentFolderId,displayName,totalItemCount,unreadItemCount,childFolderCount'})}"
    )
    return graph_folder_tree(http_client, access_token, root_url, mailbox_path=mailbox_path)


def sync_graph_source(
    db: MillieDatabase,
    profile_manager: ProfileManager,
    source_id: str,
    secret_manager: SecretManager | None = None,
    http_client: GraphHttpClient | None = None,
    folders: list[GraphFolderSelection] | None = None,
    sync_limit: int | None = None,
) -> GraphSyncResult:
    db.init()
    secret_manager = secret_manager or SecretManager(profile_manager)
    http_client = http_client or UrllibGraphHttpClient()
    source = get_graph_source(profile_manager, source_id)
    sync_folders = folders or source.folders
    if not sync_folders:
        raise ValueError("Select at least one Microsoft Graph folder before syncing")
    effective_limit = max(1, int(sync_limit or source.sync_limit))
    access_token, token_refreshed = graph_access_token(profile_manager, source, secret_manager, http_client)
    db_source_id = db.get_or_create_source("graph", source.name, graph_source_uri(source))
    job_id = db.start_import_job(
        db_source_id,
        "graph",
        {
            "source_config_id": source.id,
            "tenant_id": source.tenant_id,
            "mailbox": source.mailbox,
            "folders": [folder.to_api() for folder in sync_folders],
            "sync_limit": effective_limit,
            "sync_mode": "seen-message-id-mvp",
            "read_only": True,
        },
    )
    processed = 0
    imported = 0
    duplicates = 0
    errors = 0
    attempted = 0
    synced_folders: list[str] = []

    try:
        for folder in sync_folders:
            if attempted >= effective_limit:
                break
            try:
                synced = sync_graph_folder(
                    db,
                    http_client,
                    access_token,
                    source,
                    db_source_id,
                    job_id,
                    folder,
                    effective_limit - attempted,
                )
                attempted += synced["attempted"]
                processed += synced["processed"]
                imported += synced["imported"]
                duplicates += synced["duplicates"]
                errors += synced["errors"]
                synced_folders.append(folder.path)
            except Exception as exc:  # noqa: BLE001
                errors += 1
                db.record_import_error(
                    job_id,
                    folder.path,
                    "error",
                    str(exc),
                    {"folder_id": folder.id, "folder_path": folder.path},
                )
    except Exception as exc:
        errors += 1
        db.record_import_error(
            job_id,
            graph_source_uri(source),
            "error",
            str(exc),
            {"source_config_id": source.id, "mailbox": source.mailbox},
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

    status = "completed_with_errors" if errors else "completed"
    db.touch_source_sync(db_source_id)
    db.finish_import_job(
        job_id,
        status,
        processed,
        errors,
        new_message_count=imported,
        duplicate_count=duplicates,
    )
    return GraphSyncResult(
        import_job_id=job_id,
        source_id=db_source_id,
        processed=processed,
        imported=imported,
        duplicates=duplicates,
        errors=errors,
        folders=synced_folders,
        token_refreshed=token_refreshed,
    )


def config_from_dict(payload: dict[str, object]) -> GraphSourceConfig:
    name = str(payload.get("name") or "").strip()
    client_id = str(payload.get("client_id") or payload.get("clientId") or "").strip()
    tenant_id = str(payload.get("tenant_id") or payload.get("tenantId") or "common").strip() or "common"
    redirect_uri = str(
        payload.get("redirect_uri")
        or payload.get("redirectUri")
        or DEFAULT_GRAPH_REDIRECT_URI
    ).strip()
    mailbox = str(payload.get("mailbox") or "me").strip() or "me"
    provider = normalize_graph_provider(str(payload.get("provider") or "microsoft-graph"))
    if not name:
        raise ValueError("Microsoft Graph source name is required")
    if not client_id:
        raise ValueError("Microsoft Graph client_id is required")
    if not redirect_uri:
        raise ValueError("Microsoft Graph redirect_uri is required")

    scopes_raw = payload.get("scopes") or payload.get("scope") or list(DEFAULT_GRAPH_SCOPES)
    if isinstance(scopes_raw, str):
        scopes = [item.strip() for item in scopes_raw.replace(",", " ").split() if item.strip()]
    elif isinstance(scopes_raw, list):
        scopes = [str(item).strip() for item in scopes_raw if str(item).strip()]
    else:
        scopes = list(DEFAULT_GRAPH_SCOPES)
    scopes = dedupe_scopes(scopes or list(DEFAULT_GRAPH_SCOPES))
    folders = parse_graph_folder_selections(payload.get("folders"))
    sync_limit = max(1, int(payload.get("sync_limit") or payload.get("limit") or 100))
    source_id = str(payload.get("id") or "").strip() or unique_slug(name)
    token_ref = str(payload.get("token_ref") or "").strip() or None
    pending_auth_ref = str(payload.get("pending_auth_ref") or "").strip() or None
    auth_method = str(payload.get("auth_method") or "authorization_code_pkce").strip()

    return GraphSourceConfig(
        id=unique_slug(source_id),
        name=name,
        client_id=client_id,
        tenant_id=tenant_id,
        redirect_uri=redirect_uri,
        scopes=scopes,
        mailbox=mailbox,
        folders=folders,
        sync_limit=sync_limit,
        token_ref=token_ref,
        pending_auth_ref=pending_auth_ref,
        auth_method=auth_method,
        provider=provider,
    )


def source_to_settings(config: GraphSourceConfig) -> dict[str, Any]:
    return {
        "id": config.id,
        "name": config.name,
        "client_id": config.client_id,
        "tenant_id": config.tenant_id,
        "redirect_uri": config.redirect_uri,
        "scopes": config.scopes,
        "mailbox": config.mailbox,
        "folders": [folder.to_api() for folder in config.folders],
        "sync_limit": config.sync_limit,
        "token_ref": config.token_ref,
        "pending_auth_ref": config.pending_auth_ref,
        "auth_method": config.auth_method,
        "provider": config.provider,
    }


def authorization_url(
    config: GraphSourceConfig,
    state: str,
    code_challenge: str,
    redirect_uri: str | None = None,
) -> str:
    params = {
        "client_id": config.client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri or config.redirect_uri,
        "response_mode": "query",
        "scope": " ".join(config.scopes),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{GRAPH_AUTHORIZE_BASE.format(tenant=config.tenant_id)}?{urlencode(params)}"


def token_endpoint(tenant_id: str) -> str:
    return GRAPH_TOKEN_BASE.format(tenant=tenant_id)


def pkce_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def find_pending_graph_authorization(
    profile_manager: ProfileManager,
    state: str,
    secret_manager: SecretManager,
) -> tuple[GraphSourceConfig, dict[str, Any]]:
    for source in load_graph_sources(profile_manager):
        raw = secret_manager.read_secret(source.pending_auth_ref)
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if str(payload.get("state") or "") == state:
            return source, payload
    raise ValueError("No pending Microsoft Graph authorization matches this state")


def token_response_to_secret_payload(
    source: GraphSourceConfig,
    response: dict[str, Any],
    requested_scopes: list[str],
    existing_refresh_token: str | None = None,
) -> dict[str, Any]:
    access_token = str(response.get("access_token") or "")
    if not access_token:
        raise ValueError("Microsoft Graph token response did not include an access token")
    received_at = datetime.now(UTC).replace(microsecond=0)
    expires_in = int(response.get("expires_in") or 0)
    expires_at = received_at + timedelta(seconds=expires_in) if expires_in > 0 else None
    refresh_token = str(response.get("refresh_token") or existing_refresh_token or "")
    payload: dict[str, Any] = {
        "source_id": source.id,
        "tenant_id": source.tenant_id,
        "client_id": source.client_id,
        "mailbox": source.mailbox,
        "received_at": received_at.isoformat(),
        "expires_at": expires_at.isoformat() if expires_at else None,
        "token_type": str(response.get("token_type") or "Bearer"),
        "scope": str(response.get("scope") or " ".join(requested_scopes)),
        "access_token": access_token,
    }
    if refresh_token:
        payload["refresh_token"] = refresh_token
    if response.get("id_token"):
        payload["id_token"] = str(response["id_token"])
    return payload


def graph_access_token(
    profile_manager: ProfileManager,
    source: GraphSourceConfig,
    secret_manager: SecretManager,
    http_client: GraphHttpClient,
) -> tuple[str, bool]:
    raw = secret_manager.read_secret(source.token_ref)
    if not raw:
        raise ValueError("Microsoft Graph source has no token yet; connect the source first")
    try:
        token_payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("Stored Microsoft Graph token payload is not valid JSON") from exc
    if not isinstance(token_payload, dict):
        raise ValueError("Stored Microsoft Graph token payload has an unexpected shape")
    access_token = str(token_payload.get("access_token") or "")
    if access_token and not token_needs_refresh(token_payload):
        return access_token, False

    refresh_token = str(token_payload.get("refresh_token") or "")
    if not refresh_token:
        raise ValueError("Microsoft Graph token is expired and no refresh token is stored; reconnect the source")
    response = http_client.post_form(
        token_endpoint(source.tenant_id),
        {
            "client_id": source.client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": " ".join(source.scopes),
        },
    )
    refreshed_payload = token_response_to_secret_payload(
        source,
        response,
        source.scopes,
        existing_refresh_token=refresh_token,
    )
    token_ref = secret_manager.store_graph_token_payload(
        source.id,
        json.dumps(refreshed_payload, sort_keys=True),
    )
    if token_ref != source.token_ref:
        save_graph_source(
            profile_manager,
            {
                **source_to_settings(source),
                "token_ref": token_ref,
            },
        )
    return str(refreshed_payload["access_token"]), True


def graph_folder_tree(
    http_client: GraphHttpClient,
    access_token: str,
    start_url: str,
    mailbox_path: str,
    parent_path: str = "",
    seen: set[str] | None = None,
) -> list[GraphFolderSummary]:
    seen = seen or set()
    folders: list[GraphFolderSummary] = []
    for item in graph_collection_items(http_client, access_token, start_url):
        if not isinstance(item, dict):
            continue
        folder = graph_folder_from_payload(item, parent_path)
        if not folder.id or folder.id in seen:
            continue
        seen.add(folder.id)
        folders.append(folder)
        if (folder.child_folder_count or 0) > 0:
            child_url = (
                f"{GRAPH_API_BASE}/{mailbox_path}/mailFolders/{quote(folder.id, safe='')}/childFolders?"
                f"{urlencode({'$top': '100', '$select': 'id,parentFolderId,displayName,totalItemCount,unreadItemCount,childFolderCount'})}"
            )
            folders.extend(graph_folder_tree(http_client, access_token, child_url, mailbox_path, folder.path, seen))
    return folders


def graph_collection_items(
    http_client: GraphHttpClient,
    access_token: str,
    start_url: str,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    url: str | None = start_url
    while url:
        payload = http_client.get_json(url, access_token)
        raw_items = payload.get("value") if isinstance(payload.get("value"), list) else []
        items.extend(item for item in raw_items if isinstance(item, dict))
        url = string_or_none(payload.get("@odata.nextLink"))
    return items


def sync_graph_folder(
    db: MillieDatabase,
    http_client: GraphHttpClient,
    access_token: str,
    source: GraphSourceConfig,
    db_source_id: int,
    job_id: int,
    folder: GraphFolderSelection,
    limit: int,
) -> dict[str, int]:
    mailbox_path = graph_mailbox_path(source.mailbox)
    folder_id = quote(folder.id, safe="")
    messages_url = (
        f"{GRAPH_API_BASE}/{mailbox_path}/mailFolders/{folder_id}/messages?"
        f"{urlencode({'$top': str(min(limit, 50)), '$select': 'id,internetMessageId,isRead,categories,receivedDateTime,sentDateTime', '$orderby': 'receivedDateTime desc'})}"
    )
    mailbox_id = db.get_or_create_mailbox(db_source_id, folder.path, role=graph_folder_role(folder.path))
    scope = f"folder:{folder.id}"
    state = db.get_source_sync_state(db_source_id, scope)
    seen_message_ids = {str(item) for item in state.get("seen_message_ids", []) if str(item)}
    synced_message_ids: list[str] = []
    attempted = 0
    processed = 0
    imported = 0
    duplicates = 0
    errors = 0
    next_url: str | None = messages_url
    scanned = 0
    scan_limit = max(limit * 5, 100)

    while next_url and attempted < limit and scanned < scan_limit:
        payload = http_client.get_json(next_url, access_token)
        raw_messages = payload.get("value") if isinstance(payload.get("value"), list) else []
        for item in raw_messages:
            if attempted >= limit or scanned >= scan_limit:
                break
            if not isinstance(item, dict):
                continue
            scanned += 1
            message_id = str(item.get("id") or "")
            if not message_id or message_id in seen_message_ids:
                continue
            attempted += 1
            try:
                raw = http_client.get_bytes(
                    f"{GRAPH_API_BASE}/{mailbox_path}/mailFolders/{folder_id}/messages/{quote(message_id, safe='')}/$value",
                    access_token,
                )
                parsed = parse_raw_message(db, raw)
                flags = ["\\Seen"] if bool(item.get("isRead")) else []
                labels = parse_graph_categories(item.get("categories"))
                result = db.insert_message(
                    source_id=db_source_id,
                    mailbox_id=mailbox_id,
                    source_uid=f"GRAPH:{folder.id}:{message_id}",
                    fields=parsed["fields"],
                    headers=parsed["headers"],
                    addresses=parsed["addresses"],
                    attachments=parsed["attachments"],
                    participants_text=parsed["participants_text"],
                    flags=flags,
                    labels=labels,
                )
                processed += 1
                if result.created:
                    imported += 1
                else:
                    duplicates += 1
                synced_message_ids.append(message_id)
            except Exception as exc:  # noqa: BLE001
                errors += 1
                db.record_import_error(
                    job_id,
                    f"{folder.path}:{message_id}",
                    "error",
                    str(exc),
                    {"folder_id": folder.id, "message_id": message_id},
                )
        next_url = string_or_none(payload.get("@odata.nextLink"))

    seen_message_ids.update(synced_message_ids)
    db.set_source_sync_state(
        db_source_id,
        scope,
        {
            "seen_message_ids": sorted(seen_message_ids),
            "last_seen_count": len(seen_message_ids),
            "last_scan_count": scanned,
            "folder_id": folder.id,
            "folder_path": folder.path,
            "sync_mode": "seen-message-id-mvp",
        },
    )
    return {
        "attempted": attempted,
        "processed": processed,
        "imported": imported,
        "duplicates": duplicates,
        "errors": errors,
    }


def token_needs_refresh(token_payload: dict[str, Any]) -> bool:
    expires_at = string_or_none(token_payload.get("expires_at"))
    if not expires_at:
        return False
    try:
        parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed <= datetime.now(UTC) + TOKEN_REFRESH_SKEW


def parse_scope_list(value: object, fallback: list[str]) -> list[str]:
    if isinstance(value, str):
        scopes = [item.strip() for item in value.replace(",", " ").split() if item.strip()]
    elif isinstance(value, list):
        scopes = [str(item).strip() for item in value if str(item).strip()]
    else:
        scopes = []
    return dedupe_scopes(scopes or fallback)


def parse_graph_folder_selections(value: object) -> list[GraphFolderSelection]:
    if not isinstance(value, list):
        return []
    folders: list[GraphFolderSelection] = []
    seen: set[str] = set()
    for item in value:
        if isinstance(item, dict):
            folder_id = str(item.get("id") or "").strip()
            display_name = str(item.get("display_name") or item.get("displayName") or item.get("name") or "").strip()
            path = str(item.get("path") or display_name or folder_id).strip()
        else:
            folder_id = str(item or "").strip()
            display_name = folder_id
            path = folder_id
        if not folder_id or folder_id in seen:
            continue
        folders.append(GraphFolderSelection(id=folder_id, display_name=display_name or path, path=path or display_name))
        seen.add(folder_id)
    return folders


def parse_graph_categories(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    seen: set[str] = set()
    categories: list[str] = []
    for item in value:
        category = str(item).strip()
        if category and category not in seen:
            categories.append(category)
            seen.add(category)
    return categories


def graph_mailbox_path(mailbox: str) -> str:
    cleaned = mailbox.strip()
    if not cleaned or cleaned == "me":
        return "me"
    return f"users/{quote(cleaned, safe='')}"


def graph_folder_from_payload(payload: dict[str, Any], parent_path: str = "") -> GraphFolderSummary:
    display_name = str(payload.get("displayName") or "")
    path = "/".join(item for item in [parent_path.strip("/"), display_name.strip("/")] if item) or display_name
    return GraphFolderSummary(
        id=str(payload.get("id") or ""),
        display_name=display_name,
        path=path,
        parent_folder_id=string_or_none(payload.get("parentFolderId")),
        total_item_count=optional_int(payload.get("totalItemCount")),
        unread_item_count=optional_int(payload.get("unreadItemCount")),
        child_folder_count=optional_int(payload.get("childFolderCount")),
    )


def graph_folder_role(path: str) -> str | None:
    normalized = path.strip().lower().replace("\\", "/").rsplit("/", 1)[-1]
    if normalized in {"inbox"}:
        return "inbox"
    if normalized in {"sent", "sent items", "sent mail"}:
        return "sent"
    if normalized == "drafts":
        return "drafts"
    if normalized in {"deleted items", "deleted", "trash"}:
        return "trash"
    if normalized in {"junk email", "junk", "spam"}:
        return "junk"
    if normalized in {"archive", "all mail"}:
        return "archive"
    if normalized == "outbox":
        return "outbox"
    return None


def graph_source_uri(source: GraphSourceConfig) -> str:
    return f"graph://{source.tenant_id}/{source.mailbox}/{source.id}"


def optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def string_or_none(value: object) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def graph_error_detail(raw: bytes) -> str | None:
    if not raw:
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return raw.decode("utf-8", errors="replace").strip() or None
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message") or error.get("error_description")
        code = error.get("code")
        if message and code:
            return f"{code}: {message}"
        if message:
            return str(message)
    description = payload.get("error_description") or payload.get("error")
    return str(description) if description else None


def dedupe_scopes(scopes: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for scope in scopes:
        if scope not in seen:
            deduped.append(scope)
            seen.add(scope)
    return deduped


def normalize_graph_provider(provider: str) -> str:
    lowered = provider.strip().lower()
    return lowered if lowered in GRAPH_PROVIDER_PRESETS else "microsoft-graph"


def unique_graph_source_id(value: str, existing_sources: list[GraphSourceConfig]) -> str:
    existing = {item.id for item in existing_sources}
    base = unique_slug(value)
    candidate = base
    counter = 2
    while candidate in existing:
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


def unique_slug(value: str) -> str:
    slug = "-".join(
        item
        for item in "".join(ch.lower() if ch.isalnum() else "-" for ch in value).split("-")
        if item
    )
    return slug or "graph"
