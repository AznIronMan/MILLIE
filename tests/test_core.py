from __future__ import annotations

import tempfile
import unittest
import mailbox
import json
import re
from email.message import EmailMessage
from pathlib import Path

from millie.auth import AuthManager, SESSION_COOKIE
from millie.database import MillieDatabase
from millie.exporters import export_messages
from millie.importers import detect_format, import_path
from millie.imap_connector import (
    IMAP_SOURCES_SETTING,
    ImapSourceConfig,
    config_from_dict,
    delete_imap_source,
    discover_imap_folders,
    folder_role,
    get_imap_source,
    list_imap_provider_presets,
    load_imap_sources,
    migrate_imap_source_secrets,
    save_imap_source,
    sync_imap_source,
)
from millie.profiles import ProfileManager
from millie.secrets import SecretManager
from millie.source_scanners import scan_source


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

    def test_detects_unsupported_outlook_formats(self) -> None:
        self.assertEqual(detect_format(Path("archive.olm")), "olm")
        self.assertEqual(detect_format(Path("archive.ost")), "ost")

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
            self.assertEqual(db.list_migrations()[-1]["version"], 3)

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

    def test_scan_thunderbird_profile_and_import_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = root / "Profiles" / "abc123.default-release"
            mailbox_root = profile / "Mail" / "Local Folders"
            mailbox_root.mkdir(parents=True)
            (profile / "prefs.js").write_text("// Thunderbird fixture\n", encoding="utf-8")

            inbox_path = mailbox_root / "Inbox"
            box = mailbox.mbox(inbox_path)
            box.add(build_message("thunderbird@example.com", "Thunderbird Inbox", "Profile mailbox body"))
            box.flush()
            box.close()
            (mailbox_root / "Inbox.msf").write_text("metadata index", encoding="utf-8")

            candidates = scan_source(root, "thunderbird")
            self.assertEqual(len(candidates), 1)
            candidate = candidates[0]
            self.assertEqual(candidate.format, "mbox")
            self.assertEqual(candidate.source_type, "thunderbird")
            self.assertEqual(candidate.mailbox_path, "Inbox")
            self.assertEqual(candidate.message_estimate, 1)
            self.assertNotIn("Inbox.msf", candidate.path)

            db = MillieDatabase(root / "millie.sqlite", root / "data")
            db.init()
            result = import_path(db, Path(candidate.path), candidate.format, candidate.display_name)
            self.assertEqual(result.imported, 1)
            self.assertEqual(db.list_messages()[0]["subject"], "Thunderbird Inbox")

    def test_scan_evolution_store_and_import_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mailbox_root = root / "mail" / "local"
            mailbox_root.mkdir(parents=True)
            (root / "folders.db").write_bytes(b"sqlite fixture")

            inbox_path = mailbox_root / "Inbox"
            box = mailbox.mbox(inbox_path)
            box.add(build_message("evolution@example.com", "Evolution Inbox", "Evolution mailbox body"))
            box.flush()
            box.close()
            (mailbox_root / "Inbox.cmeta").write_text("metadata index", encoding="utf-8")

            candidates = scan_source(root, "evolution")
            self.assertEqual(len(candidates), 1)
            candidate = candidates[0]
            self.assertEqual(candidate.format, "mbox")
            self.assertEqual(candidate.source_type, "evolution")
            self.assertTrue(candidate.mailbox_path.endswith("Inbox"))
            self.assertEqual(candidate.message_estimate, 1)
            self.assertNotIn("Inbox.cmeta", candidate.path)

            db = MillieDatabase(root / "millie.sqlite", root / "data")
            db.init()
            result = import_path(db, Path(candidate.path), candidate.format, candidate.display_name)
            self.assertEqual(result.imported, 1)
            self.assertEqual(db.list_messages()[0]["subject"], "Evolution Inbox")

    def test_scan_apple_mail_emlx_folder_and_import_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            messages_dir = root / "V10" / "Mailboxes" / "Archive.mbox" / "Messages"
            messages_dir.mkdir(parents=True)
            (root / "V10" / "MailData").mkdir(parents=True)
            raw = build_message("apple@example.com", "Apple Mail Archive", "Apple Mail body").as_bytes()
            (messages_dir / "123.emlx").write_bytes(build_emlx(raw))
            (messages_dir.parent / "Info.plist").write_text("<plist></plist>", encoding="utf-8")

            candidates = scan_source(root, "apple-mail")
            self.assertEqual(len(candidates), 1)
            candidate = candidates[0]
            self.assertEqual(candidate.format, "eml-dir")
            self.assertEqual(candidate.source_type, "apple-mail")
            self.assertTrue(candidate.mailbox_path.endswith("Archive"))
            self.assertEqual(candidate.message_estimate, 1)

            db = MillieDatabase(root / "millie.sqlite", root / "data")
            db.init()
            result = import_path(db, Path(candidate.path), candidate.format, candidate.display_name)
            self.assertEqual(result.imported, 1)
            self.assertEqual(db.list_messages()[0]["subject"], "Apple Mail Archive")
            self.assertEqual(db.list_messages()[0]["mailbox_path"], "Imported")

            profile_db = MillieDatabase(root / "profiled.sqlite", root / "profiled-data")
            profile_db.init()
            result = import_path(
                profile_db,
                Path(candidate.path),
                candidate.format,
                candidate.display_name,
                candidate.mailbox_path,
            )
            self.assertEqual(result.imported, 1)
            self.assertEqual(profile_db.list_messages()[0]["mailbox_path"], "Mailboxes/Archive")

    def test_scan_unsupported_outlook_files_and_import_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            olm_path = root / "archive.olm"
            ost_path = root / "cache.ost"
            olm_path.write_bytes(b"OLM fixture")
            ost_path.write_bytes(b"OST fixture")

            olm_candidates = scan_source(olm_path, "auto")
            self.assertEqual(len(olm_candidates), 1)
            self.assertFalse(olm_candidates[0].importable)
            self.assertEqual(olm_candidates[0].format, "olm")
            self.assertEqual(olm_candidates[0].source_type, "outlook")
            self.assertIn("OLM import is not implemented", " ".join(olm_candidates[0].notes))

            ost_candidates = scan_source(ost_path, "generic")
            self.assertEqual(len(ost_candidates), 1)
            self.assertFalse(ost_candidates[0].importable)
            self.assertEqual(ost_candidates[0].format, "ost")
            self.assertIn("OST import is not implemented", " ".join(ost_candidates[0].notes))

            db = MillieDatabase(root / "millie.sqlite", root / "data")
            db.init()
            with self.assertRaisesRegex(RuntimeError, "OLM import is not implemented"):
                import_path(db, olm_path, "auto", "OLM Fixture")

            jobs = db.list_import_jobs()
            self.assertEqual(jobs[0]["status"], "failed")
            self.assertEqual(jobs[0]["kind"], "olm")
            self.assertEqual(jobs[0]["error_count"], 1)
            errors = db.get_import_job_errors(int(jobs[0]["id"]))
            self.assertEqual(len(errors), 1)
            self.assertIn("OLM import is not implemented", errors[0]["message"])

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

    def test_imap_sync_imports_incrementally_by_uid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = MillieDatabase(root / "millie.sqlite", root / "data")
            db.init()
            messages = {
                "INBOX": {
                    "1": build_message("imap-one@example.com", "IMAP One", "First IMAP body").as_bytes(),
                    "2": build_message("imap-two@example.com", "IMAP Two", "Second IMAP body").as_bytes(),
                }
            }
            config = ImapSourceConfig(
                id="unit-imap",
                name="Unit IMAP",
                host="imap.example.test",
                port=993,
                username="user@example.test",
                password="secret",
                use_tls=True,
                folders=["INBOX"],
                sync_limit=50,
            )

            first = sync_imap_source(db, config, lambda _: FakeImapClient(messages))
            self.assertEqual(first.processed, 2)
            self.assertEqual(first.imported, 2)
            self.assertEqual(first.duplicates, 0)
            self.assertEqual(first.errors, 0)
            self.assertEqual(len(db.list_messages()), 2)
            self.assertEqual(db.list_migrations()[-1]["version"], 3)
            detail = db.get_message(int(db.list_messages(query="IMAP One")[0]["id"]))
            self.assertIsNotNone(detail)
            self.assertEqual(detail["internal_date"], "2021-01-01T00:00:00+00:00")
            self.assertEqual(json.loads(detail["mailboxes"][0]["flags_json"]), ["\\Seen", "\\Flagged"])

            state = db.get_source_sync_state(first.source_id, "folder:INBOX")
            self.assertEqual(state["uidvalidity"], "999")
            self.assertEqual(state["last_uid"], 2)

            messages["INBOX"]["3"] = build_message(
                "imap-three@example.com",
                "IMAP Three",
                "Third IMAP body",
            ).as_bytes()
            second = sync_imap_source(db, config, lambda _: FakeImapClient(messages))
            self.assertEqual(second.processed, 1)
            self.assertEqual(second.imported, 1)
            self.assertEqual(second.duplicates, 0)
            self.assertEqual(second.errors, 0)
            self.assertEqual(len(db.list_messages()), 3)
            self.assertEqual(db.get_source_sync_state(first.source_id, "folder:INBOX")["last_uid"], 3)

    def test_imap_sync_can_override_folders_for_one_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = MillieDatabase(root / "millie.sqlite", root / "data")
            db.init()
            messages = {
                "INBOX": {
                    "1": build_message("inbox@example.com", "Inbox Only", "Inbox body").as_bytes(),
                },
                "Archive": {
                    "1": build_message("archive@example.com", "Archive Only", "Archive body").as_bytes(),
                },
            }
            config = ImapSourceConfig(
                id="unit-imap",
                name="Unit IMAP",
                host="imap.example.test",
                port=993,
                username="user@example.test",
                password="secret",
                use_tls=True,
                folders=["INBOX"],
                sync_limit=50,
            )

            result = sync_imap_source(db, config, lambda _: FakeImapClient(messages), folders=["Archive"])

            self.assertEqual(result.folders, ["Archive"])
            self.assertEqual(result.processed, 1)
            self.assertEqual(db.list_messages()[0]["mailbox_path"], "Archive")

    def test_imap_folder_discovery_parses_selectable_folders(self) -> None:
        config = ImapSourceConfig(
            id="unit-imap",
            name="Unit IMAP",
            host="imap.example.test",
            port=993,
            username="user@example.test",
            password="secret",
            use_tls=True,
            folders=["INBOX"],
            sync_limit=50,
        )

        client = FakeImapClient({"INBOX": {}})

        folders = discover_imap_folders(config, lambda _: client)

        names = [folder.name for folder in folders]
        self.assertEqual(client.list_calls, [('""', "*")])
        self.assertIn("INBOX", names)
        self.assertIn("Sent Items", names)
        self.assertIn("Archive/2024", names)
        noselect = next(folder for folder in folders if folder.name == "[Gmail]")
        self.assertFalse(noselect.selectable)
        sent = next(folder for folder in folders if folder.name == "Sent Items")
        self.assertEqual(sent.delimiter, "/")

    def test_imap_config_normalizes_common_gmail_host(self) -> None:
        config = config_from_dict(
            {
                "name": "Gmail",
                "host": "imap.google.com",
                "username": "user@example.test",
                "password": "secret",
            }
        )

        self.assertEqual(config.host, "imap.gmail.com")
        self.assertEqual(config.provider, "gmail")

    def test_imap_provider_presets_include_gmail_defaults(self) -> None:
        presets = {provider.id: provider for provider in list_imap_provider_presets()}

        self.assertIn("gmail", presets)
        self.assertEqual(presets["gmail"].host, "imap.gmail.com")
        self.assertEqual(presets["gmail"].default_folders, ("INBOX",))

    def test_gmail_special_folders_map_to_roles(self) -> None:
        self.assertEqual(folder_role("[Gmail]/All Mail"), "archive")
        self.assertEqual(folder_role("[Gmail]/Sent Mail"), "sent")
        self.assertEqual(folder_role("[Gmail]/Spam"), "junk")

    def test_imap_source_password_uses_secret_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = ProfileManager(
                root / "millie.settings",
                root / "profiles",
                root / "default.sqlite",
                root / "default-data",
            )
            secrets = SecretManager(manager, "local")

            source = save_imap_source(
                manager,
                {
                    "name": "Secret IMAP",
                    "host": "imap.example.test",
                    "username": "secret@example.test",
                    "password": "super-secret",
                    "folders": ["INBOX"],
                },
                secrets,
            )

            raw_sources = manager.get_profile_setting(IMAP_SOURCES_SETTING) or ""
            self.assertNotIn("super-secret", raw_sources)
            self.assertIn("auth_ref", raw_sources)

            listed = load_imap_sources(manager)
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0].password, "")
            self.assertEqual(listed[0].to_api()["secret_backend"], "local-settings")

            resolved = get_imap_source(manager, source.id, secrets)
            self.assertEqual(resolved.password, "super-secret")

            self.assertTrue(delete_imap_source(manager, source.id, secrets))
            self.assertEqual(load_imap_sources(manager), [])
            self.assertIsNone(secrets.read_secret(source.auth_ref))

    def test_legacy_imap_password_migrates_to_secret_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = ProfileManager(
                root / "millie.settings",
                root / "profiles",
                root / "default.sqlite",
                root / "default-data",
            )
            manager.set_profile_setting(
                IMAP_SOURCES_SETTING,
                json.dumps(
                    [
                        {
                            "id": "legacy-imap",
                            "name": "Legacy IMAP",
                            "host": "imap.example.test",
                            "port": 993,
                            "username": "legacy@example.test",
                            "password": "legacy-secret",
                            "use_tls": True,
                            "folders": ["INBOX"],
                            "sync_limit": 100,
                        }
                    ]
                ),
            )
            secrets = SecretManager(manager, "local")

            migrated = migrate_imap_source_secrets(manager, secrets)

            self.assertEqual(migrated, 1)
            raw_sources = manager.get_profile_setting(IMAP_SOURCES_SETTING) or ""
            self.assertNotIn("legacy-secret", raw_sources)
            self.assertIn("auth_ref", raw_sources)
            self.assertEqual(get_imap_source(manager, "legacy-imap", secrets).password, "legacy-secret")

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


