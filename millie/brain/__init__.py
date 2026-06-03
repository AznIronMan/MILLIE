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

__all__ = [
    "AUTOMATION_LEVELS",
    "ClassificationSuggestion",
    "SortCandidate",
    "UnsubscribeSuggestion",
    "automation_level",
    "automation_level_allows",
    "classify_candidate",
    "extract_unsubscribe_suggestions",
    "provider_write_allowed",
]
