from __future__ import annotations

import unittest

from millie.service import imap_protocol


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


if __name__ == "__main__":
    unittest.main()
