"""Shared import pipeline data models."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


MILLIE_NAMESPACE_UUID = uuid.UUID("0c7556e1-2f8f-4f5f-8a40-fcc1c32d86f4")


def stable_id(*parts: object) -> str:
    """Build a deterministic UUID string for imported mail records."""

    value = "\x1f".join("" if part is None else str(part) for part in parts)
    return str(uuid.uuid5(MILLIE_NAMESPACE_UUID, value))


@dataclass(slots=True)
class ExtractedMessage:
    """Raw RFC822 message bytes plus source provenance."""

    source_type: str
    source_uri: str
    source_message_id: str
    raw_bytes: bytes
    folder: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class NormalizedAddress:
    role: str
    ordinal: int
    display_name: str | None
    email_address: str | None
    raw_value: str | None


@dataclass(slots=True)
class NormalizedHeader:
    ordinal: int
    name: str
    value: str


@dataclass(slots=True)
class NormalizedPart:
    id: str
    parent_part_id: str | None
    ordinal: int
    part_path: str
    content_type: str | None
    content_disposition: str | None
    charset: str | None
    filename: str | None
    content_id: str | None
    content_location: str | None
    transfer_encoding: str | None
    is_container: bool
    is_body: bool
    is_attachment: bool
    is_inline: bool
    is_embedded_message: bool
    size_bytes: int | None
    sha256: str | None
    text_content: str | None
    binary_content: bytes | None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class NormalizedMessage:
    id: str
    source_message_id: str
    internet_message_id: str | None
    conversation_id: str | None
    thread_id: str | None
    subject: str | None
    normalized_subject: str | None
    sent_at: str | None
    received_at: str | None
    date_header: str | None
    timezone_offset_minutes: int | None
    body_text: str | None
    body_html: str | None
    body_preview: str | None
    raw_mime: bytes
    raw_mime_sha256: str
    raw_mime_size_bytes: int
    addresses: list[NormalizedAddress] = field(default_factory=list)
    headers: list[NormalizedHeader] = field(default_factory=list)
    parts: list[NormalizedPart] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def has_attachments(self) -> bool:
        return any(part.is_attachment for part in self.parts)
