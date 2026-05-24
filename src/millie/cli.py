from __future__ import annotations

import argparse
from getpass import getpass
import json
from pathlib import Path

from .config import AppConfig
from .doctor import run_doctor
from .exporters import export_messages
from .importers import import_path
from .imap_connector import (
    ImapSourceConfig,
    delete_imap_source,
    discover_imap_folders,
    get_imap_source,
    load_imap_sources,
    migrate_imap_source_secrets,
    save_imap_source,
    sync_imap_source,
)
from .api.server import run_server
from .profiles import ProfileManager
from .secrets import SecretManager
from .source_scanners import scan_source


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="millie", description="MILLIE email archive toolkit")
    parser.add_argument("--db", default=None, help="SQLite database path")
    parser.add_argument("--data-dir", default=None, help="Content-addressed data directory")
    parser.add_argument("--settings", "--profiles", dest="settings", default=None, help="Global SQLite settings file")
    parser.add_argument("--profiles-dir", default=None, help="Directory for profile databases and data")
    parser.add_argument(
        "--secret-backend",
        default=None,
        choices=["auto", "keychain", "local"],
        help="Secret backend for connector credentials",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="Initialize the SQLite database")
    subparsers.add_parser("profiles", help="List available profiles")

    doctor = subparsers.add_parser("doctor", help="Check local development prerequisites")
    doctor.add_argument("--install", action="store_true", help="Offer to install missing local dependencies")
    doctor.add_argument("--yes", action="store_true", help="Assume yes for doctor install prompts")

    profile_create = subparsers.add_parser("profile-create", help="Create a profile and make it active")
    profile_create.add_argument("name", help="Profile display name")

    profile_use = subparsers.add_parser("profile-use", help="Switch the active profile")
    profile_use.add_argument("profile_id", help="Profile id")

    serve = subparsers.add_parser("serve", help="Run the local web/API server")
    serve.add_argument("--host", default=None, help="Host to bind, default 0.0.0.0")
    serve.add_argument("--port", type=int, default=None, help="Port to bind, default 22001")
    serve.add_argument("--web-dir", default=None, help="Built web app directory")

    import_cmd = subparsers.add_parser("import", help="Import email from a file or folder")
    import_cmd.add_argument("path", help="Path to .eml, .mbox, maildir, or folder of .eml files")
    import_cmd.add_argument(
        "--format",
        default="auto",
        choices=["auto", "eml", "eml-dir", "mbox", "maildir", "pst", "olm", "ost"],
    )
    import_cmd.add_argument("--source-name", default=None)

    scan_cmd = subparsers.add_parser("scan", help="Scan a source path for importable mailbox candidates")
    scan_cmd.add_argument("path", help="Path to a mail file, mail folder, or desktop client profile")
    scan_cmd.add_argument("--type", default="auto", choices=["auto", "generic", "thunderbird", "evolution", "apple-mail"])
    scan_cmd.add_argument("--json", action="store_true", help="Print scan results as JSON")

    imap_sources = subparsers.add_parser("imap-sources", help="List saved IMAP sources for the active profile")
    imap_sources.add_argument("--json", action="store_true", help="Print source configs as JSON with secrets redacted")

    imap_add = subparsers.add_parser("imap-add", help="Save an IMAP source for the active profile")
    imap_add.add_argument("name", help="Source display name")
    imap_add.add_argument("--id", dest="source_id", default=None, help="Existing source id to update")
    imap_add.add_argument("--host", required=True, help="IMAP server host")
    imap_add.add_argument("--port", type=int, default=None, help="IMAP server port, default 993 with TLS")
    imap_add.add_argument("--username", required=True, help="IMAP username")
    imap_add.add_argument("--password", default=None, help="IMAP password or app password")
    imap_add.add_argument("--folder", action="append", dest="folders", help="Folder to sync, repeatable")
    imap_add.add_argument("--limit", type=int, default=100, help="Maximum new UIDs to attempt per sync")
    imap_add.add_argument("--no-tls", action="store_true", help="Use plain IMAP instead of IMAPS")

    imap_sync = subparsers.add_parser("imap-sync", help="Run read-only sync for a saved IMAP source")
    imap_sync.add_argument("source_id", help="Saved IMAP source id")

    imap_folders = subparsers.add_parser("imap-folders", help="Discover folders for a saved IMAP source")
    imap_folders.add_argument("source_id", help="Saved IMAP source id")
    imap_folders.add_argument("--json", action="store_true", help="Print discovered folders as JSON")

    imap_set_folders = subparsers.add_parser("imap-set-folders", help="Set folders for a saved IMAP source")
    imap_set_folders.add_argument("source_id", help="Saved IMAP source id")
    imap_set_folders.add_argument("--folder", action="append", dest="folders", required=True, help="Folder to sync, repeatable")

    imap_delete = subparsers.add_parser("imap-delete", help="Delete a saved IMAP source")
    imap_delete.add_argument("source_id", help="Saved IMAP source id")

    subparsers.add_parser("secrets-status", help="Show the active secret backend")
    subparsers.add_parser("imap-migrate-secrets", help="Move legacy IMAP passwords out of source configs")

    export_cmd = subparsers.add_parser("export", help="Export messages to a mailbox format")
    export_cmd.add_argument("--format", required=True, choices=["auto", "eml", "mbox", "maildir"])
    export_cmd.add_argument("--output", required=True, help="Output directory")
    export_cmd.add_argument("--profile", default="generic-eml")
    export_cmd.add_argument("--mailbox-id", type=int, default=None)
    export_cmd.add_argument("--message-id", action="append", type=int, dest="message_ids")
    return parser


