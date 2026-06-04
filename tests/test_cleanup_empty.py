from __future__ import annotations

import unittest

from tools.millie_cleanup_empty import CleanupSummary, bounded_limit, summary_dict


class CleanupEmptyTest(unittest.TestCase):
    def test_bounded_limit_never_returns_zero(self) -> None:
        self.assertEqual(bounded_limit(0), 1)
        self.assertEqual(bounded_limit(-10), 1)
        self.assertEqual(bounded_limit(25), 25)

    def test_summary_dict_only_reports_executed_deletes(self) -> None:
        summary = CleanupSummary(
            mode="execute",
            mailbox_leaf_folders=5,
            source_leaf_folders=4,
            blank_addresses=3,
            empty_import_jobs=2,
            empty_sources=1,
            deleted_mailbox_leaf_folders=5,
            deleted_source_leaf_folders=4,
            deleted_blank_addresses=3,
            deleted_empty_import_jobs=2,
            deleted_empty_sources=1,
        )

        self.assertEqual(
            summary_dict(summary),
            {
                "mode": "execute",
                "deleted_mailbox_leaf_folders": 5,
                "deleted_source_leaf_folders": 4,
                "deleted_blank_addresses": 3,
                "deleted_empty_import_jobs": 2,
                "deleted_empty_sources": 1,
            },
        )


if __name__ == "__main__":
    unittest.main()
