from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from millie.database import MillieDatabase
from millie.exporters import export_messages
from millie.importers import import_path
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
    def test_import_eml_and_export_eml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = MillieDatabase(root / "millie.sqlite", root / "data")
            db.init()

            source = root / "sample.eml"
            source.write_bytes(SAMPLE_EML)
            result = import_path(db, source, "eml", "Unit Test Mail")

            self.assertEqual(result.imported, 1)
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


if __name__ == "__main__":
    unittest.main()
