from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from openai.types.responses.response_input_param import FunctionCallOutput


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


def _agent_reference(agent_name: str) -> dict[str, Any]:
    return {"agent_reference": {"name": agent_name, "type": "agent_reference"}}


def _execute_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name != "search_pack_context":
        raise ValueError(f"Unsupported Foundry agent tool call: {name}")
    try:
        from .rag import search
    except ImportError:
        from rag import search

    workspace_id = str(arguments.get("workspace_id") or "demo-corpus")
    query = str(arguments.get("query") or "")
    top_k = int(arguments.get("top_k") or 5)
    top_k = max(1, min(top_k, 20))
    hits = search(workspace_id, query, top_k)
    return {
        "workspace_id": workspace_id,
        "query": query,
        "hits": hits,
        "count": len(hits),
        "source_index": os.environ.get("SEARCH_INDEX_NAME", "dataforge-workspaces"),
    }


def _function_calls(response: Any) -> list[Any]:
    return [item for item in getattr(response, "output", []) if getattr(item, "type", None) == "function_call"]


def _run_prompt_agent(
    agent_name: str,
    input_text: str,
    *,
    response_schema: dict[str, Any] | None,
    max_output_tokens: int,
    max_tool_rounds: int = 4,
) -> dict[str, Any]:
    client = _project_client()
    openai_client = client.get_openai_client()
    conversation = openai_client.conversations.create()
    text_format = _schema_format(agent_name, response_schema)
    tool_trace: list[dict[str, Any]] = []

    create_args: dict[str, Any] = {
        "input": input_text,
        "conversation": conversation.id,
        "max_output_tokens": max_output_tokens,
        "extra_body": _agent_reference(agent_name),
    }
    if text_format:
        create_args["text"] = text_format
    try:
        response = openai_client.responses.create(**create_args)
    except Exception:
        if "text" not in create_args:
            raise
        create_args.pop("text")
        response = openai_client.responses.create(**create_args)

    for _ in range(max_tool_rounds):
        calls = _function_calls(response)
        if not calls:
            break
        tool_outputs: list[FunctionCallOutput] = []
        for call in calls:
            name = str(getattr(call, "name", ""))
            raw_args = getattr(call, "arguments", "{}") or "{}"
            arguments = json.loads(raw_args)
            result = _execute_tool(name, arguments)
            tool_trace.append(
                {
                    "name": name,
                    "args": arguments,
                    "count": result.get("count"),
                    "call_id": getattr(call, "call_id", None),
                }
            )
            tool_outputs.append(
                FunctionCallOutput(
                    type="function_call_output",
                    call_id=getattr(call, "call_id"),
                    output=json.dumps(result, ensure_ascii=False),
                )
            )
        next_args: dict[str, Any] = {
            "input": tool_outputs,
            "conversation": conversation.id,
            "max_output_tokens": max_output_tokens,
            "extra_body": _agent_reference(agent_name),
        }
        if text_format:
            next_args["text"] = text_format
        try:
            response = openai_client.responses.create(**next_args)
        except Exception:
            if "text" not in next_args:
                raise
            next_args.pop("text")
            response = openai_client.responses.create(**next_args)
    else:
        raise RuntimeError(f"{agent_name} exceeded {max_tool_rounds} tool rounds")

    text = getattr(response, "output_text", "") or ""
    if not text.strip():
        raise RuntimeError(f"{agent_name} returned no final text after tool loop")
    try:
        openai_client.conversations.delete(conversation_id=conversation.id)
    except Exception:
        pass
    return {
        "text": text,
        "structured": _extract_json(text),
        "response_id": getattr(response, "id", None),
        "usage": _usage_dict(getattr(response, "usage", None)),
        "mode": "foundry_agent_service",
        "tool_calls": tool_trace,
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

    if os.environ.get("DF_AGENT_RUNTIME", "foundry_agent_service") == "foundry_agent_service":
        agent_payload = input_text
        if response_schema:
            agent_payload += (
                "\n\nReturn only valid JSON. The JSON must conform to this schema:\n"
                + json.dumps(response_schema, ensure_ascii=False)
            )
        return _run_prompt_agent(
            agent_name,
            agent_payload,
            response_schema=response_schema,
            max_output_tokens=max_output_tokens,
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
