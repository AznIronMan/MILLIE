from __future__ import annotations

import unittest

from tools.millie_classification_review_buckets import (
    APPROVE_LIKELY,
    NEEDS_SKIM,
    REJECT_LIKELY,
    ReviewItem,
    bucket_item,
    review_paths,
)


def item(**overrides: object) -> ReviewItem:
    values = {
        "classification_id": "classification-1",
        "message_id": "message-1",
        "kind": "folder",
        "value": "work",
        "target_folder_path": "Archive/Work/2017",
        "confidence": 0.62,
        "subject": "Ticket 5515 Not completed",
        "from_text": "helpdesk@charlestonent.com",
        "sender_domain": "charlestonent.com",
        "source_folders": "Inbox",
        "reason": "Matched work keywords.",
        "evidence": {"matched_keywords": ["client"]},
    }
    values.update(overrides)
    return ReviewItem(**values)  # type: ignore[arg-type]


class ClassificationReviewBucketsTest(unittest.TestCase):
    def test_known_work_domain_is_approve_likely(self) -> None:
        self.assertEqual(bucket_item(item()).bucket, APPROVE_LIKELY)

    def test_known_non_work_domain_is_reject_likely(self) -> None:
        bucketed = bucket_item(item(sender_domain="email.wwe.com", subject="WWE Superstar Sunday"))
        self.assertEqual(bucketed.bucket, REJECT_LIKELY)

    def test_provider_spam_is_approve_likely_hold(self) -> None:
        bucketed = bucket_item(
            item(
                kind="spam",
                value="likely_spam",
                target_folder_path="Hold/Reevaluate/Spam",
                source_folders="[Gmail]/Spam",
                sender_domain="example.com",
            )
        )
        self.assertEqual(bucketed.bucket, APPROVE_LIKELY)

    def test_bulk_false_positive_is_reject_likely(self) -> None:
        bucketed = bucket_item(
            item(
                kind="spam",
                value="possible_spam",
                target_folder_path="Hold/Reevaluate/Bulk",
                sender_domain="support.porkbun.com",
                subject="porkbun.com | Order - Thank You - 9348348",
            )
        )
        self.assertEqual(bucketed.bucket, REJECT_LIKELY)

    def test_recent_unknown_work_proposal_needs_skim(self) -> None:
        bucketed = bucket_item(
            item(
                target_folder_path="Archive/Work/2026",
                sender_domain="example.net",
                subject="Meeting notes",
            )
        )
        self.assertEqual(bucketed.bucket, NEEDS_SKIM)

    def test_review_paths_include_rollup_target_and_domain(self) -> None:
        bucketed = bucket_item(item())
        self.assertEqual(
            review_paths(bucketed, root="Review/Classification", include_domain=True),
            [
                "Review/Classification",
                "Review/Classification/Approve Likely",
                "Review/Classification/Approve Likely/Archive/Work/2017",
                "Review/Classification/Approve Likely/Archive/Work/2017/charlestonent.com",
            ],
        )


if __name__ == "__main__":
    unittest.main()
