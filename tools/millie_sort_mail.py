#!/usr/bin/env python3
"""Observe-only MILLIE mail sorter."""

from __future__ import annotations

import argparse
import sys
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from millie.brain.observe import (  # noqa: E402
    CLASSIFIER_TYPE,
    CLASSIFIER_VERSION,
    ClassificationSuggestion,
    LEARNED_RULE_CLASSIFIER_TYPE,
    LEARNED_RULE_CLASSIFIER_VERSION,
    SortCandidate,
    UnsubscribeSuggestion,
    classify_candidate,
    candidate_year,
    extract_unsubscribe_suggestions,
)
from millie.brain.automation import automation_level_allows  # noqa: E402
from millie.importing.models import stable_id  # noqa: E402
from millie.settings_loader import load_local_settings  # noqa: E402
from millie.storage.postgres_store import PostgresMailStore  # noqa: E402


@dataclass(frozen=True, slots=True)
class CandidateResult:
    candidate: SortCandidate
    classifications: list[ClassificationSuggestion]
    unsubscribe_suggestions: list[UnsubscribeSuggestion]
    blocked_classifications: int = 0


@dataclass(frozen=True, slots=True)
class LearnedRule:
    id: str
    rule_name: str
    condition: dict[str, Any]
    rule_action: dict[str, Any]
    confidence: float
    priority: int
    evidence_count: int


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Classify copied MILLIE mail in observe mode. The default is a dry run. "
            "Passing --apply writes suggestions and audit rows only; it does not move, "
            "delete, unsubscribe, or write to source providers."
        )
    )
    parser.add_argument(
        "--observe",
        action="store_true",
        help="Run observe mode. This is the only supported mode and is also the default.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist suggestions/audit rows into Postgres brain tables.",
    )
    parser.add_argument("--limit", type=int, default=250, help="Maximum messages to inspect. Use 0 for all.")
    parser.add_argument("--account", action="append", default=[], help="Filter by source account text.")
    parser.add_argument("--folder", action="append", default=[], help="Filter by MILLIE mailbox folder path.")
    parser.add_argument("--message-id", action="append", default=[], help="Filter to exact MILLIE message ids.")
    parser.add_argument("--since", default="", help="Only inspect messages on or after this ISO date/datetime.")
    parser.add_argument("--until", default="", help="Only inspect messages on or before this ISO date/datetime.")
    parser.add_argument(
        "--include-classified",
        action="store_true",
        help="Include messages that already have observe-v1 heuristic or learned-v1 rule classifications.",
    )
    parser.add_argument("--sample", type=int, default=20, help="Number of suggestions to print.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.observe:
        args.observe = True

    settings = load_local_settings()["settings"]
    if (settings.get("database_mode") or "").lower() != "postgres":
        raise SystemExit("millie_sort_mail currently requires database_mode=postgres.")
    if not automation_level_allows(settings, "observe"):
        raise SystemExit("automation_level does not allow observe sorting.")

    with PostgresMailStore.connect(settings) as store:
        store.initialize()
        store.connection.commit()
        candidates = load_candidates(store, args)
        attach_headers(store, candidates)
        learned_rules = load_active_learned_rules(store)
        results = []
        for candidate in candidates:
            classifications, blocked_count = classify_with_learned_rules(candidate, learned_rules)
            results.append(
                CandidateResult(
                    candidate=candidate,
                    classifications=classifications,
                    unsubscribe_suggestions=extract_unsubscribe_suggestions(candidate.headers),
                    blocked_classifications=blocked_count,
                )
            )
        if args.apply:
            write_results(store, results)
            store.connection.commit()

    print_summary(results, applied=args.apply, sample_size=args.sample)
    return 0


def load_candidates(store: PostgresMailStore, args: argparse.Namespace) -> list[SortCandidate]:
    where: list[str] = []
    params: list[Any] = []

    if args.message_id:
        where.append(f"m.id IN ({placeholders(args.message_id)})")
        params.extend(args.message_id)

    for account in args.account:
        where.append("(s.display_name ILIKE %s OR s.source_uri ILIKE %s)")
        pattern = f"%{account}%"
        params.extend([pattern, pattern])

    for folder in args.folder:
        where.append("v.folder_path = %s")
        params.append(folder)

    if args.since:
        where.append("coalesce(m.sent_at, m.received_at, m.created_at) >= %s")
        params.append(parse_filter_datetime(args.since, end_of_day=False))

    if args.until:
        where.append("coalesce(m.sent_at, m.received_at, m.created_at) <= %s")
        params.append(parse_filter_datetime(args.until, end_of_day=True))

    if not args.include_classified:
        where.append(
            """
            NOT EXISTS (
                SELECT 1
                FROM millie_message_classifications existing
                WHERE existing.message_id = m.id
                  AND existing.status IN ('proposed', 'approved', 'applied', 'rejected')
                  AND (
                    (
                      existing.classifier_type = %s
                      AND existing.classifier_version = %s
                    )
                    OR (
                      existing.classifier_type = %s
                      AND existing.classifier_version = %s
                    )
                  )
            )
            """
        )
        params.extend([
            CLASSIFIER_TYPE,
            CLASSIFIER_VERSION,
            LEARNED_RULE_CLASSIFIER_TYPE,
            LEARNED_RULE_CLASSIFIER_VERSION,
        ])

    where_sql = "WHERE " + " AND ".join(where) if where else ""
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
                    trim(coalesce(display_name, '') || ' ' || coalesce(email_address, raw_value, '')),
                    ', '
                    ORDER BY ordinal
                ) AS from_text
            FROM mail_message_addresses
            WHERE role IN ('from', 'sender')
            GROUP BY message_id
        )
        SELECT DISTINCT ON (m.id)
            m.id,
            coalesce(m.subject, '') AS subject,
            coalesce(fa.from_text, '') AS from_text,
            coalesce(v.folder_path, '') AS folder_path,
            coalesce(m.body_preview, '') AS body_preview,
            m.sent_at,
            m.received_at
        FROM mail_messages m
        JOIN mail_sources s ON s.id = m.source_id
        LEFT JOIN millie_v_mailbox_messages v ON v.message_id = m.id
        LEFT JOIN from_addresses fa ON fa.message_id = m.id
        {where_sql}
        ORDER BY
            m.id,
            CASE WHEN coalesce(v.folder_path, '') = 'All Mail' THEN 1 ELSE 0 END,
            coalesce(m.received_at, m.sent_at, now()) DESC
        {limit_sql}
        """,
        tuple(params),
    ).fetchall()

    return [
        SortCandidate(
            message_id=str(row[0]),
            subject=str(row[1] or ""),
            from_text=str(row[2] or ""),
            folder_path=str(row[3] or ""),
            body_preview=str(row[4] or ""),
            sent_at=row[5],
            received_at=row[6],
        )
        for row in rows
    ]


def load_active_learned_rules(store: PostgresMailStore) -> list[LearnedRule]:
    rows = store.connection.execute(
        """
        SELECT
            id,
            rule_name,
            condition_json,
            action_json,
            confidence,
            priority,
            evidence_count
        FROM millie_brain_rules
        WHERE status = 'active'
        ORDER BY priority DESC, evidence_count DESC, updated_at DESC
        """
    ).fetchall()
    return [
        LearnedRule(
            id=str(row[0]),
            rule_name=str(row[1] or row[0]),
            condition=dict(row[2] or {}),
            rule_action=dict(row[3] or {}),
            confidence=float(row[4] or 0),
            priority=int(row[5] or 0),
            evidence_count=int(row[6] or 0),
        )
        for row in rows
    ]


def classify_with_learned_rules(
    candidate: SortCandidate,
    learned_rules: list[LearnedRule],
) -> tuple[list[ClassificationSuggestion], int]:
    rule_suggestions: list[ClassificationSuggestion] = []
    block_rules: list[LearnedRule] = []
    for rule in learned_rules:
        action = str(rule.rule_action.get("action") or "").strip().lower()
        if action == "suggest":
            suggestion = suggestion_from_rule(rule, candidate)
            if suggestion:
                rule_suggestions.append(suggestion)
        elif action == "block_suggestion":
            block_rules.append(rule)

    suggestions: list[ClassificationSuggestion] = []
    seen: set[tuple[object, ...]] = set()
    blocked_count = 0
    for suggestion in [*rule_suggestions, *classify_candidate(candidate)]:
        if matching_block_rule(candidate, suggestion, block_rules):
            blocked_count += 1
            continue
        key = suggestion_key(suggestion)
        if key in seen:
            continue
        seen.add(key)
        suggestions.append(suggestion)
    return suggestions, blocked_count


def suggestion_from_rule(
    rule: LearnedRule,
    candidate: SortCandidate,
) -> ClassificationSuggestion | None:
    if not candidate_matches_rule_context(candidate, rule.condition):
        return None
    kind = str(
        rule.rule_action.get("classification_kind")
        or rule.condition.get("classification_kind")
        or ""
    ).strip()
    value = str(
        rule.rule_action.get("classification_value")
        or rule.condition.get("classification_value")
        or ""
    ).strip()
    if not kind or not value:
        return None
    target_tags = tuple(str(tag) for tag in rule.rule_action.get("target_tags") or ())
    target_folder_path = rule.rule_action.get("target_folder_path")
    confidence = rule.confidence if rule.confidence > 0 else 0.75
    return ClassificationSuggestion(
        kind=kind,
        value=value,
        target_folder_path=str(target_folder_path) if target_folder_path else None,
        target_tags=target_tags,
        confidence=max(0.0, min(confidence, 1.0)),
        reason=f"Learned rule: {rule.rule_name}",
        evidence={
            "source": "millie_brain_rule",
            "rule_id": rule.id,
            "rule_name": rule.rule_name,
            "condition": rule.condition,
            "evidence_count": rule.evidence_count,
        },
        classifier_type=LEARNED_RULE_CLASSIFIER_TYPE,
        classifier_version=LEARNED_RULE_CLASSIFIER_VERSION,
        rule_id=rule.id,
    )


def matching_block_rule(
    candidate: SortCandidate,
    suggestion: ClassificationSuggestion,
    block_rules: list[LearnedRule],
) -> LearnedRule | None:
    for rule in block_rules:
        if not candidate_matches_rule_context(candidate, rule.condition):
            continue
        if suggestion_matches_rule_json(suggestion, rule.condition) or suggestion_matches_rule_json(
            suggestion,
            rule.rule_action,
        ):
            return rule
    return None


def candidate_matches_rule_context(candidate: SortCandidate, condition: dict[str, Any]) -> bool:
    sender_domain = str(condition.get("sender_domain") or "").strip().lower()
    if sender_domain and sender_domain != candidate_sender_domain(candidate):
        return False
    folder_path = str(condition.get("folder_path") or "").strip()
    if folder_path and folder_path != str(candidate.folder_path or ""):
        return False
    message_year = str(condition.get("message_year") or "").strip()
    if message_year and message_year != str(candidate_year(candidate)):
        return False
    return True


def suggestion_matches_rule_json(
    suggestion: ClassificationSuggestion,
    value: dict[str, Any],
) -> bool:
    field_map = {
        "classification_kind": suggestion.kind,
        "classification_value": suggestion.value,
        "target_folder_path": suggestion.target_folder_path,
    }
    for field, suggestion_value in field_map.items():
        if field in value and normalize_optional_text(value.get(field)) != normalize_optional_text(suggestion_value):
            return False
    if "target_tags" in value:
        tags = tuple(str(tag) for tag in value.get("target_tags") or ())
        if tags != suggestion.target_tags:
            return False
    return True


def suggestion_key(suggestion: ClassificationSuggestion) -> tuple[object, ...]:
    return (
        suggestion.kind,
        suggestion.value,
        suggestion.target_folder_path,
        tuple(suggestion.target_tags),
    )


def candidate_sender_domain(candidate: SortCandidate) -> str:
    tokens = (
        str(candidate.from_text or "")
        .replace("<", " ")
        .replace(">", " ")
        .replace(",", " ")
        .split()
    )
    for token in tokens:
        value = token.strip().lower()
        if "@" in value:
            return value.rsplit("@", 1)[1].strip(" .;:")
    return ""


def normalize_optional_text(value: object) -> str:
    return str(value or "").strip()


def attach_headers(store: PostgresMailStore, candidates: list[SortCandidate]) -> None:
    if not candidates:
        return
    by_message = {candidate.message_id: candidate.headers for candidate in candidates}
    message_ids = list(by_message)
    rows = store.connection.execute(
        """
        SELECT message_id, header_name, header_value
        FROM mail_message_headers
        WHERE lower(header_name) = 'list-unsubscribe'
          AND message_id = ANY(%s)
        ORDER BY message_id, ordinal
        """,
        (message_ids,),
    ).fetchall()
    for message_id, header_name, header_value in rows:
        headers = by_message[str(message_id)]
        headers.setdefault(str(header_name), []).append(str(header_value))


def write_results(store: PostgresMailStore, results: list[CandidateResult]) -> None:
    run_id = str(uuid.uuid4())
    suggestions_created = sum(
        len(result.classifications) + len(result.unsubscribe_suggestions)
        for result in results
    )
    store.connection.execute(
        """
        INSERT INTO millie_automation_runs (
            id, run_type, automation_level, status, trigger_source,
            started_at, completed_at, messages_scanned, suggestions_created
        )
        VALUES (%s, 'sort_observe', 'observe', 'completed', 'cli',
                now(), now(), %s, %s)
        """,
        (run_id, len(results), suggestions_created),
    )

    for result in results:
        for suggestion in result.classifications:
            classification_id = stable_id(
                "millie_message_classification",
                result.candidate.message_id,
                suggestion.kind,
                suggestion.value,
                suggestion.classifier_type,
                suggestion.classifier_version,
            )
            store.connection.execute(
                """
                INSERT INTO millie_message_classifications (
                    id, message_id, rule_id, run_id, classifier_type, classifier_version,
                    classification_kind, classification_value, target_folder_path,
                    target_tags, status, automation_level, confidence, reason_text,
                    evidence_json
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    'proposed', 'observe', %s, %s, %s
                )
                ON CONFLICT(id) DO UPDATE SET
                    rule_id = excluded.rule_id,
                    run_id = excluded.run_id,
                    target_folder_path = excluded.target_folder_path,
                    target_tags = excluded.target_tags,
                    confidence = excluded.confidence,
                    reason_text = excluded.reason_text,
                    evidence_json = excluded.evidence_json,
                    status = CASE
                        WHEN millie_message_classifications.status IN ('approved', 'rejected', 'applied')
                        THEN millie_message_classifications.status
                        ELSE excluded.status
                    END,
                    updated_at = now()
                """,
                (
                    classification_id,
                    result.candidate.message_id,
                    suggestion.rule_id,
                    run_id,
                    suggestion.classifier_type,
                    suggestion.classifier_version,
                    suggestion.kind,
                    suggestion.value,
                    suggestion.target_folder_path,
                    list(suggestion.target_tags),
                    suggestion.confidence,
                    suggestion.reason,
                    Jsonb(suggestion.evidence),
                ),
            )
            write_audit(
                store,
                run_id=run_id,
                message_id=result.candidate.message_id,
                classification_id=classification_id,
                action_type="suggest_classification",
                after_json={
                    "kind": suggestion.kind,
                    "value": suggestion.value,
                    "target_folder_path": suggestion.target_folder_path,
                    "target_tags": list(suggestion.target_tags),
                    "confidence": suggestion.confidence,
                    "reason": suggestion.reason,
                    "classifier_type": suggestion.classifier_type,
                    "classifier_version": suggestion.classifier_version,
                    "rule_id": suggestion.rule_id,
                },
            )
            if suggestion.rule_id:
                store.connection.execute(
                    """
                    UPDATE millie_brain_rules
                    SET last_matched_at = now(),
                        updated_at = now()
                    WHERE id = %s
                    """,
                    (suggestion.rule_id,),
                )

        for suggestion in result.unsubscribe_suggestions:
            target = suggestion.unsubscribe_mailto or suggestion.unsubscribe_url or ""
            candidate_id = stable_id(
                "millie_unsubscribe_candidate",
                result.candidate.message_id,
                suggestion.candidate_type,
                target,
            )
            store.connection.execute(
                """
                INSERT INTO millie_unsubscribe_candidates (
                    id, message_id, run_id, candidate_type, source_header,
                    unsubscribe_url, unsubscribe_mailto, status, confidence,
                    requires_browser, result_json
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'review_required', %s, %s, %s)
                ON CONFLICT(id) DO UPDATE SET
                    run_id = excluded.run_id,
                    status = CASE
                        WHEN millie_unsubscribe_candidates.status IN ('approved', 'attempting', 'succeeded', 'ignored', 'unsafe')
                        THEN millie_unsubscribe_candidates.status
                        ELSE excluded.status
                    END,
                    confidence = excluded.confidence,
                    result_json = excluded.result_json
                """,
                (
                    candidate_id,
                    result.candidate.message_id,
                    run_id,
                    suggestion.candidate_type,
                    suggestion.source_header,
                    suggestion.unsubscribe_url,
                    suggestion.unsubscribe_mailto,
                    suggestion.confidence,
                    suggestion.requires_browser,
                    Jsonb(suggestion.evidence),
                ),
            )
            write_audit(
                store,
                run_id=run_id,
                message_id=result.candidate.message_id,
                unsubscribe_candidate_id=candidate_id,
                action_type="unsubscribe_detect",
                after_json={
                    "candidate_type": suggestion.candidate_type,
                    "unsubscribe_url": suggestion.unsubscribe_url,
                    "unsubscribe_mailto": suggestion.unsubscribe_mailto,
                    "confidence": suggestion.confidence,
                    "requires_browser": suggestion.requires_browser,
                },
            )


def write_audit(
    store: PostgresMailStore,
    *,
    run_id: str,
    message_id: str,
    action_type: str,
    classification_id: str | None = None,
    unsubscribe_candidate_id: str | None = None,
    after_json: dict[str, object],
) -> None:
    audit_id = stable_id(
        "millie_automation_audit",
        run_id,
        message_id,
        action_type,
        classification_id,
        unsubscribe_candidate_id,
    )
    store.connection.execute(
        """
        INSERT INTO millie_automation_audit_log (
            id, run_id, message_id, classification_id, unsubscribe_candidate_id,
            action_type, automation_level, status, after_json
        )
        VALUES (%s, %s, %s, %s, %s, %s, 'observe', 'recorded', %s)
        ON CONFLICT(id) DO NOTHING
        """,
        (
            audit_id,
            run_id,
            message_id,
            classification_id,
            unsubscribe_candidate_id,
            action_type,
            Jsonb(after_json),
        ),
    )


def print_summary(
    results: list[CandidateResult],
    *,
    applied: bool,
    sample_size: int,
) -> None:
    classification_counts: Counter[str] = Counter()
    classifier_counts: Counter[str] = Counter()
    unsubscribe_count = 0
    blocked_count = 0
    sample_rows: list[str] = []

    for result in results:
        blocked_count += result.blocked_classifications
        for suggestion in result.classifications:
            classification_counts[f"{suggestion.kind}:{suggestion.value}"] += 1
            classifier_counts[suggestion.classifier_type] += 1
            if len(sample_rows) < sample_size:
                sample_rows.append(
                    "  "
                    f"{result.candidate.message_id} "
                    f"{suggestion.kind}:{suggestion.value} -> "
                    f"{suggestion.target_folder_path or ','.join(suggestion.target_tags)} "
                    f"confidence={suggestion.confidence:.2f}"
                )
        unsubscribe_count += len(result.unsubscribe_suggestions)

    print("MILLIE sort observe")
    print(f"Mode: {'apply' if applied else 'dry-run'}")
    print(f"Messages scanned: {len(results)}")
    print(f"Classification suggestions: {sum(classification_counts.values())}")
    for key, count in sorted(classification_counts.items()):
        print(f"  {key}: {count}")
    if classifier_counts:
        print("Classifier sources:")
        for key, count in sorted(classifier_counts.items()):
            print(f"  {key}: {count}")
    if blocked_count:
        print(f"Suggestions suppressed by learned rules: {blocked_count}")
    print(f"Unsubscribe candidates: {unsubscribe_count}")
    if sample_rows:
        print("Sample suggestions:")
        for row in sample_rows:
            print(row)


def placeholders(values: list[object]) -> str:
    if not values:
        raise ValueError("values must not be empty")
    return ", ".join(["%s"] * len(values))


def parse_filter_datetime(value: str, *, end_of_day: bool) -> datetime:
    normalized = value.strip()
    if not normalized:
        raise ValueError("date value must not be empty")
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    if "T" not in normalized and " " not in normalized:
        normalized = f"{normalized}T{'23:59:59' if end_of_day else '00:00:00'}"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
