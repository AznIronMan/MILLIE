#!/usr/bin/env python3
"""Plan or import PST files into the MILLIE mailbox facade."""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from millie.importing.models import stable_id
from millie.importing.normalize import normalize_email
from millie.importing.sources import ImportSourceError, PstSource
from millie.service.auth import default_service_login, identity_from_settings
from millie.settings_loader import load_local_settings


DEFAULT_PST_ROOT = Path("/Users/ironman/HomeDrive/Outlook Files")
DEFAULT_EXTRACT_ROOT = PROJECT_ROOT / ".private" / "local" / "pst-bulk-extract"


@dataclass(slots=True)
class PstImportPlan:
    path: Path
    archive_label: str
    mailbox_root: str
    extract_dir: Path


@dataclass(slots=True)
class ImportStats:
    scanned: int = 0
    imported: int = 0
    skipped_existing: int = 0
    failed: int = 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Plan or import PST files into MILLIE. By default this is a dry run; "
            "pass --apply to extract and write to Postgres."
        )
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        default=[DEFAULT_PST_ROOT],
        help="PST file or directory path. Defaults to /Users/ironman/HomeDrive/Outlook Files.",
    )
    parser.add_argument("--apply", action="store_true", help="Extract PSTs and write messages to MILLIE.")
    parser.add_argument("--login", default="", help="MILLIE login. Defaults to geon@<service_mail_domain>.")
    parser.add_argument("--display-name", default="Geon", help="MILLIE mailbox display name.")
    parser.add_argument("--limit-per-pst", type=int, default=0, help="Import at most this many messages per PST.")
    parser.add_argument("--commit-every", type=int, default=250, help="Commit after this many imported messages.")
    parser.add_argument("--clean-extract", action="store_true", help="Remove existing ignored extraction output first.")
    parser.add_argument("--replace-existing", action="store_true", help="Replace existing source messages and remap UIDs.")
    parser.add_argument("--map-inbox", action="store_true", help="Also map imported PST messages into INBOX.")
    parser.add_argument("--stop-on-error", action="store_true", help="Stop instead of continuing when one PST fails.")
    parser.add_argument("--readpst-bin", default=shutil.which("readpst") or "readpst")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    pst_paths = discover_psts(args.paths)
    if not pst_paths:
        raise SystemExit("No .pst files found.")
    plans = build_plans(pst_paths)
    print_plan(plans, apply=args.apply, limit_per_pst=args.limit_per_pst)
    if not args.apply:
        print()
        print("Dry run only. Re-run with --apply to extract PSTs and write to MILLIE.")
        return 0
    import_plans(plans, args)
    return 0


def discover_psts(paths: list[Path]) -> list[Path]:
    found: list[Path] = []
    for path in paths:
        resolved = resolve_path(path)
        if resolved.is_file() and resolved.suffix.lower() == ".pst":
            found.append(resolved)
        elif resolved.is_dir():
            found.extend(sorted(item.resolve() for item in resolved.rglob("*.pst") if item.is_file()))
        else:
            print(f"Skipping missing/non-PST path: {path}")
    return sorted(dict.fromkeys(found))


def build_plans(pst_paths: list[Path]) -> list[PstImportPlan]:
    labels = archive_labels(pst_paths)
    return [
        PstImportPlan(
            path=path,
            archive_label=labels[path],
            mailbox_root=f"Sources/PST/{labels[path]}",
            extract_dir=default_extract_dir(path),
        )
        for path in pst_paths
    ]


def archive_labels(paths: list[Path]) -> dict[Path, str]:
    counts: dict[str, int] = {}
    result: dict[Path, str] = {}
    for path in paths:
        base = safe_folder_component(path.stem)
        counts[base] = counts.get(base, 0) + 1
        result[path] = base if counts[base] == 1 else f"{base}-{counts[base]}"
    return result


def default_extract_dir(path: Path) -> Path:
    stat = path.stat()
    suffix = stable_id("pst_extract", str(path), stat.st_size, int(stat.st_mtime)).replace("-", "")[:12]
    return DEFAULT_EXTRACT_ROOT / f"{safe_folder_component(path.stem)}-{suffix}"


def print_plan(plans: list[PstImportPlan], *, apply: bool, limit_per_pst: int) -> None:
    print("MILLIE PST bulk import plan")
    print(f"Mode: {'apply' if apply else 'dry-run'}")
    print(f"PST files: {len(plans)}")
    print(f"Total size: {format_bytes(sum(plan.path.stat().st_size for plan in plans))}")
    if limit_per_pst:
        print(f"Limit per PST: {limit_per_pst} messages")
    print()
    for plan in plans:
        print(f"- {display_path(plan.path)}")
        print(f"  size: {format_bytes(plan.path.stat().st_size)}")
        print(f"  mailbox root: {plan.mailbox_root}")
        print(f"  extract dir: {display_path(plan.extract_dir)}")


