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

IMPORT_MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "millie_imap_bulk_import.py"
IMPORT_SPEC = importlib.util.spec_from_file_location("millie_imap_bulk_import", IMPORT_MODULE_PATH)
assert IMPORT_SPEC is not None
imap_bulk_import = importlib.util.module_from_spec(IMPORT_SPEC)
assert IMPORT_SPEC.loader is not None
sys.modules["millie_imap_bulk_import"] = imap_bulk_import
IMPORT_SPEC.loader.exec_module(imap_bulk_import)

WEBMAIL_MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "millie_webmail_server.py"
WEBMAIL_SPEC = importlib.util.spec_from_file_location("millie_webmail_server", WEBMAIL_MODULE_PATH)
assert WEBMAIL_SPEC is not None
webmail_server = importlib.util.module_from_spec(WEBMAIL_SPEC)
assert WEBMAIL_SPEC.loader is not None
sys.modules["millie_webmail_server"] = webmail_server
WEBMAIL_SPEC.loader.exec_module(webmail_server)


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

    def test_imap_bulk_import_parses_quoted_folder_names(self) -> None:
        folder = imap_bulk_import.parse_list_response(
            b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Sent Mail"'
        )

        self.assertIsNotNone(folder)
        self.assertEqual(folder.name, "[Gmail]/Sent Mail")
        self.assertEqual(folder.delimiter, "/")
        self.assertEqual(imap_bulk_import.special_mailbox_folders(folder, disabled=False), ["Sent"])

    def test_imap_bulk_import_skips_default_non_mail_folders(self) -> None:
        self.assertTrue(imap_bulk_import.is_default_non_mail_folder("Calendar/Birthdays"))
        self.assertTrue(imap_bulk_import.is_default_non_mail_folder("Sync Issues/Conflicts"))
        self.assertFalse(imap_bulk_import.is_default_non_mail_folder("INBOX/Clients"))

    def test_imap_bulk_import_parses_batched_uid_fetch_data(self) -> None:
        parsed = imap_bulk_import.parse_uid_fetch_messages(
            [
                (b'1 (UID 123 BODY[] {5}', b"first"),
                b")",
                (b'2 (FLAGS (\\Seen) UID 124 BODY[] {6}', b"second"),
            ]
        )

        self.assertEqual(parsed, {"123": b"first", "124": b"second"})

    def test_imap_bulk_import_finds_next_uid_for_incremental_sync(self) -> None:
        next_uid = imap_bulk_import.next_uid_after_existing(
            {"12345:9", "12345:10", "54321:99", "not-a-uid"},
            "12345",
        )

        self.assertEqual(next_uid, 11)

    def test_webmail_autodiscover_post_email_parser(self) -> None:
        body = (
            b"<?xml version='1.0'?><Autodiscover><Request>"
            b"<EMailAddress>geon@millie.cnbsk.cloud</EMailAddress>"
            b"</Request></Autodiscover>"
        )

        self.assertEqual(
            webmail_server.autodiscover_request_email(body),
            "geon@millie.cnbsk.cloud",
        )


if __name__ == "__main__":
    unittest.main()
