from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ConnectorFailureDetail:
    connector: str
    category: str
    retryable: bool
    retry_after_seconds: int | None
    user_action: str
    message: str

    def to_api(self) -> dict[str, Any]:
        return {
            "connector": self.connector,
            "category": self.category,
            "retryable": self.retryable,
            "retry_after_seconds": self.retry_after_seconds,
            "user_action": self.user_action,
            "message": self.message,
        }


def classify_connector_exception(connector: str, exc: BaseException) -> ConnectorFailureDetail:
    message = str(exc)
    lowered = message.lower()
    if any(token in lowered for token in ("429", "too many requests", "throttl", "rate limit", "try again later")):
        return ConnectorFailureDetail(
            connector,
            "throttled",
            True,
            300,
            "Wait a few minutes, then continue the next batch.",
            message,
        )
    if any(
        token in lowered
        for token in (
            "authentication",
            "login failed",
            "invalid credentials",
            "invalid login",
            "unauthorized",
            "401",
            "403",
            "consent",
            "permission",
            "refresh token",
            "app password",
            "password is not configured",
        )
    ):
        return ConnectorFailureDetail(
            connector,
            "auth",
            False,
            None,
            "Reconnect or resave the source credentials, then retry.",
            message,
        )
    if any(token in lowered for token in ("timeout", "timed out", "temporarily", "connection reset", "network", "ssl")):
        return ConnectorFailureDetail(
            connector,
            "network",
            True,
            60,
            "Retry the sync; if it repeats, lower the run limit and check connectivity.",
            message,
        )
    if any(token in lowered for token in ("limit_mid_page", "partial", "cursor")):
        return ConnectorFailureDetail(
            connector,
            "partial",
            True,
            0,
            "Continue the next batch from the saved recovery state.",
            message,
        )
    return ConnectorFailureDetail(
        connector,
        "unknown",
        False,
        None,
        "Review the job error and source configuration before retrying.",
        message,
    )
