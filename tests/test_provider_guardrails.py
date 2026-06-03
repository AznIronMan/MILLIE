from __future__ import annotations

import unittest

from millie.brain.provider_guardrails import (
    ProviderWriteBlocked,
    provider_write_decision,
    require_provider_write,
)


class ProviderGuardrailsTest(unittest.TestCase):
    def test_provider_purge_blocks_without_explicit_settings(self) -> None:
        decision = provider_write_decision({}, "provider_purge_manifest", manifest_id="m1")

        self.assertFalse(decision.allowed)
        self.assertIn("automation_level=provider_write", decision.reason)
        self.assertEqual(decision.automation_level, "observe")
        self.assertFalse(decision.provider_write_enabled)

    def test_provider_purge_requires_manifest_id(self) -> None:
        decision = provider_write_decision(
            {
                "automation_level": "provider_write",
                "automation_provider_write_enabled": "true",
            },
            "provider_purge_manifest",
        )

        self.assertFalse(decision.allowed)
        self.assertIn("manifest id", decision.reason)

    def test_provider_purge_allows_explicit_manifest_path(self) -> None:
        decision = provider_write_decision(
            {
                "automation_level": "provider_write",
                "automation_provider_write_enabled": "true",
            },
            "provider_purge_manifest",
            manifest_id="m1",
        )

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.manifest_id, "m1")

    def test_browser_unsubscribe_execution_remains_disabled(self) -> None:
        decision = provider_write_decision(
            {
                "automation_level": "provider_write",
                "automation_provider_write_enabled": "true",
            },
            "unsubscribe_browser_execute",
        )

        self.assertFalse(decision.allowed)
        self.assertIn("disabled", decision.reason)

    def test_require_provider_write_raises_blocked_decision(self) -> None:
        with self.assertRaises(ProviderWriteBlocked) as context:
            require_provider_write({}, "provider_purge_manifest", manifest_id="m1")

        self.assertFalse(context.exception.decision.allowed)


if __name__ == "__main__":
    unittest.main()
