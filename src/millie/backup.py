from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__
from .database import utc_now
from .profiles import ProfileManager


SENSITIVE_SETTING_KEYS = {
    "auth.admin.password_hash",
    "auth.session_secret",
}
SENSITIVE_SETTING_PREFIXES = (
    "secrets.",
)


@dataclass(frozen=True, slots=True)
class BackupResult:
    output_path: Path
    profile_id: str
    include_secrets: bool
    file_count: int
    warnings: list[str]

    def to_api(self) -> dict[str, Any]:
        return {
            "output_path": str(self.output_path),
            "profile_id": self.profile_id,
            "include_secrets": self.include_secrets,
            "file_count": self.file_count,
            "warnings": self.warnings,
        }


def create_backup(
    profile_manager: ProfileManager,
    output_path: Path,
    *,
    include_secrets: bool = False,
) -> BackupResult:
    profile = profile_manager.active_profile()
    db = profile_manager.active_database()
    output = output_path.expanduser().resolve()
    if output.suffix.lower() != ".zip":
        output = output / f"millie-backup-{profile.id}.zip" if output.suffix == "" else output.with_suffix(".zip")
    output.parent.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []

    with tempfile.TemporaryDirectory(prefix="millie-backup-") as tmp:
        staging = Path(tmp)
        profile_root = staging / "profile"
        settings_root = staging / "settings"
        profile_root.mkdir(parents=True, exist_ok=True)
        settings_root.mkdir(parents=True, exist_ok=True)

        copy_sqlite_database(db.db_path, profile_root / "millie.sqlite")
        if profile.data_dir.exists():
            shutil.copytree(profile.data_dir, profile_root / "data", dirs_exist_ok=True)
        else:
            warnings.append(f"Profile data directory does not exist: {profile.data_dir}")

        copy_settings_database(
            profile_manager.settings_path,
            settings_root / "millie.settings",
            include_secrets=include_secrets,
        )
        copy_settings_database(
            profile.settings_path,
            settings_root / "profile.settings",
            include_secrets=include_secrets,
        )
        if not include_secrets:
            warnings.append("Secret-bearing settings were redacted; use --include-secrets only for controlled local moves.")

        entries = file_entries(staging)
        manifest = {
            "millie_version": __version__,
            "created_at": utc_now(),
            "profile": profile.to_api(),
            "include_secrets": include_secrets,
            "warnings": warnings,
            "files": entries,
        }
        manifest_path = staging / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        entries = file_entries(staging)

        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for entry in entries:
                archive.write(staging / entry["path"], entry["path"])

    return BackupResult(
        output_path=output,
        profile_id=profile.id,
        include_secrets=include_secrets,
        file_count=len(entries),
        warnings=warnings,
    )


def copy_sqlite_database(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.parent.mkdir(parents=True, exist_ok=True)
    if not source.exists():
        sqlite3.connect(destination).close()
        return
    source_conn = sqlite3.connect(source)
    try:
        dest_conn = sqlite3.connect(destination)
        try:
            source_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        source_conn.close()


def copy_settings_database(source: Path, destination: Path, *, include_secrets: bool) -> None:
    copy_sqlite_database(source, destination)
    if include_secrets:
        return
    conn = sqlite3.connect(destination)
    try:
        redact_settings_table(conn, "app_settings")
        redact_settings_table(conn, "profile_settings")
        conn.commit()
        conn.execute("VACUUM")
    finally:
        conn.close()


def redact_settings_table(conn: sqlite3.Connection, table_name: str) -> None:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    if exists is None:
        return
    conn.execute(
        f"DELETE FROM {table_name} WHERE key IN ({','.join('?' for _ in SENSITIVE_SETTING_KEYS)})",
        tuple(SENSITIVE_SETTING_KEYS),
    )
    for prefix in SENSITIVE_SETTING_PREFIXES:
        conn.execute(f"DELETE FROM {table_name} WHERE key LIKE ?", (f"{prefix}%",))


def file_entries(root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        entries.append(
            {
                "path": relative,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return entries


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
