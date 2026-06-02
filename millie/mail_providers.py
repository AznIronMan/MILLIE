"""Mail provider defaults used by settings and import tools."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


ICLOUD_DOMAINS = {"icloud.com", "me.com", "mac.com"}


def normalize_mail_account(account: dict[str, Any]) -> dict[str, Any]:
    """Return an account copy with provider defaults filled when safe."""

    normalized = deepcopy(account)
    account_type = str(normalized.get("account_type") or "")
    email_address = str(normalized.get("email_address") or "")
    host = str(normalized.get("host") or "")
    provider = provider_for_account(email_address=email_address, host=host)
    if provider != "icloud":
        return normalized

    if account_type == "imap":
        normalized["host"] = host or "imap.mail.me.com"
        normalized["port"] = str(normalized.get("port") or "993")
        normalized["security"] = normalized.get("security") or "ssl_tls"
        normalized["auth_method"] = normalized.get("auth_method") or "password"
        if not normalized.get("username"):
            normalized["username"] = icloud_imap_username(email_address)
    elif account_type == "smtp":
        normalized["host"] = host or "smtp.mail.me.com"
        normalized["port"] = str(normalized.get("port") or "587")
        normalized["security"] = normalized.get("security") or "starttls"
        normalized["auth_method"] = normalized.get("auth_method") or "password"
        if not normalized.get("username"):
            normalized["username"] = email_address
    return normalized


def provider_for_account(*, email_address: str, host: str) -> str | None:
    host_value = host.strip().lower()
    if host_value in {"imap.mail.me.com", "smtp.mail.me.com"}:
        return "icloud"
    domain = email_domain(email_address)
    if domain in ICLOUD_DOMAINS:
        return "icloud"
    return None


def icloud_imap_username(email_address: str) -> str:
    local_part = email_address.split("@", 1)[0].strip()
    return local_part or email_address


def email_domain(email_address: str) -> str:
    if "@" not in email_address:
        return ""
    return email_address.rsplit("@", 1)[-1].strip().lower()
