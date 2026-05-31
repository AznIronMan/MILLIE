"""Storage helpers for MILLIE mail data."""

from .schema import apply_sqlite_schema, load_schema, schema_path
from .sqlite_store import SQLiteMailStore

try:
    from .postgres_store import PostgresMailStore
except ImportError:  # pragma: no cover - psycopg is optional for SQLite-only use.
    PostgresMailStore = None  # type: ignore[assignment]

__all__ = [
    "PostgresMailStore",
    "SQLiteMailStore",
    "apply_sqlite_schema",
    "load_schema",
    "schema_path",
]
