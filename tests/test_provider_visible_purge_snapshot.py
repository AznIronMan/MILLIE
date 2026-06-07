from __future__ import annotations

import unittest
from datetime import datetime, timezone

from tools.millie_remote_purge_visible_snapshot import (
    imap_search_before_date,
    parse_cutoff,
    parse_internaldate_fetch,
)


class ProviderVisiblePurgeSnapshotTest(unittest.TestCase):
    def test_parse_internaldate_fetch_returns_utc_by_uid(self) -> None:
        parsed = parse_internaldate_fetch(
            [
                b'1 (UID 42 INTERNALDATE "06-Jun-2026 03:00:00 +0000")',
                b'2 (INTERNALDATE "05-Jun-2026 20:30:00 -0700" UID 43)',
            ]
        )

        self.assertEqual(parsed["42"], datetime(2026, 6, 6, 3, 0, tzinfo=timezone.utc))
        self.assertEqual(parsed["43"], datetime(2026, 6, 6, 3, 30, tzinfo=timezone.utc))

    def test_imap_search_before_date_is_broad_then_exact_filter_can_apply(self) -> None:
        cutoff = parse_cutoff("2026-06-06T03:44:00+00:00")

        self.assertEqual(imap_search_before_date(cutoff), "07-Jun-2026")


if __name__ == "__main__":
    unittest.main()
