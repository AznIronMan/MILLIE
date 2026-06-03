from __future__ import annotations

import unittest

from millie.storage.schema import load_schema


class BrainSchemaTest(unittest.TestCase):
    def test_postgres_brain_tables_are_present(self) -> None:
        schema = load_schema("postgres")

        for table_name in (
            "millie_automation_runs",
            "millie_brain_rules",
            "millie_message_classifications",
            "millie_user_feedback_events",
            "millie_retention_policies",
            "millie_unsubscribe_candidates",
            "millie_automation_audit_log",
        ):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table_name}", schema)

    def test_brain_defaults_are_non_destructive(self) -> None:
        schema = load_schema("postgres")

        self.assertIn("automation_level TEXT NOT NULL DEFAULT 'observe'", schema)
        self.assertIn("requires_review BOOLEAN NOT NULL DEFAULT TRUE", schema)
        self.assertIn("'block_provider_write'", schema)
        self.assertIn("'provider_purge_manifest'", schema)
        self.assertNotIn("'delete_provider_message'", schema)

    def test_classifications_and_audit_reference_messages(self) -> None:
        schema = load_schema("postgres")

        self.assertIn(
            "message_id TEXT NOT NULL REFERENCES mail_messages(id) ON DELETE CASCADE",
            schema,
        )
        self.assertIn(
            "message_id TEXT REFERENCES mail_messages(id) ON DELETE SET NULL",
            schema,
        )


if __name__ == "__main__":
    unittest.main()
