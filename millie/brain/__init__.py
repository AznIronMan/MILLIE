"""MILLIE brain helpers for safe sorting and learning."""

from .automation import (
    AUTOMATION_LEVELS,
    automation_level,
    automation_level_allows,
    provider_write_allowed,
)
from .observe import (
    ClassificationSuggestion,
    SortCandidate,
    UnsubscribeSuggestion,
    classify_candidate,
    extract_unsubscribe_suggestions,
)
from .provider_guardrails import (
    ProviderWriteBlocked,
    ProviderWriteDecision,
    provider_write_decision,
    require_provider_write,
)
from .retention import (
    HeldMessage,
    RetentionCandidate,
    RetentionPolicy,
    RetentionStatus,
    retention_candidate,
    retention_status,
)

__all__ = [
    "AUTOMATION_LEVELS",
    "ClassificationSuggestion",
    "HeldMessage",
    "RetentionCandidate",
    "RetentionPolicy",
    "RetentionStatus",
    "SortCandidate",
    "UnsubscribeSuggestion",
    "ProviderWriteBlocked",
    "ProviderWriteDecision",
    "automation_level",
    "automation_level_allows",
    "classify_candidate",
    "extract_unsubscribe_suggestions",
    "provider_write_allowed",
    "provider_write_decision",
    "require_provider_write",
    "retention_candidate",
    "retention_status",
]
