from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential


ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "agents" / "prompts"

PROMPT_FILES = {
    "df-feasibility-analyst": "feasibility_analyst.md",
    "df-auditor": "auditor.md",
}


def _project_client() -> AIProjectClient:
    return AIProjectClient(
        endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        credential=DefaultAzureCredential(),
        allow_preview=True,
    )


def _usage_dict(usage: Any) -> dict[str, Any]:
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        return usage.model_dump()
    if isinstance(usage, dict):
        return usage
    return {
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }


def _extract_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, flags=re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def _schema_format(agent_name: str, response_schema: dict[str, Any] | None) -> dict[str, Any] | None:
    if not response_schema:
        return None
    return {
        "format": {
            "type": "json_schema",
            "name": agent_name.replace("-", "_"),
            "schema": response_schema,
            "strict": False,
        }
    }


def run_agent(
    agent_name: str,
    input_text: str,
    *,
    response_schema: dict[str, Any] | None = None,
    max_output_tokens: int = 3200,
) -> dict[str, Any]:
    prompt_file = PROMPT_FILES.get(agent_name)
    if not prompt_file:
        raise ValueError(f"Unsupported agent: {agent_name}")
    instructions = (PROMPTS / prompt_file).read_text(encoding="utf-8")
    if response_schema:
        instructions += (
            "\n\nReturn only valid JSON. The JSON must conform to this schema:\n"
            + json.dumps(response_schema, ensure_ascii=False)
        )

    client = _project_client()
    openai_client = client.get_openai_client()
    create_args = {
        "model": os.environ.get("DF_CHAT_DEPLOYMENT", "gpt-5.1"),
        "instructions": instructions,
        "input": input_text,
        "max_output_tokens": max_output_tokens,
    }
    text_format = _schema_format(agent_name, response_schema)
    if text_format:
        create_args["text"] = text_format
    try:
        response = openai_client.responses.create(**create_args)
    except Exception:
        if "text" not in create_args:
            raise
        create_args.pop("text")
        response = openai_client.responses.create(**create_args)
    text = getattr(response, "output_text", "") or ""
    return {
        "text": text,
        "structured": _extract_json(text),
        "response_id": getattr(response, "id", None),
        "usage": _usage_dict(getattr(response, "usage", None)),
    }
