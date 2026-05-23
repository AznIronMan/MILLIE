from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .database import MillieDatabase, utc_now


GLOBAL_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profiles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    db_path TEXT NOT NULL,
    data_dir TEXT NOT NULL,
    settings_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_opened_at TEXT NOT NULL
);
"""


PROFILE_SCHEMA = """
CREATE TABLE IF NOT EXISTS profile_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


@dataclass(slots=True)
class Profile:
    id: str
    name: str
    db_path: Path
    data_dir: Path
    settings_path: Path
    created_at: str
    last_opened_at: str

    def to_api(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "db_path": str(self.db_path),
            "data_dir": str(self.data_dir),
            "settings_path": str(self.settings_path),
            "created_at": self.created_at,
            "last_opened_at": self.last_opened_at,
        }


class ProfileManager:
    def __init__(self, settings_path: Path, profiles_dir: Path, default_db_path: Path, default_data_dir: Path):
        self.settings_path = settings_path
        self.profiles_dir = profiles_dir
        self.default_db_path = default_db_path
        self.default_data_dir = default_data_dir
        self.active_profile_id = "default"
        self.profiles: dict[str, Profile] = {}
        self.load()

    @contextmanager
    def connect(self) -> Iterable[sqlite3.Connection]:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.settings_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def load(self) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(GLOBAL_SCHEMA)
            if self.profile_count(conn) == 0:
                self.migrate_legacy_profiles(conn)
            if self.profile_count(conn) == 0:
                self.insert_profile(conn, self.default_profile())
                self.set_setting(conn, "active_profile_id", "default")

            active = self.get_setting(conn, "active_profile_id") or "default"
            rows = conn.execute("SELECT * FROM profiles ORDER BY LOWER(name)").fetchall()
            self.profiles = {str(row["id"]): self.profile_from_row(row) for row in rows}
            if "default" not in self.profiles:
                default = self.default_profile()
                self.insert_profile(conn, default)
                self.profiles[default.id] = default
            if active not in self.profiles:
                active = "default"
                self.set_setting(conn, "active_profile_id", active)
            self.active_profile_id = active

        for profile in self.profiles.values():
            self.init_profile_settings(profile)

    def profile_count(self, conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT COUNT(*) AS count FROM profiles").fetchone()
        return int(row["count"])

    def default_profile(self) -> Profile:
        now = utc_now()
        return Profile(
            id="default",
            name="Default",
            db_path=self.default_db_path,
            data_dir=self.default_data_dir,
            settings_path=(self.profiles_dir / "default" / "default.settings").resolve(),
            created_at=now,
            last_opened_at=now,
        )

    def profile_from_row(self, row: sqlite3.Row) -> Profile:
        return Profile(
            id=str(row["id"]),
            name=str(row["name"]),
            db_path=Path(str(row["db_path"])).expanduser().resolve(),
            data_dir=Path(str(row["data_dir"])).expanduser().resolve(),
            settings_path=Path(str(row["settings_path"])).expanduser().resolve(),
            created_at=str(row["created_at"]),
            last_opened_at=str(row["last_opened_at"]),
        )

    def list_profiles(self) -> list[dict[str, Any]]:
        return [profile.to_api() for profile in sorted(self.profiles.values(), key=lambda item: item.name.lower())]

    def active_profile(self) -> Profile:
        return self.profiles[self.active_profile_id]

    def active_database(self) -> MillieDatabase:
        profile = self.active_profile()
        db = MillieDatabase(profile.db_path, profile.data_dir)
        db.init()
        return db

    def create_profile(self, name: str, switch: bool = True, profile_id: str | None = None) -> Profile:
        cleaned_name = name.strip() or "New Profile"
        new_id = self.unique_profile_id(profile_id or cleaned_name)
        now = utc_now()
        root = self.profiles_dir / new_id
        profile = Profile(
            id=new_id,
            name=cleaned_name,
            db_path=(root / "millie.sqlite").resolve(),
            data_dir=(root / "data").resolve(),
            settings_path=(root / f"{new_id}.settings").resolve(),
            created_at=now,
            last_opened_at=now,
        )
        with self.connect() as conn:
            self.insert_profile(conn, profile)
            if switch:
                self.set_setting(conn, "active_profile_id", new_id)
        self.profiles[new_id] = profile
        if switch:
            self.active_profile_id = new_id
        self.init_profile_settings(profile)
        self.active_database()
        return profile

    def set_active(self, profile_id: str) -> Profile:
        if profile_id not in self.profiles:
            raise KeyError(f"Unknown profile: {profile_id}")
        profile = self.profiles[profile_id]
        profile.last_opened_at = utc_now()
        with self.connect() as conn:
            conn.execute(
                "UPDATE profiles SET last_opened_at = ? WHERE id = ?",
                (profile.last_opened_at, profile_id),
            )
            self.set_setting(conn, "active_profile_id", profile_id)
        self.active_profile_id = profile_id
        self.init_profile_settings(profile)
        self.active_database()
        return profile

    def insert_profile(self, conn: sqlite3.Connection, profile: Profile) -> None:
        conn.execute(
            """
            INSERT OR REPLACE INTO profiles
                (id, name, db_path, data_dir, settings_path, created_at, last_opened_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profile.id,
                profile.name,
                str(profile.db_path),
                str(profile.data_dir),
                str(profile.settings_path),
                profile.created_at,
                profile.last_opened_at,
            ),
        )

    def get_setting(self, conn: sqlite3.Connection, key: str) -> str | None:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        return str(row["value"])

    def get_app_setting(self, key: str) -> str | None:
        with self.connect() as conn:
            return self.get_setting(conn, key)

    def set_setting(self, conn: sqlite3.Connection, key: str, value: str) -> None:
        conn.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, utc_now()),
        )

    def set_app_setting(self, key: str, value: str) -> None:
        with self.connect() as conn:
            self.set_setting(conn, key, value)

    def init_profile_settings(self, profile: Profile) -> None:
        profile.settings_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(profile.settings_path)
        try:
            conn.executescript(PROFILE_SCHEMA)
            now = utc_now()
            rows = {
                "profile_id": profile.id,
                "profile_name": profile.name,
                "db_path": str(profile.db_path),
                "data_dir": str(profile.data_dir),
            }
            for key, value in rows.items():
                conn.execute(
                    """
                    INSERT INTO profile_settings (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                    """,
                    (key, value, now),
                )
            conn.commit()
        finally:
            conn.close()

    def unique_profile_id(self, value: str) -> str:
        base = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "profile"
        candidate = base
        counter = 2
        while candidate in self.profiles:
            candidate = f"{base}-{counter}"
            counter += 1
        return candidate

    def migrate_legacy_profiles(self, conn: sqlite3.Connection) -> None:
        legacy_path = self.settings_path.with_name("profiles.json")
        if not legacy_path.exists():
            return
        try:
            data = json.loads(legacy_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return

        for item in data.get("profiles", []):
            profile_id = str(item.get("id") or self.unique_profile_id(str(item.get("name") or "profile")))
            profile = Profile(
                id=profile_id,
                name=str(item.get("name") or profile_id),
                db_path=Path(str(item.get("db_path") or self.default_db_path)).expanduser().resolve(),
                data_dir=Path(str(item.get("data_dir") or self.default_data_dir)).expanduser().resolve(),
                settings_path=(self.profiles_dir / profile_id / f"{profile_id}.settings").resolve(),
                created_at=str(item.get("created_at") or utc_now()),
                last_opened_at=str(item.get("last_opened_at") or item.get("created_at") or utc_now()),
            )
            self.insert_profile(conn, profile)

        active = str(data.get("active_profile_id") or "default")
        self.set_setting(conn, "active_profile_id", active)
