#!/usr/bin/env python3
"""Print a dormant MILLIE mail import plan without extracting or writing data."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from millie.storage.schema import schema_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Describe how MILLIE would import a mail source. This command does "
            "not connect to IMAP, extract PST contents, or write database rows."
        )
    )
    parser.add_argument(
        "--source",
        choices=("pst", "imap", "exchange-oauth"),
        required=True,
        help="Source type to plan.",
    )
    parser.add_argument(
        "--database",
        choices=("sqlite", "postgres"),
        default="sqlite",
        help="Target schema dialect to plan.",
    )
    parser.add_argument("--pst", type=Path, help="PST path for PST planning.")
    parser.add_argument("--host", help="IMAP host for IMAP planning.")
    parser.add_argument("--port", type=int, help="IMAP port for IMAP planning.")
    parser.add_argument("--mailbox", default="INBOX", help="IMAP mailbox name.")
    parser.add_argument("--username", help="IMAP username.")
    parser.add_argument("--password-env", help="PST or IMAP password env var name.")
    parser.add_argument("--oauth-token-env", help="OAuth access token env var name.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    print("MILLIE mail import plan")
    print("Status: dormant; no extraction, network connection, or DB write performed")
    print(f"Source: {args.source}")
    print(f"Database schema: {schema_path(args.database)}")
    print()

    if args.source == "pst":
        describe_pst(args)
    else:
        describe_imap(args)

    print()
    print("Pipeline:")
    print("  1. Extract RFC822 message bytes from the source in read-only mode.")
    print("  2. Preserve source provenance and original raw MIME bytes.")
    print("  3. Normalize headers, addresses, dates, bodies, MIME parts, attachments, and inline items.")
    print("  4. Store the connected graph in mail_* tables.")
    print("  5. Populate search documents for SQLite FTS5 or PostgreSQL tsvector indexing.")
    print("  6. Rehydrate messages from raw MIME plus connected DB records when called.")
    return 0


def describe_pst(args: argparse.Namespace) -> None:
    readpst = shutil.which("readpst")
    print(f"readpst: {readpst or 'not found'}")
    if args.pst:
        pst = args.pst.expanduser()
        if not pst.is_absolute():
            pst = (Path.cwd() / pst).resolve()
        print(f"PST path: {display_path(pst)}")
        print(f"PST exists: {pst.is_file()}")
    else:
        print("PST path: not provided")

    password_state = "none"
    if args.password_env:
        password_state = "available" if os.environ.get(args.password_env) else "missing"
    print(f"PST password input: {password_state}")
    print("PST password backend: readpst currently has no password parameter")
    print("PST output staging: .private/local/pst-extract/<pst-name>-<hash>/")


def describe_imap(args: argparse.Namespace) -> None:
    source = "exchange_imap_oauth" if args.source == "exchange-oauth" else "imap"
    auth = "oauth" if args.source == "exchange-oauth" else "password"
    print(f"Normalized source type: {source}")
    print(f"Host: {args.host or 'not provided'}")
    print(f"Port: {args.port or default_port(auth)}")
    print(f"Mailbox: {args.mailbox}")
    print(f"Username: {args.username or 'not provided'}")
    if auth == "oauth":
        state = "available" if args.oauth_token_env and os.environ.get(args.oauth_token_env) else "missing"
        print(f"OAuth token input: {state}")
    else:
        state = "available" if args.password_env and os.environ.get(args.password_env) else "missing"
        print(f"Password input: {state}")
    print("IMAP fetch mode: readonly SELECT plus BODY.PEEK[] by UID")


def default_port(auth: str) -> int:
    return 993 if auth in {"password", "oauth"} else 143


def display_path(path: Path) -> str:
    project_root = Path(__file__).resolve().parents[1]
    try:
        return str(path.resolve().relative_to(project_root))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
