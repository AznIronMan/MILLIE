from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class AppConfig:
    db_path: Path
    data_dir: Path
    settings_path: Path
    profiles_dir: Path
    web_dir: Path
    host: str
    port: int

    @classmethod
    def from_env(cls) -> "AppConfig":
        return cls(
            db_path=Path(os.getenv("MILLIE_DB", ".private/local/millie.sqlite")),
            data_dir=Path(os.getenv("MILLIE_DATA_DIR", ".private/local/data")),
            settings_path=Path(os.getenv("MILLIE_SETTINGS", ".private/local/millie.settings")),
            profiles_dir=Path(os.getenv("MILLIE_PROFILES_DIR", ".private/local/profiles")),
            web_dir=Path(os.getenv("MILLIE_WEB_DIR", "web/dist")),
            host=os.getenv("MILLIE_HOST", "0.0.0.0"),
            port=int(os.getenv("MILLIE_PORT", "22001")),
        )

    def resolved(self) -> "AppConfig":
        return AppConfig(
            db_path=self.db_path.expanduser().resolve(),
            data_dir=self.data_dir.expanduser().resolve(),
            settings_path=self.settings_path.expanduser().resolve(),
            profiles_dir=self.profiles_dir.expanduser().resolve(),
            web_dir=self.web_dir.expanduser().resolve(),
            host=self.host,
            port=self.port,
        )
