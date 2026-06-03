#!/usr/bin/env python3
"""Review and prepare approved unsubscribe candidates without auto-clicking links."""

from __future__ import annotations

import argparse
import html
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from psycopg.types.json import Jsonb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from millie.settings_loader import load_local_settings  # noqa: E402
from millie.storage.postgres_store import PostgresMailStore  # noqa: E402


DEFAULT_ASSIST_PATH = PROJECT_ROOT / ".private" / "local" / "unsubscribe_manual_assist.html"
UNSUBSCRIBE_STATUSES = {
    "detected",
    "review_required",
    "approved",
    "attempting",
    "succeeded",
    "failed",
    "ignored",
    "unsafe",
}


@dataclass(frozen=True, slots=True)
class UnsubscribeCandidate:
    id: str
    message_id: str
    candidate_type: str
    unsubscribe_url: str
    unsubscribe_mailto: str
    status: str
    confidence: float
    requires_browser: bool
    subject: str
    from_text: str
    result: dict[str, object]
    error_message: str

    @property
    def target(self) -> str:
        return self.unsubscribe_mailto or self.unsubscribe_url


@dataclass(frozen=True, slots=True)
class PreparedState:
    candidate: UnsubscribeCandidate
    next_status: str
    reason: str
    manual_target: str
    requires_browser: bool


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "List and prepare reviewed unsubscribe candidates. This command never "
            "loads provider unsubscribe URLs or submits forms."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List unsubscribe candidates.")
    add_filters(list_parser, default_status=["approved"])

    prepare_parser = subparsers.add_parser("prepare", help="Record approved candidates as manual unsubscribe work.")
    add_filters(prepare_parser, default_status=["approved"])
    prepare_parser.add_argument("--execute", action="store_true", help="Write attempting/unsafe states.")
    prepare_parser.add_argument(
        "--allow-browser-manual",
        action="store_true",
        help="Allow browser-required candidates to be prepared for manual browser assist.",
    )

    ignore_parser = subparsers.add_parser("ignore", help="Mark candidates ignored.")
    add_filters(ignore_parser, default_status=["approved", "attempting", "failed", "unsafe"])
    ignore_parser.add_argument("--execute", action="store_true", help="Write ignored state.")

    unsafe_parser = subparsers.add_parser("unsafe", help="Mark candidates unsafe.")
    add_filters(unsafe_parser, default_status=["approved", "attempting", "failed"])
    unsafe_parser.add_argument("--reason", default="manual unsafe review")
    unsafe_parser.add_argument("--execute", action="store_true", help="Write unsafe state.")

    assist_parser = subparsers.add_parser("assist", help="Write a local manual-assist HTML checklist.")
    add_filters(assist_parser, default_status=["approved", "attempting"])
    assist_parser.add_argument("--output", type=Path, default=DEFAULT_ASSIST_PATH)

    return parser


def add_filters(parser: argparse.ArgumentParser, *, default_status: list[str]) -> None:
    parser.add_argument("--candidate-id", action="append", default=[])
    parser.add_argument("--status", action="append", default=default_status, choices=sorted(UNSUBSCRIBE_STATUSES))
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--include-browser", action="store_true", help="Include candidates flagged as requiring a browser.")


def main() -> int:
    args = build_parser().parse_args()
    settings = load_local_settings()["settings"]
    if (settings.get("database_mode") or "").lower() != "postgres":
        raise SystemExit("millie_unsubscribe_review currently requires database_mode=postgres.")

    with PostgresMailStore.connect(settings) as store:
        candidates = load_candidates(store, args)
        if args.command == "list":
            print_candidates(candidates)
        elif args.command == "prepare":
            states = [prepare_candidate(candidate, allow_browser_manual=args.allow_browser_manual) for candidate in candidates]
            print_prepared(states, execute=args.execute)
            if args.execute:
                for state in states:
                    write_prepared_state(store, state)
                store.connection.commit()
        elif args.command == "ignore":
            print_state_change(candidates, status="ignored", execute=args.execute)
            if args.execute:
                for candidate in candidates:
                    write_simple_state(store, candidate, status="ignored", reason="manual ignore")
                store.connection.commit()
        elif args.command == "unsafe":
            print_state_change(candidates, status="unsafe", execute=args.execute)
            if args.execute:
                for candidate in candidates:
                    write_simple_state(store, candidate, status="unsafe", reason=args.reason)
                store.connection.commit()
        elif args.command == "assist":
            write_assist_file(candidates, args.output)
            print(f"manual_assist_file={args.output}")
            print(f"candidates={len(candidates)}")
        else:
            raise SystemExit(f"Unsupported command: {args.command}")
    return 0


