#!/usr/bin/env python3
"""Run MILLIE sync, learning, and safe internal upkeep while this process is active."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from psycopg.types.json import Jsonb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from millie.brain.automation import automation_level, automation_level_allows  # noqa: E402
from millie.settings_loader import load_local_settings  # noqa: E402
from millie.storage.postgres_store import PostgresMailStore  # noqa: E402


@dataclass(frozen=True, slots=True)
class Step:
    name: str
    command: list[str]


@dataclass(slots=True)
class StepResult:
    name: str
    return_code: int
    elapsed_seconds: float


@dataclass(slots=True)
class UpkeepResult:
    run_id: str
    started_at: datetime
    step_results: list[StepResult] = field(default_factory=list)

    @property
    def failed_steps(self) -> list[StepResult]:
        return [step for step in self.step_results if step.return_code != 0]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run one or more MILLIE live-upkeep passes: live IMAP/OAuth sync, dedupe "
            "backfill, optional Gmail label aliasing, observe sorting, retention scan, "
            "and safe internal apply tools."
        )
    )
    parser.add_argument("--once", action="store_true", help="Run one upkeep pass and exit.")
    parser.add_argument("--interval", type=int, default=900, help="Seconds between upkeep passes.")
    parser.add_argument("--account", action="append", default=[], help="Account selector for live sync/import tools.")
    parser.add_argument("--stop-on-error", action="store_true")
    parser.add_argument("--skip-sync", action="store_true")
    parser.add_argument("--skip-dedupe", action="store_true")
    parser.add_argument("--skip-sort", action="store_true")
    parser.add_argument("--skip-retention", action="store_true")
    parser.add_argument("--skip-internal-apply", action="store_true")
    parser.add_argument("--gmail-label-folder", action="append", default=[], help="Exact Gmail label folder to alias.")
    parser.add_argument("--sort-limit", type=int, default=250)
    parser.add_argument("--retention-limit", type=int, default=100)
    parser.add_argument("--apply-limit", type=int, default=100)
    parser.add_argument("--dedupe-limit", type=int, default=0, help="Maximum messages to dedupe-backfill. 0 means all.")
    parser.add_argument("--fetch-batch-size", type=int, default=10)
    parser.add_argument("--commit-every", type=int, default=50)
    parser.add_argument("--imap-timeout", type=int, default=120)
    parser.add_argument("--include-non-mail-folders", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    settings = load_local_settings()["settings"]
    if (settings.get("database_mode") or "").lower() != "postgres":
        raise SystemExit("millie_live_upkeep currently requires database_mode=postgres.")

    exit_code = 0
    try:
        while True:
            result = run_upkeep_pass(args, settings)
            if result.failed_steps:
                exit_code = 1
                if args.stop_on_error:
                    return exit_code
            if args.once:
                return exit_code
            time.sleep(max(args.interval, 1))
    except KeyboardInterrupt:
        return exit_code


def run_upkeep_pass(args: argparse.Namespace, settings: dict[str, str]) -> UpkeepResult:
    started_at = datetime.now(timezone.utc)
    run_id = create_upkeep_run(settings, started_at=started_at, args=args)
    result = UpkeepResult(run_id=run_id, started_at=started_at)
    print(f"MILLIE live upkeep starting run_id={run_id}", flush=True)
    for step in build_steps(args, settings):
        step_result = run_step(step)
        result.step_results.append(step_result)
        if step_result.return_code != 0 and args.stop_on_error:
            break
    complete_upkeep_run(settings, result)
    status = "failed" if result.failed_steps else "completed"
    print(f"MILLIE live upkeep {status} run_id={run_id}", flush=True)
    return result


def build_steps(args: argparse.Namespace, settings: dict[str, str]) -> list[Step]:
    steps: list[Step] = []
    python = sys.executable

    if not args.skip_sync:
        command = [
            python,
            str(PROJECT_ROOT / "tools" / "millie_live_sync.py"),
            "--once",
            "--fetch-batch-size",
            str(args.fetch_batch_size),
            "--commit-every",
            str(args.commit_every),
            "--imap-timeout",
            str(args.imap_timeout),
        ]
        for account in args.account:
            command.extend(["--account", account])
        if args.include_non_mail_folders:
            command.append("--include-non-mail-folders")
        if args.stop_on_error:
            command.append("--stop-on-error")
        steps.append(Step("live_sync", command))

    if not args.skip_dedupe:
        command = [
            python,
            str(PROJECT_ROOT / "tools" / "millie_dedupe_report.py"),
            "--backfill",
            "--samples",
            "0",
        ]
        if args.dedupe_limit > 0:
            command.extend(["--limit", str(args.dedupe_limit)])
        steps.append(Step("dedupe_backfill", command))

    for folder in args.gmail_label_folder:
        command = [
            python,
            str(PROJECT_ROOT / "tools" / "millie_gmail_label_alias_sync.py"),
            "--apply",
            "--folder",
            folder,
            "--imap-timeout",
            str(args.imap_timeout),
        ]
        for account in args.account:
            command.extend(["--account", account])
        steps.append(Step("gmail_label_alias_sync", command))

    if not args.skip_sort:
        steps.append(
            Step(
                "sort_observe",
                [
                    python,
                    str(PROJECT_ROOT / "tools" / "millie_sort_mail.py"),
                    "--observe",
                    "--apply",
                    "--limit",
                    str(args.sort_limit),
                ],
            )
        )

    if not args.skip_retention:
        steps.append(
            Step(
                "retention_scan",
                [
                    python,
                    str(PROJECT_ROOT / "tools" / "millie_retention_scan.py"),
                    "--record-scan",
                    "--limit",
                    str(args.retention_limit),
                ],
            )
        )

    if not args.skip_internal_apply:
        internal_execute = automation_level_allows(settings, "auto_internal")
        suggestion_command = [
            python,
            str(PROJECT_ROOT / "tools" / "millie_apply_suggestions.py"),
            "--limit",
            str(args.apply_limit),
        ]
        retention_command = [
            python,
            str(PROJECT_ROOT / "tools" / "millie_apply_retention.py"),
            "--limit",
            str(args.apply_limit),
        ]
        if internal_execute:
            suggestion_command.extend(["--execute", "--record-blocked"])
            retention_command.append("--execute")
        steps.append(Step("apply_suggestions", suggestion_command))
        steps.append(Step("apply_retention", retention_command))

    return steps


def run_step(step: Step) -> StepResult:
    print(f"UPKEEP step={step.name} starting", flush=True)
    started = time.monotonic()
    process = subprocess.Popen(
        step.command,
        cwd=PROJECT_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
    )
    assert process.stdout is not None
    for line in process.stdout:
        print(f"{step.name.upper()} {line.rstrip()}", flush=True)
    return_code = int(process.wait())
    elapsed = time.monotonic() - started
    print(f"UPKEEP step={step.name} rc={return_code} elapsed={elapsed:.1f}s", flush=True)
    return StepResult(name=step.name, return_code=return_code, elapsed_seconds=elapsed)


def create_upkeep_run(
    settings: dict[str, str],
    *,
    started_at: datetime,
    args: argparse.Namespace,
) -> str:
    run_id = str(uuid.uuid4())
    with PostgresMailStore.connect(settings) as store:
        store.initialize()
        store.connection.execute(
            """
            INSERT INTO millie_automation_runs (
                id, run_type, automation_level, status, trigger_source,
                started_at, metadata_json
            )
            VALUES (%s, 'live_upkeep', %s, 'running', 'cli', %s, %s)
            """,
            (
                run_id,
                automation_level(settings),
                started_at,
                Jsonb(
                    {
                        "accounts": list(args.account),
                        "interval_seconds": args.interval,
                        "once": bool(args.once),
                        "gmail_label_folders": list(args.gmail_label_folder),
                    }
                ),
            ),
        )
        store.connection.commit()
    return run_id


def complete_upkeep_run(settings: dict[str, str], result: UpkeepResult) -> None:
    status = "failed" if result.failed_steps else "completed"
    with PostgresMailStore.connect(settings) as store:
        store.connection.execute(
            """
            UPDATE millie_automation_runs
            SET status = %s,
                completed_at = now(),
                error_message = %s,
                metadata_json = metadata_json || %s
            WHERE id = %s
            """,
            (
                status,
                ", ".join(f"{step.name} rc={step.return_code}" for step in result.failed_steps) or None,
                Jsonb(
                    {
                        "steps": [
                            {
                                "name": step.name,
                                "return_code": step.return_code,
                                "elapsed_seconds": round(step.elapsed_seconds, 3),
                            }
                            for step in result.step_results
                        ]
                    }
                ),
                result.run_id,
            ),
        )
        store.connection.commit()


if __name__ == "__main__":
    raise SystemExit(main())
