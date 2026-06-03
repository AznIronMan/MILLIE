from __future__ import annotations

import unittest

from millie.brain.automation import (
    automation_level,
    automation_level_allows,
    provider_write_allowed,
)


class AutomationLevelsTest(unittest.TestCase):
    def test_invalid_level_defaults_to_observe(self) -> None:
        self.assertEqual(automation_level({}), "observe")
        self.assertEqual(automation_level({"automation_level": "bad"}), "observe")

    def test_level_ordering(self) -> None:
        self.assertTrue(automation_level_allows({"automation_level": "review"}, "observe"))
        self.assertTrue(automation_level_allows({"automation_level": "review"}, "review"))
        self.assertFalse(automation_level_allows({"automation_level": "review"}, "auto_internal"))

    def test_provider_write_requires_second_switch(self) -> None:
        self.assertFalse(provider_write_allowed({"automation_level": "provider_write"}))
        self.assertFalse(
            provider_write_allowed(
                {
                    "automation_level": "auto_internal",
                    "automation_provider_write_enabled": "true",
                }
            )
        )
        self.assertTrue(
            provider_write_allowed(
                {
                    "automation_level": "provider_write",
                    "automation_provider_write_enabled": "true",
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
