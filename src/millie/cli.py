from __future__ import annotations

import argparse
from pathlib import Path

from .config import AppConfig
from .database import MillieDatabase
from .exporters import export_messages
from .importers import import_path
from .api.server import run_server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="millie", description="MILLIE email archive toolkit")
    parser.add_argument("--db", default=None, help="SQLite database path")
    parser.add_argument("--data-dir", default=None, help="Content-addressed data directory")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="Initialize the SQLite database")

    serve = subparsers.add_parser("serve", help="Run the local web/API server")
    serve.add_argument("--host", default=None, help="Host to bind, default 0.0.0.0")
    serve.add_argument("--port", type=int, default=None, help="Port to bind, default 8765")
    serve.add_argument("--web-dir", default=None, help="Built web app directory")

    import_cmd = subparsers.add_parser("import", help="Import email from a file or folder")
    import_cmd.add_argument("path", help="Path to .eml, .mbox, maildir, or folder of .eml files")
    import_cmd.add_argument("--format", default="auto", choices=["auto", "eml", "eml-dir", "mbox", "maildir"])
    import_cmd.add_argument("--source-name", default=None)

    export_cmd = subparsers.add_parser("export", help="Export messages to a mailbox format")
    export_cmd.add_argument("--format", required=True, choices=["eml", "mbox", "maildir"])
    export_cmd.add_argument("--output", required=True, help="Output directory")
    export_cmd.add_argument("--profile", default="generic")
    export_cmd.add_argument("--mailbox-id", type=int, default=None)
    export_cmd.add_argument("--message-id", action="append", type=int, dest="message_ids")
    return parser


def config_from_args(args: argparse.Namespace) -> AppConfig:
    config = AppConfig.from_env()
    if args.db:
        config.db_path = Path(args.db)
    if args.data_dir:
        config.data_dir = Path(args.data_dir)
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
    config = config_from_args(args)
    db = MillieDatabase(config.db_path, config.data_dir)

    if args.command == "init-db":
        db.init()
        print(f"Initialized {config.db_path}")
        return 0
    if args.command == "serve":
        run_server(config)
        return 0
    if args.command == "import":
        result = import_path(db, Path(args.path), args.format, args.source_name)
        print(
            f"Import job {result.import_job_id}: imported={result.imported} "
            f"errors={result.errors} format={result.format}"
        )
        return 0 if result.errors == 0 else 1
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
