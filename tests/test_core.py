from __future__ import annotations

import tempfile
import unittest
import mailbox
import json
from email.message import EmailMessage
from pathlib import Path

from millie.auth import AuthManager, SESSION_COOKIE
from millie.database import MillieDatabase
from millie.exporters import export_messages
from millie.importers import detect_format, import_path
from millie.profiles import ProfileManager


SAMPLE_EML = b"""From: Alice Example <alice@example.com>\r
To: Bob Example <bob@example.com>\r
Subject: Hello from MILLIE\r
Message-ID: <sample-1@example.com>\r
Date: Fri, 01 Jan 2021 00:00:00 +0000\r
MIME-Version: 1.0\r
Content-Type: text/plain; charset=utf-8\r
\r
Hello Bob.\r
This message is a tiny archive seed.\r
"""


class CoreImportExportTests(unittest.TestCase):
    def test_detects_pst_format(self) -> None:
        self.assertEqual(detect_format(Path("archive.pst")), "pst")

    def test_import_eml_and_export_eml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = MillieDatabase(root / "millie.sqlite", root / "data")
            db.init()

            source = root / "sample.eml"
            source.write_bytes(SAMPLE_EML)
            result = import_path(db, source, "eml", "Unit Test Mail")

            self.assertEqual(result.imported, 1)
            self.assertEqual(result.processed, 1)
            self.assertEqual(result.duplicates, 0)
            messages = db.list_messages()
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0]["subject"], "Hello from MILLIE")

            detail = db.get_message(int(messages[0]["id"]))
            self.assertIsNotNone(detail)
            self.assertEqual(len(detail["addresses"]), 2)

            export_dir = root / "exports"
            export_result = export_messages(db, export_dir, "eml")
            self.assertEqual(export_result.exported, 1)
            self.assertTrue(export_result.manifest_path.exists())
            self.assertEqual(len(list(export_dir.rglob("*.eml"))), 1)
            manifest = json.loads(export_result.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["target_profile"], "generic-eml")
            self.assertEqual(manifest["format"], "eml")
            self.assertEqual(manifest["folder_count"], 1)
            self.assertEqual(manifest["attachment_count"], 0)
            self.assertEqual(manifest["source_ids"], [result.source_id])
            self.assertEqual(manifest["items"][0]["source_id"], result.source_id)
            self.assertEqual(db.list_migrations()[0]["version"], 1)
            self.assertEqual(db.list_migrations()[-1]["version"], 2)

    def test_repeat_import_deduplicates_by_raw_content_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = MillieDatabase(root / "millie.sqlite", root / "data")
            db.init()

            source = root / "sample.eml"
            source.write_bytes(SAMPLE_EML)
            first = import_path(db, source, "eml", "Unit Test Mail")
            second = import_path(db, source, "eml", "Unit Test Mail")

            self.assertEqual(first.imported, 1)
            self.assertEqual(first.processed, 1)
            self.assertEqual(first.duplicates, 0)
            self.assertEqual(second.imported, 0)
            self.assertEqual(second.processed, 1)
            self.assertEqual(second.duplicates, 1)
            self.assertEqual(len(db.list_messages()), 1)

            jobs = db.list_import_jobs()
            self.assertEqual(jobs[0]["message_count"], 1)
            self.assertEqual(jobs[0]["new_message_count"], 0)
            self.assertEqual(jobs[0]["duplicate_count"], 1)

    def test_search_handles_addresses_and_punctuation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = MillieDatabase(root / "millie.sqlite", root / "data")
            db.init()

            source = root / "sample.eml"
            source.write_bytes(SAMPLE_EML)
            import_path(db, source, "eml", "Unit Test Mail")

            self.assertEqual(len(db.list_messages(query="alice@example.com")), 1)
            self.assertEqual(len(db.list_messages(query="Hello: MILLIE?")), 1)
            self.assertEqual(len(db.list_messages(query="does-not-exist@example.com")), 0)

    def test_import_html_and_attachment_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = MillieDatabase(root / "millie.sqlite", root / "data")
            db.init()

            source = root / "multipart.eml"
            source.write_bytes(build_multipart_message().as_bytes())
            result = import_path(db, source, "eml", "Multipart Fixture")

            self.assertEqual(result.imported, 1)
            self.assertEqual(result.processed, 1)
            messages = db.list_messages(query="quarterly")
            self.assertEqual(len(messages), 1)

            detail = db.get_message(int(messages[0]["id"]))
            self.assertIsNotNone(detail)
            self.assertEqual(detail["subject"], "Multipart Fixture")
            self.assertIsNotNone(detail["body_html_ref"])
            self.assertIsNotNone(detail["body_sanitized_html_ref"])
            sanitized = db.get_sanitized_message_html(int(messages[0]["id"]))
            self.assertIsNotNone(sanitized)
            sanitized_text = sanitized.decode("utf-8")
            self.assertIn("HTML quarterly archive body.", sanitized_text)
            self.assertIn("https://example.com/report", sanitized_text)
            self.assertNotIn("script", sanitized_text.lower())
            self.assertNotIn("onclick", sanitized_text.lower())
            self.assertNotIn("javascript:", sanitized_text.lower())
            self.assertNotIn("//example.com/tracker", sanitized_text.lower())
            self.assertEqual(len(detail["attachments"]), 1)
            self.assertEqual(detail["attachments"][0]["filename"], "report.csv")
            attachment = db.get_attachment(int(detail["attachments"][0]["id"]))
            self.assertIsNotNone(attachment)
            self.assertEqual(attachment["content"], b"quarter,value\nQ1,42\n")

    def test_import_mbox_and_export_mbox(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = MillieDatabase(root / "millie.sqlite", root / "data")
            db.init()

            mbox_path = root / "fixture.mbox"
            box = mailbox.mbox(mbox_path)
            box.add(build_message("one@example.com", "MBOX One", "First archive body"))
            box.add(build_message("two@example.com", "MBOX Two", "Second archive body"))
            box.flush()
            box.close()

            result = import_path(db, mbox_path, "mbox", "MBOX Fixture")
            self.assertEqual(result.imported, 2)
            self.assertEqual(len(db.list_messages()), 2)

            export_dir = root / "exports"
            export_result = export_messages(db, export_dir, "mbox")
            self.assertEqual(export_result.exported, 2)
            self.assertTrue((export_dir / "fixture.mbox").exists())

    def test_export_profile_auto_selects_recommended_format(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = MillieDatabase(root / "millie.sqlite", root / "data")
            db.init()

            source = root / "sample.eml"
            source.write_bytes(SAMPLE_EML)
            import_path(db, source, "eml", "Unit Test Mail")

            export_dir = root / "exports"
            export_result = export_messages(db, export_dir, "auto", target_profile="thunderbird")

            self.assertEqual(export_result.exported, 1)
            manifest = json.loads(export_result.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["target_profile"], "thunderbird")
            self.assertEqual(manifest["format"], "mbox")
            self.assertEqual(manifest["target_profile_display_name"], "Thunderbird Import")
            self.assertGreater(len(manifest["import_instructions"]), 0)
            self.assertTrue((export_dir / "Imported.mbox").exists())

    def test_import_maildir_and_export_maildir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = MillieDatabase(root / "millie.sqlite", root / "data")
            db.init()

            maildir_path = root / "Maildir"
            maildir = mailbox.Maildir(maildir_path, create=True)
            maildir.add(build_message("maildir@example.com", "Maildir Fixture", "Maildir archive body"))
            maildir.flush()
            maildir.close()

            result = import_path(db, maildir_path, "maildir", "Maildir Fixture")
            self.assertEqual(result.imported, 1)
            self.assertEqual(len(db.list_messages()), 1)

            export_dir = root / "exports"
            export_result = export_messages(db, export_dir, "maildir")
            self.assertEqual(export_result.exported, 1)
            self.assertEqual(len(list((export_dir / "Maildir" / "new").glob("*"))), 1)

    def test_profile_manager_remembers_active_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = ProfileManager(
                root / "millie.settings",
                root / "profiles",
                root / "default.sqlite",
                root / "default-data",
            )

            created = manager.create_profile("Fixture Mail", switch=True)
            self.assertEqual(manager.active_profile_id, created.id)
            self.assertTrue(created.db_path.exists())

            reloaded = ProfileManager(
                root / "millie.settings",
                root / "profiles",
                root / "default.sqlite",
                root / "default-data",
            )
            self.assertEqual(reloaded.active_profile_id, created.id)
            self.assertEqual(reloaded.active_profile().name, "Fixture Mail")
            self.assertTrue((root / "millie.settings").exists())
            self.assertTrue(created.settings_path.exists())

    def test_auth_defaults_to_dev_bypass_and_supports_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = ProfileManager(
                root / "millie.settings",
                root / "profiles",
                root / "default.sqlite",
                root / "default-data",
            )
            auth = AuthManager(manager)

            status = auth.status(None)
            self.assertTrue(status.dev_bypass)
            self.assertTrue(status.authenticated)
            self.assertTrue(status.setup_required)

            manager.set_app_setting("auth.dev_bypass", "false")
            self.assertFalse(auth.status(None).authenticated)

            token = auth.setup_admin("admin", "password123")
            cookie = f"{SESSION_COOKIE}={token}"
            self.assertTrue(auth.status(cookie).authenticated)
            self.assertEqual(auth.status(cookie).username, "admin")

            login_token = auth.login("admin", "password123")
            self.assertTrue(auth.status(f"{SESSION_COOKIE}={login_token}").authenticated)
            with self.assertRaises(ValueError):
                auth.login("admin", "wrong-password")


def build_message(sender: str, subject: str, body: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = f"Fixture Sender <{sender}>"
    message["To"] = "Fixture Recipient <recipient@example.com>"
    message["Subject"] = subject
    message["Message-ID"] = f"<{subject.lower().replace(' ', '-')}@example.com>"
    message["Date"] = "Fri, 01 Jan 2021 00:00:00 +0000"
    message.set_content(body)
    return message


def build_multipart_message() -> EmailMessage:
    message = build_message(
        "multipart@example.com",
        "Multipart Fixture",
        "Plain quarterly archive body.",
    )
    message.add_alternative(
        """
        <html>
          <body>
            <p onclick="alert('x')">HTML quarterly archive body.</p>
            <script>alert('bad')</script>
            <a href="javascript:alert('bad')">bad link</a>
            <a href="//example.com/tracker">protocol-relative link</a>
            <a href="https://example.com/report">good link</a>
            <img src="https://example.com/track.png" alt="blocked image" />
          </body>
        </html>
        """,
        subtype="html",
    )
    message.add_attachment(
        b"quarter,value\nQ1,42\n",
        maintype="text",
        subtype="csv",
        filename="report.csv",
    )
    return message


if __name__ == "__main__":
    unittest.main()
