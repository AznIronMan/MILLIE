from __future__ import annotations

import unittest
from datetime import datetime, timezone

from tools.millie_taxonomy_folders import (
    rollup_paths,
    taxonomy_target_for_classification,
    taxonomy_target_for_source_folder,
)


class TaxonomyFoldersTest(unittest.TestCase):
    def test_receipts_are_top_level(self) -> None:
        self.assertEqual(
            taxonomy_target_for_classification(
                {
                    "kind": "folder",
                    "value": "receipts",
                    "target_folder_path": "Archive/Receipts/2026",
                }
            ),
            "Receipts/2026",
        )

    def test_taxes_move_under_archive_personal(self) -> None:
        self.assertEqual(
            taxonomy_target_for_classification(
                {
                    "kind": "folder",
                    "value": "taxes",
                    "target_folder_path": "Archive/Taxes/2025",
                }
            ),
            "Archive/Personal/Taxes/2025",
        )

    def test_cnb_inbox_gets_cnb_year_bucket(self) -> None:
        self.assertEqual(
            taxonomy_target_for_source_folder(
                "Sources/IMAP/geoff@cnb.llc/INBOX",
                datetime(2026, 6, 4, tzinfo=timezone.utc),
            ),
            "CNB/Inbox/2026",
        )

    def test_source_deleted_goes_to_trash_hold(self) -> None:
        self.assertEqual(
            taxonomy_target_for_source_folder(
                "Sources/IMAP/geoff@cnb.llc/Deleted Items",
                datetime(2026, 6, 4, tzinfo=timezone.utc),
            ),
            "Trash_Hold/CNB/Deleted/2026",
        )

    def test_pst_gmail_export_goes_to_archive_personal_gmail(self) -> None:
        self.assertEqual(
            taxonomy_target_for_source_folder(
                "Sources/PST/20191207_Export_gclark82@gmail.com/Inbox",
                datetime(2020, 2, 2, tzinfo=timezone.utc),
            ),
            "Archive/Personal/Gmail/2020",
        )

    def test_unknown_pst_goes_to_archive_misc(self) -> None:
        self.assertEqual(
            taxonomy_target_for_source_folder(
                "Sources/PST/ECS_Archive/Inbox",
                datetime(2019, 2, 2, tzinfo=timezone.utc),
            ),
            "Archive/Misc/2019",
        )

    def test_csu_pst_goes_to_archive_education(self) -> None:
        self.assertEqual(
            taxonomy_target_for_source_folder(
                "Sources/PST/CSU_Archive/Inbox",
                datetime(2022, 2, 2, tzinfo=timezone.utc),
            ),
            "Archive/Education/2022",
        )

    def test_clarktribe_inbox_goes_to_personal_inbox(self) -> None:
        self.assertEqual(
            taxonomy_target_for_source_folder(
                "Sources/IMAP/geoff@clarktribe.com/INBOX",
                datetime(2026, 6, 4, tzinfo=timezone.utc),
            ),
            "Personal/ClarkTribe/Inbox/2026",
        )

    def test_personal_invoice_source_goes_to_receipts(self) -> None:
        self.assertEqual(
            taxonomy_target_for_source_folder(
                "Sources/IMAP/geoff@clarktribe.com/INBOX/Invoices",
                datetime(2026, 6, 4, tzinfo=timezone.utc),
            ),
            "Receipts/2026",
        )

    def test_rollup_paths_include_top_level(self) -> None:
        self.assertEqual(
            rollup_paths("Archive/Personal/Taxes/2026"),
            [
                "Archive",
                "Archive/Personal",
                "Archive/Personal/Taxes",
                "Archive/Personal/Taxes/2026",
            ],
        )


if __name__ == "__main__":
    unittest.main()