def load_candidates(store: PostgresMailStore, args: argparse.Namespace) -> list[UnsubscribeCandidate]:
    where = [f"u.status IN ({placeholders(args.status)})"]
    params: list[object] = list(args.status)
    if args.candidate_id:
        where.append(f"u.id IN ({placeholders(args.candidate_id)})")
        params.extend(args.candidate_id)
    if not args.include_browser:
        where.append("u.requires_browser = FALSE")
    limit_sql = ""
    if args.limit and args.limit > 0:
        limit_sql = "LIMIT %s"
        params.append(args.limit)
    rows = store.connection.execute(
        f"""
        WITH from_addresses AS (
            SELECT
                message_id,
                string_agg(
                    CASE
                        WHEN coalesce(display_name, '') <> '' AND coalesce(email_address, '') <> ''
                            THEN display_name || ' <' || email_address || '>'
                        WHEN coalesce(email_address, '') <> '' THEN email_address
                        ELSE coalesce(raw_value, '')
                    END,
                    ', ' ORDER BY ordinal
                ) AS from_text
            FROM mail_message_addresses
            WHERE role = 'from'
            GROUP BY message_id
        )
        SELECT
            u.id,
            u.message_id,
            u.candidate_type,
            coalesce(u.unsubscribe_url, '') AS unsubscribe_url,
            coalesce(u.unsubscribe_mailto, '') AS unsubscribe_mailto,
            u.status,
            u.confidence,
            u.requires_browser,
            coalesce(m.subject, '(no subject)') AS subject,
            coalesce(fa.from_text, '') AS from_text,
            u.result_json,
            coalesce(u.error_message, '') AS error_message
        FROM millie_unsubscribe_candidates u
        JOIN mail_messages m ON m.id = u.message_id
        LEFT JOIN from_addresses fa ON fa.message_id = u.message_id
        WHERE {" AND ".join(where)}
        ORDER BY
            CASE u.status
                WHEN 'approved' THEN 0
                WHEN 'attempting' THEN 1
                WHEN 'failed' THEN 2
                WHEN 'review_required' THEN 3
                WHEN 'detected' THEN 4
                ELSE 5
            END,
            u.confidence DESC,
            u.discovered_at DESC
        {limit_sql}
        """,
        tuple(params),
    ).fetchall()
    return [
        UnsubscribeCandidate(
            id=str(row[0]),
            message_id=str(row[1]),
            candidate_type=str(row[2]),
            unsubscribe_url=str(row[3] or ""),
            unsubscribe_mailto=str(row[4] or ""),
            status=str(row[5]),
            confidence=float(row[6] or 0),
            requires_browser=bool(row[7]),
            subject=str(row[8] or "(no subject)"),
            from_text=str(row[9] or ""),
            result=row[10] or {},
            error_message=str(row[11] or ""),
        )
        for row in rows
    ]


def prepare_candidate(candidate: UnsubscribeCandidate, *, allow_browser_manual: bool) -> PreparedState:
    if not candidate.target:
        return PreparedState(candidate, "unsafe", "missing unsubscribe target", "", candidate.requires_browser)
    manual_target = normalized_target(candidate)
    if not manual_target:
        return PreparedState(candidate, "unsafe", "unsupported target scheme", candidate.target, candidate.requires_browser)
    if candidate.requires_browser and not allow_browser_manual:
        return PreparedState(candidate, "unsafe", "browser assist not allowed for this preparation run", manual_target, True)
    if candidate.candidate_type in {"body_url", "browser", "provider_api"} and not allow_browser_manual:
        return PreparedState(candidate, "unsafe", f"{candidate.candidate_type} requires manual browser review", manual_target, True)
    return PreparedState(candidate, "attempting", "prepared for manual unsubscribe assist", manual_target, candidate.requires_browser)


def normalized_target(candidate: UnsubscribeCandidate) -> str:
    if candidate.unsubscribe_mailto:
        value = candidate.unsubscribe_mailto.strip()
        return value if value.lower().startswith("mailto:") else f"mailto:{value}"
    value = candidate.unsubscribe_url.strip()
    parsed = urlparse(value)
    if parsed.scheme.lower() in {"http", "https", "mailto"}:
        return value
    return ""


def write_prepared_state(store: PostgresMailStore, state: PreparedState) -> None:
    metadata = {
        "manual_assist_target": state.manual_target,
        "manual_assist_required": True,
        "manual_browser_required": state.requires_browser,
        "review_reason": state.reason,
    }
    update_candidate_state(
        store,
        state.candidate,
        status=state.next_status,
        result=metadata,
        error="" if state.next_status == "attempting" else state.reason,
    )
    write_attempt_audit(
        store,
        state.candidate,
        audit_status="recorded" if state.next_status == "attempting" else "blocked",
        after_json={"status": state.next_status, **metadata},
        error="" if state.next_status == "attempting" else state.reason,
    )


