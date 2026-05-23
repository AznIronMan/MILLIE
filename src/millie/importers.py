from __future__ import annotations

import mailbox
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from email import policy
from pathlib import Path

from .database import MillieDatabase
from .mailparse import parse_raw_message


@dataclass(slots=True)
class ImportResult:
    import_job_id: int
    source_id: int
    imported: int
    processed: int
    duplicates: int
    errors: int
    format: str


def detect_format(path: Path) -> str:
    if path.is_dir():
        if (path / "cur").exists() and (path / "new").exists():
            return "maildir"
        return "eml-dir"
    suffix = path.suffix.lower()
    if suffix in {".eml", ".emlx"}:
        return "eml"
    if suffix in {".mbox", ".mbx"}:
        return "mbox"
    if suffix == ".pst":
        return "pst"
    return "mbox"


def read_message_file(path: Path) -> bytes:
    raw = path.read_bytes()
    if path.suffix.lower() == ".emlx":
        return normalize_emlx(raw)
    return raw


def normalize_emlx(raw: bytes) -> bytes:
    first_line_end = raw.find(b"\n")
    if first_line_end <= 0:
        return raw
    declared_size_raw = raw[:first_line_end].strip()
    if not declared_size_raw.isdigit():
        return raw
    message_start = first_line_end + 1
    declared_size = int(declared_size_raw)
    message_end = message_start + declared_size
    if len(raw) >= message_end:
        return raw[message_start:message_end]
    return raw[message_start:]


def import_path(
    db: MillieDatabase,
    path: Path,
    import_format: str = "auto",
    source_name: str | None = None,
    mailbox_path_override: str | None = None,
) -> ImportResult:
    db.init()
    path = path.expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(path)

    resolved_format = detect_format(path) if import_format == "auto" else import_format
    source_id = db.get_or_create_source(
        kind=f"file:{resolved_format}",
        display_name=source_name or path.name,
        source_uri=str(path),
    )
    job_id = db.start_import_job(source_id, resolved_format, {"path": str(path)})
    imported = 0
    processed = 0
    duplicates = 0
    errors = 0

    def resolved_mailbox_path(default: str, relative: str | None = None) -> str:
        base = (mailbox_path_override or "").strip().strip("/")
        cleaned_relative = "" if relative in {None, "", "."} else str(relative).strip("/")
        if base and cleaned_relative:
            return f"{base}/{cleaned_relative}"
        return base or cleaned_relative or default

    def ingest(raw: bytes, mailbox_path: str, source_uid: str) -> None:
        nonlocal imported, processed, duplicates
        parsed = parse_raw_message(db, raw)
        mailbox_id = db.get_or_create_mailbox(source_id, mailbox_path)
        result = db.insert_message(
            source_id=source_id,
            mailbox_id=mailbox_id,
            source_uid=source_uid,
            fields=parsed["fields"],
            headers=parsed["headers"],
            addresses=parsed["addresses"],
            attachments=parsed["attachments"],
            participants_text=parsed["participants_text"],
        )
        processed += 1
        if result.created:
            imported += 1
        else:
            duplicates += 1

    try:
        if resolved_format == "eml":
            ingest(read_message_file(path), resolved_mailbox_path("Imported"), path.name)
        elif resolved_format == "eml-dir":
            for item in sorted(path.rglob("*")):
                if item.is_file() and item.suffix.lower() in {".eml", ".emlx"}:
                    try:
                        relative_parent = str(item.parent.relative_to(path))
                        target_mailbox_path = resolved_mailbox_path("Imported", relative_parent)
                        ingest(read_message_file(item), target_mailbox_path, str(item.relative_to(path)))
                    except Exception as exc:  # noqa: BLE001
                        errors += 1
                        db.record_import_error(job_id, str(item), "error", str(exc))
        elif resolved_format == "mbox":
            box = mailbox.mbox(path)
            try:
                for idx, message in enumerate(box):
                    try:
                        raw = message.as_bytes(policy=policy.SMTP)
                        ingest(raw, resolved_mailbox_path(path.stem or "Imported"), str(idx))
                    except Exception as exc:  # noqa: BLE001
                        errors += 1
                        db.record_import_error(job_id, f"{path}:{idx}", "error", str(exc))
            finally:
                box.close()
        elif resolved_format == "maildir":
            box = mailbox.Maildir(path, create=False)
            try:
                for key in box.keys():
                    try:
                        message = box.get_message(key)
                        raw = message.as_bytes(policy=policy.SMTP)
                        ingest(raw, resolved_mailbox_path(path.name or "Maildir"), key)
                    except Exception as exc:  # noqa: BLE001
                        errors += 1
                        db.record_import_error(job_id, f"{path}:{key}", "error", str(exc))
            finally:
                box.close()
        elif resolved_format == "pst":
            readpst = shutil.which("readpst")
            if readpst is None:
                raise RuntimeError("PST import requires the `readpst` command from libpst")
            extract_parent = db.data_dir / "pst-extracts"
            extract_parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="readpst-", dir=extract_parent) as temp_root:
                completed = subprocess.run(
                    [readpst, "-q", "-M", "-e", "-o", temp_root, str(path)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if completed.returncode != 0:
                    detail = {
                        "stdout": completed.stdout[-4000:],
                        "stderr": completed.stderr[-4000:],
                        "returncode": completed.returncode,
                    }
                    db.record_import_error(job_id, str(path), "error", "readpst failed", detail)
                    raise RuntimeError(f"readpst failed with exit code {completed.returncode}")

                root = Path(temp_root)
                for item in sorted(root.rglob("*.eml")):
                    try:
                        mailbox_path = str(item.parent.relative_to(root)) or path.stem
                        ingest(item.read_bytes(), mailbox_path, str(item.relative_to(root)))
                    except Exception as exc:  # noqa: BLE001
                        errors += 1
                        db.record_import_error(job_id, str(item.relative_to(root)), "error", str(exc))
        else:
            raise ValueError(f"Unsupported import format: {resolved_format}")
    except Exception:
        db.finish_import_job(
            job_id,
            "failed",
            processed,
            errors + 1,
            new_message_count=imported,
            duplicate_count=duplicates,
        )
        raise

    status = "completed_with_errors" if errors else "completed"
    db.finish_import_job(
        job_id,
        status,
        processed,
        errors,
        new_message_count=imported,
        duplicate_count=duplicates,
    )
    return ImportResult(job_id, source_id, imported, processed, duplicates, errors, resolved_format)
