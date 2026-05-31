"""Dormant mail import pipeline primitives."""

from .models import (
    ExtractedMessage,
    NormalizedAddress,
    NormalizedHeader,
    NormalizedMessage,
    NormalizedPart,
)
from .normalize import normalize_email

__all__ = [
    "ExtractedMessage",
    "NormalizedAddress",
    "NormalizedHeader",
    "NormalizedMessage",
    "NormalizedPart",
    "normalize_email",
]
