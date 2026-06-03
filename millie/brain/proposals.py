"""Helpers for review-only rule and taxonomy proposals."""

from __future__ import annotations


def proposal_confidence(avg_confidence: object, evidence_count: object) -> float:
    """Return a bounded confidence score for aggregate proposal evidence."""

    try:
        average = float(avg_confidence or 0)
    except (TypeError, ValueError):
        average = 0
    try:
        count = int(evidence_count or 0)
    except (TypeError, ValueError):
        count = 0
    evidence_bonus = min(max(count - 1, 0), 10) * 0.01
    return round(max(0.01, min(average + evidence_bonus, 0.95)), 4)


def target_label(
    *,
    kind: str,
    value: str,
    target_folder_path: str | None,
    target_tags: list[str] | tuple[str, ...],
) -> str:
    """Return a readable proposal target label."""

    if target_folder_path:
        return target_folder_path
    if target_tags:
        return ", ".join(str(tag) for tag in target_tags)
    return f"{kind}:{value}"


def compact_values(values: list[str], *, limit: int = 8) -> list[str]:
    """Return a stable, compact set of non-empty strings."""

    seen: set[str] = set()
    compacted: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        compacted.append(normalized)
        if len(compacted) >= limit:
            break
    return compacted