def write_simple_state(
    store: PostgresMailStore,
    candidate: UnsubscribeCandidate,
    *,
    status: str,
    reason: str,
) -> None:
    update_candidate_state(
        store,
        candidate,
        status=status,
        result={"review_reason": reason},
        error=reason if status in {"unsafe", "failed"} else "",
    )
    write_attempt_audit(
        store,
        candidate,
        audit_status="blocked" if status == "unsafe" else "recorded",
        after_json={"status": status, "review_reason": reason},
        error=reason if status == "unsafe" else "",
    )


def update_candidate_state(
    store: PostgresMailStore,
    candidate: UnsubscribeCandidate,
    *,
    status: str,
    result: dict[str, object],
    error: str,
) -> None:
    completed_sql = ", completed_at = CASE WHEN %s IN ('succeeded', 'failed', 'ignored', 'unsafe') THEN now() ELSE completed_at END"
    store.connection.execute(
        f"""
        UPDATE millie_unsubscribe_candidates
        SET status = %s,
            attempted_at = CASE WHEN %s = 'attempting' THEN coalesce(attempted_at, now()) ELSE attempted_at END
            {completed_sql},
            result_json = result_json || %s,
            error_message = %s,
            metadata_json = metadata_json || %s
        WHERE id = %s
        """,
        (
            status,
            status,
            status,
            Jsonb(result),
            error or None,
            Jsonb({"managed_by": "millie_unsubscribe_review"}),
            candidate.id,
        ),
    )


def write_attempt_audit(
    store: PostgresMailStore,
    candidate: UnsubscribeCandidate,
    *,
    audit_status: str,
    after_json: dict[str, object],
    error: str,
) -> None:
    store.connection.execute(
        """
        INSERT INTO millie_automation_audit_log (
            id, message_id, unsubscribe_candidate_id, action_type,
            automation_level, status, after_json, error_message
        )
        VALUES (%s, %s, %s, 'unsubscribe_attempt', 'review', %s, %s, %s)
        """,
        (
            str(uuid.uuid4()),
            candidate.message_id,
            candidate.id,
            audit_status,
            Jsonb(after_json),
            error or None,
        ),
    )


def write_assist_file(candidates: list[UnsubscribeCandidate], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = "\n".join(assist_row(candidate) for candidate in candidates)
    output.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>MILLIE Unsubscribe Manual Assist</title>
  <style>
    body {{ font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; color: #17202f; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border-bottom: 1px solid #d7dde7; padding: 8px; text-align: left; vertical-align: top; }}
    th {{ background: #f3f6fa; }}
    a {{ color: #0b57d0; overflow-wrap: anywhere; }}
    .muted {{ color: #657084; }}
  </style>
</head>
<body>
  <h1>MILLIE Unsubscribe Manual Assist</h1>
  <p class="muted">This page only links reviewed candidates. MILLIE has not clicked, submitted, or contacted any provider.</p>
  <table>
    <thead><tr><th>Status</th><th>Type</th><th>From</th><th>Subject</th><th>Target</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</body>
</html>
""",
        encoding="utf-8",
    )


def assist_row(candidate: UnsubscribeCandidate) -> str:
    target = normalized_target(candidate)
    target_html = html.escape(target or candidate.target or "")
    if target:
        target_html = f'<a href="{html.escape(target, quote=True)}">{target_html}</a>'
    return (
        "<tr>"
        f"<td>{html.escape(candidate.status)}</td>"
        f"<td>{html.escape(candidate.candidate_type)}</td>"
        f"<td>{html.escape(candidate.from_text)}</td>"
        f"<td>{html.escape(candidate.subject)}</td>"
        f"<td>{target_html}</td>"
        "</tr>"
    )


def print_candidates(candidates: list[UnsubscribeCandidate]) -> None:
    print(f"MILLIE unsubscribe candidates: {len(candidates)}")
    for candidate in candidates:
        browser = " browser" if candidate.requires_browser else ""
        print(
            f"{candidate.status}{browser} {candidate.candidate_type} "
            f"confidence={candidate.confidence:.2f} id={candidate.id} "
            f"target={candidate.target!r} subject={candidate.subject!r}"
        )


def print_prepared(states: list[PreparedState], *, execute: bool) -> None:
    print(f"MILLIE unsubscribe prepare {'execute' if execute else 'dry-run'}")
    print(f"Candidates: {len(states)}")
    for state in states:
        print(
            f"{state.next_status} id={state.candidate.id} "
            f"reason={state.reason!r} target={state.manual_target!r}"
        )


def print_state_change(candidates: list[UnsubscribeCandidate], *, status: str, execute: bool) -> None:
    print(f"MILLIE unsubscribe set {status} {'execute' if execute else 'dry-run'}")
    print(f"Candidates: {len(candidates)}")
    for candidate in candidates:
        print(f"{candidate.status} -> {status} id={candidate.id} target={candidate.target!r}")


def placeholders(values: list[object]) -> str:
    if not values:
        raise ValueError("values must not be empty")
    return ", ".join(["%s"] * len(values))


if __name__ == "__main__":
    raise SystemExit(main())
