from __future__ import annotations

import unittest

from millie.brain.apply import (
    normalize_target_folder,
    plan_classification_action,
    provider_like_target,
)
from millie.brain.automation import automation_level_allows


class ApplySuggestionsTest(unittest.TestCase):
    def test_plans_safe_folder_classification(self) -> None:
        action = plan_classification_action(
            {
                "classification_id": "classification-1",
                "message_id": "message-1",
                "kind": "folder",
                "value": "receipts",
                "target_folder_path": " Archive / Receipts / 2026 ",
                "confidence": 0.76,
                "reason": "matched receipt",
            }
        )

        self.assertIsNotNone(action)
        assert action is not None
        self.assertEqual(action.target_folder_path, "Archive/Receipts/2026")

    def test_rejects_non_internal_targets(self) -> None:
        self.assertTrue(provider_like_target("imap://example/INBOX"))
        self.assertIsNone(
            plan_classification_action(
                {
                    "classification_id": "classification-1",
                    "message_id": "message-1",
                    "kind": "folder",
                    "value": "remote",
                    "target_folder_path": "imap://example/INBOX",
                }
            )
        )

    def test_auto_internal_requires_level(self) -> None:
        self.assertFalse(automation_level_allows({"automation_level": "review"}, "auto_internal"))
        self.assertTrue(automation_level_allows({"automation_level": "auto_internal"}, "auto_internal"))

    def test_normalize_target_folder_removes_empty_parts(self) -> None:
        self.assertEqual(normalize_target_folder("/Hold//Spam/"), "Hold/Spam")


if __name__ == "__main__":
    unittest.main()
