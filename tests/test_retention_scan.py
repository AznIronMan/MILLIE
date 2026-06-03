from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from millie.brain.retention import (
    HeldMessage,
    RetentionPolicy,
    human_duration,
    normalize_folder,
    retention_candidate,
    retention_status,
)


class RetentionScanTest(unittest.TestCase):
    def test_folder_normalization(self) -> None:
        self.assertEqual(normalize_folder("/Hold//Trash/"), "Hold/Trash")

    def test_message_becomes_candidate_after_hold_duration(self) -> None:
        policy = RetentionPolicy(
            id="policy-1",
            name="Trash review",
            status="proposed",
            target_kind="folder",
            target_value="Hold/Trash",
            hold_duration=timedelta(days=30),
            action="no_action",
            requires_review=True,
        )
        message = HeldMessage(
            mailbox_message_id="mailbox-message-1",
            message_id="message-1",
            folder_path="Hold/Trash",
            imap_uid=1,
            copied_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            subject="Old message",
        )

        candidate = retention_candidate(
            policy,
            message,
            now=datetime(2026, 2, 1, tzinfo=timezone.utc),
        )

        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate.eligible_at, datetime(2026, 1, 31, tzinfo=timezone.utc))

    def test_recent_message_is_not_candidate(self) -> None:
        policy = RetentionPolicy(
            id="policy-1",
            name="Trash review",
            status="proposed",
            target_kind="folder",
            target_value="Hold/Trash",
            hold_duration=timedelta(days=30),
            action="no_action",
            requires_review=True,
        )
        message = HeldMessage(
            mailbox_message_id="mailbox-message-1",
            message_id="message-1",
            folder_path="Hold/Trash",
            imap_uid=1,
            copied_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
            subject="Recent message",
        )

        self.assertIsNone(
            retention_candidate(
                policy,
                message,
                now=datetime(2026, 2, 1, tzinfo=timezone.utc),
            )
        )

    def test_retention_status_reports_future_eligibility(self) -> None:
        policy = RetentionPolicy(
            id="policy-1",
            name="Trash review",
            status="proposed",
            target_kind="folder",
            target_value="Hold/Trash",
            hold_duration=timedelta(days=30),
            action="no_action",
            requires_review=True,
        )
        message = HeldMessage(
            mailbox_message_id="mailbox-message-1",
            message_id="message-1",
            folder_path="Hold/Trash",
            imap_uid=1,
            copied_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
            subject="Recent message",
        )

        status = retention_status(
            policy,
            message,
            now=datetime(2026, 2, 1, tzinfo=timezone.utc),
        )

        self.assertIsNotNone(status)
        assert status is not None
        self.assertFalse(status.is_eligible)
        self.assertEqual(status.eligible_at, datetime(2026, 2, 14, tzinfo=timezone.utc))
        self.assertEqual(status.age_seconds, 17 * 86400)

    def test_human_duration(self) -> None:
        self.assertEqual(human_duration(timedelta(days=14)), "14 days")
        self.assertEqual(human_duration(timedelta(hours=1)), "1 hour")
        self.assertEqual(human_duration(None), "none")


if __name__ == "__main__":
    unittest.main()
