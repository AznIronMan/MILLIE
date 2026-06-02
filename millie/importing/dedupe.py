"""Deterministic duplicate fingerprints for imported mail."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from html import unescape
from typing import Iterable


@dataclass(frozen=True, slots=True)
class DedupeFields:
    normalized_body_sha256: str | None
    attachment_set_sha256: str | None
    normalized_message_fingerprint: str | None


def dedupe_fields(
    *,
    internet_message_id: str | None,
    normalized_subject: str | None,
    sent_at: str | None,
    body_text: str | None,
    body_html: str | None,
    addresses: Iterable[tuple[str, str | None]],
    attachments: Iterable[tuple[str | None, int | None, str | None]],
) -> DedupeFields:
    """Build conservative, non-vector duplicate fingerprints."""

    body_sha = normalized_body_sha256(body_text=body_text, body_html=body_html)
    attachments_sha = attachment_set_sha256(attachments)
    address_values = normalized_addresses(addresses)
    payload = {
        "internet_message_id": normalize_message_id(internet_message_id),
        "subject": normalize_text(normalized_subject),
        "sent_at": normalize_sent_at(sent_at),
        "from": address_values.get("from", []),
        "sender": address_values.get("sender", []),
        "to": address_values.get("to", []),
        "cc": address_values.get("cc", []),
        "bcc": address_values.get("bcc", []),
        "body": body_sha,
        "attachments": attachments_sha,
    }
    meaningful = [
        payload["internet_message_id"],
        payload["subject"],
        payload["sent_at"],
        payload["from"],
        payload["to"],
        payload["cc"],
        payload["bcc"],
        payload["body"],
        payload["attachments"],
    ]
    fingerprint = hash_json(payload) if any(meaningful) else None
    return DedupeFields(
        normalized_body_sha256=body_sha,
        attachment_set_sha256=attachments_sha,
        normalized_message_fingerprint=fingerprint,
    )


def normalized_body_sha256(*, body_text: str | None, body_html: str | None) -> str | None:
    body = normalize_text(body_text) or normalize_text(html_to_text(body_html or ""))
    return sha256_text(body) if body else None


def attachment_set_sha256(
    attachments: Iterable[tuple[str | None, int | None, str | None]]
) -> str | None:
    values = sorted(
        {
            json.dumps(
                {
                    "filename": normalize_text(filename),
                    "size": size,
                    "sha256": sha256_value,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            for filename, size, sha256_value in attachments
            if filename or size is not None or sha256_value
        }
    )
    if not values:
        return None
    return sha256_text("\n".join(values))


def normalized_addresses(addresses: Iterable[tuple[str, str | None]]) -> dict[str, list[str]]:
    result: dict[str, set[str]] = {}
    for role, email_address in addresses:
        normalized = normalize_email_address(email_address)
        if not normalized:
            continue
        result.setdefault(role, set()).add(normalized)
    return {role: sorted(values) for role, values in result.items()}


def normalize_email_address(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().lower()
    return normalized or None


def normalize_message_id(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().strip("<>").strip().lower()
    return normalized or None


def normalize_sent_at(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip()[:19] or None


def normalize_text(value: str | None) -> str | None:
    if not value:
        return None
    normalized = re.sub(r"\s+", " ", value).strip().lower()
    return normalized or None


def html_to_text(value: str) -> str:
    if not value:
        return ""
    without_tags = re.sub(r"<[^>]+>", " ", value)
    return unescape(re.sub(r"\s+", " ", without_tags)).strip()


def hash_json(value: object) -> str:
    return sha256_text(json.dumps(value, sort_keys=True, separators=(",", ":")))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()
