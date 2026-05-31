"""Normalize RFC822 messages into MILLIE's canonical mail records."""

from __future__ import annotations

import hashlib
import re
from datetime import timezone
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from html import unescape
from typing import Any

from .models import (
    NormalizedAddress,
    NormalizedHeader,
    NormalizedMessage,
    NormalizedPart,
    stable_id,
)


ADDRESS_HEADERS: tuple[tuple[str, str], ...] = (
    ("from", "From"),
    ("sender", "Sender"),
    ("reply_to", "Reply-To"),
    ("to", "To"),
    ("cc", "Cc"),
    ("bcc", "Bcc"),
    ("resent_from", "Resent-From"),
    ("resent_to", "Resent-To"),
)


def normalize_email(
    raw_bytes: bytes,
    *,
    source_message_id: str | None = None,
    source_uri: str | None = None,
    folder: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> NormalizedMessage:
    """Convert raw RFC822 bytes into connected, storage-ready records."""

    message = BytesParser(policy=policy.default).parsebytes(raw_bytes)
    raw_hash = hashlib.sha256(raw_bytes).hexdigest()
    internet_message_id = _clean_message_id(message.get("Message-ID"))
    source_id = source_message_id or internet_message_id or raw_hash
    message_id = stable_id("message", source_uri or "unknown", source_id, raw_hash)

    date_header = _header_text(message, "Date")
    sent_at, offset_minutes = _parse_date(date_header)
    received_at, _ = _parse_date(_header_text(message, "Delivery-Date"))

    body_texts: list[str] = []
    body_htmls: list[str] = []
    parts = _normalize_parts(message, message_id, body_texts, body_htmls)
    body_text = _join_body_text(body_texts)
    body_html = _join_body_text(body_htmls)

    subject = _header_text(message, "Subject")
    normalized_subject = _normalize_subject(subject)
    combined_preview = body_text or _html_to_text(body_html or "")

    normalized = NormalizedMessage(
        id=message_id,
        source_message_id=source_id,
        internet_message_id=internet_message_id,
        conversation_id=_header_text(message, "Thread-Index")
        or _header_text(message, "X-Conversation-Id"),
        thread_id=_header_text(message, "References")
        or _header_text(message, "In-Reply-To"),
        subject=subject,
        normalized_subject=normalized_subject,
        sent_at=sent_at,
        received_at=received_at,
        date_header=date_header,
        timezone_offset_minutes=offset_minutes,
        body_text=body_text,
        body_html=body_html,
        body_preview=_preview(combined_preview),
        raw_mime=raw_bytes,
        raw_mime_sha256=raw_hash,
        raw_mime_size_bytes=len(raw_bytes),
        addresses=_normalize_addresses(message),
        headers=_normalize_headers(message),
        parts=parts,
        metadata={
            "folder": folder,
            "source_uri": source_uri,
            **(metadata or {}),
        },
    )
    return normalized


def _header_text(message: Message, name: str) -> str | None:
    value = message.get(name)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean_message_id(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip().strip("<>").strip() or None


def _parse_date(value: str | None) -> tuple[str | None, int | None]:
    if not value:
        return None, None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None, None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    offset = parsed.utcoffset()
    offset_minutes = int(offset.total_seconds() // 60) if offset else 0
    return parsed.isoformat(), offset_minutes


def _normalize_subject(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = re.sub(r"^\s*(re|fw|fwd)\s*:\s*", "", value, flags=re.I)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized or None


def _normalize_headers(message: Message) -> list[NormalizedHeader]:
    if hasattr(message, "raw_items"):
        pairs = list(message.raw_items())
    else:
        pairs = list(message.items())
    return [
        NormalizedHeader(ordinal=index, name=str(name), value=str(value))
        for index, (name, value) in enumerate(pairs)
    ]


def _normalize_addresses(message: Message) -> list[NormalizedAddress]:
    addresses: list[NormalizedAddress] = []
    for role, header in ADDRESS_HEADERS:
        ordinal = 0
        for raw_value in message.get_all(header, []):
            for display_name, email_address in getaddresses([raw_value]):
                if not display_name and not email_address:
                    continue
                addresses.append(
                    NormalizedAddress(
                        role=role,
                        ordinal=ordinal,
                        display_name=display_name or None,
                        email_address=email_address.lower() if email_address else None,
                        raw_value=str(raw_value),
                    )
                )
                ordinal += 1
    return addresses


def _normalize_parts(
    root: Message,
    message_id: str,
    body_texts: list[str],
    body_htmls: list[str],
) -> list[NormalizedPart]:
    parts: list[NormalizedPart] = []

    def visit(part: Message, part_path: str, parent_id: str | None, ordinal: int) -> None:
        part_id = stable_id("part", message_id, part_path)
        content_type = part.get_content_type()
        disposition = (part.get_content_disposition() or "").lower() or None
        filename = part.get_filename()
        content_id = _strip_angle_header(part.get("Content-ID"))
        content_location = _header_text(part, "Content-Location")
        transfer_encoding = _header_text(part, "Content-Transfer-Encoding")
        charset = part.get_content_charset()
        is_container = part.is_multipart()
        is_embedded = content_type == "message/rfc822"
        is_attachment = disposition == "attachment"
        is_inline = disposition == "inline" or bool(content_id or content_location)
        if filename and not is_inline:
            is_attachment = True
        is_body = (
            not is_container
            and not is_attachment
            and content_type in {"text/plain", "text/html"}
            and not filename
        )

        text_content: str | None = None
        binary_content: bytes | None = None
        size_bytes: int | None = None
        sha256: str | None = None

        if not is_container:
            payload = part.get_payload(decode=True)
            if payload is None and isinstance(part, EmailMessage):
                payload = _content_to_bytes(part)
            if payload is not None:
                binary_content = payload
                size_bytes = len(payload)
                sha256 = hashlib.sha256(payload).hexdigest()

            if content_type.startswith("text/"):
                text_content = _part_text(part, binary_content)
                if is_body and text_content:
                    if content_type == "text/html":
                        body_htmls.append(text_content)
                    else:
                        body_texts.append(text_content)
                if is_body:
                    binary_content = None

        parts.append(
            NormalizedPart(
                id=part_id,
                parent_part_id=parent_id,
                ordinal=ordinal,
                part_path=part_path,
                content_type=content_type,
                content_disposition=disposition,
                charset=charset,
                filename=filename,
                content_id=content_id,
                content_location=content_location,
                transfer_encoding=transfer_encoding,
                is_container=is_container,
                is_body=is_body,
                is_attachment=is_attachment,
                is_inline=is_inline,
                is_embedded_message=is_embedded,
                size_bytes=size_bytes,
                sha256=sha256,
                text_content=text_content,
                binary_content=binary_content if not is_body else None,
                metadata={"defects": [type(defect).__name__ for defect in part.defects]},
            )
        )

        if part.is_multipart():
            for child_index, child in enumerate(part.iter_parts(), start=1):
                visit(child, f"{part_path}.{child_index}", part_id, child_index)

    visit(root, "1", None, 1)
    return parts


def _part_text(part: Message, binary_content: bytes | None) -> str | None:
    if isinstance(part, EmailMessage):
        try:
            content = part.get_content()
            if isinstance(content, str):
                return content
        except (LookupError, UnicodeDecodeError, TypeError):
            pass
    if binary_content is None:
        return None
    charset = part.get_content_charset() or "utf-8"
    try:
        return binary_content.decode(charset, errors="replace")
    except LookupError:
        return binary_content.decode("utf-8", errors="replace")


def _content_to_bytes(part: EmailMessage) -> bytes | None:
    try:
        content = part.get_content()
    except (LookupError, UnicodeDecodeError, TypeError):
        return None
    if isinstance(content, bytes):
        return content
    if isinstance(content, str):
        return content.encode(part.get_content_charset() or "utf-8", errors="replace")
    return None


def _strip_angle_header(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip().strip("<>").strip() or None


def _join_body_text(values: list[str]) -> str | None:
    cleaned = [value.strip() for value in values if value and value.strip()]
    return "\n\n".join(cleaned) if cleaned else None


def _preview(value: str | None, limit: int = 500) -> str | None:
    if not value:
        return None
    compact = re.sub(r"\s+", " ", value).strip()
    return compact[:limit] if compact else None


def _html_to_text(value: str) -> str:
    if not value:
        return ""
    without_tags = re.sub(r"<[^>]+>", " ", value)
    return unescape(re.sub(r"\s+", " ", without_tags)).strip()
