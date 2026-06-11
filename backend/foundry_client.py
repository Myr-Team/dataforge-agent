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

_WEB_TOOL_CACHE: dict[str, Any] | None = None


CLARIFY_GUIDANCE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "question": {
            "type": "string",
            "description": "A concise Chinese onboarding guide and next-step question.",
        }
    },
    "required": ["question"],
}


MARKET_WEB_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "external_findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "claim": {"type": "string"},
                    "source_title": {"type": "string"},
                    "source_url": {"type": "string"},
                    "confidence": {"type": "string"},
                },
                "required": ["claim", "source_title", "source_url", "confidence"],
            },
        },
        "positioning_note": {"type": "string"},
    },
    "required": ["external_findings", "positioning_note"],
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


def _stream_delta(event: Any) -> str:
    event_type = str(getattr(event, "type", "") or "")
    delta = getattr(event, "delta", None)
    if isinstance(delta, str) and delta:
        return delta
    if event_type.endswith(".delta") and delta is not None:
        return str(delta)
    return ""


def _response_meta(response: Any, mode: str) -> dict[str, Any]:
    return {
        "mode": mode,
        "response_id": getattr(response, "id", None),
        "usage": _usage_dict(getattr(response, "usage", None)),
    }


def _to_plain_data(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return {key: _to_plain_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_plain_data(item) for item in value]
    return value


def _response_sources(response: Any) -> list[dict[str, str]]:
    data = _to_plain_data(getattr(response, "output", []))
    sources: list[dict[str, str]] = []
    seen: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            url = value.get("url") or value.get("source_url")
            title = value.get("title") or value.get("source_title") or value.get("text")
            if isinstance(url, str) and url.startswith(("http://", "https://")) and url not in seen:
                seen.add(url)
                sources.append({"title": str(title or url), "url": url, "confidence": "market_inferred"})
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(data)
    return sources[:8]


def _response_tool_trace(response: Any) -> list[dict[str, Any]]:
    data = _to_plain_data(getattr(response, "output", []))
    calls: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            item_type = str(value.get("type") or "")
            if any(token in item_type for token in ("web_search", "bing_grounding", "search_call")):
                calls.append(
                    {
                        "type": item_type,
                        "status": value.get("status"),
                        "id": value.get("id"),
                    }
                )
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(data)
    return calls


def _foundry_web_tool_candidates() -> list[tuple[str, dict[str, Any]]]:
    candidates: list[tuple[str, dict[str, Any]]] = []
    bing_connection_id = (
        os.environ.get("DF_BING_CONNECTION_ID")
        or os.environ.get("BING_CONNECTION_ID")
        or os.environ.get("AZURE_BING_CONNECTION_ID")
    )
    if bing_connection_id:
        candidates.append(
            (
                "bing_grounding",
                {
                    "type": "bing_grounding",
                    "bing_grounding": {
                        "search_configurations": [
                            {
                                "project_connection_id": bing_connection_id,
                                "market": os.environ.get("DF_WEB_MARKET", "zh-CN"),
                                "count": 5,
                            }
                        ]
                    },
                },
            )
        )
    candidates.append(("web_search_preview", {"type": "web_search_preview", "search_context_size": "medium"}))
    candidates.append(("web_search", {"type": "web_search", "search_context_size": "medium"}))
    return candidates


def _verify_foundry_web_tool(openai_client: Any) -> dict[str, Any]:
    global _WEB_TOOL_CACHE
    if _WEB_TOOL_CACHE is not None:
        return _WEB_TOOL_CACHE

    failures: list[dict[str, str]] = []
    instructions = (
        "Use the provided Foundry web search tool for this availability check. "
        "Return a compact JSON object with a brief claim and source_url."
    )
    for name, tool in _foundry_web_tool_candidates():
        try:
            response = openai_client.responses.create(
                model=os.environ.get("DF_CHAT_DEPLOYMENT", "gpt-5.1"),
                instructions=instructions,
                input="Check current public information about enterprise data analytics platforms.",
                tools=[tool],
                max_output_tokens=500,
            )
            text = getattr(response, "output_text", "") or ""
            sources = _response_sources(response)
            tool_calls = _response_tool_trace(response)
            if text.strip() and (sources or tool_calls):
                _WEB_TOOL_CACHE = {
                    "available": True,
                    "name": name,
                    "tool": tool,
                    "sources": sources,
                    "tool_calls": tool_calls,
                    "response_id": getattr(response, "id", None),
                    "usage": _usage_dict(getattr(response, "usage", None)),
                }
                return _WEB_TOOL_CACHE
            failures.append({"tool": name, "error": "no_web_call_or_source_detected"})
        except Exception as exc:
            failures.append({"tool": name, "error": str(exc)[:500]})
    _WEB_TOOL_CACHE = {"available": False, "failures": failures}
    return _WEB_TOOL_CACHE


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


def run_coordinator_guidance(payload: dict[str, Any]) -> dict[str, Any]:
    client = _project_client()
    openai_client = client.get_openai_client()
    instructions = (
        "你是 DataForge 的协调器。用户的输入过短、过泛或当前工作区证据不足时，"
        "你要生成中文上下文引导，而不是固定模板。必须包含三件事："
        "1) 简短自我介绍；2) 结合 workspace_context 说明当前工作区能做什么；"
        "3) 给用户一个明确的下一步提问。"
        "语气自然，避免每次复用同一句话。不要编造 profile_summary 之外的具体事实。"
        "只返回 JSON。"
    )
    create_args: dict[str, Any] = {
        "model": os.environ.get("DF_CHAT_DEPLOYMENT", "gpt-5.1"),
        "instructions": instructions,
        "input": json.dumps(payload, ensure_ascii=False, indent=2),
        "max_output_tokens": 700,
        "text": _schema_format("df_coordinator_guidance", CLARIFY_GUIDANCE_SCHEMA),
    }
    try:
        response = openai_client.responses.create(**create_args)
    except Exception:
        create_args.pop("text", None)
        response = openai_client.responses.create(**create_args)
    text = getattr(response, "output_text", "") or ""
    data = _extract_json(text)
    return {
        "question": str(data.get("question") or "").strip(),
        "response_id": getattr(response, "id", None),
        "usage": _usage_dict(getattr(response, "usage", None)),
        "mode": "coordinator_llm",
    }


def run_market_web_research(payload: dict[str, Any]) -> dict[str, Any]:
    client = _project_client()
    openai_client = client.get_openai_client()
    verification = _verify_foundry_web_tool(openai_client)
    if not verification.get("available"):
        return {
            "external_findings": [],
            "sources": [],
            "positioning_note": "Foundry native web search is unavailable for this deployment; market analysis is limited to internal competitor lookup.",
            "_llm": {
                "mode": "foundry_web_unavailable",
                "response_id": None,
                "usage": {},
                "verification": verification,
            },
        }

    instructions = (
        "你是 DataForge 的市场研究员。请使用已经验证可用的 Foundry 原生 web 搜索工具查询公开市场信息，"
        "只返回 JSON。所有外部网页信息都必须标记为 market_inferred，不能说成工作区数据已确认。"
        "输出 2 到 4 条和输入机会相关的外部行情/竞品/需求线索，每条必须尽量带 source_url。"
    )
    create_args: dict[str, Any] = {
        "model": os.environ.get("DF_CHAT_DEPLOYMENT", "gpt-5.1"),
        "instructions": instructions,
        "input": json.dumps(payload, ensure_ascii=False, indent=2),
        "tools": [verification["tool"]],
        "max_output_tokens": 1200,
        "text": _schema_format("df_market_web_research", MARKET_WEB_SCHEMA),
    }
    try:
        response = openai_client.responses.create(**create_args)
    except Exception:
        create_args.pop("text", None)
        response = openai_client.responses.create(**create_args)
    text = getattr(response, "output_text", "") or ""
    sources = _response_sources(response)
    tool_calls = _response_tool_trace(response)
    try:
        data = _extract_json(text)
    except Exception:
        data = {"external_findings": [], "positioning_note": text.strip()}
    findings = data.get("external_findings") or []
    clean_findings: list[dict[str, Any]] = []
    for idx, finding in enumerate(findings[:4]):
        if not isinstance(finding, dict):
            continue
        source = sources[idx] if idx < len(sources) else {}
        item = {
            "claim": str(finding.get("claim") or "").strip(),
            "source_title": str(finding.get("source_title") or source.get("title") or "").strip(),
            "source_url": str(finding.get("source_url") or source.get("url") or "").strip(),
            "confidence": "market_inferred",
            "source_type": "market",
        }
        if item["claim"]:
            clean_findings.append(item)
            if item["source_url"] and all(source.get("url") != item["source_url"] for source in sources):
                sources.append(
                    {
                        "title": item["source_title"] or item["source_url"],
                        "url": item["source_url"],
                        "confidence": "market_inferred",
                    }
                )
    meta = _response_meta(response, f"foundry_native_{verification['name']}")
    meta["verification"] = {
        "tool": verification.get("name"),
        "response_id": verification.get("response_id"),
        "sources": verification.get("sources", []),
        "tool_calls": verification.get("tool_calls", []),
    }
    meta["tool_calls"] = tool_calls
    return {
        "external_findings": clean_findings,
        "sources": sources,
        "positioning_note": str(data.get("positioning_note") or "").strip(),
        "_llm": meta,
    }


def stream_grounded_answer(payload: dict[str, Any]) -> Any:
    client = _project_client()
    openai_client = client.get_openai_client()
    instructions = (
        "你是 DataForge 的最终回答输出器。请把结构化 FeasibilityReport、审计结论、"
        "工作区证据和市场补充信息整理成中文分析叙述。要求："
        "1) 只基于输入中的事实与证据，不编造；"
        "2) 每个主要判断都写明证据 ref 或来源类型，并标注 data_confirmed、market_inferred 或 speculative；"
        "3) 工作区证据与外部市场推断必须区分，market_inferred 不能写成工作区已确认事实；"
        "4) 如果审计指出问题，要自然说明修订或保守处理；"
        "5) 不要只输出模板句，要结合本次数据给出 3 到 6 段可读分析。"
    )
    create_args = {
        "model": os.environ.get("DF_CHAT_DEPLOYMENT", "gpt-5.1"),
        "instructions": instructions,
        "input": json.dumps(payload, ensure_ascii=False, indent=2),
        "max_output_tokens": 1400,
        "stream": True,
    }
    completed_meta: dict[str, Any] | None = None
    stream = openai_client.responses.create(**create_args)
    for event in stream:
        delta = _stream_delta(event)
        if delta:
            yield {"type": "delta", "delta": delta}
            continue
        event_type = str(getattr(event, "type", "") or "")
        if event_type in {"response.completed", "response.done"}:
            response = getattr(event, "response", None)
            if response is not None:
                completed_meta = _response_meta(response, "responses_stream")
    yield {"type": "meta", **(completed_meta or {"mode": "responses_stream", "usage": {}})}
