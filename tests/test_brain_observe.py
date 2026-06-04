from __future__ import annotations

import unittest
from datetime import datetime, timezone

from millie.brain.observe import (
    BULK_REEVALUATION_FOLDER,
    LEARNED_RULE_CLASSIFIER_TYPE,
    SPAM_REEVALUATION_FOLDER,
    SortCandidate,
    TRASH_REEVALUATION_FOLDER,
    classify_candidate,
    extract_unsubscribe_suggestions,
)
from tools.millie_sort_mail import (
    LearnedRule,
    classify_with_learned_rules,
    parse_filter_datetime,
    unsubscribe_within_scope,
)


class BrainObserveTest(unittest.TestCase):
    def test_trash_source_folder_goes_to_reevaluation_hold_bucket(self) -> None:
        suggestions = classify_candidate(
            SortCandidate(
                message_id="message-1",
                subject="Old message",
                folder_path="Deleted Items",
            )
        )

        self.assertEqual(suggestions[0].kind, "trash")
        self.assertEqual(suggestions[0].target_folder_path, TRASH_REEVALUATION_FOLDER)
        self.assertEqual(suggestions[0].target_tags, ("trash", "hold", "reevaluate"))

    def test_spam_source_folder_goes_to_spam_reevaluation_bucket(self) -> None:
        suggestions = classify_candidate(
            SortCandidate(
                message_id="message-spam",
                subject="Bulk message",
                folder_path="Junk Email",
            )
        )

        self.assertEqual(suggestions[0].kind, "spam")
        self.assertEqual(suggestions[0].value, "likely_spam")
        self.assertEqual(suggestions[0].target_folder_path, SPAM_REEVALUATION_FOLDER)

    def test_spam_language_goes_to_bulk_reevaluation_bucket(self) -> None:
        suggestions = classify_candidate(
            SortCandidate(
                message_id="message-bulk",
                subject="Limited time offer",
                body_preview="Unsubscribe from this list at any time.",
            )
        )

        self.assertEqual(suggestions[0].kind, "spam")
        self.assertEqual(suggestions[0].value, "possible_spam")
        self.assertEqual(suggestions[0].target_folder_path, BULK_REEVALUATION_FOLDER)

    def test_receipt_message_gets_year_bucket(self) -> None:
        suggestions = classify_candidate(
            SortCandidate(
                message_id="message-2",
                subject="Your invoice and payment receipt",
                from_text="billing@example.test",
                received_at=datetime(2025, 4, 1, tzinfo=timezone.utc),
            )
        )

        self.assertEqual(suggestions[0].kind, "folder")
        self.assertEqual(suggestions[0].value, "receipts")
        self.assertEqual(suggestions[0].target_folder_path, "Receipts/2025")
        self.assertIn("receipt", suggestions[0].evidence["matched_keywords"])

    def test_education_message_gets_archive_education_bucket(self) -> None:
        suggestions = classify_candidate(
            SortCandidate(
                message_id="message-education",
                subject="Blackboard course reminder",
                from_text="teacher@example.test",
                received_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        )

        self.assertEqual(suggestions[0].kind, "folder")
        self.assertEqual(suggestions[0].value, "education")
        self.assertEqual(suggestions[0].target_folder_path, "Archive/Education/2026")

    def test_list_unsubscribe_header_requires_review(self) -> None:
        suggestions = extract_unsubscribe_suggestions(
            {
                "List-Unsubscribe": [
                    "<mailto:leave@example.test>, <https://example.test/unsub>"
                ]
            }
        )

        self.assertEqual(len(suggestions), 2)
        self.assertTrue(all(not item.requires_browser for item in suggestions))
        self.assertTrue(any(item.unsubscribe_mailto for item in suggestions))
        self.assertTrue(any(item.unsubscribe_url for item in suggestions))

    def test_sort_date_filter_parses_date_bounds(self) -> None:
        self.assertEqual(
            parse_filter_datetime("2026-06-03", end_of_day=False).isoformat(),
            "2026-06-03T00:00:00+00:00",
        )
        self.assertEqual(
            parse_filter_datetime("2026-06-03", end_of_day=True).isoformat(),
            "2026-06-03T23:59:59+00:00",
        )

    def test_unsubscribe_candidates_are_limited_to_recent_messages(self) -> None:
        now = datetime(2026, 6, 4, tzinfo=timezone.utc)

        self.assertTrue(
            unsubscribe_within_scope(
                SortCandidate(
                    message_id="recent",
                    received_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                ),
                lookback_days=183,
                now=now,
            )
        )
        self.assertFalse(
            unsubscribe_within_scope(
                SortCandidate(
                    message_id="old",
                    received_at=datetime(2025, 6, 1, tzinfo=timezone.utc),
                ),
                lookback_days=183,
                now=now,
            )
        )
        self.assertFalse(
            unsubscribe_within_scope(
                SortCandidate(message_id="undated"),
                lookback_days=183,
                now=now,
            )
        )
        self.assertTrue(
            unsubscribe_within_scope(
                SortCandidate(message_id="unscoped"),
                lookback_days=0,
                now=now,
            )
        )

    def test_active_learned_rule_suggests_matching_candidate(self) -> None:
        suggestions, blocked = classify_with_learned_rules(
            SortCandidate(
                message_id="message-3",
                subject="Team update",
                from_text="Ops <ops@example.test>",
                folder_path="INBOX",
                received_at=datetime(2026, 1, 5, tzinfo=timezone.utc),
            ),
            [
                LearnedRule(
                    id="rule-1",
                    rule_name="Always suggest work for example.test",
                    condition={
                        "sender_domain": "example.test",
                        "folder_path": "INBOX",
                        "message_year": "2026",
                    },
                    rule_action={
                        "action": "suggest",
                        "classification_kind": "folder",
                        "classification_value": "work",
                        "target_folder_path": "Archive/Work/2026",
                        "target_tags": ["work", "2026"],
                    },
                    confidence=0.83,
                    priority=100,
                    evidence_count=3,
                )
            ],
        )

        self.assertEqual(blocked, 0)
        self.assertEqual(suggestions[0].classifier_type, LEARNED_RULE_CLASSIFIER_TYPE)
        self.assertEqual(suggestions[0].rule_id, "rule-1")
        self.assertEqual(suggestions[0].target_folder_path, "Archive/Work/2026")

    def test_active_never_rule_suppresses_matching_heuristic(self) -> None:
        suggestions, blocked = classify_with_learned_rules(
            SortCandidate(
                message_id="message-4",
                subject="Your invoice and receipt",
                from_text="billing@example.test",
                folder_path="INBOX",
                received_at=datetime(2026, 1, 5, tzinfo=timezone.utc),
            ),
            [
                LearnedRule(
                    id="rule-2",
                    rule_name="Never suggest receipts for example.test",
                    condition={
                        "sender_domain": "example.test",
                        "classification_kind": "folder",
                        "classification_value": "receipts",
                        "target_folder_path": "Receipts/2026",
                        "target_tags": ["receipts", "2026"],
                    },
                    rule_action={
                        "action": "block_suggestion",
                        "classification_kind": "folder",
                        "classification_value": "receipts",
                        "target_folder_path": "Receipts/2026",
                        "target_tags": ["receipts", "2026"],
                    },
                    confidence=0.76,
                    priority=10,
                    evidence_count=2,
                )
            ],
        )

        self.assertEqual(suggestions, [])
        self.assertEqual(blocked, 1)


if __name__ == "__main__":
    unittest.main()