def import_plans(plans: list[PstImportPlan], args: argparse.Namespace) -> None:
    from millie.storage.postgres_store import PostgresMailStore

    config = load_local_settings()
    settings = config["settings"]
    login = args.login or default_service_login(settings, "geon")
    identity = identity_from_settings(login, args.display_name, settings)
    readpst = shutil.which(args.readpst_bin) if "/" not in args.readpst_bin else args.readpst_bin
    if not readpst or ("/" in args.readpst_bin and not Path(readpst).exists()):
        raise SystemExit("readpst is not installed. On macOS, install libpst with Homebrew.")

    totals = ImportStats()
    store = PostgresMailStore.connect(settings)
    try:
        store.initialize()
        mailbox_id = store.ensure_identity(identity)
        store.connection.commit()
        for plan in plans:
            try:
                stats = import_one_pst(store, mailbox_id=mailbox_id, plan=plan, args=args)
                totals.scanned += stats.scanned
                totals.imported += stats.imported
                totals.skipped_existing += stats.skipped_existing
            except Exception as exc:  # noqa: BLE001 - keep bulk imports moving unless requested.
                store.connection.rollback()
                totals.failed += 1
                print(f"FAILED {display_path(plan.path)}: {type(exc).__name__}: {exc}")
                if args.stop_on_error:
                    raise
                continue
            finally:
                store.connection.commit()
        print(
            "millie_pst_bulk_import=done "
            f"scanned={totals.scanned} imported={totals.imported} "
            f"skipped_existing={totals.skipped_existing} failed={totals.failed}"
        )
    finally:
        store.close()


def import_one_pst(
    store,
    *,
    mailbox_id: str,
    plan: PstImportPlan,
    args: argparse.Namespace,
) -> ImportStats:
    print(f"Importing {display_path(plan.path)} -> {plan.mailbox_root}")
    store.ensure_mailbox_folder(mailbox_id, plan.mailbox_root)
    source_id = store.upsert_source(
        source_type="pst",
        source_uri=str(plan.path),
        display_name=plan.archive_label,
        auth_mode=None,
        is_active=False,
    )
    job_id = store.create_import_job(
        source_id=source_id,
        mode="pst_bulk_import",
        metadata={
            "pst_path": str(plan.path),
            "archive_label": plan.archive_label,
            "mailbox_root": plan.mailbox_root,
        },
    )
    source = PstSource(
        pst_path=plan.path,
        output_dir=plan.extract_dir,
        readpst_bin=args.readpst_bin,
    )
    stats = ImportStats()
    try:
        iterator = source.iter_messages(clean=args.clean_extract)
        for raw_message in iterator:
            stats.scanned += 1
            if args.limit_per_pst and stats.scanned > args.limit_per_pst:
                break
            if (
                not args.replace_existing
                and store.source_message_exists(
                    source_id=source_id,
                    source_message_id=raw_message.source_message_id,
                )
            ):
                stats.skipped_existing += 1
                continue
            target_folder = pst_target_folder(plan.mailbox_root, raw_message.folder)
            store.ensure_mailbox_folder(mailbox_id, target_folder)
            normalized = normalize_email(
                raw_message.raw_bytes,
                source_message_id=raw_message.source_message_id,
                source_uri=str(plan.path),
                folder=raw_message.folder,
                metadata={
                    **raw_message.metadata,
                    "pst_archive_label": plan.archive_label,
                    "millie_mailbox_folder": target_folder,
                },
            )
            store.store_message(
                source_id=source_id,
                import_job_id=job_id,
                message=normalized,
                folder=raw_message.folder,
            )
            store.map_message_to_mailbox(
                mailbox_id=mailbox_id,
                folder_path=target_folder,
                message_id=normalized.id,
            )
            store.map_message_to_mailbox(
                mailbox_id=mailbox_id,
                folder_path="All Mail",
                message_id=normalized.id,
            )
            if args.map_inbox:
                store.map_message_to_mailbox(
                    mailbox_id=mailbox_id,
                    folder_path="INBOX",
                    message_id=normalized.id,
                )
            stats.imported += 1
            if stats.imported % max(args.commit_every, 1) == 0:
                store.connection.commit()
                print(f"  imported={stats.imported} scanned={stats.scanned}")
    except ImportSourceError:
        raise
    print(
        "  done "
        f"scanned={stats.scanned} imported={stats.imported} "
        f"skipped_existing={stats.skipped_existing}"
    )
    return stats


def pst_target_folder(mailbox_root: str, original_folder: str | None) -> str:
    if not original_folder:
        return mailbox_root
    cleaned = "/".join(
        safe_folder_component(part)
        for part in original_folder.replace("\\", "/").split("/")
        if part and part != "."
    )
    return f"{mailbox_root}/{cleaned}" if cleaned else mailbox_root


def safe_folder_component(value: str) -> str:
    text = re.sub(r"[\r\n\t/\\:]+", "_", value).strip(" ._")
    text = re.sub(r"\s+", " ", text)
    return text or "Archive"


def resolve_path(path: Path) -> Path:
    expanded = path.expanduser()
    if expanded.is_absolute():
        return expanded.resolve()
    return (Path.cwd() / expanded).resolve()


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def format_bytes(value: int) -> str:
    amount = float(value)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if amount < 1024 or unit == "TB":
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024
    return f"{value} B"


if __name__ == "__main__":
    raise SystemExit(main())
