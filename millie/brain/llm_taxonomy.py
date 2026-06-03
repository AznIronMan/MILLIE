"""Manual LLM assistance for aggregate-only taxonomy review."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


TAXONOMY_ASSISTANT_INSTRUCTIONS = """\
You are MILLIE's cautious email taxonomy review assistant.

Use only the aggregate proposal context provided by MILLIE. Do not ask for raw
email content, full addresses, attachments, or message samples. Your output is
advisory only: recommend whether each taxonomy target should be kept, renamed,
merged, split, or manually reviewed. Do not claim that any action was applied.
"""

TAXONOMY_ASSISTANT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {
            "type": "string",
            "description": "Short summary of the proposed taxonomy direction.",
        },
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "proposal_id": {"type": "string"},
                    "target": {"type": "string"},
                    "recommendation": {
                        "type": "string",
                        "enum": ["keep", "rename", "merge", "split", "manual_review"],
                    },
                    "suggested_target": {"type": "string"},
                    "confidence": {"type": "number"},
                    "rationale": {"type": "string"},
                    "risks": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": [
                    "proposal_id",
                    "target",
                    "recommendation",
                    "suggested_target",
                    "confidence",
                    "rationale",
                    "risks",
                ],
            },
        },
        "safety_notes": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["summary", "recommendations", "safety_notes"],
}


class LLMProviderError(RuntimeError):
    """Raised when a configured LLM provider cannot complete a manual request."""


@dataclass(frozen=True, slots=True)
class LLMProviderConfig:
    tier: str
    provider: str
    api_key: str
    model: str
    thinking: str


def configured_llm_provider(settings: dict[str, str], *, tier: str = "main") -> LLMProviderConfig:
    """Return one configured provider tier from decrypted MILLIE settings."""

    tier = tier.strip().lower()
    if tier not in {"main", "second", "third"}:
        raise LLMProviderError(f"Unsupported LLM provider tier: {tier}")
    prefix = "main" if tier == "main" else f"{tier}"
    provider = str(settings.get(f"{prefix}_api_provider") or "").strip().lower()
    return LLMProviderConfig(
        tier=tier,
        provider=provider,
        api_key=str(settings.get(f"{prefix}_api_key") or ""),
        model=str(settings.get(f"{prefix}_api_model") or "").strip(),
        thinking=str(settings.get(f"{prefix}_api_thinking") or "").strip().lower(),
    )


def taxonomy_assistant_context(proposals: list[dict[str, Any]]) -> dict[str, Any]:
    """Build aggregate-only context for the LLM taxonomy assistant."""

    safe_proposals = []
    for proposal in proposals:
        condition = proposal.get("condition")
        if not isinstance(condition, dict):
            condition = {}
        safe_proposals.append(
            {
                "proposal_id": str(proposal.get("id") or ""),
                "target": str(proposal.get("target") or ""),
                "classification_kind": str(condition.get("classification_kind") or ""),
                "classification_value": str(condition.get("classification_value") or ""),
                "target_folder_path": condition.get("target_folder_path"),
                "target_tags": list(condition.get("target_tags") or []),
                "evidence_count": int(proposal.get("evidence_count") or 0),
                "confidence": float(proposal.get("confidence") or 0),
                "sender_domains": list(proposal.get("sender_domains") or []),
                "source_folders": list(proposal.get("source_folders") or []),
                "message_years": list(proposal.get("message_years") or []),
            }
        )
    return {
        "task": "review_email_archive_taxonomy",
        "privacy_boundary": (
            "Aggregate proposal context only. No raw email bodies, full subjects, "
            "full addresses, attachments, or message samples are included."
        ),
        "proposal_count": len(safe_proposals),
        "proposals": safe_proposals,
    }


def build_openai_taxonomy_request(
    *,
    model: str,
    proposals: list[dict[str, Any]],
    thinking: str = "",
) -> dict[str, Any]:
    """Build an OpenAI Responses API request for structured taxonomy advice."""

    if not model:
        raise LLMProviderError("OpenAI taxonomy assistant requires main_api_model.")
    request: dict[str, Any] = {
        "model": model,
        "instructions": TAXONOMY_ASSISTANT_INSTRUCTIONS,
        "input": json.dumps(taxonomy_assistant_context(proposals), indent=2, sort_keys=True),
        "store": False,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "millie_taxonomy_assistant",
                "strict": True,
                "schema": TAXONOMY_ASSISTANT_SCHEMA,
            }
        },
    }
    effort = openai_reasoning_effort(thinking)
    if effort:
        request["reasoning"] = {"effort": effort}
    return request


def openai_reasoning_effort(thinking: str) -> str:
    value = str(thinking or "").strip().lower()
    return {
        "low": "low",
        "med": "medium",
        "medium": "medium",
        "high": "high",
        "xhigh": "high",
    }.get(value, "")


def run_taxonomy_assistant(
    settings: dict[str, str],
    proposals: list[dict[str, Any]],
    *,
    tier: str = "main",
    timeout: int = 60,
) -> dict[str, Any]:
    """Run the manual taxonomy assistant against the configured provider tier."""

    config = configured_llm_provider(settings, tier=tier)
    if not config.provider:
        raise LLMProviderError(f"No LLM provider configured for {config.tier} tier.")
    if config.provider != "openai":
        raise LLMProviderError(
            f"Taxonomy assistant currently supports openai only; configured provider is {config.provider}."
        )
    if not config.api_key:
        raise LLMProviderError("OpenAI taxonomy assistant requires an API key.")
    request = build_openai_taxonomy_request(
        model=config.model,
        proposals=proposals,
        thinking=config.thinking,
    )
    response = post_openai_response(config.api_key, request, timeout=timeout)
    output_text = extract_response_text(response)
    parsed = parse_taxonomy_assistant_output(output_text)
    return {
        "ok": True,
        "provider": config.provider,
        "tier": config.tier,
        "model": response.get("model") or config.model,
        "proposal_count": len(proposals),
        "request": redact_openai_request(request),
        "response_id": response.get("id"),
        "usage": response.get("usage") or {},
        "output_text": output_text,
        "assistant": parsed,
    }


def post_openai_response(api_key: str, payload: dict[str, Any], *, timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise LLMProviderError(f"OpenAI request failed with HTTP {exc.code}: {body[:1000]}") from exc
    except urllib.error.URLError as exc:
        raise LLMProviderError(f"OpenAI request failed: {exc}") from exc


def extract_response_text(response: dict[str, Any]) -> str:
    direct = response.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    parts: list[str] = []
    for item in response.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict) and content.get("type") == "output_text":
                parts.append(str(content.get("text") or ""))
    return "\n".join(part for part in parts if part).strip()


def parse_taxonomy_assistant_output(output_text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(output_text)
    except json.JSONDecodeError:
        return {
            "summary": output_text.strip(),
            "recommendations": [],
            "safety_notes": ["The model response was not valid JSON."],
        }
    if not isinstance(parsed, dict):
        return {
            "summary": output_text.strip(),
            "recommendations": [],
            "safety_notes": ["The model response was not a JSON object."],
        }
    parsed.setdefault("summary", "")
    parsed.setdefault("recommendations", [])
    parsed.setdefault("safety_notes", [])
    return parsed


def redact_openai_request(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": request.get("model"),
        "store": request.get("store"),
        "text_format": request.get("text", {}).get("format", {}).get("type"),
        "reasoning": request.get("reasoning") or {},
        "input_preview": str(request.get("input") or "")[:2000],
    }
