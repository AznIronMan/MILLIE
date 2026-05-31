from __future__ import annotations

import unittest

from millie.service.auth import (
    MillieIdentity,
    build_identity_sql,
    hash_password,
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


if __name__ == "__main__":
    unittest.main()
