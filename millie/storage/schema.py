"""Schema loading helpers."""

from __future__ import annotations

import sqlite3
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = PROJECT_ROOT / "db" / "schema"


def schema_path(dialect: str) -> Path:
    normalized = dialect.lower()
    if normalized not in {"sqlite", "postgres"}:
        raise ValueError(f"Unsupported schema dialect: {dialect}")
    return SCHEMA_DIR / f"{normalized}.sql"


def load_schema(dialect: str) -> str:
    return schema_path(dialect).read_text()


def apply_sqlite_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(load_schema("sqlite"))
