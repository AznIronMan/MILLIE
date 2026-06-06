#!/usr/bin/env python3
"""Run the guarded hourly provider cleanup flow."""

from __future__ import annotations

import argparse
import fcntl
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK_FILE = PROJECT_ROOT / ".private" / "local" / "millie_hourly_provider_purge.lock"
DEFAULT_ACCOUNTS = (
    "geoff@clarktribe.com",
    "geoff@cnb.llc",
    "aznblusuazn@me.com",
    "gclark82@gmail.com",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create a provider-delete manifest for source UIDs copied into MILLIE before "
            "a safety cutoff, dry-run it, and optionally execute it."
        )
    )
    parser.add_argument(
        "--account",
        action="append",
        default=[],
        help="Account email/id/display name to include. Defaults to the four configured online accounts.",
    )
    parser.add_argument(
        "--cutoff-hours",
        type=float,
        default=24.0,
        help="Leave source UIDs copied into MILLIE within this many hours untouched.",
    )
    parser.add_argument(
        "--limit-source-uids",
        type=int,
        default=5000,
        help="Maximum source UIDs to include per hourly manifest. Use 0 for unlimited.",
    )
    parser.add_argument("--manifest-prefix", default="remote-purge-hourly")
    parser.add_argument("--execute", action="store_true", help="Execute provider deletion after dry-run succeeds.")
    parser.add_argument("--imap-timeout", type=int, default=120)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--lock-file", type=Path, default=DEFAULT_LOCK_FILE)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    args.lock_file.parent.mkdir(parents=True, exist_ok=True)
    with args.lock_file.open("w") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("hourly_provider_purge=skipped reason=already_running", flush=True)
            return 0
        return run_locked(args)


def run_locked(args: argparse.Namespace) -> int:
    accounts = args.account or list(DEFAULT_ACCOUNTS)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    cutoff = now - timedelta(hours=max(0.0, args.cutoff_hours))
    manifest_id = f"{args.manifest_prefix}-{now.strftime('%Y%m%dT%H%M%SZ')}"
    print(
        "hourly_provider_purge=starting "
        f"manifest_id={manifest_id} execute={args.execute} "
        f"cutoff_utc={cutoff.isoformat()} accounts={len(accounts)} "
        f"limit_source_uids={args.limit_source_uids}",
        flush=True,
    )

    snapshot = [
        sys.executable,
        "tools/millie_remote_purge_snapshot.py",
        "--cutoff-utc",
        cutoff.isoformat(),
        "--manifest-id",
        manifest_id,
        "--action",
        "delete",
    ]
    if args.limit_source_uids > 0:
        snapshot.extend(["--limit-source-uids", str(args.limit_source_uids)])
    for account in accounts:
        snapshot.extend(["--account", account])
    snapshot_rc = run_command(snapshot)
    if snapshot_rc != 0:
        return snapshot_rc

    dry_run = [
        sys.executable,
        "tools/millie_remote_provider_purge.py",
        "--manifest-id",
        manifest_id,
        "--imap-timeout",
        str(args.imap_timeout),
        "--batch-size",
        str(args.batch_size),
    ]
    for account in accounts:
        dry_run.extend(["--account", account])
    dry_run_rc = run_command(dry_run)
    if dry_run_rc != 0:
        return dry_run_rc

    if not args.execute:
        print(
            f"hourly_provider_purge=prepared manifest_id={manifest_id} provider_action_not_run=true",
            flush=True,
        )
        return 0

    execute = [*dry_run, "--execute"]
    execute_rc = run_command(execute)
    if execute_rc == 0:
        print(f"hourly_provider_purge=done manifest_id={manifest_id}", flush=True)
    return execute_rc


def run_command(command: list[str]) -> int:
    print("running=" + " ".join(command), flush=True)
    result = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
    print(f"command_rc={result.returncode}", flush=True)
    return int(result.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
