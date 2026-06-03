"""Plan safe internal actions from approved MILLIE brain suggestions."""

from __future__ import annotations

from dataclasses import dataclass


APPLICABLE_CLASSIFICATION_KINDS = {"folder", "spam", "trash"}


@dataclass(frozen=True, slots=True)
class ClassificationAction:
    """A safe internal mailbox action derived from an approved suggestion."""

    classification_id: str
    message_id: str
    kind: str
    value: str
    target_folder_path: str
    confidence: float
    reason: str


def plan_classification_action(row: dict[str, object]) -> ClassificationAction | None:
    """Return an internal action for an approved classification row."""

    kind = str(row.get("kind") or "")
    if kind not in APPLICABLE_CLASSIFICATION_KINDS:
        return None
    target_folder_path = str(row.get("target_folder_path") or "").strip()
    if not target_folder_path:
        return None
    if provider_like_target(target_folder_path):
        return None
    return ClassificationAction(
        classification_id=str(row["classification_id"]),
        message_id=str(row["message_id"]),
        kind=kind,
        value=str(row.get("value") or ""),
        target_folder_path=normalize_target_folder(target_folder_path),
        confidence=float(row.get("confidence") or 0),
        reason=str(row.get("reason") or ""),
    )


def normalize_target_folder(value: str) -> str:
    """Normalize a MILLIE mailbox folder path."""

    return "/".join(part.strip() for part in value.split("/") if part.strip())


def provider_like_target(value: str) -> bool:
    """Block targets that look like remote/provider instructions."""

    normalized = value.strip().lower()
    return normalized.startswith(("imap://", "smtp://", "http://", "https://"))
