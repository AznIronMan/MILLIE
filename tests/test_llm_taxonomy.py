from __future__ import annotations

import json
import unittest

from millie.brain.llm_taxonomy import (
    LLMProviderError,
    build_openai_taxonomy_request,
    configured_llm_provider,
    extract_response_text,
    parse_taxonomy_assistant_output,
    run_taxonomy_assistant,
    taxonomy_assistant_context,
)


class LLMTaxonomyTest(unittest.TestCase):
    def sample_proposal(self) -> dict[str, object]:
        return {
            "id": "proposal-1",
            "target": "Archive/Receipts/2026",
            "condition": {
                "classification_kind": "folder",
                "classification_value": "receipts",
                "target_folder_path": "Archive/Receipts/2026",
                "target_tags": ["receipts"],
            },
            "confidence": 0.87,
            "evidence_count": 11,
            "sender_domains": ["vendor.example"],
            "source_folders": ["INBOX", "Receipts"],
            "message_years": ["2026"],
            "samples": [
                {
                    "subject": "Private order subject",
                    "from": "person@example.com",
                    "body": "do not send this",
                }
            ],
            "llm_context": {
                "instruction": "old context should not be copied blindly",
            },
        }

    def test_taxonomy_context_is_aggregate_only(self) -> None:
        context = taxonomy_assistant_context([self.sample_proposal()])
        serialized = json.dumps(context)
        self.assertIn("vendor.example", serialized)
        self.assertIn("Archive/Receipts/2026", serialized)
        self.assertNotIn("Private order subject", serialized)
        self.assertNotIn("person@example.com", serialized)
        self.assertNotIn("do not send this", serialized)
        self.assertNotIn("old context should not be copied blindly", serialized)

    def test_openai_request_uses_structured_outputs_and_store_false(self) -> None:
        request = build_openai_taxonomy_request(
            model="gpt-test",
            proposals=[self.sample_proposal()],
            thinking="med",
        )
        self.assertEqual(request["model"], "gpt-test")
        self.assertFalse(request["store"])
        self.assertEqual(request["reasoning"], {"effort": "medium"})
        self.assertEqual(request["text"]["format"]["type"], "json_schema")
        self.assertTrue(request["text"]["format"]["strict"])
        self.assertIn("Aggregate proposal context only", request["input"])

    def test_extract_response_text_reads_output_content(self) -> None:
        response = {
            "output": [
                {
                    "content": [
                        {
                            "type": "output_text",
                            "text": '{"summary":"ok","recommendations":[],"safety_notes":[]}',
                        }
                    ]
                }
            ]
        }
        self.assertEqual(
            extract_response_text(response),
            '{"summary":"ok","recommendations":[],"safety_notes":[]}',
        )

    def test_parse_taxonomy_output_falls_back_for_plain_text(self) -> None:
        parsed = parse_taxonomy_assistant_output("plain advice")
        self.assertEqual(parsed["summary"], "plain advice")
        self.assertEqual(parsed["recommendations"], [])
        self.assertIn("not valid JSON", parsed["safety_notes"][0])

    def test_provider_tier_reads_settings(self) -> None:
        config = configured_llm_provider(
            {
                "main_api_provider": "openai",
                "main_api_key": "key",
                "main_api_model": "model",
                "main_api_thinking": "low",
            }
        )
        self.assertEqual(config.provider, "openai")
        self.assertEqual(config.model, "model")

    def test_non_openai_provider_is_explicitly_unsupported(self) -> None:
        with self.assertRaises(LLMProviderError):
            run_taxonomy_assistant(
                {
                    "main_api_provider": "claude",
                    "main_api_key": "key",
                    "main_api_model": "model",
                    "main_api_thinking": "",
                },
                [self.sample_proposal()],
                timeout=1,
            )


if __name__ == "__main__":
    unittest.main()
