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
    tls_cert: Path | None = None
    tls_key: Path | None = None

    @classmethod
    def from_env(cls) -> "AppConfig":
        tls_cert = os.getenv("MILLIE_TLS_CERT", "").strip()
        tls_key = os.getenv("MILLIE_TLS_KEY", "").strip()
        return cls(
            db_path=Path(os.getenv("MILLIE_DB", ".private/local/millie.sqlite")),
            data_dir=Path(os.getenv("MILLIE_DATA_DIR", ".private/local/data")),
            settings_path=Path(os.getenv("MILLIE_SETTINGS", ".private/local/millie.settings")),
            profiles_dir=Path(os.getenv("MILLIE_PROFILES_DIR", ".private/local/profiles")),
            web_dir=Path(os.getenv("MILLIE_WEB_DIR", "web/dist")),
            host=os.getenv("MILLIE_HOST", "0.0.0.0"),
            port=int(os.getenv("MILLIE_PORT", "22001")),
            tls_cert=Path(tls_cert) if tls_cert else None,
            tls_key=Path(tls_key) if tls_key else None,
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
            tls_cert=self.tls_cert.expanduser().resolve() if self.tls_cert else None,
            tls_key=self.tls_key.expanduser().resolve() if self.tls_key else None,
        )
