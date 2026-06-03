#!/usr/bin/env python3
"""Run MILLIE live-mail sync while this process is active."""

from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from millie.sync.live_mail import LiveSyncConfig, run_sync_loop, run_sync_once


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check configured live mailboxes and import new IMAP/OAuth messages into MILLIE."
    )
    parser.add_argument(
        "--account",
        action="append",
        default=[],
        help="Account email/id/display name to sync. May be repeated. Defaults to all enabled IMAP accounts.",
    )
    parser.add_argument(
        "--folder",
        action="append",
        default=[],
        help="Exact IMAP folder name to sync. May be repeated. Defaults to all selectable folders.",
    )
    parser.add_argument("--once", action="store_true", help="Run one sync pass and exit.")
    parser.add_argument(
        "--interval",
        type=int,
        default=900,
        help="Seconds between sync passes when not using --once.",
    )
    parser.add_argument("--fetch-batch-size", type=int, default=10)
    parser.add_argument("--commit-every", type=int, default=50)
    parser.add_argument("--imap-timeout", type=int, default=120)
    parser.add_argument("--include-non-mail-folders", action="store_true")
    parser.add_argument("--stop-on-error", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = LiveSyncConfig(
        accounts=tuple(args.account),
        folders=tuple(args.folder),
        interval_seconds=args.interval,
        fetch_batch_size=args.fetch_batch_size,
        commit_every=args.commit_every,
        imap_timeout_seconds=args.imap_timeout,
        include_non_mail_folders=args.include_non_mail_folders,
        stop_on_error=args.stop_on_error,
    )
    if args.once:
        return run_sync_once(config)

    stop_event = threading.Event()
    try:
        run_sync_loop(config, stop_event=stop_event, run_immediately=True)
    except KeyboardInterrupt:
        stop_event.set()
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
