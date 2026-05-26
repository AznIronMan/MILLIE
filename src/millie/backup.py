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
from .database import MillieDatabase, utc_now
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


@dataclass(frozen=True, slots=True)
class RestoreResult:
    input_path: Path
    profile_id: str
    profile_name: str
    file_count: int
    switched: bool
    warnings: list[str]

    def to_api(self) -> dict[str, Any]:
        return {
            "input_path": str(self.input_path),
            "profile_id": self.profile_id,
            "profile_name": self.profile_name,
            "file_count": self.file_count,
            "switched": self.switched,
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


def restore_backup(
    profile_manager: ProfileManager,
    backup_path: Path,
    *,
    profile_name: str | None = None,
    profile_id: str | None = None,
    switch: bool = True,
) -> RestoreResult:
    source = backup_path.expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"Backup not found: {source}")
    with tempfile.TemporaryDirectory(prefix="millie-restore-") as tmp:
        restore_root = Path(tmp)
        manifest = validate_backup_archive(source)
        with zipfile.ZipFile(source) as archive:
            safe_extract_archive(archive, restore_root)

        original_profile = manifest_profile(manifest)
        target_name = (profile_name or f"Restored {original_profile.get('name') or 'MILLIE Profile'}").strip()
        requested_id = (profile_id or f"restored-{original_profile.get('id') or 'profile'}").strip()
        if requested_id in profile_manager.profiles:
            raise ValueError(f"Profile already exists: {requested_id}")

        profile = profile_manager.create_profile(target_name, switch=False, profile_id=requested_id)
        restored_db = restore_root / "profile" / "millie.sqlite"
        restored_data = restore_root / "profile" / "data"
        restored_settings = restore_root / "settings" / "profile.settings"
        if not restored_db.exists():
            raise ValueError("Backup is missing profile/millie.sqlite")
        shutil.copy2(restored_db, profile.db_path)
        if profile.data_dir.exists():
            shutil.rmtree(profile.data_dir)
        if restored_data.exists():
            shutil.copytree(restored_data, profile.data_dir)
        else:
            profile.data_dir.mkdir(parents=True, exist_ok=True)
        if restored_settings.exists():
            shutil.copy2(restored_settings, profile.settings_path)

        profile_manager.init_profile_settings(profile)
        MillieDatabase(profile.db_path, profile.data_dir).init()
        if switch:
            profile_manager.set_active(profile.id)

    return RestoreResult(
        input_path=source,
        profile_id=profile.id,
        profile_name=profile.name,
        file_count=len(manifest.get("files") or []),
        switched=switch,
        warnings=list(manifest.get("warnings") or []),
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


def validate_backup_archive(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        names: set[str] = set()
        for member in archive.infolist():
            if not safe_archive_path(member.filename):
                raise ValueError(f"Unsafe backup path: {member.filename}")
            if member.is_dir():
                continue
            if member.filename in names:
                raise ValueError(f"Duplicate backup path: {member.filename}")
            names.add(member.filename)
        if "manifest.json" not in names:
            raise ValueError("Backup is missing manifest.json")
        try:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("Backup manifest is not valid JSON") from exc
        if not isinstance(manifest, dict):
            raise ValueError("Backup manifest has an unexpected shape")
        entries = manifest.get("files")
        if not isinstance(entries, list):
            raise ValueError("Backup manifest is missing file entries")
        listed_paths: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("Backup manifest contains an invalid file entry")
            raw_path = str(entry.get("path") or "")
            if not safe_archive_path(raw_path):
                raise ValueError(f"Unsafe backup path: {raw_path}")
            if raw_path in listed_paths:
                raise ValueError(f"Duplicate backup manifest entry: {raw_path}")
            listed_paths.add(raw_path)
            if raw_path not in names:
                raise ValueError(f"Backup is missing file listed in manifest: {raw_path}")
            expected_hash = str(entry.get("sha256") or "")
            if not expected_hash:
                raise ValueError(f"Backup manifest entry is missing sha256: {raw_path}")
            actual_hash = hashlib.sha256(archive.read(raw_path)).hexdigest()
            if actual_hash != expected_hash:
                raise ValueError(f"Backup file hash mismatch: {raw_path}")
        for name in names:
            if name != "manifest.json" and name not in listed_paths:
                raise ValueError(f"Backup contains unlisted file: {name}")
        if "profile/millie.sqlite" not in names:
            raise ValueError("Backup is missing profile/millie.sqlite")
        return manifest


def safe_extract_archive(archive: zipfile.ZipFile, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for member in archive.infolist():
        if not safe_archive_path(member.filename):
            raise ValueError(f"Unsafe backup path: {member.filename}")
        if member.is_dir():
            continue
        target = destination / member.filename
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member) as src, target.open("wb") as dst:
            shutil.copyfileobj(src, dst)


def safe_archive_path(value: str) -> bool:
    path = Path(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts


def manifest_profile(manifest: dict[str, Any]) -> dict[str, Any]:
    profile = manifest.get("profile")
    return profile if isinstance(profile, dict) else {}
