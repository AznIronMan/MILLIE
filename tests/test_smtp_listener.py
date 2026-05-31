from __future__ import annotations

import importlib.util
import smtplib
import ssl
import threading
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "millie_smtp_listener.py"
SPEC = importlib.util.spec_from_file_location("millie_smtp_listener", MODULE_PATH)
assert SPEC is not None
smtp_listener = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(smtp_listener)


class QuietSmtpHandler(smtp_listener.MillieSmtpHandler):
    def log_event(self, event: str, **fields: object) -> None:
        return None


class SmtpListenerTest(unittest.TestCase):
    def setUp(self) -> None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.server = smtp_listener.MillieSmtpServer(
            ("127.0.0.1", 0),
            QuietSmtpHandler,
            implicit_tls=False,
            ssl_context=context,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.server.server_address

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def test_accepts_message_without_authentication_for_setup_checks(self) -> None:
        client = smtplib.SMTP(self.host, self.port, timeout=5)
        client.ehlo()

        refused = client.sendmail(
            "anything@example.test",
            ["nobody@example.test"],
            "Subject: discarded\r\n\r\nNo auth setup check.",
        )
        client.quit()

        self.assertEqual(refused, {})

    def test_accepts_any_authentication_for_setup_checks(self) -> None:
        client = smtplib.SMTP(self.host, self.port, timeout=5)
        client.ehlo()

        code, response = client.login("fake-user@example.test", "wrong-password")
        refused = client.sendmail(
            "anything@example.test",
            ["nobody@example.test"],
            "Subject: discarded\r\n\r\nFake auth setup check.",
        )
        client.quit()

        self.assertEqual(code, 235)
        self.assertIn(b"outbound SMTP is disabled", response)
        self.assertEqual(refused, {})


if __name__ == "__main__":
    unittest.main()
