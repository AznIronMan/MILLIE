"""Safety checks for MILLIE Postgres settings."""

from __future__ import annotations


DEDICATED_MILLIE_CLUSTER = "10.0.10.81:55432/millie"
QUARANTINED_MILLIE_ENDPOINTS = {("10.0.10.81", 5432, "millie")}


class UnsafePostgresEndpointError(RuntimeError):
    """Raised when settings point MILLIE at a quarantined database endpoint."""


def validate_postgres_settings(settings: dict[str, str]) -> tuple[str, int, str]:
    """Return a safe Postgres endpoint tuple or reject known unsafe settings."""

    host = str(settings["postgres_host_ip"]).strip()
    database = str(settings["postgres_database"]).strip()
    raw_port = str(settings.get("postgres_port") or "5432").strip()
    try:
        port = int(raw_port)
    except ValueError:
        raise ValueError("postgres_port must be numeric.") from None

    endpoint = (host.lower(), port, database.lower())
    if endpoint in QUARANTINED_MILLIE_ENDPOINTS:
        raise UnsafePostgresEndpointError(
            "Refusing to connect to quarantined MILLIE endpoint "
            f"{host}:{port}/{database}. Use the dedicated recovery cluster "
            f"{DEDICATED_MILLIE_CLUSTER}; do not point MILLIE clients at the "
            "main Jazmine/Postgres cluster."
        )
    return host, port, database
