"""Live IMAP/OAuth sync loop used while MILLIE is running."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class LiveSyncConfig:
    accounts: tuple[str, ...] = field(default_factory=tuple)
    folders: tuple[str, ...] = field(default_factory=tuple)
    interval_seconds: int = 900
    fetch_batch_size: int = 10
    commit_every: int = 50
    imap_timeout_seconds: int = 120
    include_non_mail_folders: bool = False
    stop_on_error: bool = False


LogCallback = Callable[[str], None]


def default_log(value: str) -> None:
    print(value, flush=True)


def import_command(config: LiveSyncConfig) -> list[str]:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "tools" / "millie_imap_bulk_import.py"),
        "--apply",
        "--newer-than-existing",
        "--fetch-batch-size",
        str(config.fetch_batch_size),
        "--commit-every",
        str(config.commit_every),
        "--imap-timeout",
        str(config.imap_timeout_seconds),
    ]
    for account in config.accounts:
        command.extend(["--account", account])
    for folder in config.folders:
        command.extend(["--folder", folder])
    if config.include_non_mail_folders:
        command.append("--include-non-mail-folders")
    if config.stop_on_error:
        command.append("--stop-on-error")
    return command


def run_sync_once(config: LiveSyncConfig, log: LogCallback = default_log) -> int:
    command = import_command(config)
    log("MILLIE live sync starting")
    process = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
    )
    assert process.stdout is not None
    for line in process.stdout:
        log(f"SYNC {line.rstrip()}")
    return_code = process.wait()
    log(f"MILLIE live sync exited rc={return_code}")
    return int(return_code)


def run_sync_loop(
    config: LiveSyncConfig,
    *,
    stop_event: threading.Event,
    run_immediately: bool = True,
    log: LogCallback = default_log,
) -> None:
    if run_immediately:
        run_sync_once(config, log=log)
    while not stop_event.wait(max(config.interval_seconds, 1)):
        run_sync_once(config, log=log)


def start_live_sync_thread(
    config: LiveSyncConfig,
    *,
    run_immediately: bool = True,
    log: LogCallback = default_log,
) -> tuple[threading.Thread, threading.Event]:
    stop_event = threading.Event()
    thread = threading.Thread(
        target=run_sync_loop,
        kwargs={
            "config": config,
            "stop_event": stop_event,
            "run_immediately": run_immediately,
            "log": log,
        },
        name="millie-live-mail-sync",
        daemon=True,
    )
    thread.start()
    return thread, stop_event


def sleep_until_stopped(stop_event: threading.Event) -> None:
    while not stop_event.wait(1):
        time.sleep(0)
