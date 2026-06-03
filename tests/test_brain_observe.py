from __future__ import annotations

import unittest
from datetime import datetime, timezone

from millie.brain.observe import (
    SortCandidate,
    classify_candidate,
    extract_unsubscribe_suggestions,
)


class BrainObserveTest(unittest.TestCase):
    def test_trash_source_folder_goes_to_hold_trash(self) -> None:
        suggestions = classify_candidate(
            SortCandidate(
                message_id="message-1",
                subject="Old message",
                folder_path="Deleted Items",
            )
        )

        self.assertEqual(suggestions[0].kind, "trash")
        self.assertEqual(suggestions[0].target_folder_path, "Hold/Trash")

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
        self.assertEqual(suggestions[0].target_folder_path, "Archive/Receipts/2025")
        self.assertIn("receipt", suggestions[0].evidence["matched_keywords"])

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


if __name__ == "__main__":
    unittest.main()
