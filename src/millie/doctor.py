from __future__ import annotations

import argparse
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class CheckResult:
    name: str
    ok: bool
    detail: str
    required: bool = True


def run_doctor(args: argparse.Namespace, project_root: Path) -> int:
    project_root = project_root.resolve()
    results = collect_checks(project_root)

    for result in results:
        marker = "ok" if result.ok else ("missing" if result.required else "optional")
        print(f"[{marker}] {result.name}: {result.detail}")

    if args.install:
        maybe_create_venv(project_root, assume_yes=args.yes)
        maybe_install_node_modules(project_root, assume_yes=args.yes)
        results = collect_checks(project_root)

    missing_required = [result for result in results if result.required and not result.ok]
    if missing_required:
        print()
        print("Missing required prerequisites:")
        for result in missing_required:
            print(f"- {result.name}: {result.detail}")
        print()
        print("Install Python 3.11+ from https://www.python.org/ or your system package manager.")
        print("Install Node.js/npm from https://nodejs.org/ or your system package manager.")
        return 1
    return 0


def collect_checks(project_root: Path) -> list[CheckResult]:
    return [
        check_python(),
        check_sqlite(),
        check_node(),
        check_npm(),
        check_readpst(),
        check_package_json(project_root),
        check_node_modules(project_root),
        check_venv(project_root),
    ]


def check_python() -> CheckResult:
    version = sys.version_info
    ok = version >= (3, 11)
    detail = f"{version.major}.{version.minor}.{version.micro}"
    if not ok:
        detail = f"{detail}; Python 3.11+ is required"
    return CheckResult("Python", ok, detail)


def check_sqlite() -> CheckResult:
    try:
        version = sqlite3.sqlite_version
    except Exception as exc:  # noqa: BLE001
        return CheckResult("SQLite module", False, str(exc))
    return CheckResult("SQLite module", True, version)


def check_node() -> CheckResult:
    node = shutil.which("node")
    if node is None:
        return CheckResult("Node.js", False, "node command not found")
    version = command_text([node, "--version"])
    return CheckResult("Node.js", True, version or node)


def check_npm() -> CheckResult:
    npm = shutil.which("npm")
    if npm is None:
        return CheckResult("npm", False, "npm command not found")
    version = command_text([npm, "--version"])
    return CheckResult("npm", True, version or npm)


def check_readpst() -> CheckResult:
    readpst = shutil.which("readpst")
    if readpst is None:
        return CheckResult("readpst/libpst", False, "readpst command not found; PST import will be disabled", required=False)
    version = command_text([readpst, "-V"])
    return CheckResult("readpst/libpst", True, version.splitlines()[0] if version else readpst, required=False)


def check_package_json(project_root: Path) -> CheckResult:
    path = project_root / "web" / "package.json"
    return CheckResult("web/package.json", path.exists(), str(path))


def check_node_modules(project_root: Path) -> CheckResult:
    path = project_root / "web" / "node_modules"
    return CheckResult("web/node_modules", path.exists(), str(path))


def check_venv(project_root: Path) -> CheckResult:
    path = project_root / ".venv"
    return CheckResult("Python .venv", path.exists(), f"{path} (optional)", required=False)


def maybe_create_venv(project_root: Path, assume_yes: bool) -> None:
    venv = project_root / ".venv"
    if venv.exists():
        return
    if not should_run("Create local Python virtualenv at .venv?", assume_yes):
        return
    subprocess.run([sys.executable, "-m", "venv", str(venv)], cwd=project_root, check=True)
    requirements = project_root / "requirements.txt"
    pip = venv / "bin" / "pip"
    if requirements.exists() and pip.exists():
        subprocess.run([str(pip), "install", "-r", str(requirements)], cwd=project_root, check=True)


def maybe_install_node_modules(project_root: Path, assume_yes: bool) -> None:
    node_modules = project_root / "web" / "node_modules"
    package_json = project_root / "web" / "package.json"
    npm = shutil.which("npm")
    if node_modules.exists() or not package_json.exists() or npm is None:
        return
    if not should_run("Install web dependencies with npm install?", assume_yes):
        return
    subprocess.run([npm, "install"], cwd=project_root / "web", check=True)


def should_run(question: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print(f"Skipped: {question} Re-run with --install --yes to perform this automatically.")
        return False
    answer = input(f"{question} [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def command_text(command: list[str]) -> str:
    try:
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError):
        return ""
    return completed.stdout.strip()
