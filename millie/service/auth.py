"""Postgres-backed identity/auth scaffolding for the MILLIE service facade."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass
from typing import Iterable, Mapping

from millie.importing.models import stable_id
from millie.service.mailbox import default_mailbox_folders


DEFAULT_PASSWORD_ITERATIONS = 310_000
PASSWORD_ALGORITHM = "pbkdf2_sha256"
DEFAULT_SERVICE_MAIL_DOMAIN = "millie.cnbsk.cloud"
DEFAULT_SERVICE_LOCAL_DOMAIN = "MILLIE"


@dataclass(frozen=True, slots=True)
class MillieIdentity:
    login_address: str
    display_name: str = ""
    primary_domain: str | None = None
    domain_aliases: tuple[str, ...] = ()

    @property
    def normalized_login(self) -> str:
        return normalize_login_address(
            self.login_address,
            primary_domain=self.primary_domain,
            domain_aliases=self.domain_aliases,
        )

    @property
    def local_part(self) -> str:
        return self.normalized_login.split("@", 1)[0]

    @property
    def domain(self) -> str:
        return self.normalized_login.split("@", 1)[1]

    @property
    def id(self) -> str:
        return stable_id("millie_identity", self.normalized_login)

    @property
    def mailbox_id(self) -> str:
        return stable_id("millie_mailbox", self.id, self.normalized_login)

    @property
    def login_candidates(self) -> list[str]:
        return login_address_candidates(
            self.login_address,
            primary_domain=self.primary_domain,
            domain_aliases=self.domain_aliases,
        )


def identity_from_settings(
    login_address: str,
    display_name: str,
    settings: Mapping[str, str],
) -> MillieIdentity:
    return MillieIdentity(
        login_address=login_address,
        display_name=display_name,
        primary_domain=service_mail_domain(settings),
        domain_aliases=service_mail_domain_aliases(settings),
    )


def default_service_login(settings: Mapping[str, str], local_part: str = "geon") -> str:
    return normalize_login_address(f"{local_part}@{service_mail_domain(settings)}")


def service_mail_domain(settings: Mapping[str, str] | None = None) -> str:
    value = (settings or {}).get("service_mail_domain", "")
    return normalize_domain(value) or DEFAULT_SERVICE_MAIL_DOMAIN


def service_mail_domain_aliases(settings: Mapping[str, str] | None = None) -> tuple[str, ...]:
    settings = settings or {}
    aliases = [
        settings.get("service_mail_local_domain", "") or DEFAULT_SERVICE_LOCAL_DOMAIN,
        "millie",
    ]
    aliases.extend(split_domain_list(settings.get("service_mail_domain_aliases", "")))
    primary = service_mail_domain(settings)
    return tuple(unique_domains(alias for alias in aliases if normalize_domain(alias) != primary))


def normalize_login_address(
    value: str,
    *,
    primary_domain: str | None = None,
    domain_aliases: tuple[str, ...] | list[str] = (),
) -> str:
    login = " ".join(str(value).strip().split())
    if "@" not in login:
        raise ValueError("MILLIE login addresses must include @, such as geon@MILLIE.")
    local_part, domain = login.rsplit("@", 1)
    local_part = local_part.strip().lower()
    domain = domain.strip().lower()
    if not local_part or not domain:
        raise ValueError("MILLIE login local part and domain are required.")
    primary = normalize_domain(primary_domain or "")
    aliases = set(unique_domains(domain_aliases))
    if primary and domain in aliases:
        domain = primary
    return f"{local_part}@{domain}"


def login_address_candidates(
    value: str,
    *,
    primary_domain: str | None = None,
    domain_aliases: tuple[str, ...] | list[str] = (),
) -> list[str]:
    normalized = normalize_login_address(value)
    local_part, input_domain = normalized.split("@", 1)
    domains = [input_domain]
    primary = normalize_domain(primary_domain or "")
    if primary:
        domains.insert(0, primary)
    domains.extend(unique_domains(domain_aliases))
    return [f"{local_part}@{domain}" for domain in unique_domains(domains)]


def split_domain_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [
        item.strip()
        for item in str(value).replace("\n", ",").split(",")
        if item.strip()
    ]


def unique_domains(values: Iterable[object]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        domain = normalize_domain(str(value))
        if domain and domain not in seen:
            result.append(domain)
            seen.add(domain)
    return result


def normalize_domain(value: str | None) -> str:
    return str(value or "").strip().strip(".").lower()


def hash_password(password: str, *, iterations: int = DEFAULT_PASSWORD_ITERATIONS) -> str:
    if password == "":
        raise ValueError("Password cannot be empty.")
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "$".join(
        [
            PASSWORD_ALGORITHM,
            str(iterations),
            base64.urlsafe_b64encode(salt).decode("ascii").rstrip("="),
            base64.urlsafe_b64encode(digest).decode("ascii").rstrip("="),
        ]
    )


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        algorithm, iterations_text, salt_text, digest_text = encoded_hash.split("$", 3)
        if algorithm != PASSWORD_ALGORITHM:
            return False
        iterations = int(iterations_text)
        salt = _decode_unpadded_base64(salt_text)
        expected = _decode_unpadded_base64(digest_text)
    except (ValueError, TypeError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def build_identity_sql(
    identity: MillieIdentity,
    *,
    password_hash: str | None = None,
) -> str:
    """Build idempotent Postgres SQL for a MILLIE identity and mailbox."""

    login = identity.normalized_login
    display_name = identity.display_name or identity.local_part
    mailbox_id = identity.mailbox_id
    credential_id = stable_id("millie_credential", identity.id, "primary-password")
    values: list[str] = [
        "-- MILLIE identity/bootstrap SQL. Review before applying to Postgres.",
        "BEGIN;",
        """
