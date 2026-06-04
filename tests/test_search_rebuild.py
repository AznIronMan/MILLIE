from __future__ import annotations

import unittest

from tools.millie_rebuild_search_documents import (
    address_display,
    search_text,
    truncate_search_text,
)


class SearchRebuildTest(unittest.TestCase):
    def test_address_display_prefers_structured_name_and_email(self) -> None:
        self.assertEqual(
            address_display("Sender Name", "sender@example.test", "raw"),
            "Sender Name sender@example.test",
        )

    def test_address_display_falls_back_to_raw_value(self) -> None:
        self.assertEqual(address_display(None, None, "raw@example.test"), "raw@example.test")

    def test_search_text_skips_empty_parts(self) -> None:
        self.assertEqual(search_text(["subject", "", None, "body"]), "subject body")

    def test_truncate_search_text_respects_utf8_boundary(self) -> None:
        value = "a" + ("é" * 10)
        truncated = truncate_search_text(value, max_bytes=6)
        self.assertEqual(truncated, "aéé")


if __name__ == "__main__":
    unittest.main()
