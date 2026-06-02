from __future__ import annotations

import unittest

from millie.mail_providers import normalize_mail_account


class MailProviderTest(unittest.TestCase):
    def test_icloud_imap_defaults_for_icloud_domain(self) -> None:
        account = normalize_mail_account(
            {
                "account_type": "imap",
                "email_address": "geoff@icloud.com",
                "host": "",
                "port": "",
                "username": "",
                "security": "",
                "auth_method": "",
            }
        )

        self.assertEqual(account["host"], "imap.mail.me.com")
        self.assertEqual(account["port"], "993")
        self.assertEqual(account["username"], "geoff")
        self.assertEqual(account["security"], "ssl_tls")
        self.assertEqual(account["auth_method"], "password")

    def test_icloud_imap_defaults_for_me_domain(self) -> None:
        account = normalize_mail_account(
            {
                "account_type": "imap",
                "email_address": "geoff@me.com",
                "host": "",
                "port": "",
                "username": "",
                "security": "",
                "auth_method": "",
            }
        )

        self.assertEqual(account["host"], "imap.mail.me.com")
        self.assertEqual(account["username"], "geoff")

    def test_icloud_smtp_defaults_use_full_email_username(self) -> None:
        account = normalize_mail_account(
            {
                "account_type": "smtp",
                "email_address": "geoff@mac.com",
                "host": "",
                "port": "",
                "username": "",
                "security": "",
                "auth_method": "",
            }
        )

        self.assertEqual(account["host"], "smtp.mail.me.com")
        self.assertEqual(account["port"], "587")
        self.assertEqual(account["username"], "geoff@mac.com")
        self.assertEqual(account["security"], "starttls")


if __name__ == "__main__":
    unittest.main()
