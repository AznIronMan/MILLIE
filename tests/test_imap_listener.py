from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

from millie.service import imap_protocol


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "millie_imap_listener.py"
SPEC = importlib.util.spec_from_file_location("millie_imap_listener", MODULE_PATH)
assert SPEC is not None
imap_listener = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules["millie_imap_listener"] = imap_listener
SPEC.loader.exec_module(imap_listener)


class ImapListenerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.listener = imap_protocol

    def test_body_peek_fetch_response_uses_body_name(self) -> None:
        name = self.listener.body_literal_name("BODY.PEEK[HEADER.FIELDS (DATE FROM)]")

        self.assertEqual(name, "BODY[HEADER.FIELDS (DATE FROM)]")

    def test_partial_body_fetch_response_keeps_start_offset(self) -> None:
        name = self.listener.body_literal_name("BODY.PEEK[]<0.393216>")

        self.assertEqual(name, "BODY[]<0>")

    def test_plaintext_capabilities_do_not_offer_starttls(self) -> None:
        capabilities = self.listener.imap_capabilities()

        self.assertNotIn("STARTTLS", capabilities)
        self.assertIn("MOVE", capabilities)

    def test_store_operation_parser_handles_silent_flag_adds(self) -> None:
        operation = imap_listener.parse_store_operation("1:* +FLAGS.SILENT (\\Seen \\Deleted $Archive)")

        self.assertIsNotNone(operation)
        self.assertEqual(operation.set_spec, "1:*")
        self.assertEqual(operation.mode, "add")
        self.assertTrue(operation.silent)
        self.assertEqual(operation.flags, ["\\Seen", "\\Deleted", "$Archive"])

    def test_append_parser_preserves_quoted_folder_with_spaces(self) -> None:
        request = imap_listener.parse_append_request(
            '"All Mail" (\\Seen) "01-Jan-2026 10:00:00 +0000" {5}',
            b"hello",
        )

        self.assertIsNotNone(request)
        self.assertEqual(request.folder, "All Mail")
        self.assertEqual(request.flags, ["\\Seen"])
        self.assertEqual(request.literal, b"hello")
        self.assertIsNotNone(request.internal_date)

    def test_expunge_sequence_numbers_shift_after_each_expunge(self) -> None:
        messages = [{"uid": 10}, {"uid": 11}, {"uid": 12}, {"uid": 13}]

        self.assertEqual(imap_listener.expunge_sequence_numbers(messages, [10, 12]), [1, 2])


if __name__ == "__main__":
    unittest.main()