INSERT INTO millie_identities (
    id, login_address, login_local_part, login_domain, display_name, status
)
VALUES (
    {identity_id}, {login}, {local_part}, {domain}, {display_name}, 'active'
)
ON CONFLICT (id) DO UPDATE SET
    login_address = excluded.login_address,
    login_local_part = excluded.login_local_part,
    login_domain = excluded.login_domain,
    display_name = excluded.display_name,
    updated_at = now();
""".format(
            identity_id=sql_quote(identity.id),
            login=sql_quote(login),
            local_part=sql_quote(identity.local_part),
            domain=sql_quote(identity.domain),
            display_name=sql_quote(display_name),
        ).strip(),
        """
INSERT INTO millie_mailboxes (
    id, owner_identity_id, mailbox_address, display_name, is_primary
)
VALUES (
    {mailbox_id}, {identity_id}, {login}, {display_name}, TRUE
)
ON CONFLICT (id) DO UPDATE SET
    mailbox_address = excluded.mailbox_address,
    display_name = excluded.display_name,
    updated_at = now();
""".format(
            mailbox_id=sql_quote(mailbox_id),
            identity_id=sql_quote(identity.id),
            login=sql_quote(login),
            display_name=sql_quote(display_name),
        ).strip(),
    ]

    if password_hash:
        values.append(
            """
INSERT INTO millie_identity_credentials (
    id, identity_id, credential_type, credential_label, secret_hash
)
VALUES (
    {credential_id},
    {identity_id},
    'password_pbkdf2_sha256',
    'primary password',
    {password_hash}
)
ON CONFLICT (id) DO UPDATE SET
    secret_hash = excluded.secret_hash,
    disabled_at = NULL,
    metadata_json = excluded.metadata_json;
""".format(
                credential_id=sql_quote(credential_id),
                identity_id=sql_quote(identity.id),
                password_hash=sql_quote(password_hash),
            ).strip()
        )

    for folder in default_mailbox_folders(mailbox_id):
        values.append(
            """
INSERT INTO millie_mailbox_folders (
    id, mailbox_id, parent_id, folder_path, display_name, folder_role,
    special_use, sort_order
)
VALUES (
    {folder_id}, {mailbox_id}, {parent_id}, {folder_path}, {display_name},
    {folder_role}, {special_use}, {sort_order}
)
ON CONFLICT (mailbox_id, folder_path) DO UPDATE SET
    display_name = excluded.display_name,
    folder_role = excluded.folder_role,
    special_use = excluded.special_use,
    sort_order = excluded.sort_order,
    updated_at = now();
""".format(
                folder_id=sql_quote(folder.id),
                mailbox_id=sql_quote(mailbox_id),
                parent_id=sql_quote(folder.parent_id) if folder.parent_id else "NULL",
                folder_path=sql_quote(folder.path),
                display_name=sql_quote(folder.display_name),
                folder_role=sql_quote(folder.role),
                special_use=sql_quote(folder.special_use) if folder.special_use else "NULL",
                sort_order=folder.sort_order,
            ).strip()
        )

    values.extend(["COMMIT;", ""])
    return "\n\n".join(values)


def sql_quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _decode_unpadded_base64(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))
