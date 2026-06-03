"""Retention planning helpers for MILLIE hold folders."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    id: str
    name: str
    status: str
    target_kind: str
    target_value: str
    hold_duration: timedelta | None
    action: str
    requires_review: bool


@dataclass(frozen=True, slots=True)
class HeldMessage:
    mailbox_message_id: str
    message_id: str
    folder_path: str
    imap_uid: int
    copied_at: datetime
    subject: str


@dataclass(frozen=True, slots=True)
class RetentionCandidate:
    policy: RetentionPolicy
    message: HeldMessage
    eligible_at: datetime
    age_seconds: int


@dataclass(frozen=True, slots=True)
class RetentionStatus:
    policy: RetentionPolicy
    message: HeldMessage
    eligible_at: datetime | None
    age_seconds: int
    is_eligible: bool


def retention_status(
    policy: RetentionPolicy,
    message: HeldMessage,
    *,
    now: datetime | None = None,
) -> RetentionStatus | None:
    """Return retention timing for a matching policy/message pair."""

    if policy.target_kind != "folder":
        return None
    if normalize_folder(policy.target_value) != normalize_folder(message.folder_path):
        return None
    current_time = now or datetime.now(timezone.utc)
    copied_at = ensure_aware(message.copied_at)
    age_seconds = max(int((current_time - copied_at).total_seconds()), 0)
    if policy.hold_duration is None:
        return RetentionStatus(
            policy=policy,
            message=message,
            eligible_at=None,
            age_seconds=age_seconds,
            is_eligible=False,
        )
    eligible_at = copied_at + policy.hold_duration
    return RetentionStatus(
        policy=policy,
        message=message,
        eligible_at=eligible_at,
        age_seconds=age_seconds,
        is_eligible=current_time >= eligible_at,
    )


def retention_candidate(
    policy: RetentionPolicy,
    message: HeldMessage,
    *,
    now: datetime | None = None,
) -> RetentionCandidate | None:
    """Return a candidate if the message has reached the policy hold duration."""

    status = retention_status(policy, message, now=now)
    if not status or not status.is_eligible or status.eligible_at is None:
        return None
    return RetentionCandidate(
        policy=policy,
        message=message,
        eligible_at=status.eligible_at,
        age_seconds=status.age_seconds,
    )


def normalize_folder(value: str) -> str:
    return "/".join(part.strip() for part in value.split("/") if part.strip())


def ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def human_duration(value: timedelta | None) -> str:
    if value is None:
        return "none"
    total_seconds = int(value.total_seconds())
    if total_seconds % 86400 == 0:
        days = total_seconds // 86400
        return f"{days} day{'s' if days != 1 else ''}"
    if total_seconds % 3600 == 0:
        hours = total_seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''}"
    return f"{total_seconds} seconds"
