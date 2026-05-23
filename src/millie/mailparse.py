from __future__ import annotations

from datetime import datetime
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from typing import Any

from .database import MillieDatabase
from .html_sanitize import sanitize_html_document


def parse_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        if isinstance(parsed, datetime):
            return parsed.isoformat()
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    return None


def safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def parse_raw_message(db: MillieDatabase, raw: bytes) -> dict[str, Any]:
    parsed = BytesParser(policy=policy.default).parsebytes(raw)
    raw_blob = db.store_blob("raw_message", raw, "message/rfc822")

    body_text, body_html_ref, body_sanitized_html_ref = extract_bodies(db, parsed)
    attachments = extract_attachments(db, parsed)
    headers = list(parsed.raw_items()) if hasattr(parsed, "raw_items") else list(parsed.items())

    address_items: list[dict[str, Any]] = []
    participants: list[str] = []
    from_address_id = None
    reply_to_address_id = None
    role_headers = [
        ("from", "from"),
        ("sender", "sender"),
        ("reply_to", "reply-to"),
        ("to", "to"),
        ("cc", "cc"),
        ("bcc", "bcc"),
    ]
    for role, header_name in role_headers:
        parsed_addresses = getaddresses(parsed.get_all(header_name, []))
        for ordinal, (display_name, email) in enumerate(parsed_addresses):
            if not email and not display_name:
                continue
            address_id = db.get_or_create_address(email, display_name or None)
            if role == "from" and from_address_id is None:
                from_address_id = address_id
            if role == "reply_to" and reply_to_address_id is None:
                reply_to_address_id = address_id
            address_items.append(
                {
                    "address_id": address_id,
                    "role": role,
                    "display_name": display_name or None,
                    "ordinal": ordinal,
                }
            )
            participants.append(f"{display_name} {email}".strip())

    message_id = safe_text(parsed.get("message-id")).strip() or None
    subject = safe_text(parsed.get("subject")).strip()
    fields = {
        "source_message_id": message_id,
        "internet_message_id": message_id,
        "subject": subject,
        "sent_at": parse_date(parsed.get("date")),
        "received_at": parse_date(parsed.get("received")),
        "internal_date": None,
        "from_address_id": from_address_id,
        "reply_to_address_id": reply_to_address_id,
        "in_reply_to": safe_text(parsed.get("in-reply-to")).strip() or None,
        "references_raw": safe_text(parsed.get("references")).strip() or None,
        "conversation_id": message_id,
        "body_text": body_text,
        "body_html_ref": body_html_ref,
        "body_sanitized_html_ref": body_sanitized_html_ref,
        "raw_message_ref": raw_blob["storage_ref"],
        "content_hash": raw_blob["content_hash"],
        "size_bytes": raw_blob["size_bytes"],
    }
    return {
        "fields": fields,
        "headers": headers,
        "addresses": address_items,
        "attachments": attachments,
        "participants_text": " ".join(participants),
    }


def extract_bodies(db: MillieDatabase, parsed: Message) -> tuple[str, str | None, str | None]:
    text_body = ""
    html_ref: str | None = None
    sanitized_html_ref: str | None = None

    body_parts = parsed.walk() if parsed.is_multipart() else [parsed]
    for part in body_parts:
        if part.is_multipart():
            continue
        disposition = safe_text(part.get_content_disposition()).lower()
        content_type = part.get_content_type().lower()
        filename = part.get_filename()
        if disposition == "attachment" or filename:
            continue
        try:
            content = part.get_content()
        except (LookupError, UnicodeDecodeError, TypeError):
            payload = part.get_payload(decode=True) or b""
            content = payload.decode(part.get_content_charset() or "utf-8", errors="replace")

        if content_type == "text/plain" and not text_body:
            text_body = safe_text(content)
        elif content_type == "text/html" and html_ref is None:
            html_text = safe_text(content)
            html_bytes = html_text.encode("utf-8")
            html_ref = db.store_blob("body_html", html_bytes, "text/html")["storage_ref"]
            sanitized_html = sanitize_html_document(html_text).encode("utf-8")
            sanitized_html_ref = db.store_blob("body_sanitized_html", sanitized_html, "text/html")[
                "storage_ref"
            ]

    return text_body, html_ref, sanitized_html_ref


def extract_attachments(db: MillieDatabase, parsed: Message) -> list[dict[str, Any]]:
    attachments: list[dict[str, Any]] = []
    parts = parsed.walk() if parsed.is_multipart() else [parsed]
    for part in parts:
        if part.is_multipart():
            continue
        disposition = safe_text(part.get_content_disposition()).lower()
        filename = part.get_filename()
        content_id = part.get("content-id")
        if not filename and disposition not in {"attachment", "inline"}:
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            content = safe_text(part.get_payload()).encode("utf-8", errors="replace")
        else:
            content = payload
        blob = db.store_blob("attachment", content, part.get_content_type())
        attachments.append(
            {
                "filename": filename,
                "mime_type": part.get_content_type(),
                "content_id": content_id,
                "disposition": disposition or None,
                "size_bytes": blob["size_bytes"],
                "content_hash": blob["content_hash"],
                "storage_ref": blob["storage_ref"],
                "is_inline": disposition == "inline",
            }
        )
    return attachments
