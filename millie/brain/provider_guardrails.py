"""Guardrails for actions that can mutate upstream mail providers."""

from __future__ import annotations

from dataclasses import dataclass

from .automation import automation_level, provider_write_allowed, truthy


MANIFEST_PROVIDER_WRITE_ACTIONS = {"provider_purge_manifest"}
BROWSER_UNSUBSCRIBE_ACTIONS = {"unsubscribe_browser_execute", "unsubscribe_form_submit"}
PROVIDER_WRITE_ACTIONS = MANIFEST_PROVIDER_WRITE_ACTIONS | BROWSER_UNSUBSCRIBE_ACTIONS


@dataclass(frozen=True, slots=True)
class ProviderWriteDecision:
    action_type: str
    allowed: bool
    reason: str
    automation_level: str
    provider_write_enabled: bool
    manifest_id: str | None = None

    def audit_json(self) -> dict[str, object]:
        return {
            "action_type": self.action_type,
            "allowed": self.allowed,
            "reason": self.reason,
            "automation_level": self.automation_level,
            "provider_write_enabled": self.provider_write_enabled,
            "manifest_id": self.manifest_id,
        }


class ProviderWriteBlocked(RuntimeError):
    def __init__(self, decision: ProviderWriteDecision) -> None:
        super().__init__(decision.reason)
        self.decision = decision


def provider_write_decision(
    settings: dict[str, str],
    action_type: str,
    *,
    manifest_id: str | None = None,
) -> ProviderWriteDecision:
    """Return whether a provider-write action is allowed by local settings."""

    normalized_action = str(action_type or "").strip().lower()
    level = automation_level(settings)
    switch_enabled = truthy(settings.get("automation_provider_write_enabled"))
    if normalized_action not in PROVIDER_WRITE_ACTIONS:
        return ProviderWriteDecision(
            action_type=normalized_action,
            allowed=False,
            reason=f"Unknown provider-write action: {normalized_action}",
            automation_level=level,
            provider_write_enabled=switch_enabled,
            manifest_id=manifest_id,
        )
    if normalized_action in BROWSER_UNSUBSCRIBE_ACTIONS:
        return ProviderWriteDecision(
            action_type=normalized_action,
            allowed=False,
            reason="Browser unsubscribe execution is disabled; use manual assist.",
            automation_level=level,
            provider_write_enabled=switch_enabled,
            manifest_id=manifest_id,
        )
    if not provider_write_allowed(settings):
        return ProviderWriteDecision(
            action_type=normalized_action,
            allowed=False,
            reason=(
                "Provider writes require automation_level=provider_write and "
                "automation_provider_write_enabled=true."
            ),
            automation_level=level,
            provider_write_enabled=switch_enabled,
            manifest_id=manifest_id,
        )
    if normalized_action in MANIFEST_PROVIDER_WRITE_ACTIONS and not manifest_id:
        return ProviderWriteDecision(
            action_type=normalized_action,
            allowed=False,
            reason="Provider purge requires an explicit manifest id.",
            automation_level=level,
            provider_write_enabled=switch_enabled,
            manifest_id=manifest_id,
        )
    return ProviderWriteDecision(
        action_type=normalized_action,
        allowed=True,
        reason="Provider write allowed by explicit settings.",
        automation_level=level,
        provider_write_enabled=switch_enabled,
        manifest_id=manifest_id,
    )


def require_provider_write(
    settings: dict[str, str],
    action_type: str,
    *,
    manifest_id: str | None = None,
) -> ProviderWriteDecision:
    decision = provider_write_decision(settings, action_type, manifest_id=manifest_id)
    if not decision.allowed:
        raise ProviderWriteBlocked(decision)
    return decision