def build_emlx(raw_message: bytes) -> bytes:
    return str(len(raw_message)).encode("ascii") + b"\n" + raw_message + b"\n<plist></plist>\n"


class FakeImapClient:
    def __init__(self, messages: dict[str, dict[str, bytes]]):
        self.messages = messages
        self.selected = "INBOX"
        self.list_calls: list[tuple[str, str]] = []

    def login(self, user: str, password: str) -> tuple[str, list[bytes]]:
        if user and password:
            return "OK", [b"logged in"]
        return "NO", [b"missing credentials"]

    def list(self, directory: str = '""', pattern: str = "*") -> tuple[str, list[bytes]]:
        self.list_calls.append((directory, pattern))
        return "OK", [
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\HasNoChildren \\Sent) "/" "Sent Items"',
            b'(\\HasNoChildren) "/" "Archive/2024"',
            b'(\\Noselect \\HasChildren) "/" "[Gmail]"',
        ]

    def select(self, mailbox_name: str = "INBOX", readonly: bool = False) -> tuple[str, list[bytes]]:
        del readonly
        self.selected = mailbox_name.strip('"')
        if self.selected not in self.messages:
            return "NO", [b"unknown folder"]
        return "OK", [str(len(self.messages[self.selected])).encode("ascii")]

    def response(self, code: str) -> tuple[str, list[bytes | None]]:
        if code.upper() == "UIDVALIDITY":
            return "OK", [b"999"]
        return "OK", [None]

    def uid(self, command: str, *args: object) -> tuple[str, list[object]]:
        if command.upper() == "SEARCH":
            joined = " ".join(str(item) for item in args if item is not None)
            match = re.search(r"(\d+):\*", joined)
            start = int(match.group(1)) if match else 1
            uids = [
                uid.encode("ascii")
                for uid in sorted(self.messages[self.selected], key=int)
                if int(uid) >= start
            ]
            return "OK", [b" ".join(uids)]
        if command.upper() == "FETCH":
            uid = str(args[0])
            raw = self.messages[self.selected][uid]
            return "OK", [
                (
                    (
                        f'{uid} (UID {uid} FLAGS (\\Seen \\Flagged) '
                        f'INTERNALDATE "01-Jan-2021 00:00:00 +0000" RFC822 {{{len(raw)}}}'
                    ).encode("ascii"),
                    raw,
                )
            ]
        return "BAD", [b"unsupported command"]

    def close(self) -> tuple[str, list[bytes]]:
        return "OK", [b"closed"]

    def logout(self) -> tuple[str, list[bytes]]:
        return "OK", [b"logged out"]


if __name__ == "__main__":
    unittest.main()
