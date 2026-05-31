from __future__ import annotations

import unittest

from millie.service.auth import (
    MillieIdentity,
    build_identity_sql,
    default_service_login,
    hash_password,
    identity_from_settings,
    login_address_candidates,
    normalize_login_address,
    verify_password,
)
from millie.service.mailbox import default_mailbox_folders


class ServiceFacadeTest(unittest.TestCase):
    def test_password_hash_round_trip(self) -> None:
        encoded = hash_password("correct horse battery staple")

        self.assertTrue(verify_password("correct horse battery staple", encoded))
        self.assertFalse(verify_password("wrong password", encoded))
        self.assertNotIn("correct horse battery staple", encoded)

    def test_identity_sql_creates_default_mailbox_folders(self) -> None:
        identity = MillieIdentity("Geon@MILLIE", display_name="Geon")
        password_hash = hash_password("temporary secret")
        sql = build_identity_sql(identity, password_hash=password_hash)

        self.assertEqual(normalize_login_address("Geon@MILLIE"), "geon@millie")
        self.assertIn("INSERT INTO millie_identities", sql)
        self.assertIn("INSERT INTO millie_mailboxes", sql)
        self.assertIn("INSERT INTO millie_identity_credentials", sql)
        self.assertIn("'geon@millie'", sql)
        self.assertIn("'Sources/PST'", sql)
        self.assertNotIn("temporary secret", sql)

    def test_default_folders_are_stable_for_mail_clients(self) -> None:
        folders = default_mailbox_folders("mailbox-1")
        paths = [folder.path for folder in folders]

        self.assertEqual(paths[0], "INBOX")
        self.assertIn("All Mail", paths)
        self.assertIn("Sources/IMAP", paths)
        self.assertIn("Sources/PST", paths)

    def test_settings_domain_canonicalizes_local_aliases(self) -> None:
        settings = {
            "service_mail_domain": "millie.cnbsk.cloud",
            "service_mail_local_domain": "MILLIE",
            "service_mail_domain_aliases": "local.millie.test",
        }
        identity = identity_from_settings("Geon@MILLIE", "Geon", settings)

        self.assertEqual(default_service_login(settings, "geon"), "geon@millie.cnbsk.cloud")
        self.assertEqual(identity.normalized_login, "geon@millie.cnbsk.cloud")
        self.assertIn(
            "geon@millie",
            login_address_candidates(
                "geon@millie.cnbsk.cloud",
                primary_domain="millie.cnbsk.cloud",
                domain_aliases=("MILLIE",),
            ),
        )


if __name__ == "__main__":
    unittest.main()
