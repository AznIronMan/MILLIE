from __future__ import annotations

import tempfile
import unittest
import imaplib
import mailbox
import json
import re
import socket
import threading
import zipfile
from email.message import EmailMessage
from pathlib import Path

from millie.auth import AuthManager, SESSION_COOKIE
from millie.backup import create_backup, restore_backup
from millie.config import AppConfig
from millie.database import MillieDatabase
from millie.exporters import export_messages
from millie.graph_connector import (
    GRAPH_SOURCES_SETTING,
    complete_graph_authorization,
    create_graph_authorization_request,
    delete_graph_source,
    discover_graph_folders,
    list_graph_provider_presets,
    load_graph_sources,
    probe_graph_source,
    save_graph_source,
    sync_graph_source,
)
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
from millie.pop_connector import (
    POP_SOURCES_SETTING,
    PopSourceConfig,
    config_from_dict as pop_config_from_dict,
    delete_pop_source,
    get_pop_source,
    list_pop_provider_presets,
    load_pop_sources,
    migrate_pop_source_secrets,
    probe_pop_source,
    save_pop_source,
    sync_pop_source,
)
from millie.imap_facade import ImapFacadeAuth, MillieIMAPServer, is_loopback_host, run_imap_facade
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
    def test_app_config_defaults_to_http_with_optional_tls_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = AppConfig(
                db_path=root / "millie.sqlite",
                data_dir=root / "data",
                settings_path=root / "millie.settings",
                profiles_dir=root / "profiles",
                web_dir=root / "web",
                host="0.0.0.0",
                port=22013,
                tls_cert=root / "dev.crt",
                tls_key=root / "dev.key",
            )

            resolved = config.resolved()

            self.assertEqual(resolved.host, "0.0.0.0")
            self.assertEqual(resolved.port, 22013)
            self.assertEqual(resolved.tls_cert, (root / "dev.crt").resolve())
            self.assertEqual(resolved.tls_key, (root / "dev.key").resolve())

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
            self.assertEqual(manifest["fidelity"]["strategy"], "raw_mime_first")
            self.assertEqual(manifest["fidelity"]["raw_mime_preserved_count"], 1)
            self.assertEqual(manifest["fidelity"]["output_hash_verified_count"], 1)
            self.assertEqual(manifest["items"][0]["source_id"], result.source_id)
            self.assertTrue(manifest["items"][0]["raw_mime_preserved"])
            self.assertTrue(manifest["items"][0]["output_matches_raw"])
            self.assertEqual(db.list_migrations()[0]["version"], 1)
            self.assertEqual(db.list_migrations()[-1]["version"], 3)

    def test_read_only_imap_facade_lists_selects_and_fetches_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = MillieDatabase(root / "millie.sqlite", root / "data")
            db.init()
            source = root / "sample.eml"
            source.write_bytes(SAMPLE_EML)
            import_path(db, source, "eml", "IMAP Facade Fixture")

            server = MillieIMAPServer(("127.0.0.1", 0), db)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            client = imaplib.IMAP4("127.0.0.1", server.server_address[1])
            try:
                status, _ = client.login("dev", "dev")
                self.assertEqual(status, "OK")
                status, capability = client.capability()
                self.assertEqual(status, "OK")
                capability_payload = b" ".join(capability or [])
                self.assertIn(b"IDLE", capability_payload)
                self.assertIn(b"SPECIAL-USE", capability_payload)
                status, listed = client.list()
                self.assertEqual(status, "OK")
                self.assertIn(b"Imported", b"\n".join(listed or []))
                status, selected = client.select("Imported", readonly=True)
                self.assertEqual(status, "OK")
                self.assertEqual(selected[0], b"1")
                status, searched = client.search(None, "ALL")
                self.assertEqual(status, "OK")
                self.assertEqual(searched[0], b"1")
                status, fetched = client.uid(
                    "fetch",
                    "1:*",
                    "(UID ENVELOPE BODYSTRUCTURE RFC822.SIZE BODY.PEEK[HEADER.FIELDS (Subject From)])",
                )
                self.assertEqual(status, "OK")
                metadata_payload = b"".join(
                    item[0] + item[1] if isinstance(item, tuple) else item
                    for item in fetched
                )
                self.assertIn(b"ENVELOPE", metadata_payload)
                self.assertIn(b"BODYSTRUCTURE", metadata_payload)
                self.assertIn(b"Subject: Hello from MILLIE", metadata_payload)
                status, fetched = client.uid("fetch", "1:*", "(UID RFC822.SIZE BODY.PEEK[])")
                self.assertEqual(status, "OK")
                self.assertIn(b"Hello from MILLIE", b"".join(item[1] for item in fetched if isinstance(item, tuple)))
                status, fetched = client.uid("fetch", "1", "(BODY.PEEK[TEXT]<0.20>)")
                self.assertEqual(status, "OK")
                self.assertIn(b"Hello Bob", b"".join(item[1] for item in fetched if isinstance(item, tuple)))
                status, _ = client.store("1", "+FLAGS", "\\Seen")
                self.assertEqual(status, "NO")
                self.assert_imap_facade_compat_commands(server.server_address[1])
            finally:
                try:
                    client.logout()
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=2)

    def assert_imap_facade_compat_commands(self, port: int) -> None:
        def recv_until(sock: socket.socket, marker: bytes) -> bytes:
            data = b""
            while marker not in data:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
            return data

        with socket.create_connection(("127.0.0.1", port), timeout=2) as sock:
            sock.settimeout(2)
            self.assertIn(b"OK MILLIE", recv_until(sock, b"\r\n"))
            sock.sendall(b'a1 LOGIN "dev" "dev"\r\n')
            self.assertIn(b"a1 OK", recv_until(sock, b"a1 OK"))
            sock.sendall(b'a2 ID ("name" "unit-test")\r\n')
            self.assertIn(b'* ID ("name" "MILLIE"', recv_until(sock, b"a2 OK"))
            sock.sendall(b"a3 ENABLE UTF8=ACCEPT\r\n")
            self.assertIn(b"* ENABLED UTF8=ACCEPT", recv_until(sock, b"a3 OK"))
            sock.sendall(b'a4 XLIST "" "*"\r\n')
            self.assertIn(b"* XLIST", recv_until(sock, b"a4 OK"))
            sock.sendall(b'a5 SELECT "Imported"\r\n')
            self.assertIn(b"a5 OK", recv_until(sock, b"a5 OK"))
            sock.sendall(b"a6 CHECK\r\n")
            self.assertIn(b"a6 OK", recv_until(sock, b"a6 OK"))
            sock.sendall(b"a7 UNSELECT\r\n")
            self.assertIn(b"a7 OK", recv_until(sock, b"a7 OK"))
            sock.sendall(b"a8 IDLE\r\n")
            self.assertIn(b"+ idling", recv_until(sock, b"+ idling"))
            sock.sendall(b"DONE\r\n")
            self.assertIn(b"a8 OK", recv_until(sock, b"a8 OK"))
            sock.sendall(b"a9 LOGOUT\r\n")
            self.assertIn(b"a9 OK", recv_until(sock, b"a9 OK"))

    def test_imap_facade_exact_auth_and_non_loopback_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = MillieDatabase(root / "millie.sqlite", root / "data")
            db.init()
            source = root / "sample.eml"
            source.write_bytes(SAMPLE_EML)
            import_path(db, source, "eml", "IMAP Auth Fixture")

            self.assertTrue(is_loopback_host("127.0.0.1"))
            self.assertFalse(is_loopback_host("0.0.0.0"))
            with self.assertRaisesRegex(ValueError, "requires both username and password"):
                run_imap_facade(db, "0.0.0.0", 0, allow_dev_login=False)

            server = MillieIMAPServer(
                ("127.0.0.1", 0),
                db,
                ImapFacadeAuth(username="archive", password="secret", allow_dev_login=False),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            client = imaplib.IMAP4("127.0.0.1", server.server_address[1])
            try:
                with self.assertRaises(imaplib.IMAP4.error):
                    client.login("archive", "wrong")
                status, _ = client.login("archive", "secret")
                self.assertEqual(status, "OK")
                status, selected = client.select("Imported", readonly=True)
                self.assertEqual(status, "OK")
                self.assertEqual(selected[0], b"1")
            finally:
                try:
                    client.logout()
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=2)

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

    def test_backup_packages_active_profile_and_redacts_local_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = ProfileManager(
                root / "millie.settings",
                root / "profiles",
                root / "default.sqlite",
                root / "default-data",
            )
            db = manager.active_database()
            source = root / "sample.eml"
            source.write_bytes(SAMPLE_EML)
            import_path(db, source, "eml", "Backup Fixture")
            manager.set_app_setting("auth.session_secret", "super-secret-session")
            manager.set_app_setting("auth.admin.password_hash", "super-secret-hash")
            manager.set_profile_setting("secrets.local.v1", json.dumps({"token": "super-secret-token"}))

            result = create_backup(manager, root / "backups")

            self.assertTrue(result.output_path.exists())
            self.assertFalse(result.include_secrets)
            with zipfile.ZipFile(result.output_path) as archive:
                names = set(archive.namelist())
                self.assertIn("manifest.json", names)
                self.assertIn("profile/millie.sqlite", names)
                self.assertIn("settings/millie.settings", names)
                self.assertIn("settings/profile.settings", names)
                manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
                self.assertEqual(manifest["profile"]["id"], "default")
                self.assertFalse(manifest["include_secrets"])
                payload = b"".join(archive.read(name) for name in names)

            self.assertIn(b"Hello from MILLIE", payload)
            self.assertNotIn(b"super-secret-session", payload)
            self.assertNotIn(b"super-secret-hash", payload)
            self.assertNotIn(b"super-secret-token", payload)

            restored = restore_backup(
                manager,
                result.output_path,
                profile_name="Restored Backup",
                profile_id="restored-backup",
            )

            self.assertEqual(restored.profile_id, "restored-backup")
            self.assertTrue(restored.switched)
            self.assertEqual(manager.active_profile_id, "restored-backup")
            self.assertEqual(len(manager.active_database().list_messages(query="Hello")), 1)

    def test_restore_rejects_backup_with_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = ProfileManager(
                root / "millie.settings",
                root / "profiles",
                root / "default.sqlite",
                root / "default-data",
            )
            db = manager.active_database()
            source = root / "sample.eml"
            source.write_bytes(SAMPLE_EML)
            import_path(db, source, "eml", "Backup Fixture")
            result = create_backup(manager, root / "backups")

            tampered = root / "tampered.zip"
            with zipfile.ZipFile(result.output_path) as source_zip, zipfile.ZipFile(tampered, "w") as target_zip:
                for item in source_zip.infolist():
                    data = source_zip.read(item.filename)
                    if item.filename == "profile/millie.sqlite":
                        data += b"tamper"
                    target_zip.writestr(item, data)

            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                restore_backup(manager, tampered, profile_id="tampered")

    def test_restore_rejects_unlisted_backup_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = ProfileManager(
                root / "millie.settings",
                root / "profiles",
                root / "default.sqlite",
                root / "default-data",
            )
            db = manager.active_database()
            source = root / "sample.eml"
            source.write_bytes(SAMPLE_EML)
            import_path(db, source, "eml", "Backup Fixture")
            result = create_backup(manager, root / "backups")

            extra = root / "extra.zip"
            with zipfile.ZipFile(result.output_path) as source_zip, zipfile.ZipFile(extra, "w") as target_zip:
                for item in source_zip.infolist():
                    target_zip.writestr(item, source_zip.read(item.filename))
                target_zip.writestr("profile/data/unlisted.bin", b"not in manifest")

            with self.assertRaisesRegex(ValueError, "unlisted file"):
                restore_backup(manager, extra, profile_id="extra")

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
            manifest = json.loads(export_result.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["fidelity"]["strategy"], "raw_mime_first")
            self.assertEqual(manifest["fidelity"]["raw_mime_preserved_count"], 2)
            self.assertEqual(manifest["fidelity"]["containerized_count"], 2)
            self.assertTrue(all(item["containerized"] for item in manifest["items"]))

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
            self.assertEqual(first.sync_limit, 50)
            self.assertEqual(len(db.list_messages()), 2)
            self.assertEqual(db.list_migrations()[-1]["version"], 3)
            detail = db.get_message(int(db.list_messages(query="IMAP One")[0]["id"]))
            self.assertIsNotNone(detail)
            self.assertEqual(detail["internal_date"], "2021-01-01T00:00:00+00:00")
            self.assertEqual(json.loads(detail["mailboxes"][0]["flags_json"]), ["\\Seen", "\\Flagged"])

            state = db.get_source_sync_state(first.source_id, "folder:INBOX")
            self.assertEqual(state["uidvalidity"], "999")
            self.assertEqual(state["last_uid"], 2)
            states = db.list_source_sync_states()
            self.assertEqual(states[0]["source_name"], "Unit IMAP")
            self.assertEqual(states[0]["scope"], "folder:INBOX")
            self.assertEqual(states[0]["state"]["last_uid"], 2)
            self.assertEqual(states[0]["source_config_id"], "unit-imap")
            self.assertEqual(states[0]["sync_action"], "retry")
            self.assertFalse(states[0]["is_partial"])

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

    def test_imap_sync_keeps_failed_uid_recoverable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = MillieDatabase(root / "millie.sqlite", root / "data")
            db.init()
            messages = {
                "INBOX": {
                    "1": build_message("imap-one@example.com", "IMAP One", "First IMAP body").as_bytes(),
                    "2": build_message("imap-two@example.com", "IMAP Two", "Second IMAP body").as_bytes(),
                    "3": build_message("imap-three@example.com", "IMAP Three", "Third IMAP body").as_bytes(),
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

            first = sync_imap_source(db, config, lambda _: FakeImapClient(messages, {"2"}))

            self.assertEqual(first.processed, 2)
            self.assertEqual(first.imported, 2)
            self.assertEqual(first.errors, 1)
            state = db.get_source_sync_state(first.source_id, "folder:INBOX")
            self.assertEqual(state["last_uid"], 1)
            self.assertEqual(state["last_attempted_uid"], 3)
            self.assertEqual(state["last_failed_uids"], ["2"])
            self.assertEqual(state["last_status"], "partial")

            second = sync_imap_source(db, config, lambda _: FakeImapClient(messages))

            self.assertEqual(second.processed, 2)
            self.assertEqual(second.imported, 1)
            self.assertEqual(second.duplicates, 1)
            self.assertEqual(second.errors, 0)
            self.assertEqual(db.get_source_sync_state(first.source_id, "folder:INBOX")["last_uid"], 3)
            self.assertEqual(len(db.list_messages()), 3)

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

            result = sync_imap_source(db, config, lambda _: FakeImapClient(messages), folders=["Archive"], sync_limit=1)

            self.assertEqual(result.folders, ["Archive"])
            self.assertEqual(result.sync_limit, 1)
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
        self.assertEqual(presets["outlook"].host, "outlook.office365.com")
        self.assertEqual(presets["yahoo"].host, "imap.mail.yahoo.com")
        self.assertEqual(presets["icloud"].host, "imap.mail.me.com")
        self.assertEqual(presets["aol"].host, "imap.aol.com")
        self.assertEqual(presets["fastmail"].host, "imap.fastmail.com")
        self.assertEqual(presets["zoho"].host, "imap.zoho.com")

    def test_imap_config_detects_common_provider_hosts(self) -> None:
        cases = {
            "outlook.office365.com": "outlook",
            "imap.mail.yahoo.com": "yahoo",
            "imap.mail.me.com": "icloud",
            "imap.aol.com": "aol",
            "imap.fastmail.com": "fastmail",
            "imap.zoho.com": "zoho",
        }

        for host, provider in cases.items():
            with self.subTest(host=host):
                config = config_from_dict(
                    {
                        "name": provider,
                        "host": host,
                        "username": "user@example.test",
                        "password": "secret",
                    }
                )
                self.assertEqual(config.provider, provider)

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

    def test_pop_source_password_uses_secret_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = ProfileManager(
                root / "millie.settings",
                root / "profiles",
                root / "default.sqlite",
                root / "default-data",
            )
            secrets = SecretManager(manager, "local")

            source = save_pop_source(
                manager,
                {
                    "name": "Secret POP",
                    "host": "pop.example.test",
                    "username": "secret@example.test",
                    "password": "super-secret",
                },
                secrets,
            )

            raw_sources = manager.get_profile_setting(POP_SOURCES_SETTING) or ""
            self.assertNotIn("super-secret", raw_sources)
            self.assertIn("auth_ref", raw_sources)
            self.assertEqual(load_pop_sources(manager)[0].password, "")
            self.assertEqual(get_pop_source(manager, source.id, secrets).password, "super-secret")
            self.assertTrue(delete_pop_source(manager, source.id, secrets))
            self.assertIsNone(secrets.read_secret(source.auth_ref))

    def test_pop_provider_presets_include_gmail_defaults(self) -> None:
        presets = {provider.id: provider for provider in list_pop_provider_presets()}

        self.assertIn("gmail", presets)
        self.assertEqual(presets["gmail"].host, "pop.gmail.com")
        self.assertEqual(presets["gmail"].port, 995)
        self.assertEqual(presets["outlook"].host, "outlook.office365.com")
        self.assertEqual(presets["yahoo"].host, "pop.mail.yahoo.com")
        self.assertNotIn("icloud", presets)
        self.assertEqual(presets["aol"].host, "pop.aol.com")
        self.assertEqual(presets["fastmail"].host, "pop.fastmail.com")
        self.assertEqual(presets["zoho"].host, "pop.zoho.com")

    def test_pop_config_detects_common_provider_hosts(self) -> None:
        cases = {
            "outlook.office365.com": "outlook",
            "pop.mail.yahoo.com": "yahoo",
            "pop.aol.com": "aol",
            "pop.fastmail.com": "fastmail",
            "pop.zoho.com": "zoho",
        }

        for host, provider in cases.items():
            with self.subTest(host=host):
                config = pop_config_from_dict(
                    {
                        "name": provider,
                        "host": host,
                        "username": "user@example.test",
                        "password": "secret",
                    }
                )
                self.assertEqual(config.provider, provider)

    def test_legacy_pop_password_migrates_to_secret_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = ProfileManager(
                root / "millie.settings",
                root / "profiles",
                root / "default.sqlite",
                root / "default-data",
            )
            manager.set_profile_setting(
                POP_SOURCES_SETTING,
                json.dumps(
                    [
                        {
                            "id": "legacy-pop",
                            "name": "Legacy POP",
                            "host": "pop.example.test",
                            "port": 995,
                            "username": "legacy@example.test",
                            "password": "legacy-secret",
                            "use_ssl": True,
                            "sync_limit": 100,
                        }
                    ]
                ),
            )
            secrets = SecretManager(manager, "local")

            migrated = migrate_pop_source_secrets(manager, secrets)

            self.assertEqual(migrated, 1)
            raw_sources = manager.get_profile_setting(POP_SOURCES_SETTING) or ""
            self.assertNotIn("legacy-secret", raw_sources)
            self.assertIn("auth_ref", raw_sources)
            self.assertEqual(get_pop_source(manager, "legacy-pop", secrets).password, "legacy-secret")

    def test_pop_probe_uses_no_retr_or_dele(self) -> None:
        source = pop_fixture_source()
        client = FakePopClient({"uid-1": build_message("pop@example.com", "POP One", "POP body").as_bytes()})

        result = probe_pop_source(source, lambda _: client)

        self.assertEqual(result.message_count, 1)
        self.assertTrue(result.uidl_available)
        self.assertNotIn("RETR", client.commands)
        self.assertNotIn("DELE", client.commands)

    def test_pop_sync_imports_incrementally_without_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = MillieDatabase(root / "millie.sqlite", root / "data")
            db.init()
            messages = {
                "uid-1": build_message("pop-one@example.com", "POP One", "First POP body").as_bytes(),
                "uid-2": build_message("pop-two@example.com", "POP Two", "Second POP body").as_bytes(),
            }
            source = pop_fixture_source(sync_limit=10)
            client = FakePopClient(messages)

            first = sync_pop_source(db, source, lambda _: client)
            self.assertEqual(first.processed, 2)
            self.assertEqual(first.imported, 2)
            self.assertEqual(first.duplicates, 0)
            self.assertEqual(first.errors, 0)
            self.assertEqual(first.sync_limit, 10)
            self.assertNotIn("DELE", client.commands)
            self.assertEqual(len(db.list_messages()), 2)
            state = db.get_source_sync_state(first.source_id, "maildrop")
            self.assertEqual(state["delete_policy"], "never")
            self.assertEqual(state["seen_uidls"], ["uid-1", "uid-2"])

            messages["uid-3"] = build_message("pop-three@example.com", "POP Three", "Third POP body").as_bytes()
            second_client = FakePopClient(messages)
            second = sync_pop_source(db, source, lambda _: second_client)
            self.assertEqual(second.processed, 1)
            self.assertEqual(second.imported, 1)
            self.assertEqual(second.errors, 0)
            self.assertNotIn("DELE", second_client.commands)
            self.assertEqual(len(db.list_messages()), 3)

    def test_pop_sync_keeps_failed_uidl_recoverable_and_respects_attempt_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = MillieDatabase(root / "millie.sqlite", root / "data")
            db.init()
            messages = {
                "uid-1": build_message("pop-one@example.com", "POP One", "First POP body").as_bytes(),
                "uid-2": build_message("pop-two@example.com", "POP Two", "Second POP body").as_bytes(),
                "uid-3": build_message("pop-three@example.com", "POP Three", "Third POP body").as_bytes(),
            }
            source = pop_fixture_source(sync_limit=2)

            first = sync_pop_source(db, source, lambda _: FakePopClient(messages, {"uid-1"}))

            self.assertEqual(first.processed, 1)
            self.assertEqual(first.imported, 1)
            self.assertEqual(first.errors, 1)
            self.assertEqual(first.sync_limit, 2)
            state = db.get_source_sync_state(first.source_id, "maildrop")
            self.assertEqual(state["seen_uidls"], ["uid-2"])
            self.assertEqual(state["last_attempted_uidls"], ["uid-1", "uid-2"])
            self.assertEqual(state["last_failed_uidls"], ["uid-1"])
            self.assertEqual(state["last_status"], "partial")

            second = sync_pop_source(db, source, lambda _: FakePopClient(messages))

            self.assertEqual(second.processed, 2)
            self.assertEqual(second.imported, 2)
            self.assertEqual(second.errors, 0)
            self.assertEqual(db.get_source_sync_state(first.source_id, "maildrop")["seen_uidls"], ["uid-1", "uid-2", "uid-3"])

    def test_graph_source_saves_config_without_token_secret(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = ProfileManager(
                root / "millie.settings",
                root / "profiles",
                root / "default.sqlite",
                root / "default-data",
            )

            source = save_graph_source(
                manager,
                {
                    "name": "Graph Mail",
                    "client_id": "client-id-123",
                    "tenant_id": "common",
                    "redirect_uri": "http://localhost:22013/api/v1/graph/oauth/callback",
                    "scopes": ["openid", "offline_access", "User.Read", "Mail.Read", "Mail.Read"],
                },
            )

            self.assertEqual(source.scopes, ["openid", "offline_access", "User.Read", "Mail.Read"])
            raw_sources = manager.get_profile_setting(GRAPH_SOURCES_SETTING) or ""
            self.assertIn("client-id-123", raw_sources)
            self.assertNotIn("refresh_token", raw_sources)
            self.assertFalse(load_graph_sources(manager)[0].to_api()["token_configured"])

    def test_graph_auth_request_uses_pkce_and_secret_pending_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = ProfileManager(
                root / "millie.settings",
                root / "profiles",
                root / "default.sqlite",
                root / "default-data",
            )
            secrets = SecretManager(manager, "local")
            source = save_graph_source(
                manager,
                {
                    "name": "Graph Mail",
                    "client_id": "client-id-123",
                    "tenant_id": "common",
                },
            )

            auth_request = create_graph_authorization_request(manager, source.id, secrets)

            self.assertIn("https://login.microsoftonline.com/common/oauth2/v2.0/authorize", auth_request.authorization_url)
            self.assertIn("response_type=code", auth_request.authorization_url)
            self.assertIn("code_challenge_method=S256", auth_request.authorization_url)
            self.assertIn("Mail.Read", auth_request.scopes)
            updated = load_graph_sources(manager)[0]
            self.assertTrue(updated.pending_auth_ref)
            pending_payload = secrets.read_secret(updated.pending_auth_ref)
            self.assertIsNotNone(pending_payload)
            self.assertIn("code_verifier", pending_payload or "")
            raw_sources = manager.get_profile_setting(GRAPH_SOURCES_SETTING) or ""
            self.assertNotIn("code_verifier", raw_sources)
            self.assertTrue(delete_graph_source(manager, source.id, secrets))
            self.assertEqual(load_graph_sources(manager), [])
            self.assertIsNone(secrets.read_secret(updated.pending_auth_ref))

    def test_graph_auth_completion_stores_tokens_in_secret_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = ProfileManager(
                root / "millie.settings",
                root / "profiles",
                root / "default.sqlite",
                root / "default-data",
            )
            secrets = SecretManager(manager, "local")
            source = save_graph_source(
                manager,
                {
                    "name": "Graph Mail",
                    "client_id": "client-id-123",
                    "tenant_id": "tenant-123",
                    "redirect_uri": "http://localhost",
                },
            )
            auth_request = create_graph_authorization_request(
                manager,
                source.id,
                secrets,
                redirect_uri="http://localhost:22013",
            )
            pending_ref = load_graph_sources(manager)[0].pending_auth_ref
            client = FakeGraphHttpClient(
                token_response={
                    "access_token": "access-token-value",
                    "refresh_token": "refresh-token-value",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                    "scope": "openid offline_access User.Read Mail.Read",
                }
            )

            completion = complete_graph_authorization(
                manager,
                "authorization-code",
                auth_request.state,
                secrets,
                client,
            )

            self.assertTrue(completion.source.token_ref)
            self.assertFalse(completion.source.pending_auth_ref)
            self.assertEqual(client.post_forms[0][0], "https://login.microsoftonline.com/tenant-123/oauth2/v2.0/token")
            self.assertEqual(client.post_forms[0][1]["grant_type"], "authorization_code")
            self.assertEqual(client.post_forms[0][1]["code"], "authorization-code")
            self.assertEqual(client.post_forms[0][1]["redirect_uri"], "http://localhost:22013")
            self.assertTrue(client.post_forms[0][1]["code_verifier"])
            raw_sources = manager.get_profile_setting(GRAPH_SOURCES_SETTING) or ""
            self.assertIn("token_ref", raw_sources)
            self.assertNotIn("access-token-value", raw_sources)
            self.assertNotIn("refresh-token-value", raw_sources)
            self.assertIsNone(secrets.read_secret(pending_ref))
            token_payload = secrets.read_secret(completion.source.token_ref)
            self.assertIn("access-token-value", token_payload or "")
            self.assertIn("refresh-token-value", token_payload or "")

    def test_graph_probe_refreshes_expired_token_and_reads_folder_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = ProfileManager(
                root / "millie.settings",
                root / "profiles",
                root / "default.sqlite",
                root / "default-data",
            )
            secrets = SecretManager(manager, "local")
            source = save_graph_source(
                manager,
                {
                    "name": "Graph Mail",
                    "client_id": "client-id-123",
                    "tenant_id": "tenant-123",
                    "redirect_uri": "http://localhost",
                },
            )
            token_ref = secrets.store_graph_token_payload(
                source.id,
                json.dumps(
                    {
                        "access_token": "expired-access-token",
                        "refresh_token": "refresh-token-value",
                        "expires_at": "2000-01-01T00:00:00+00:00",
                        "token_type": "Bearer",
                        "scope": "openid offline_access User.Read Mail.Read",
                    }
                ),
            )
            save_graph_source(manager, {**source.to_api(), "token_ref": token_ref})
            client = FakeGraphHttpClient(
                token_response={
                    "access_token": "new-access-token",
                    "refresh_token": "new-refresh-token",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                    "scope": "openid offline_access User.Read Mail.Read",
                },
                get_responses=[
                    {
                        "displayName": "Graph User",
                        "userPrincipalName": "user@example.com",
                        "mail": "user@example.com",
                    },
                    {
                        "value": [
                            {
                                "id": "inbox-id",
                                "displayName": "Inbox",
                                "totalItemCount": 12,
                                "unreadItemCount": 3,
                                "childFolderCount": 0,
                            }
                        ]
                    },
                ],
            )

            result = probe_graph_source(manager, source.id, secrets, client)

            self.assertTrue(result.token_refreshed)
            self.assertEqual(result.user_principal_name, "user@example.com")
            self.assertEqual(result.folder_count, 1)
            self.assertEqual(result.folders[0].display_name, "Inbox")
            self.assertEqual(client.post_forms[0][1]["grant_type"], "refresh_token")
            self.assertEqual(client.get_requests[0][1], "new-access-token")
            raw_sources = manager.get_profile_setting(GRAPH_SOURCES_SETTING) or ""
            self.assertNotIn("new-access-token", raw_sources)
            self.assertNotIn("new-refresh-token", raw_sources)

    def test_graph_folder_discovery_recurses_and_source_saves_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = ProfileManager(
                root / "millie.settings",
                root / "profiles",
                root / "default.sqlite",
                root / "default-data",
            )
            secrets = SecretManager(manager, "local")
            source = save_graph_source(
                manager,
                {
                    "name": "Graph Mail",
                    "client_id": "client-id-123",
                    "tenant_id": "tenant-123",
                    "redirect_uri": "http://localhost",
                },
            )
            token_ref = secrets.store_graph_token_payload(
                source.id,
                json.dumps({"access_token": "access-token-value", "expires_at": "2999-01-01T00:00:00+00:00"}),
            )
            source = save_graph_source(manager, {**source.to_api(), "token_ref": token_ref})
            client = FakeGraphHttpClient(
                token_response={},
                get_responses=[
                    {
                        "value": [
                            {
                                "id": "inbox-id",
                                "displayName": "Inbox",
                                "parentFolderId": "root",
                                "totalItemCount": 2,
                                "unreadItemCount": 1,
                                "childFolderCount": 1,
                            }
                        ]
                    },
                    {
                        "value": [
                            {
                                "id": "child-id",
                                "displayName": "Project",
                                "parentFolderId": "inbox-id",
                                "totalItemCount": 1,
                                "unreadItemCount": 0,
                                "childFolderCount": 0,
                            }
                        ]
                    },
                ],
            )

            folders = discover_graph_folders(manager, source.id, secrets, client)
            updated = save_graph_source(
                manager,
                {
                    **source.to_api(),
                    "folders": [folders[1].to_selection().to_api()],
                },
            )

            self.assertEqual([folder.path for folder in folders], ["Inbox", "Inbox/Project"])
            self.assertEqual(updated.folders[0].id, "child-id")
            self.assertEqual(updated.folders[0].path, "Inbox/Project")
            raw_sources = manager.get_profile_setting(GRAPH_SOURCES_SETTING) or ""
            self.assertIn("Inbox/Project", raw_sources)
            self.assertNotIn("access-token-value", raw_sources)

    def test_graph_sync_imports_mime_without_message_content_in_source_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = ProfileManager(
                root / "millie.settings",
                root / "profiles",
                root / "default.sqlite",
                root / "default-data",
            )
            db = MillieDatabase(root / "millie.sqlite", root / "data")
            db.init()
            secrets = SecretManager(manager, "local")
            source = save_graph_source(
                manager,
                {
                    "name": "Graph Mail",
                    "client_id": "client-id-123",
                    "tenant_id": "tenant-123",
                    "redirect_uri": "http://localhost",
                    "folders": [{"id": "inbox-id", "display_name": "Inbox", "path": "Inbox"}],
                },
            )
            token_ref = secrets.store_graph_token_payload(
                source.id,
                json.dumps({"access_token": "access-token-value", "expires_at": "2999-01-01T00:00:00+00:00"}),
            )
            save_graph_source(manager, {**source.to_api(), "token_ref": token_ref})
            raw_message = build_message("graph@example.com", "Graph One", "Graph body").as_bytes()
            client = FakeGraphHttpClient(
                token_response={},
                get_responses=[
                    {
                        "@odata.deltaLink": "https://graph.example/delta-1",
                        "value": [
                            {
                                "id": "message-id-1",
                                "internetMessageId": "<graph-one@example.com>",
                                "isRead": True,
                                "categories": ["Blue"],
                            }
                        ]
                    }
                ],
                byte_responses=[raw_message],
            )

            result = sync_graph_source(db, manager, source.id, secrets, client, sync_limit=10)
            second = sync_graph_source(
                db,
                manager,
                source.id,
                secrets,
                FakeGraphHttpClient(
                    token_response={},
                    get_responses=[
                        {
                            "@odata.deltaLink": "https://graph.example/delta-2",
                            "value": [{"id": "message-id-1", "@removed": {"reason": "deleted"}}],
                        }
                    ],
                ),
                sync_limit=10,
            )

            self.assertEqual(result.processed, 1)
            self.assertEqual(result.imported, 1)
            self.assertEqual(result.errors, 0)
            self.assertEqual(result.sync_limit, 10)
            self.assertEqual(second.processed, 0)
            self.assertEqual(second.removed, 1)
            self.assertEqual(len(db.list_messages()), 1)
            state = db.get_source_sync_state(result.source_id, "folder:inbox-id")
            self.assertEqual(state["delta_link"], "https://graph.example/delta-2")
            self.assertEqual(state["removed_message_ids"], ["message-id-1"])
            self.assertEqual(state["sync_mode"], "delta")
            listed_state = db.list_source_sync_states()[0]
            self.assertNotIn("https://graph.example/delta-2", listed_state["state_json"])
            self.assertTrue(listed_state["state"]["delta_link_configured"])
            self.assertEqual(client.get_bytes_requests[0][1], "access-token-value")
            export_result = export_messages(db, root / "exports", "eml")
            self.assertEqual(export_result.exported, 1)
            self.assertEqual(len(list((root / "exports").rglob("*.eml"))), 1)
            raw_sources = manager.get_profile_setting(GRAPH_SOURCES_SETTING) or ""
            self.assertNotIn("Graph body", raw_sources)
            self.assertNotIn("access-token-value", raw_sources)

    def test_graph_sync_preserves_delta_when_mime_fetch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = ProfileManager(
                root / "millie.settings",
                root / "profiles",
                root / "default.sqlite",
                root / "default-data",
            )
            db = MillieDatabase(root / "millie.sqlite", root / "data")
            db.init()
            secrets = SecretManager(manager, "local")
            source = save_graph_source(
                manager,
                {
                    "name": "Graph Mail",
                    "client_id": "client-id-123",
                    "tenant_id": "tenant-123",
                    "redirect_uri": "http://localhost",
                    "folders": [{"id": "inbox-id", "display_name": "Inbox", "path": "Inbox"}],
                },
            )
            token_ref = secrets.store_graph_token_payload(
                source.id,
                json.dumps({"access_token": "access-token-value", "expires_at": "2999-01-01T00:00:00+00:00"}),
            )
            save_graph_source(manager, {**source.to_api(), "token_ref": token_ref})
            first = sync_graph_source(
                db,
                manager,
                source.id,
                secrets,
                FakeGraphHttpClient(
                    token_response={},
                    get_responses=[
                        {
                            "@odata.deltaLink": "https://graph.example/delta-1",
                            "value": [{"id": "message-id-1", "isRead": True}],
                        }
                    ],
                    byte_responses=[build_message("graph@example.com", "Graph One", "Graph body").as_bytes()],
                ),
                sync_limit=10,
            )

            second = sync_graph_source(
                db,
                manager,
                source.id,
                secrets,
                FailingGraphHttpClient(
                    token_response={},
                    get_responses=[
                        {
                            "@odata.deltaLink": "https://graph.example/delta-2",
                            "value": [{"id": "message-id-2", "isRead": False}],
                        }
                    ],
                ),
                sync_limit=10,
            )

            self.assertEqual(first.errors, 0)
            self.assertEqual(second.processed, 0)
            self.assertEqual(second.errors, 1)
            state = db.get_source_sync_state(first.source_id, "folder:inbox-id")
            self.assertEqual(state["delta_link"], "https://graph.example/delta-1")
            self.assertEqual(state["last_failed_message_ids"], ["message-id-2"])
            self.assertEqual(state["last_status"], "partial")
            self.assertEqual(state["last_status_reason"], "errors")

    def test_graph_sync_does_not_store_next_link_when_limit_splits_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = ProfileManager(
                root / "millie.settings",
                root / "profiles",
                root / "default.sqlite",
                root / "default-data",
            )
            db = MillieDatabase(root / "millie.sqlite", root / "data")
            db.init()
            secrets = SecretManager(manager, "local")
            source = save_graph_source(
                manager,
                {
                    "name": "Graph Mail",
                    "client_id": "client-id-123",
                    "tenant_id": "tenant-123",
                    "redirect_uri": "http://localhost",
                    "folders": [{"id": "inbox-id", "display_name": "Inbox", "path": "Inbox"}],
                },
            )
            token_ref = secrets.store_graph_token_payload(
                source.id,
                json.dumps({"access_token": "access-token-value", "expires_at": "2999-01-01T00:00:00+00:00"}),
            )
            save_graph_source(manager, {**source.to_api(), "token_ref": token_ref})

            result = sync_graph_source(
                db,
                manager,
                source.id,
                secrets,
                FakeGraphHttpClient(
                    token_response={},
                    get_responses=[
                        {
                            "@odata.nextLink": "https://graph.example/next-page",
                            "value": [
                                {"id": "message-id-1", "isRead": True},
                                {"id": "message-id-2", "isRead": False},
                            ],
                        }
                    ],
                    byte_responses=[build_message("graph@example.com", "Graph One", "Graph body").as_bytes()],
                ),
                sync_limit=1,
            )

            self.assertEqual(result.processed, 1)
            self.assertEqual(result.errors, 0)
            state = db.get_source_sync_state(result.source_id, "folder:inbox-id")
            self.assertIsNone(state["next_link"])
            self.assertEqual(state["last_status"], "partial")
            self.assertEqual(state["last_status_reason"], "limit_mid_page")

    def test_graph_provider_presets_include_read_only_scopes(self) -> None:
        presets = {provider.id: provider for provider in list_graph_provider_presets()}

        self.assertIn("microsoft-graph", presets)
        self.assertIn("Mail.Read", presets["microsoft-graph"].default_scopes)
        self.assertIn("offline_access", presets["microsoft-graph"].default_scopes)

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


class FakeGraphHttpClient:
    def __init__(
        self,
        token_response: dict[str, object],
        get_responses: list[dict[str, object]] | None = None,
        byte_responses: list[bytes] | None = None,
    ):
        self.token_response = token_response
        self.get_responses = list(get_responses or [])
        self.byte_responses = list(byte_responses or [])
        self.post_forms: list[tuple[str, dict[str, str]]] = []
        self.get_requests: list[tuple[str, str]] = []
        self.get_bytes_requests: list[tuple[str, str]] = []

    def post_form(self, url: str, data: dict[str, str]) -> dict[str, object]:
        self.post_forms.append((url, dict(data)))
        return dict(self.token_response)

    def get_json(self, url: str, access_token: str) -> dict[str, object]:
        self.get_requests.append((url, access_token))
        if not self.get_responses:
            return {}
        return self.get_responses.pop(0)

    def get_bytes(self, url: str, access_token: str) -> bytes:
        self.get_bytes_requests.append((url, access_token))
        if not self.byte_responses:
            return b""
        return self.byte_responses.pop(0)


class FailingGraphHttpClient(FakeGraphHttpClient):
    def get_bytes(self, url: str, access_token: str) -> bytes:
        self.get_bytes_requests.append((url, access_token))
        raise RuntimeError("planned Graph MIME fetch failure")


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


def pop_fixture_source(sync_limit: int = 100) -> PopSourceConfig:
    return PopSourceConfig(
        id="unit-pop",
        name="Unit POP",
        host="pop.example.test",
        port=995,
        username="user@example.test",
        password="secret",
        use_ssl=True,
        sync_limit=sync_limit,
    )


class FakeImapClient:
    def __init__(self, messages: dict[str, dict[str, bytes]], fetch_errors: set[str] | None = None):
        self.messages = messages
        self.fetch_errors = fetch_errors or set()
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
            if uid in self.fetch_errors:
                raise RuntimeError(f"planned fetch failure for UID {uid}")
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


class FakePopClient:
    def __init__(self, messages: dict[str, bytes], retr_errors: set[str] | None = None):
        self.messages = messages
        self.retr_errors = retr_errors or set()
        self.commands: list[str] = []

    def user(self, user: str) -> bytes:
        self.commands.append("USER")
        if not user:
            raise RuntimeError("missing user")
        return b"+OK"

    def pass_(self, password: str) -> bytes:
        self.commands.append("PASS")
        if not password:
            raise RuntimeError("missing password")
        return b"+OK"

    def stat(self) -> tuple[int, int]:
        self.commands.append("STAT")
        return len(self.messages), sum(len(message) for message in self.messages.values())

    def uidl(self, which: int | None = None) -> tuple[bytes, list[bytes], int] | bytes:
        self.commands.append("UIDL")
        items = list(self.messages)
        if which is not None:
            return f"{which} {items[which - 1]}".encode("ascii")
        lines = [f"{index} {uidl}".encode("ascii") for index, uidl in enumerate(items, start=1)]
        return b"+OK", lines, sum(len(line) for line in lines)

    def retr(self, which: int) -> tuple[bytes, list[bytes], int]:
        self.commands.append("RETR")
        uidl = list(self.messages)[which - 1]
        if uidl in self.retr_errors:
            raise RuntimeError(f"planned retrieve failure for UIDL {uidl}")
        raw = self.messages[uidl]
        lines = raw.splitlines()
        return b"+OK", lines, len(raw)

    def dele(self, which: int) -> bytes:
        self.commands.append("DELE")
        raise AssertionError(f"POP delete must not be called for message {which}")

    def capa(self) -> dict[bytes, list[bytes]]:
        self.commands.append("CAPA")
        return {b"UIDL": [], b"USER": []}

    def quit(self) -> bytes:
        self.commands.append("QUIT")
        return b"+OK"

    def close(self) -> None:
        self.commands.append("CLOSE")


if __name__ == "__main__":
    unittest.main()
