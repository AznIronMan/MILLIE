from __future__ import annotations

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

from .profiles import ProfileManager
from .secrets import SecretManager, backend_from_ref


GRAPH_SOURCES_SETTING = "graph.sources.v1"
GRAPH_AUTHORIZE_BASE = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"
GRAPH_TOKEN_BASE = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"
DEFAULT_GRAPH_SCOPES = ("openid", "offline_access", "User.Read", "Mail.Read")
DEFAULT_GRAPH_REDIRECT_URI = "http://localhost:22013/api/v1/graph/oauth/callback"


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


@dataclass(slots=True)
class GraphSourceConfig:
    id: str
    name: str
    client_id: str
    tenant_id: str
    redirect_uri: str
    scopes: list[str]
    mailbox: str
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
        config_payload["token_ref"] = str(config_payload.get("token_ref") or existing.token_ref or "")
        config_payload["pending_auth_ref"] = str(
            config_payload.get("pending_auth_ref") or existing.pending_auth_ref or ""
        )
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
) -> GraphAuthRequest:
    secret_manager = secret_manager or SecretManager(profile_manager)
    source = get_graph_source(profile_manager, source_id)
    state = secrets.token_urlsafe(24)
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = pkce_challenge(code_verifier)
    secret_manager.delete_secret(source.pending_auth_ref)
    pending_payload = {
        "state": state,
        "code_verifier": code_verifier,
        "created_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "source_id": source.id,
        "redirect_uri": source.redirect_uri,
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
        authorization_url=authorization_url(updated, state, code_challenge),
        tenant_id=updated.tenant_id,
        client_id=updated.client_id,
        redirect_uri=updated.redirect_uri,
        scopes=updated.scopes,
        state=state,
        code_challenge=code_challenge,
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
        "sync_limit": config.sync_limit,
        "token_ref": config.token_ref,
        "pending_auth_ref": config.pending_auth_ref,
        "auth_method": config.auth_method,
        "provider": config.provider,
    }


def authorization_url(config: GraphSourceConfig, state: str, code_challenge: str) -> str:
    params = {
        "client_id": config.client_id,
        "response_type": "code",
        "redirect_uri": config.redirect_uri,
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
