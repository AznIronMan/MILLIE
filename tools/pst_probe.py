#!/usr/bin/env python3
"""Read-only PST probe for MILLIE.

The probe uses libpst/readpst to extract email messages in MH format into an
ignored local directory, then prints metadata counts without dumping message
contents, senders, recipients, or subjects.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import shutil
import subprocess
from collections import Counter
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXTRACT_ROOT = PROJECT_ROOT / ".private" / "local" / "pst-extract"
MANIFEST_NAME = "pst_probe_manifest.json"


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    return cleaned or "pst"


def default_output_dir(pst_path: Path, source_hash: str) -> Path:
    return DEFAULT_EXTRACT_ROOT / f"{safe_name(pst_path.stem)}-{source_hash[:12]}"


def remove_output_dir(path: Path) -> None:
    resolved = path.resolve()
    unsafe = {
        Path("/").resolve(),
        PROJECT_ROOT.resolve(),
        Path.home().resolve(),
        DEFAULT_EXTRACT_ROOT.resolve(),
    }
    if resolved in unsafe:
        raise SystemExit(f"Refusing to clean unsafe output path: {display_path(path)}")
    if ".private" not in resolved.parts:
        raise SystemExit(
            "Refusing to clean an output path outside .private. "
            "Choose an ignored .private output path or remove it manually."
        )
    shutil.rmtree(resolved)


def run_readpst(
    readpst_bin: str,
    pst_path: Path,
    output_dir: Path,
    *,
    password_supplied: bool = False,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        readpst_bin,
        "-q",
        "-M",
        "-te",
        "-o",
        str(output_dir),
        str(pst_path),
    ]
    try:
        result = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        raise SystemExit(f"readpst not found: {readpst_bin}") from exc
    if result.returncode != 0:
        stderr = result.stderr.strip()
        stdout = result.stdout.strip()
        details = stderr or stdout or f"exit code {result.returncode}"
        if password_supplied:
            raise SystemExit(
                "readpst failed. A PST password was supplied, but the installed "
                "readpst backend has no password parameter. Configure a "
                "password-capable PST backend before importing this file. "
                f"Backend detail: {details}"
            )
        raise SystemExit(f"readpst failed: {details}")


def find_message_root(output_dir: Path) -> Path:
    children = [child for child in output_dir.iterdir() if child.name != MANIFEST_NAME]
    directories = [child for child in children if child.is_dir()]
    files = [child for child in children if child.is_file()]
    if len(directories) == 1 and not files:
        return directories[0]
    return output_dir


def parse_message_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def count_attachment_parts(message: Any) -> int:
    count = 0
    for part in message.walk():
        if part.is_multipart():
            continue
        disposition = (part.get_content_disposition() or "").lower()
        if disposition == "attachment" or part.get_filename():
            count += 1
    return count


def scan_extracted_messages(message_root: Path) -> dict[str, Any]:
    folders: Counter[str] = Counter()
    errors: list[dict[str, str]] = []
    parsed_dates: list[datetime] = []
    messages_with_attachments = 0
    attachment_parts = 0
    messages_with_message_id = 0
    messages_with_from = 0
    messages_with_to = 0
    parsed_messages = 0

    for path in sorted(message_root.rglob("*")):
        if not path.is_file() or path.name == MANIFEST_NAME:
            continue
        relative_path = path.relative_to(message_root)
        folder = str(relative_path.parent)
        if folder == ".":
            folder = "(root)"

        try:
            with path.open("rb") as file:
                message = BytesParser(policy=policy.default).parse(file)
        except Exception as exc:  # noqa: BLE001 - keep probing even if one item is malformed.
            errors.append({"path": str(relative_path), "error": type(exc).__name__})
            continue

        parsed_messages += 1
        folders[folder] += 1

        message_date = parse_message_date(message.get("Date"))
        if message_date:
            parsed_dates.append(message_date)

        attachment_count = count_attachment_parts(message)
        if attachment_count:
            messages_with_attachments += 1
            attachment_parts += attachment_count

        if message.get("Message-ID"):
            messages_with_message_id += 1
        if message.get("From"):
            messages_with_from += 1
        if message.get("To"):
            messages_with_to += 1

    return {
        "parsed_messages": parsed_messages,
        "parse_errors": len(errors),
        "parse_error_samples": errors[:20],
        "folders_with_messages": len(folders),
        "folder_counts": dict(sorted(folders.items())),
        "messages_with_attachments": messages_with_attachments,
        "total_attachment_parts": attachment_parts,
        "messages_with_message_id": messages_with_message_id,
        "messages_with_from": messages_with_from,
        "messages_with_to": messages_with_to,
        "date_min": min(parsed_dates).isoformat() if parsed_dates else None,
        "date_max": max(parsed_dates).isoformat() if parsed_dates else None,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Probe a PST with readpst in read-only source mode and summarize "
            "extracted email metadata."
        )
    )
    parser.add_argument("pst", help="Path to the source .pst file.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Extraction directory. Defaults to .private/local/pst-extract/<pst>-<sha>.",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove the output directory before extracting.",
    )
    parser.add_argument(
        "--readpst-bin",
        default=shutil.which("readpst") or "readpst",
        help="Path to the readpst executable.",
    )
    password_group = parser.add_mutually_exclusive_group()
    password_group.add_argument(
        "--password-env",
        help=(
            "Name of an environment variable containing the PST password. "
            "The value is validated but is not printed."
        ),
    )
    password_group.add_argument(
        "--password-file",
        type=Path,
        help="Path to a local file containing the PST password.",
    )
    password_group.add_argument(
        "--password-prompt",
        action="store_true",
        help="Prompt for a PST password without echoing it.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full JSON manifest instead of the compact report.",
    )
    return parser


def print_report(manifest: dict[str, Any]) -> None:
    print(f"PST: {manifest['pst_path']}")
    print(f"PST SHA-256: {manifest['pst_sha256']}")
    print(f"readpst: {manifest['readpst_bin']}")
    print(f"Extraction: {'created' if manifest['extracted_now'] else 'reused'}")
    print(f"Extract dir: {manifest['extract_dir']}")
    print(f"Manifest: {manifest['manifest_path']}")
    if manifest["pst_password_mode"] != "none":
        print(
            "PST password: provided and validated; readpst cannot receive "
            "passwords, so extraction uses the installed backend as-is"
        )
    print(
        "Messages: "
        f"{manifest['parsed_messages']} parsed, "
        f"{manifest['parse_errors']} parse errors"
    )
    print(
        "Attachments: "
        f"{manifest['messages_with_attachments']} messages, "
        f"{manifest['total_attachment_parts']} MIME parts"
    )
    print(
        "Header presence: "
        f"Message-ID {manifest['messages_with_message_id']}, "
        f"From {manifest['messages_with_from']}, "
        f"To {manifest['messages_with_to']}"
    )
    if manifest["date_min"] or manifest["date_max"]:
        print(f"Date range: {manifest['date_min']} to {manifest['date_max']}")
    print(f"Source unchanged after extraction: {manifest['source_unchanged']}")
    print("Folder counts:")
    for folder, count in manifest["folder_counts"].items():
        print(f"  {count:5d}  {folder}")


def resolve_password_mode(args: argparse.Namespace) -> str:
    if args.password_env:
        if not os.environ.get(args.password_env):
            raise SystemExit(f"PST password env var is empty or missing: {args.password_env}")
        return "env"
    if args.password_file:
        password_file = args.password_file.expanduser()
        if not password_file.is_absolute():
            password_file = (Path.cwd() / password_file).resolve()
        if not password_file.is_file():
            raise SystemExit(f"PST password file not found: {display_path(password_file)}")
        if not password_file.read_text().strip():
            raise SystemExit(f"PST password file is empty: {display_path(password_file)}")
        return "file"
    if args.password_prompt:
        if not getpass.getpass("PST password: "):
            raise SystemExit("PST password cannot be empty.")
        return "prompt"
    return "none"


def main() -> int:
    args = build_parser().parse_args()
    pst_path = args.pst
    pst = Path(pst_path).expanduser()
    if not pst.is_absolute():
        pst = (Path.cwd() / pst).resolve()
    if not pst.is_file():
        raise SystemExit(f"PST not found: {pst_path}")
    password_mode = resolve_password_mode(args)
    password_supplied = password_mode != "none"

    readpst_bin = (
        shutil.which(args.readpst_bin)
        if "/" not in args.readpst_bin
        else args.readpst_bin
    )
    if not readpst_bin or ("/" in args.readpst_bin and not Path(readpst_bin).exists()):
        raise SystemExit("readpst is not installed. On macOS, install libpst with Homebrew.")

    source_hash_before = sha256_file(pst)
    output_dir = args.output_dir or default_output_dir(pst, source_hash_before)
    if not output_dir.is_absolute():
        output_dir = (Path.cwd() / output_dir).resolve()

    extracted_now = False
    if output_dir.exists() and args.clean:
        remove_output_dir(output_dir)
    if output_dir.exists() and not (output_dir / MANIFEST_NAME).exists():
        raise SystemExit(
            "Existing PST extraction has no manifest and may be incomplete. "
            "Rerun with --clean or choose a different --output-dir."
        )
    if not output_dir.exists():
        run_readpst(readpst_bin, pst, output_dir, password_supplied=password_supplied)
        extracted_now = True

    message_root = find_message_root(output_dir)
    scan = scan_extracted_messages(message_root)
    source_hash_after = sha256_file(pst)

    manifest = {
        "pst_path": display_path(pst),
        "pst_size_bytes": pst.stat().st_size,
        "pst_sha256": source_hash_before,
        "source_hash_after": source_hash_after,
        "source_unchanged": source_hash_before == source_hash_after,
        "extract_dir": display_path(output_dir),
        "message_root": display_path(message_root),
        "manifest_path": display_path(output_dir / MANIFEST_NAME),
        "readpst_bin": readpst_bin,
        "readpst_mode": "MH email-only (-M -te)",
        "pst_password_mode": password_mode,
        "pst_password_backend": "readpst-no-password-argument",
        "extracted_now": extracted_now,
        **scan,
    }

    manifest_path = output_dir / MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    if args.json:
        print(json.dumps(manifest, indent=2, sort_keys=True))
    else:
        print_report(manifest)

    if (
        not manifest["source_unchanged"]
        or manifest["parse_errors"]
        or not manifest["parsed_messages"]
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
