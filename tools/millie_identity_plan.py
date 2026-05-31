#!/usr/bin/env python3
"""Generate dormant Postgres bootstrap SQL for a MILLIE login/mailbox."""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from millie.service.auth import MillieIdentity, build_identity_sql, hash_password


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate SQL for a Postgres-backed MILLIE identity and mailbox. "
            "This does not connect to Postgres or start any service."
        )
    )
    parser.add_argument(
        "--login",
        required=True,
        help="MILLIE login address, for example geon@MILLIE.",
    )
    parser.add_argument("--display-name", default="", help="Display name for the mailbox.")
    password_group = parser.add_mutually_exclusive_group()
    password_group.add_argument(
        "--password-env",
        help="Environment variable containing the initial password.",
    )
    password_group.add_argument(
        "--password-prompt",
        action="store_true",
        help="Prompt for the initial password without echoing it.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional ignored file path for the generated SQL.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    password_hash = None
    if args.password_env:
        password = os.environ.get(args.password_env)
        if not password:
            raise SystemExit(f"Password env var is empty or missing: {args.password_env}")
        password_hash = hash_password(password)
    elif args.password_prompt:
        password = getpass.getpass("Initial MILLIE password: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            raise SystemExit("Passwords do not match.")
        password_hash = hash_password(password)

    identity = MillieIdentity(login_address=args.login, display_name=args.display_name)
    sql = build_identity_sql(identity, password_hash=password_hash)

    if args.output:
        output = args.output.expanduser()
        if not output.is_absolute():
            output = (Path.cwd() / output).resolve()
        if ".private" not in output.parts:
            raise SystemExit("Refusing to write bootstrap SQL outside ignored .private/.")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(sql)
        print(f"Wrote {output}")
    else:
        print(sql, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