def config_from_args(args: argparse.Namespace) -> AppConfig:
    config = AppConfig.from_env()
    if args.db:
        config.db_path = Path(args.db)
    if args.data_dir:
        config.data_dir = Path(args.data_dir)
    if args.settings:
        config.settings_path = Path(args.settings)
    if args.profiles_dir:
        config.profiles_dir = Path(args.profiles_dir)
    if getattr(args, "host", None):
        config.host = args.host
    if getattr(args, "port", None):
        config.port = args.port
    if getattr(args, "web_dir", None):
        config.web_dir = Path(args.web_dir)
    return config.resolved()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "doctor":
        return run_doctor(args, Path(__file__).resolve().parents[2])

    config = config_from_args(args)
    profile_manager = ProfileManager(
        config.settings_path,
        config.profiles_dir,
        config.db_path,
        config.data_dir,
    )
    db = profile_manager.active_database()
    secret_manager = SecretManager(profile_manager, args.secret_backend)

    if args.command == "init-db":
        db.init()
        active = profile_manager.active_profile()
        print(f"Initialized profile {active.id} at {active.db_path}")
        return 0
    if args.command == "profiles":
        for profile in profile_manager.list_profiles():
            marker = "*" if profile["id"] == profile_manager.active_profile_id else " "
            print(f"{marker} {profile['id']}: {profile['name']} ({profile['db_path']})")
        return 0
    if args.command == "profile-create":
        profile = profile_manager.create_profile(args.name, switch=True)
        print(f"Created and selected profile {profile.id}: {profile.name}")
        return 0
    if args.command == "profile-use":
        profile = profile_manager.set_active(args.profile_id)
        print(f"Selected profile {profile.id}: {profile.name}")
        return 0
    if args.command == "serve":
        run_server(config, args.secret_backend)
        return 0
    if args.command == "import":
        try:
            result = import_path(db, Path(args.path), args.format, args.source_name)
        except Exception as exc:  # noqa: BLE001
            print(f"Import failed: {exc}")
            return 1
        print(
            f"Import job {result.import_job_id}: processed={result.processed} "
            f"imported={result.imported} duplicates={result.duplicates} "
            f"errors={result.errors} format={result.format}"
        )
        return 0 if result.errors == 0 else 1
    if args.command == "scan":
        candidates = scan_source(Path(args.path), args.type)
        if args.json:
            print(
                json.dumps(
                    {
                        "path": str(Path(args.path).expanduser().resolve()),
                        "source_type": args.type,
                        "candidates": [candidate.to_api() for candidate in candidates],
                    },
                    indent=2,
                )
            )
        else:
            print(f"Found {len(candidates)} candidate(s)")
            for candidate in candidates:
                estimate = (
                    "unknown"
                    if candidate.message_estimate is None
                    else str(candidate.message_estimate)
                )
                print(
                    f"- {candidate.display_name}: {candidate.format}, "
                    f"{estimate} message(s), "
                    f"{'importable' if candidate.importable else 'not importable yet'}, "
                    f"{candidate.path}"
                )
        return 0
    if args.command == "imap-sources":
        sources = load_imap_sources(profile_manager)
        if args.json:
            print(json.dumps({"sources": [source.to_api() for source in sources]}, indent=2))
        else:
            if not sources:
                print("No IMAP sources saved for the active profile.")
            for source in sources:
                folders = ", ".join(source.folders)
                security = "TLS" if source.use_tls else "plain"
                secret = source.to_api().get("secret_backend") or "no secret"
                print(
                    f"- {source.id}: {source.name} "
                    f"({source.username}@{source.host}:{source.port}, "
                    f"{security}, {secret}, folders={folders})"
                )
        return 0
    if args.command == "imap-add":
        use_tls = not args.no_tls
        password = args.password if args.password is not None else getpass("IMAP password/app password: ")
        source = save_imap_source(
            profile_manager,
            {
                "id": args.source_id,
                "name": args.name,
                "host": args.host,
                "port": args.port or (993 if use_tls else 143),
                "username": args.username,
                "password": password,
                "use_tls": use_tls,
                "folders": args.folders or ["INBOX"],
                "sync_limit": args.limit,
            },
            secret_manager,
        )
        print(f"Saved IMAP source {source.id}: {source.name}")
        return 0
    if args.command == "imap-folders":
        try:
            source = get_imap_source(profile_manager, args.source_id, secret_manager)
            folders = discover_imap_folders(source)
        except Exception as exc:  # noqa: BLE001
            print(f"IMAP folder discovery failed: {exc}")
            return 1
        if args.json:
            print(json.dumps({"folders": [folder.to_api() for folder in folders]}, indent=2))
        else:
            for folder in folders:
                marker = " " if folder.selectable else "-"
                print(f"{marker} {folder.name} ({','.join(folder.flags) or 'no flags'})")
        return 0
    if args.command == "imap-set-folders":
        try:
            source = get_imap_source(profile_manager, args.source_id, secret_manager)
            updated = save_imap_source(profile_manager, source_update_payload(source, args.folders), secret_manager)
        except Exception as exc:  # noqa: BLE001
            print(f"IMAP folder update failed: {exc}")
            return 1
        print(f"Updated IMAP source {updated.id}: folders={','.join(updated.folders)}")
        return 0
    if args.command == "imap-delete":
        deleted = delete_imap_source(profile_manager, args.source_id, secret_manager)
        if not deleted:
            print(f"Unknown IMAP source: {args.source_id}")
            return 1
        print(f"Deleted IMAP source {args.source_id}")
        return 0
    if args.command == "imap-sync":
        try:
            source = get_imap_source(profile_manager, args.source_id, secret_manager)
            result = sync_imap_source(db, source)
        except Exception as exc:  # noqa: BLE001
            print(f"IMAP sync failed: {exc}")
            return 1
        print(
            f"Import job {result.import_job_id}: processed={result.processed} "
            f"imported={result.imported} duplicates={result.duplicates} "
            f"errors={result.errors} folders={','.join(result.folders)}"
        )
        return 0 if result.errors == 0 else 1
    if args.command == "secrets-status":
        print(json.dumps(secret_manager.status(), indent=2))
        return 0
    if args.command == "imap-migrate-secrets":
        migrated = migrate_imap_source_secrets(profile_manager, secret_manager)
        print(f"Migrated {migrated} IMAP secret(s).")
        return 0
    if args.command == "export":
        result = export_messages(
            db,
            Path(args.output),
            args.format,
            target_profile=args.profile,
            mailbox_id=args.mailbox_id,
            message_ids=args.message_ids,
        )
        print(
            f"Export job {result.export_job_id}: exported={result.exported} "
            f"errors={result.errors} warnings={result.warnings} manifest={result.manifest_path}"
        )
        return 0 if result.errors == 0 else 1
    parser.error(f"Unknown command: {args.command}")
    return 2


def source_update_payload(source: ImapSourceConfig, folders: list[str]) -> dict[str, object]:
    return {
        "id": source.id,
        "name": source.name,
        "host": source.host,
        "port": source.port,
        "username": source.username,
        "use_tls": source.use_tls,
        "folders": folders,
        "sync_limit": source.sync_limit,
        "auth_method": source.auth_method,
    }
