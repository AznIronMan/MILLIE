"""Automation level guardrails."""

from __future__ import annotations

AUTOMATION_LEVELS = ("observe", "review", "auto_internal", "provider_write")
DEFAULT_AUTOMATION_LEVEL = "observe"


def automation_level(settings: dict[str, str]) -> str:
    """Return the configured automation level, defaulting to observe."""

    value = str(settings.get("automation_level") or "").strip().lower()
    return value if value in AUTOMATION_LEVELS else DEFAULT_AUTOMATION_LEVEL


def automation_level_allows(settings: dict[str, str], required_level: str) -> bool:
    """Return whether configured automation may perform a required level."""

    if required_level not in AUTOMATION_LEVELS:
        raise ValueError(f"Unknown automation level: {required_level}")
    current = automation_level(settings)
    return AUTOMATION_LEVELS.index(current) >= AUTOMATION_LEVELS.index(required_level)


def provider_write_allowed(settings: dict[str, str]) -> bool:
    """Provider writes require the highest level plus an explicit second switch."""

    if not automation_level_allows(settings, "provider_write"):
        return False
    return truthy(settings.get("automation_provider_write_enabled"))


def truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}
