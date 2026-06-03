"""Observe-only mail classification heuristics."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable


CLASSIFIER_TYPE = "heuristic"
CLASSIFIER_VERSION = "observe-v1"


@dataclass(frozen=True, slots=True)
class SortCandidate:
    """Message fields needed for non-destructive sorting suggestions."""

    message_id: str
    subject: str = ""
    from_text: str = ""
    folder_path: str = ""
    body_preview: str = ""
    sent_at: datetime | None = None
    received_at: datetime | None = None
    headers: dict[str, list[str]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ClassificationSuggestion:
    """A proposed internal classification for later review or application."""

    kind: str
    value: str
    target_folder_path: str | None
    target_tags: tuple[str, ...]
    confidence: float
    reason: str
    evidence: dict[str, object]


@dataclass(frozen=True, slots=True)
class UnsubscribeSuggestion:
    """A detected unsubscribe route requiring review before use."""

    candidate_type: str
    source_header: str | None
    unsubscribe_url: str | None
    unsubscribe_mailto: str | None
    confidence: float
    requires_browser: bool
    evidence: dict[str, object]


KEYWORD_GROUPS: tuple[tuple[str, str, tuple[str, ...], str, float], ...] = (
    (
        "folder",
        "taxes",
        (
            "1099",
            "w-2",
            "w2",
            "irs",
            "tax",
            "taxes",
            "turbotax",
            "quickbooks",
        ),
        "Archive/Taxes/{year}",
        0.82,
    ),
    (
        "folder",
        "receipts",
        (
            "receipt",
            "invoice",
            "payment",
            "paid",
            "order",
            "statement",
            "subscription",
            "renewal",
            "paypal",
            "stripe",
            "amazon",
            "apple",
        ),
        "Archive/Receipts/{year}",
        0.76,
    ),
    (
        "folder",
        "travel",
        (
            "flight",
            "boarding",
            "airline",
            "hotel",
            "reservation",
            "itinerary",
            "airbnb",
            "rental car",
            "trip",
        ),
        "Archive/Travel/{year}",
        0.74,
    ),
    (
        "folder",
        "work",
        (
            "contract",
            "statement of work",
            "sow",
            "proposal",
            "meeting",
            "invoice due",
            "client",
        ),
        "Archive/Work/{year}",
        0.62,
    ),
)

SPAM_HINTS = (
    "unsubscribe from this list",
    "limited time offer",
    "act now",
    "winner",
    "prize",
    "crypto",
    "free gift",
)

TRASH_FOLDER_HINTS = ("trash", "deleted")
SPAM_FOLDER_HINTS = ("spam", "junk")


def classify_candidate(candidate: SortCandidate) -> list[ClassificationSuggestion]:
    """Return non-destructive suggestions for one message."""

    text = searchable_text(candidate)
    folder = candidate.folder_path.lower()
    year = candidate_year(candidate)

    if any(hint in folder for hint in TRASH_FOLDER_HINTS):
        return [
            ClassificationSuggestion(
                kind="trash",
                value="likely_trash",
                target_folder_path="Hold/Trash",
                target_tags=("trash",),
                confidence=0.9,
                reason="Message is already in a trash/deleted source folder.",
                evidence={"folder_path": candidate.folder_path},
            )
        ]

    if any(hint in folder for hint in SPAM_FOLDER_HINTS):
        return [
            ClassificationSuggestion(
                kind="spam",
                value="likely_spam",
                target_folder_path="Hold/Spam",
                target_tags=("spam",),
                confidence=0.9,
                reason="Message is already in a spam/junk source folder.",
                evidence={"folder_path": candidate.folder_path},
            )
        ]

    if any(hint in text for hint in SPAM_HINTS):
        return [
            ClassificationSuggestion(
                kind="spam",
                value="possible_spam",
                target_folder_path="Hold/Spam",
                target_tags=("spam", "review"),
                confidence=0.58,
                reason="Message contains common spam or bulk-mail language.",
                evidence={"matched_hints": matched_keywords(text, SPAM_HINTS)},
            )
        ]

    suggestions: list[ClassificationSuggestion] = []
    for kind, value, keywords, folder_template, confidence in KEYWORD_GROUPS:
        matches = matched_keywords(text, keywords)
        if not matches:
            continue
        suggestions.append(
            ClassificationSuggestion(
                kind=kind,
                value=value,
                target_folder_path=folder_template.format(year=year),
                target_tags=(value, str(year)),
                confidence=confidence,
                reason=f"Matched {value} keywords in message text.",
                evidence={"matched_keywords": matches},
            )
        )
        break

    return suggestions


def extract_unsubscribe_suggestions(
    headers: dict[str, list[str]],
) -> list[UnsubscribeSuggestion]:
    """Extract review-required unsubscribe candidates from mail headers."""

    values = []
    for name, header_values in headers.items():
        if name.lower() == "list-unsubscribe":
            values.extend(header_values)
    suggestions: list[UnsubscribeSuggestion] = []
    seen: set[tuple[str, str]] = set()
    for raw_value in values:
        for target in parse_list_unsubscribe(raw_value):
            candidate_type = "header_mailto" if target.lower().startswith("mailto:") else "header_url"
            key = (candidate_type, target)
            if key in seen:
                continue
            seen.add(key)
            suggestions.append(
                UnsubscribeSuggestion(
                    candidate_type=candidate_type,
                    source_header=raw_value,
                    unsubscribe_url=None if candidate_type == "header_mailto" else target,
                    unsubscribe_mailto=target if candidate_type == "header_mailto" else None,
                    confidence=0.9,
                    requires_browser=False,
                    evidence={"source": "List-Unsubscribe"},
                )
            )
    return suggestions


def searchable_text(candidate: SortCandidate) -> str:
    return " ".join(
        part
        for part in (
            candidate.subject,
            candidate.from_text,
            candidate.folder_path,
            candidate.body_preview,
        )
        if part
    ).lower()


def candidate_year(candidate: SortCandidate) -> int:
    value = candidate.sent_at or candidate.received_at
    if value:
        return value.year
    return datetime.now().year


def matched_keywords(text: str, keywords: Iterable[str]) -> list[str]:
    return [keyword for keyword in keywords if keyword in text]


def parse_list_unsubscribe(raw_value: str) -> list[str]:
    targets = re.findall(r"<([^>]+)>", raw_value)
    if not targets:
        targets = [part.strip() for part in raw_value.split(",")]
    return [
        target.strip()
        for target in targets
        if target.strip().lower().startswith(("mailto:", "http://", "https://"))
    ]
