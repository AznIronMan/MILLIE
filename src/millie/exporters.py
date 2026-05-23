from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from email.parser import BytesParser
from pathlib import Path

from . import __version__
from .database import MillieDatabase, utc_now
from .export_profiles import get_export_profile, resolve_export_format


@dataclass(slots=True)
class ExportResult:
    export_job_id: int
    exported: int
    errors: int
    warnings: int
    manifest_path: Path


def safe_path_part(value: str | None, fallback: str = "Imported") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", value or "").strip(" .")
    return cleaned or fallback


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def export_messages(
    db: MillieDatabase,
    output_root: Path,
    export_format: str,
    *,
    target_profile: str = "generic",
    mailbox_id: int | None = None,
    message_ids: list[int] | None = None,
) -> ExportResult:
    db.init()
    output_root = output_root.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    profile_hint = (target_profile or "generic").strip().lower()
    format_hint = (export_format or "auto").strip().lower()
    if profile_hint == "generic" and format_hint in {"mbox", "maildir"}:
        profile = get_export_profile(f"generic-{format_hint}")
    else:
        profile = get_export_profile(target_profile)
    export_format = resolve_export_format(profile, export_format)
    options = {
        "mailbox_id": mailbox_id,
        "message_ids": message_ids or [],
        "target_profile": profile.id,
        "requested_format": export_format,
    }
    job_id = db.start_export_job(profile.id, export_format, output_root, options)
    messages = db.messages_for_export(mailbox_id=mailbox_id, message_ids=message_ids)
    unique_message_ids = sorted({int(item["id"]) for item in messages})
    mailbox_paths = sorted({str(item.get("mailbox_path") or "Imported") for item in messages})
    source_ids = sorted({int(item["source_id"]) for item in messages if item.get("source_id") is not None})
    attachment_count = db.attachment_count_for_messages(unique_message_ids)
    exported = 0
    errors = 0
    warnings = 0
    manifest_items: list[dict[str, object]] = []

    try:
        if export_format == "eml":
            for item in messages:
                item_warnings: list[str] = []
                raw = raw_for_export(db, item, item_warnings)
                mailbox_part = safe_path_part(item.get("mailbox_path"))
                folder = output_root / mailbox_part
                folder.mkdir(parents=True, exist_ok=True)
                filename = f"{item['id']}-{str(item['content_hash'])[:12]}.eml"
                output_path = folder / filename
                output_path.write_bytes(raw)
                output_hash = sha256_file(output_path)
                warnings += len(item_warnings)
                exported += 1
                db.record_export_item(
                    job_id,
                    int(item["id"]),
                    item.get("mailbox_id"),
                    output_path,
                    output_hash,
                    export_format,
                    "exported",
                    item_warnings,
                )
                manifest_items.append(manifest_item(item, output_path, output_hash, item_warnings))
        elif export_format == "mbox":
            grouped = group_by_mailbox(messages)
            for mailbox_path, items in grouped.items():
                output_path = output_root / f"{safe_path_part(mailbox_path)}.mbox"
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with output_path.open("wb") as handle:
                    for item in items:
                        item_warnings = []
                        raw = raw_for_export(db, item, item_warnings)
                        write_mbox_message(handle, raw)
                        warnings += len(item_warnings)
                        exported += 1
                        db.record_export_item(
                            job_id,
                            int(item["id"]),
                            item.get("mailbox_id"),
                            output_path,
                            None,
                            export_format,
                            "exported",
                            item_warnings,
                        )
                        manifest_items.append(manifest_item(item, output_path, None, item_warnings))
                output_hash = sha256_file(output_path)
                for manifest in manifest_items:
                    if manifest["output_path"] == str(output_path):
                        manifest["output_hash"] = output_hash
        elif export_format == "maildir":
            grouped = group_by_mailbox(messages)
            for mailbox_path, items in grouped.items():
                maildir_root = output_root / safe_path_part(mailbox_path)
                for child in ("tmp", "new", "cur"):
                    (maildir_root / child).mkdir(parents=True, exist_ok=True)
                for item in items:
                    item_warnings = []
                    raw = raw_for_export(db, item, item_warnings)
                    filename = f"{int(time.time())}.M{item['id']}.{str(item['content_hash'])[:16]}"
                    output_path = maildir_root / "new" / filename
                    output_path.write_bytes(raw)
                    output_hash = sha256_file(output_path)
                    warnings += len(item_warnings)
                    exported += 1
                    db.record_export_item(
                        job_id,
                        int(item["id"]),
                        item.get("mailbox_id"),
                        output_path,
                        output_hash,
                        export_format,
                        "exported",
                        item_warnings,
                    )
                    manifest_items.append(manifest_item(item, output_path, output_hash, item_warnings))
        else:
            raise ValueError(f"Unsupported export format: {export_format}")

        manifest_path = output_root / "millie-export-manifest.json"
        manifest = {
            "millie_version": __version__,
            "export_job_id": job_id,
            "target_profile": profile.id,
            "target_profile_display_name": profile.display_name,
            "profile": profile.to_api(),
            "format": export_format,
            "created_at": utc_now(),
            "source_filters": {
                "mailbox_id": mailbox_id,
                "message_ids": message_ids or [],
            },
            "message_count": exported,
            "unique_message_count": len(unique_message_ids),
            "folder_count": len(mailbox_paths),
            "attachment_count": attachment_count,
            "source_ids": source_ids,
            "error_count": errors,
            "warning_count": warnings,
            "import_instructions": list(profile.import_instructions),
            "known_limitations": list(profile.limitations),
            "items": manifest_items,
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        db.finish_export_job(job_id, "completed", exported, errors, warnings, str(manifest_path))
        return ExportResult(job_id, exported, errors, warnings, manifest_path)
    except Exception:
        db.finish_export_job(job_id, "failed", exported, errors + 1, warnings, None)
        raise


def raw_for_export(db: MillieDatabase, item: dict[str, object], warnings: list[str]) -> bytes:
    ref = item.get("raw_message_ref")
    if not ref:
        warnings.append("raw_message_missing_reconstructed_export_not_available")
        raise ValueError(f"Message {item['id']} has no raw MIME content to export")
    return db.read_blob(str(ref))


def group_by_mailbox(messages: list[dict[str, object]]) -> dict[str, list[dict[str, object]]]:
    grouped: dict[str, list[dict[str, object]]] = {}
    for item in messages:
        grouped.setdefault(str(item.get("mailbox_path") or "Imported"), []).append(item)
    return grouped


def write_mbox_message(handle, raw: bytes) -> None:  # type: ignore[no-untyped-def]
    parsed = BytesParser().parsebytes(raw)
    sender = parsed.get("from", "unknown").split()[-1].strip("<>") or "unknown"
    date_text = time.strftime("%a %b %d %H:%M:%S %Y", time.gmtime())
    handle.write(f"From {sender} {date_text}\n".encode("utf-8"))
    for line in raw.splitlines(keepends=True):
        if line.startswith(b"From "):
            handle.write(b">")
        handle.write(line)
    if not raw.endswith(b"\n"):
        handle.write(b"\n")
    handle.write(b"\n")


def manifest_item(
    item: dict[str, object],
    output_path: Path,
    output_hash: str | None,
    warnings: list[str],
) -> dict[str, object]:
    return {
        "message_id": item["id"],
        "source_id": item.get("source_id"),
        "mailbox_id": item.get("mailbox_id"),
        "mailbox_path": item.get("mailbox_path"),
        "subject": item.get("subject"),
        "content_hash": item.get("content_hash"),
        "output_path": str(output_path),
        "output_hash": output_hash,
        "warnings": warnings,
    }
