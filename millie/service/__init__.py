"""Dormant MILLIE service facade helpers."""

from .auth import (
    MillieIdentity,
    build_identity_sql,
    default_service_login,
    hash_password,
    identity_from_settings,
    login_address_candidates,
    normalize_login_address,
    service_mail_domain,
    service_mail_domain_aliases,
    verify_password,
)
from .mailbox import default_mailbox_folders

__all__ = [
    "MillieIdentity",
    "build_identity_sql",
    "default_service_login",
    "default_mailbox_folders",
    "hash_password",
    "identity_from_settings",
    "login_address_candidates",
    "normalize_login_address",
    "service_mail_domain",
    "service_mail_domain_aliases",
    "verify_password",
]
