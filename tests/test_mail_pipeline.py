from __future__ import annotations

import sqlite3
import unittest
from email.message import EmailMessage

from millie.importing.normalize import normalize_email
from millie.storage.sqlite_store import SQLiteMailStore


class MailPipelineTest(unittest.TestCase):
    def test_normalize_and_store_complete_message_graph(self) -> None:
        message = EmailMessage()
        message["From"] = "Sender Example <sender@example.test>"
        message["To"] = "Recipient Example <recipient@example.test>"
        message["Cc"] = "Copy Example <copy@example.test>"
        message["Subject"] = "Re: Test message"
        message["Date"] = "Mon, 01 Jan 2024 10:15:00 -0800"
        message["Message-ID"] = "<message-1@example.test>"
        message.set_content("Plain body for search.")
        message.add_alternative("<p>HTML body</p>", subtype="html")
        message.add_attachment(
            b"attachment bytes",
            maintype="application",
            subtype="octet-stream",
            filename="example.bin",
        )
        raw_bytes = message.as_bytes()

        normalized = normalize_email(
            raw_bytes,
            source_message_id="uid-1",
            source_uri="imap://imap.example.test/INBOX",
            folder="INBOX",
        )

        self.assertEqual(normalized.internet_message_id, "message-1@example.test")
        self.assertEqual(normalized.normalized_subject, "Test message")
        self.assertTrue(normalized.has_attachments)
        self.assertIn("Plain body", normalized.body_text or "")
        self.assertTrue(any(address.role == "to" for address in normalized.addresses))
        self.assertTrue(any(part.filename == "example.bin" for part in normalized.parts))

        connection = sqlite3.connect(":memory:")
        store = SQLiteMailStore(connection)
        store.initialize()
        source_id = store.upsert_source(
            source_type="imap",
            source_uri="imap://imap.example.test/INBOX",
            display_name="Test IMAP",
            auth_mode="password",
        )
        job_id = store.create_import_job(source_id=source_id, status="planned")
        store.store_message(
            source_id=source_id,
            import_job_id=job_id,
            message=normalized,
            folder="INBOX",
        )

        self.assertEqual(
            connection.execute("SELECT count(*) FROM mail_messages").fetchone()[0],
            1,
        )
        self.assertEqual(
            connection.execute("SELECT count(*) FROM mail_raw_mime").fetchone()[0],
            1,
        )
        self.assertGreater(
            connection.execute("SELECT count(*) FROM mail_message_parts").fetchone()[0],
            1,
        )
        self.assertEqual(
            connection.execute(
                "SELECT count(*) FROM mail_message_addresses WHERE role = 'to'"
            ).fetchone()[0],
            1,
        )
        search_text = connection.execute(
            "SELECT search_text FROM mail_search_documents"
        ).fetchone()[0]
        self.assertIn("Plain body for search.", search_text)

        recalled = store.get_email_message(normalized.id)
        self.assertIsNotNone(recalled)
        self.assertEqual(recalled["Message-ID"], "<message-1@example.test>")


if __name__ == "__main__":
    unittest.main()
