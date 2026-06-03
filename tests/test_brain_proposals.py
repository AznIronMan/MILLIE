from __future__ import annotations

import unittest

from millie.brain.proposals import compact_values, proposal_confidence, target_label


class BrainProposalTest(unittest.TestCase):
    def test_proposal_confidence_is_bounded_and_rewards_evidence(self) -> None:
        self.assertEqual(proposal_confidence(0.8, 1), 0.8)
        self.assertEqual(proposal_confidence(0.8, 6), 0.85)
        self.assertEqual(proposal_confidence(0.99, 100), 0.95)
        self.assertEqual(proposal_confidence("bad", "bad"), 0.01)

    def test_target_label_prefers_folder_then_tags_then_kind_value(self) -> None:
        self.assertEqual(
            target_label(
                kind="folder",
                value="receipts",
                target_folder_path="Archive/Receipts/2026",
                target_tags=("receipts", "2026"),
            ),
            "Archive/Receipts/2026",
        )
        self.assertEqual(
            target_label(
                kind="tag",
                value="client",
                target_folder_path=None,
                target_tags=["client", "important"],
            ),
            "client, important",
        )
        self.assertEqual(
            target_label(
                kind="spam",
                value="possible_spam",
                target_folder_path=None,
                target_tags=[],
            ),
            "spam:possible_spam",
        )

    def test_compact_values_removes_empty_duplicates_and_limits(self) -> None:
        self.assertEqual(
            compact_values(["", "a", "b", "a", "c"], limit=2),
            ["a", "b"],
        )


if __name__ == "__main__":
    unittest.main()

