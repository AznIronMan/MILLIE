from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from email.parser import BytesParser
from pathlib import Path
from typing import Any

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


@dataclass(slots=True)
class ExportVerificationResult:
    manifest_path: Path
    ok: bool
    checked_count: int
    missing_count: int
    mismatch_count: int
    warning_count: int
    messages: list[str]

    def to_api(self) -> dict[str, object]:
        return {
            "manifest_path": str(self.manifest_path),
            "ok": self.ok,
            "checked_count": self.checked_count,
            "missing_count": self.missing_count,
            "mismatch_count": self.mismatch_count,
            "warning_count": self.warning_count,
            "messages": self.messages,
        }


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
                manifest_items.append(manifest_item(item, output_path, output_hash, item_warnings, export_format))
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
                        manifest_items.append(manifest_item(item, output_path, None, item_warnings, export_format))
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
                    manifest_items.append(manifest_item(item, output_path, output_hash, item_warnings, export_format))
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
            "fidelity": export_fidelity_summary(manifest_items, export_format),
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
    export_format: str,
) -> dict[str, object]:
    content_hash = item.get("content_hash")
    output_matches_raw = output_hash == content_hash if output_hash else None
    return {
        "message_id": item["id"],
        "source_id": item.get("source_id"),
        "mailbox_id": item.get("mailbox_id"),
        "mailbox_path": item.get("mailbox_path"),
        "subject": item.get("subject"),
        "content_hash": item.get("content_hash"),
        "raw_message_hash": content_hash,
        "raw_mime_preserved": True,
        "output_matches_raw": output_matches_raw,
        "containerized": export_format == "mbox",
        "output_path": str(output_path),
        "output_hash": output_hash,
        "warnings": warnings,
    }


def export_fidelity_summary(items: list[dict[str, object]], export_format: str) -> dict[str, object]:
    return {
        "strategy": "raw_mime_first",
        "format": export_format,
        "raw_mime_preserved_count": sum(1 for item in items if item.get("raw_mime_preserved")),
        "reconstructed_count": sum(1 for item in items if not item.get("raw_mime_preserved")),
        "output_hash_verified_count": sum(1 for item in items if item.get("output_matches_raw") is True),
        "containerized_count": sum(1 for item in items if item.get("containerized")),
        "validation_notes": fidelity_notes(export_format),
    }


def fidelity_notes(export_format: str) -> list[str]:
    if export_format == "mbox":
        return [
            "Raw MIME is written into an MBOX container, so per-message hashes are preserved as raw_message_hash.",
            "MBOX output_hash values identify the full mailbox container file.",
        ]
    return ["output_matches_raw verifies the exported file bytes match the stored raw MIME hash."]


def verify_export_manifest(manifest_path: Path) -> ExportVerificationResult:
    manifest_path = manifest_path.expanduser().resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("Export manifest must be a JSON object")
    items = manifest.get("items")
    if not isinstance(items, list):
        raise ValueError("Export manifest is missing an items list")

    checked = 0
    missing = 0
    mismatches = 0
    warnings = 0
    messages: list[str] = []
    hash_cache: dict[str, str] = {}

    expected_message_count = int(manifest.get("message_count") or 0)
    if expected_message_count != len(items):
        warnings += 1
        messages.append(f"Manifest message_count={expected_message_count} but items={len(items)}")

    for item in items:
        if not isinstance(item, dict):
            warnings += 1
            messages.append("Skipped non-object manifest item")
            continue
        output_path = item_path(item)
        if output_path is None:
            warnings += 1
            messages.append(f"Message {item.get('message_id')} has no output_path")
            continue
        expected_hash = string_or_none(item.get("output_hash"))
        if not output_path.exists():
            missing += 1
            messages.append(f"Missing output file: {output_path}")
            continue
        if expected_hash:
            checked += 1
            output_key = str(output_path)
            actual_hash = hash_cache.get(output_key)
            if actual_hash is None:
                actual_hash = sha256_file(output_path)
                hash_cache[output_key] = actual_hash
            if actual_hash != expected_hash:
                mismatches += 1
                messages.append(f"Hash mismatch for {output_path}")
        if item.get("output_matches_raw") is True:
            raw_hash = string_or_none(item.get("raw_message_hash") or item.get("content_hash"))
            actual_hash = hash_cache.get(str(output_path))
            if actual_hash is None:
                actual_hash = sha256_file(output_path)
                hash_cache[str(output_path)] = actual_hash
            if raw_hash and actual_hash != raw_hash:
                mismatches += 1
                messages.append(f"Raw MIME hash mismatch for {output_path}")

    ok = missing == 0 and mismatches == 0
    if ok and not messages:
        messages.append("Export manifest verified")
    return ExportVerificationResult(manifest_path, ok, checked, missing, mismatches, warnings, messages)


def item_path(item: dict[str, Any]) -> Path | None:
    raw = string_or_none(item.get("output_path"))
    return Path(raw).expanduser().resolve() if raw else None


def string_or_none(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None
