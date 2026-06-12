from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import os
import re
import threading
import time
import urllib.request
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from starlette.concurrency import run_in_threadpool

try:
    from . import cache_store
    from .chat_loop_primitives import sse
    from .conversation_store import append_message, conversation_context
    from .foundry_client import (
        run_agent,
        run_coordinator_direct_reply,
        run_coordinator_guidance,
        run_coordinator_route,
        run_followup_rewrite,
        run_market_web_research,
        stream_grounded_answer,
    )
    from .rag import search
    from .router import deterministic_route
    from .run_store import complete_run, get_run, record_event, start_run
    from .schemas import AuditVerdict, ChatRequest, Evidence, FeasibilityReport, RoutingDecision
    from .tracing import trace_event
    from .tools.generate_image import generate_image
    from .tools.narrate_summary import narrate_summary
    from .tools.render_pdf import render_pdf_report
    from .workspace_store import workspace_context, workspace_reference_images
except ImportError:
    import cache_store
    from chat_loop_primitives import sse
    from conversation_store import append_message, conversation_context
    from foundry_client import (
        run_agent,
        run_coordinator_direct_reply,
        run_coordinator_guidance,
        run_coordinator_route,
        run_followup_rewrite,
        run_market_web_research,
        stream_grounded_answer,
    )
    from rag import search
    from router import deterministic_route
    from run_store import complete_run, get_run, record_event, start_run
    from schemas import AuditVerdict, ChatRequest, Evidence, FeasibilityReport, RoutingDecision
    from tracing import trace_event
    from tools.generate_image import generate_image
    from tools.narrate_summary import narrate_summary
    from tools.render_pdf import render_pdf_report
    from workspace_store import workspace_context, workspace_reference_images


PRODUCT_TERMS = (
    "product",
    "saas",
    "business",
    "proposal",
    "package",
    "\u4ea7\u54c1",
    "\u5546\u4e1a",
    "\u9879\u76ee\u4e66",
    "\u65b9\u6848",
    "\u53d8\u73b0",
)
_MARKET_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_MARKET_CACHE_SECONDS = float(os.environ.get("DF_MARKET_CACHE_SECONDS", "600"))


def _contains(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in terms)


def _looks_like_solution_request(message: str) -> bool:
    text = str(message or "").strip().lower()
    if not text:
        return False
    cjk_action = re.search(r"(怎么|如何|怎样|方案|建议|推荐|策略|计划|活动|推广|落地|执行|设计)", text)
    latin_action = re.search(r"\b(how|plan|strategy|recommend|recommendation|advice|campaign|promote|proposal|approach)\b", text)
    return bool(cjk_action or latin_action)


def _clean_text(value: Any, limit: int | None = None) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit] if limit else text


def _request_with_history(req: ChatRequest, history: list[dict[str, Any]]) -> ChatRequest:
    if not history:
        return req
    turns = []
    for item in history[-6:]:
        role = str(item.get("role") or "message")
        text = _clean_text(item.get("text"), 700)
        if text:
            turns.append(f"{role}: {text}")
    if not turns:
        return req
    contextual_message = (
        "Conversation history for continuity:\n"
        + "\n".join(turns)
        + "\n\nCurrent user message:\n"
        + req.message
    )
    return req.model_copy(update={"message": contextual_message})


def _compact_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for item in history[-6:]:
        text = _clean_text(item.get("text"), 900)
        if text:
            compact.append({"role": item.get("role"), "text": text, "time": item.get("time")})
    return compact


def _persist_user_message(conversation_id: str, workspace_id: str, text: str) -> None:
    try:
        append_message(conversation_id, workspace_id=workspace_id, role="user", text=text)
    except Exception:
        pass


def _persist_assistant_message(conversation_id: str, workspace_id: str, text: str, verdict: str | None = None) -> None:
    try:
        append_message(conversation_id, workspace_id=workspace_id, role="assistant", text=text, verdict=verdict)
    except Exception:
        pass


def _artifact_verdict(artifact: dict[str, Any], fallback: str | None = None) -> str | None:
    feasibility = artifact.get("feasibility") or {}
    if isinstance(feasibility, dict) and feasibility.get("verdict"):
        return str(feasibility.get("verdict"))
    return fallback


def _slug(value: str, fallback: str = "generated-data-product") -> str:
    compact = _collapse_repeated_slug(value)
    slug = re.sub(r"[^a-z0-9]+", "-", compact.lower()).strip("-")
    tokens = [token for token in slug.split("-") if token]
    if len(tokens) > 8:
        slug = "-".join(tokens[:8])
    return slug[:72].strip("-") or fallback


def _collapse_repeated_slug(value: Any) -> str:
    raw = str(value or "").strip()
    tokens = [token for token in re.split(r"[-_\s]+", raw) if token]
    if not tokens:
        return raw
    collapsed: list[str] = []
    idx = 0
    while idx < len(tokens):
        best_size = 0
        best_end = idx
        max_size = (len(tokens) - idx) // 2
        for size in range(1, max_size + 1):
            seq = tokens[idx : idx + size]
            end = idx + size
            repeats = 1
            while end + size <= len(tokens) and tokens[end : end + size] == seq:
                repeats += 1
                end += size
            if repeats > 1 and size > best_size:
                best_size = size
                best_end = end
        if best_size:
            collapsed.extend(tokens[idx : idx + best_size])
            idx = best_end
        else:
            if not collapsed or collapsed[-1].lower() != tokens[idx].lower():
                collapsed.append(tokens[idx])
            idx += 1
    for size in range(1, len(collapsed) // 2 + 1):
        prefix = collapsed[:size]
        tail = collapsed[size:]
        if tail and len(tail) <= size and all(
            prefix[pos].lower().startswith(tail[pos].lower()) or tail[pos].lower().startswith(prefix[pos].lower())
            for pos in range(len(tail))
        ):
            collapsed = prefix
            break
    return " ".join(collapsed)


def _human_title_from_opportunity(value: Any) -> str:
    text = _collapse_repeated_slug(value)
    text = re.sub(r"[\\/#.]+", " ", text)
    text = re.sub(r"[_-]+", " ", text)
    text = re.sub(r"\b(raw|docs|json|csv|xlsx|xlsm|profile)\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" -_")
    if not text:
        return "当前工作区机会"
    if re.search(r"[\u4e00-\u9fff]", text):
        return text[:36]
    words = text.split()
    return " ".join(word.capitalize() if word.isascii() else word for word in words[:8])


def _query_action_title(message: str) -> str:
    text = re.sub(r"\s+", "", str(message or ""))
    text = re.sub(r"[，。！？!?；;：:、,.]", "", text)
    text = re.sub(r"^(我想|请|帮我|能不能|可以|想要|需要)", "", text)
    text = re.sub(r"请基于.*$", "", text)
    text = text.replace("该怎么做", "怎么做")
    if re.search(r"[\u4e00-\u9fff]", text):
        for marker in ("怎么做", "如何做", "怎么", "如何"):
            text = text.replace(marker, "")
        text = text[:14]
        if text and not re.search(r"(方案|建议|策略|计划)$", text):
            text += "方案"
        return text
    words = [word for word in re.findall(r"[A-Za-z0-9]+", str(message or "")) if len(word) > 2]
    return " ".join(words[:5]).title()


def _clean_opportunity_label(raw: Any, req: ChatRequest, artifact: dict[str, Any]) -> str:
    collapsed = _human_title_from_opportunity(raw)
    raw_text = str(raw or "")
    raw_tokens = [token for token in re.split(r"[-_\s]+", raw_text.lower()) if token]
    collapsed_tokens = [token for token in re.split(r"\s+", collapsed.lower()) if token]
    repeated_or_truncated = len(raw_tokens) > len(collapsed_tokens) + 2 or len(raw_text) > 72
    query_title = _query_action_title(req.message) if _looks_like_solution_request(req.message) else ""
    if query_title and (repeated_or_truncated or len(collapsed_tokens) <= 3):
        if re.search(r"[\u4e00-\u9fff]", query_title):
            prefix = collapsed if collapsed and not re.search(r"^(Workspace|Generated|Current)", collapsed) else ""
            candidate = f"{prefix} {query_title}".strip()
            return candidate[:48]
        return f"{collapsed} {query_title}".strip()[:60]
    return collapsed[:60]


def _normalize_feasibility_opportunity(report: FeasibilityReport, req: ChatRequest, artifact: dict[str, Any]) -> FeasibilityReport:
    data = report.model_dump()
    data["opportunity_id"] = _clean_opportunity_label(data.get("opportunity_id"), req, artifact)
    return FeasibilityReport.model_validate(data)


def _evidence_from_hit(hit: dict[str, Any]) -> Evidence:
    quote = _clean_text(hit.get("content", ""), 360)
    ref_parts = [
        str(hit.get("source_file") or "unknown"),
        str(hit.get("sheet") or ""),
        str(hit.get("row") or ""),
        str(hit.get("chunk_id") or hit.get("id") or "chunk"),
    ]
    ref = "#".join(part for part in ref_parts if part)
    return Evidence(source_type="corpus", ref=ref, quote=quote or None)


def _frame(event: str, data: Any, conversation_id: str | None = None) -> str:
    if event != "answer_delta" or os.environ.get("DF_TRACE_DELTAS") == "1":
        trace_event(event, data, conversation_id)
    try:
        record_event(conversation_id, event, data)
    except Exception:
        pass
    return sse(event, data)


def _agent_tool_events(agent: str, meta: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for call in meta.get("tool_calls", []):
        events.append(
            (
                "tool_call",
                {
                    "agent": agent,
                    "name": call.get("name"),
                    "args": call.get("args") or {},
                    "autonomous": True,
                },
            )
        )
        events.append(
            (
                "tool_result",
                {
                    "agent": agent,
                    "name": call.get("name"),
                    "count": call.get("count"),
                    "autonomous": True,
                },
            )
        )
    return events


def _coordinator(req: ChatRequest, history: list[dict[str, Any]]) -> tuple[RoutingDecision, dict[str, Any]]:
    context = workspace_context(req.workspace_id)
    payload = {
        "workspace_id": req.workspace_id,
        "workspace_context": context,
        "current_message": req.message,
        "conversation_history": _compact_history(history),
        "has_previous_assistant_answer": any(item.get("role") == "assistant" for item in history),
        "allowed_agents": [
            "df-corpus-analyst",
            "df-feasibility-analyst",
            "df-market-researcher",
            "df-auditor",
            "df-producer",
        ],
        "style_nonce": uuid.uuid4().hex[:8],
    }
    try:
        raw = run_coordinator_route(payload)
        decision = _routing_decision_from_llm(req, raw)
        return decision, raw.get("_llm") or {"mode": "coordinator_route"}
    except Exception as exc:
        if os.environ.get("DF_COORDINATOR_ALLOW_DETERMINISTIC_FALLBACK") == "1":
            decision = deterministic_route(req.message, req.workspace_id, {"doc_count": context.get("doc_count", 0)})
            decision.reason = f"Coordinator LLM failed; deterministic development fallback used: {_clean_text(exc, 240)}"
            return decision, {"mode": "deterministic_fallback", "error": _clean_text(exc, 300)}
        return (
            RoutingDecision(
                workspace_id=req.workspace_id,
                intent="clarify_needed",
                experts=[],
                output_mode="chat",
                needs_clarification=True,
                clarifying_question=None,
                reason=f"Coordinator LLM unavailable, so DataForge needs clarification instead of guessing: {_clean_text(exc, 240)}",
            ),
            {"mode": "coordinator_route_unavailable", "error": _clean_text(exc, 300)},
        )


def _routing_decision_from_llm(req: ChatRequest, raw: dict[str, Any]) -> RoutingDecision:
    intent = str(raw.get("intent") or "clarify_needed").strip()
    allowed_intents = {"feasibility_analysis", "followup_edit", "smalltalk_or_meta", "clarify_needed", "corpus_qa"}
    if intent not in allowed_intents:
        intent = "clarify_needed"
    context = workspace_context(req.workspace_id)
    forced_grounded_answer = False
    if intent == "clarify_needed" and _looks_like_solution_request(req.message) and int(context.get("doc_count") or 0) > 0:
        intent = "corpus_qa"
        raw["needs_clarification"] = False
        forced_grounded_answer = True
    experts = [str(item) for item in (raw.get("experts") or []) if isinstance(item, str)]
    allowed_agents = {
        "df-corpus-analyst",
        "df-feasibility-analyst",
        "df-market-researcher",
        "df-auditor",
        "df-producer",
    }
    experts = [agent for agent in experts if agent in allowed_agents]
    if intent in {"followup_edit", "smalltalk_or_meta", "clarify_needed"}:
        experts = []
    elif intent == "corpus_qa":
        experts = ["df-corpus-analyst"]
    elif intent == "feasibility_analysis":
        if "df-corpus-analyst" not in experts:
            experts.insert(0, "df-corpus-analyst")
        if "df-feasibility-analyst" not in experts:
            experts.append("df-feasibility-analyst")
        if "df-auditor" not in experts:
            experts.append("df-auditor")
    output_mode = str(raw.get("output_mode") or ("report" if intent == "feasibility_analysis" else "chat"))
    if output_mode not in {"chat", "report", "full_package"}:
        output_mode = "report" if intent == "feasibility_analysis" else "chat"
    if output_mode == "full_package" and "df-producer" not in experts and intent == "feasibility_analysis":
        experts.append("df-producer")
    return RoutingDecision(
        workspace_id=req.workspace_id,
        intent=intent,
        experts=experts,
        output_mode=output_mode,  # type: ignore[arg-type]
        needs_clarification=bool(raw.get("needs_clarification")) or intent == "clarify_needed",
        clarifying_question=str(raw.get("clarifying_question") or "").strip() or None,
        reason=(
            (_clean_text(raw.get("reason"), 800) + "；用户是在请求可基于当前资料回答的行动方案，已稳定路由到 grounded answer。")
            if forced_grounded_answer
            else (_clean_text(raw.get("reason"), 900) or "Coordinator routed the request by intent.")
        ),
    )


def _clarify_guidance(req: ChatRequest, decision: RoutingDecision, conversation_id: str) -> dict[str, Any]:
    context = workspace_context(req.workspace_id)
    payload = {
        "conversation_id": conversation_id,
        "workspace_context": context,
        "user_message": req.message,
        "routing_reason": decision.reason,
        "style_nonce": uuid.uuid4().hex[:8],
        "requirements": [
            "中文输出",
            "自我介绍",
            "说明当前工作区能做什么",
            "给出下一步问题",
            "避免固定模板和逐字复用",
        ],
    }
    try:
        result = run_coordinator_guidance(payload)
        question = _clean_text(result.get("question"), 1200)
        if question:
            return result | {"question": question}
    except Exception as exc:
        return {
            "question": _fallback_clarify_question(context),
            "mode": "coordinator_fallback",
            "error": _clean_text(exc, 300),
        }
    return {"question": _fallback_clarify_question(context), "mode": "coordinator_fallback"}


def _fallback_clarify_question(context: dict[str, Any]) -> str:
    name = context.get("name") or context.get("workspace_id") or "当前工作区"
    summary = context.get("profile_summary") or f"当前工作区已有 {context.get('doc_count', 0)} 条可检索资料。"
    return (
        f"你好，我是 DataForge 协调器，可以先帮你把「{name}」里的资料变成可评估的数据产品方向。"
        f"{summary} 你下一步想让我做资料问答、产品可行性评估，还是生成项目方案包？"
    )


def _run_corpus_analyst(req: ChatRequest) -> dict[str, Any]:
    hits = search(req.workspace_id, req.message, 8)
    evidence = [_evidence_from_hit(hit).model_dump() for hit in hits[:5]]
    title = _infer_title(req.message, hits)
    opportunities: list[dict[str, Any]] = []
    if evidence:
        opportunities.append(
            {
                "id": _slug(title),
                "title": title,
                "description": "A product opportunity inferred from the current workspace evidence.",
                "supporting_evidence": evidence[:3],
            }
        )
    return {
        "profile": {
            "workspace_id": req.workspace_id,
            "assets": sorted({hit.get("title", "Untitled") for hit in hits}),
            "asset_evidence": evidence,
            "gaps_observed": [] if hits else ["No workspace evidence matched the request."],
        },
        "hits": hits,
        "opportunities": opportunities,
    }


def _infer_title(message: str, hits: list[dict[str, Any]]) -> str:
    if _looks_like_solution_request(message):
        query_title = _query_action_title(message)
        if query_title:
            source = _human_title_from_opportunity((hits[0] or {}).get("source_file") or (hits[0] or {}).get("title")) if hits else ""
            if source and not source.lower().startswith("profile"):
                return f"{source} {query_title}".strip()[:60]
            return query_title
    titles = [str(hit.get("title") or "").strip() for hit in hits if hit.get("title")]
    if titles:
        first = _human_title_from_opportunity(titles[0])
        return f"{first} Product Opportunity"
    words = [word for word in re.findall(r"[A-Za-z0-9]+", message) if len(word) > 2]
    return " ".join(words[:5]).title() or "Workspace Product Opportunity"


def _evidence_catalog(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for evidence in artifact.get("corpus", {}).get("profile", {}).get("asset_evidence", []):
        ref = str(evidence.get("ref") or "")
        if ref and ref not in seen:
            seen.add(ref)
            items.append(
                {
                    "source_type": evidence.get("source_type", "corpus"),
                    "ref": ref,
                    "quote": _clean_text(evidence.get("quote"), 420),
                }
            )
    for hit in artifact.get("corpus", {}).get("hits", []):
        evidence = _evidence_from_hit(hit).model_dump()
        ref = str(evidence.get("ref") or "")
        if ref and ref not in seen:
            seen.add(ref)
            items.append(
                {
                    "source_type": "corpus",
                    "ref": ref,
                    "id": hit.get("id"),
                    "quote": _clean_text(evidence.get("quote"), 420),
                    "metadata": {
                        "title": hit.get("title"),
                        "source_file": hit.get("source_file"),
                        "sheet": hit.get("sheet"),
                        "row": hit.get("row"),
                    },
                }
            )
    return items


def _normalize_ref(value: Any) -> str:
    text = str(value or "").replace("\\", "/").strip().lower()
    text = re.sub(r"/+", "/", text)
    text = re.sub(r"(^|#)(?:raw_docs|external)/", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip("# ")


def _ref_tail(ref: str) -> str:
    return _normalize_ref(ref).split("#")[-1]


def _ref_source(ref: str) -> str:
    return _normalize_ref(ref).split("#")[0]


def _ref_tokens(ref: str) -> set[str]:
    normalized = _normalize_ref(ref)
    parts = [part for part in normalized.split("#") if part]
    tokens = set(parts[1:])
    tail = parts[-1] if parts else ""
    if tail:
        tokens.add(tail)
        tokens.update(re.findall(r"[a-z0-9_-]+-row-\d+", tail))
        tokens.update(re.findall(r"row-\d+", tail))
    return {token for token in tokens if len(token) > 1}


def _refs_match(candidate_ref: str, catalog_item: dict[str, Any]) -> bool:
    allowed_ref = str(catalog_item.get("ref") or "")
    candidate = _normalize_ref(candidate_ref)
    allowed = _normalize_ref(allowed_ref)
    if not candidate or not allowed:
        return False
    if candidate == allowed:
        return True
    catalog_id = _normalize_ref(catalog_item.get("id"))
    if catalog_id and candidate == catalog_id:
        return True

    candidate_source = _ref_source(candidate)
    allowed_source = _ref_source(allowed)
    source_matches = candidate_source == allowed_source or Path(candidate_source).name == Path(allowed_source).name
    if not source_matches:
        return False

    candidate_tail = _ref_tail(candidate)
    allowed_tail = _ref_tail(allowed)
    if candidate_tail and allowed_tail and candidate_tail == allowed_tail:
        return True

    candidate_tokens = _ref_tokens(candidate)
    allowed_tokens = _ref_tokens(allowed)
    if candidate_tokens & allowed_tokens:
        return True

    metadata = catalog_item.get("metadata") or {}
    sheet = _normalize_ref(metadata.get("sheet"))
    row = _normalize_ref(metadata.get("row"))
    if sheet and row and f"#{sheet}#{row}#" in f"#{candidate}#":
        return True
    return False


def _quote_matches(candidate_quote: str | None, catalog_item: dict[str, Any]) -> bool:
    quote = _clean_text(candidate_quote)
    allowed = _clean_text(catalog_item.get("quote"))
    return bool(quote and allowed and (quote in allowed or allowed in quote))


def _verify_evidence(report: FeasibilityReport, catalog: list[dict[str, Any]]) -> tuple[FeasibilityReport, list[str]]:
    warnings: list[str] = []
    cleaned_dimensions = []
    for dimension in report.dimensions:
        cleaned_evidence = []
        for evidence in dimension.evidence:
            matched = next(
                (
                    item
                    for item in catalog
                    if _refs_match(evidence.ref, item) or _quote_matches(evidence.quote, item)
                ),
                None,
            )
            if not matched:
                warnings.append(f"{dimension.name}:{evidence.ref}")
                continue
            cleaned_evidence.append(evidence)
        if cleaned_evidence:
            data = dimension.model_dump()
            data["evidence"] = [item.model_dump() for item in cleaned_evidence]
            cleaned_dimensions.append(data)
        else:
            warnings.append(f"{dimension.name}:dimension_dropped_no_verified_evidence")
    data = report.model_dump()
    data["dimensions"] = cleaned_dimensions
    return FeasibilityReport.model_validate(data), warnings


def _normalize_feasibility_confidence(report: FeasibilityReport) -> FeasibilityReport:
    rank = {"speculative": 0, "market_inferred": 1, "data_confirmed": 2}
    labels = {value: key for key, value in rank.items()}
    data = report.model_dump()
    normalized_dimensions = []
    confidences: list[str] = []
    for dimension in data.get("dimensions", []):
        evidence = dimension.get("evidence") or []
        source_types = {str(item.get("source_type") or "") for item in evidence}
        if not evidence:
            confidence = "speculative"
        elif "market" in source_types:
            confidence = "market_inferred"
        elif source_types & {"corpus", "computed"}:
            confidence = "data_confirmed"
        else:
            confidence = "speculative"
        model_confidence = str(dimension.get("confidence") or confidence)
        confidence = labels[min(rank.get(model_confidence, 0), rank.get(confidence, 0))]
        dimension["confidence"] = confidence
        confidences.append(confidence)
        normalized_dimensions.append(dimension)
    data["dimensions"] = normalized_dimensions
    if not confidences:
        data["overall_confidence"] = "speculative"
    else:
        model_overall = str(data.get("overall_confidence") or "speculative")
        weakest_dimension = labels[min(rank.get(item, 0) for item in confidences)]
        data["overall_confidence"] = labels[min(rank.get(model_overall, 0), rank.get(weakest_dimension, 0))]
    return FeasibilityReport.model_validate(data)


def _fallback_feasibility(req: ChatRequest, artifact: dict[str, Any], catalog: list[dict[str, Any]], reason: str) -> dict[str, Any]:
    first = catalog[0] if catalog else {}
    evidence = []
    if first:
        evidence.append(
            Evidence(
                source_type=first.get("source_type", "corpus"),
                ref=str(first.get("ref") or "unknown"),
                quote=_clean_text(first.get("quote"), 300) or None,
            )
        )
    report = FeasibilityReport(
        opportunity_id=str(
            (artifact.get("corpus", {}).get("opportunities") or [{}])[0].get("id")
            or "fallback-evidence-review"
        ),
        dimensions=[
            {
                "name": "asset_data",
                "score": 1,
                "rationale": "模型结构化分析未能稳定完成，系统已保留检索证据并给出保守降级结论，避免因单次证据引用或模型输出问题中断整条运行。",
                "evidence": [item.model_dump() for item in evidence],
                "confidence": "speculative",
            }
        ]
        if evidence
        else [],
        verdict="_".join(["not", "yet", "feasible"]),
        overall_confidence="speculative",
        gap_list=[
            "模型结构化可行性分析暂不可用；当前结果为基于已检索证据的保守降级结论。",
            "请复核证据引用与模型输出格式后再提升结论强度。",
        ],
    )
    report = _normalize_feasibility_opportunity(report, req, artifact)
    data = report.model_dump()
    data["_llm"] = {
        "mode": "fallback_after_agent_error",
        "response_id": None,
        "usage": {},
        "error": _clean_text(reason, 500),
    }
    return data


def _model_meta(result: dict[str, Any]) -> dict[str, Any]:
    meta = {
        "response_id": result.get("response_id"),
        "usage": result.get("usage") or {},
    }
    if result.get("mode"):
        meta["mode"] = result["mode"]
    if result.get("tool_calls") is not None:
        meta["tool_calls"] = result["tool_calls"]
    return meta


def _clean_sentence_end(text: str) -> str:
    return str(text or "").rstrip("\u3002. ")


_FEASIBILITY_PROMPT_VERSION = "df-feasibility-analyst:batch5-r1"


def _corpus_fingerprint(artifact: dict[str, Any]) -> tuple[str, str]:
    hits = artifact.get("corpus", {}).get("hits", [])
    retrieval_modes = sorted({str(hit.get("retrieval_mode") or "unknown") for hit in hits}) or ["none"]
    parts = []
    for hit in hits[:12]:
        parts.append(
            {
                "id": hit.get("id"),
                "source_file": hit.get("source_file"),
                "chunk_id": hit.get("chunk_id"),
                "retrieval_mode": hit.get("retrieval_mode"),
                "content": _clean_text(hit.get("content"), 1600),
            }
        )
    raw = json.dumps(parts, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24], "+".join(retrieval_modes)


def _feasibility_cache_key(req: ChatRequest, artifact: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    fingerprint, retrieval_mode = _corpus_fingerprint(artifact)
    query_hash = hashlib.sha256(req.message.encode("utf-8")).hexdigest()[:16]
    key = (
        "dataforge:analysis:v1"
        f":workspace={req.workspace_id}"
        f":fingerprint={fingerprint}"
        f":prompt={_FEASIBILITY_PROMPT_VERSION}"
        f":retrieval={retrieval_mode}"
        f":query={query_hash}"
    )
    return key, {
        "workspace_id": req.workspace_id,
        "chunk_fingerprint": fingerprint,
        "prompt_version": _FEASIBILITY_PROMPT_VERSION,
        "retrieval_mode": retrieval_mode,
        "query_hash": query_hash,
        "key_sample": key,
    }


def _run_feasibility_analyst(
    req: ChatRequest,
    artifact: dict[str, Any],
    audit_feedback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    catalog = _evidence_catalog(artifact)
    if not catalog:
        report = FeasibilityReport(
            opportunity_id="insufficient-evidence",
            dimensions=[],
            verdict="_".join(["not", "yet", "feasible"]),
            overall_confidence="speculative",
            gap_list=["\u5de5\u4f5c\u533a\u4e2d\u672a\u68c0\u7d22\u5230\u4e0e\u8be5\u8bf7\u6c42\u76f8\u5173\u7684\u8bc1\u636e\u3002"],
        )
        report = _normalize_feasibility_opportunity(report, req, artifact)
        data = report.model_dump()
        data["_llm"] = {"mode": "empty_evidence_deterministic", "response_id": None, "usage": {}}
        return data
    cache_key, cache_meta = _feasibility_cache_key(req, artifact)
    if not audit_feedback and os.environ.get("DF_DISABLE_REDIS_CACHE") != "1":
        cached, get_meta = cache_store.get_json(cache_key)
        if cached:
            cached["_llm"] = {
                **(cached.get("_llm") or {}),
                "mode": "redis_cached_feasibility",
                "cache": get_meta | cache_meta,
                "response_id": None,
                "usage": {},
            }
            return cached
        artifact["_feasibility_cache"] = {"get": get_meta | cache_meta}
    payload = {
        "workspace_id": req.workspace_id,
        "user_request": req.message,
        "candidate_opportunities": artifact.get("corpus", {}).get("opportunities", []),
        "evidence_catalog": catalog,
        "audit_feedback": audit_feedback,
    }
    try:
        result = run_agent(
            "df-feasibility-analyst",
            json.dumps(payload, ensure_ascii=False, indent=2),
            response_schema=FeasibilityReport.model_json_schema(),
            max_output_tokens=2800,
        )
        report = FeasibilityReport.model_validate(result["structured"])
        report, evidence_warnings = _verify_evidence(report, catalog)
        report = _normalize_feasibility_confidence(report)
        report = _normalize_feasibility_opportunity(report, req, artifact)
        data = report.model_dump()
        data["_llm"] = _model_meta(result)
        data["_llm"]["evidence_warnings"] = evidence_warnings
        if not audit_feedback and os.environ.get("DF_DISABLE_REDIS_CACHE") != "1":
            set_meta = cache_store.set_json(cache_key, {key: value for key, value in data.items() if key != "_llm"})
            data["_llm"]["cache"] = (artifact.get("_feasibility_cache") or {}).get("get", {}) | {"set": set_meta, **cache_meta}
        return data
    except Exception as exc:
        return _fallback_feasibility(req, artifact, catalog, str(exc))


def _market_lookup(category: str, keywords: list[str]) -> dict[str, Any]:
    base = os.environ.get("MCP_MARKET_URL", "https://ca-dataforge-mcp.thankfultree-c0fc8321.eastus2.azurecontainerapps.io")
    url = base.rstrip("/")
    if url.endswith("/mcp"):
        url = url.removesuffix("/mcp")
    payload = json.dumps({"category": category, "keywords": keywords}).encode("utf-8")
    req = urllib.request.Request(f"{url}/tools/market_lookup", data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _run_market_researcher(artifact: dict[str, Any]) -> dict[str, Any]:
    feasibility = artifact.get("feasibility", {})
    corpus_opportunity = ((artifact.get("corpus", {}).get("opportunities") or [{}])[0] or {})
    opportunity = str(
        feasibility.get("opportunity_id")
        or corpus_opportunity.get("id")
        or corpus_opportunity.get("title")
        or "workspace-product"
    )
    category = opportunity.replace("-", " ")
    keywords = [word for word in category.split() if len(word) > 2][:4] or ["analytics", "workflow"]
    cache_key = f"{artifact.get('workspace_id')}|{opportunity}|{' '.join(keywords)}"
    cached = _MARKET_CACHE.get(cache_key)
    now = time.monotonic()
    if cached and now < cached[0]:
        data = json.loads(json.dumps(cached[1], ensure_ascii=False))
        data.setdefault("_llm", {})["cache"] = "memory"
        return data
    competitors: list[dict[str, Any]] = []
    errors: dict[str, str] = {}

    def lookup_competitors() -> list[dict[str, Any]]:
        data = _market_lookup(category, keywords)
        raw_competitors = data.get("competitors") or data.get("results") or (data if isinstance(data, list) else [])
        return [_market_inferred_item(item) for item in raw_competitors[:5] if isinstance(item, dict)]

    web_payload = {
        "opportunity_id": opportunity,
        "category": category,
        "keywords": keywords,
        "feasibility": {key: value for key, value in feasibility.items() if key != "_llm"},
        "evidence_catalog": _evidence_catalog(artifact)[:6],
    }
    with concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="dataforge-market") as pool:
        competitor_future = pool.submit(lookup_competitors)
        web_future = pool.submit(run_market_web_research, web_payload)
        try:
            competitors = competitor_future.result()
        except Exception as exc:
            errors["market_lookup"] = _clean_text(exc, 300)
        try:
            web_result = web_future.result()
        except Exception as exc:
            web_result = {
                "external_findings": [],
                "sources": [],
                "positioning_note": "",
                "_llm": {"mode": "web_search_unavailable", "response_id": None, "usage": {}, "error": _clean_text(exc, 300)},
            }
    result = {
        "opportunity_id": opportunity,
        "competitors": competitors,
        "external_findings": web_result.get("external_findings", []),
        "sources": web_result.get("sources", []),
        "positioning_note": web_result.get("positioning_note")
        or "Compare the workspace-backed concept against adjacent commercial offerings before packaging.",
        "confidence": "market_inferred",
        "_llm": web_result.get("_llm", {}),
        "errors": errors,
    }
    _MARKET_CACHE[cache_key] = (now + _MARKET_CACHE_SECONDS, json.loads(json.dumps(result, ensure_ascii=False)))
    return result


def _market_inferred_item(item: dict[str, Any]) -> dict[str, Any]:
    data = dict(item)
    data.setdefault("confidence", "market_inferred")
    data.setdefault("source_type", "market")
    return data


def _proposal_payload(artifact: dict[str, Any]) -> dict[str, Any]:
    feasibility = artifact.get("feasibility", {})
    corpus = artifact.get("corpus", {})
    market = artifact.get("market", {})
    opportunity_id = str(feasibility.get("opportunity_id") or "workspace-product")
    title = opportunity_id.replace("-", " ").title()
    narrative = (
        artifact.get("answer", {}).get("text")
        or artifact.get("narrative")
        or artifact.get("executive_summary")
        or _final_text(
            RoutingDecision(
                workspace_id=artifact.get("workspace_id", "demo-corpus"),
                intent="product_feasibility",
                experts=[],
                output_mode="report",
                needs_clarification=False,
                reason="producer payload",
            ),
            artifact,
        )
    )
    return {
        "opportunity_id": opportunity_id,
        "title": title,
        "executive_summary": narrative,
        "feasibility": {key: value for key, value in feasibility.items() if key != "_llm"},
        "corpus_profile": corpus.get("profile", {}),
        "opportunities": corpus.get("opportunities", []),
        "market": market,
        "audit": artifact.get("audit", {}),
        "workspace_id": artifact.get("workspace_id"),
        "reference_images": artifact.get("reference_images") or [],
    }


def _run_producer(artifact: dict[str, Any]) -> dict[str, Any]:
    if not artifact.get("reference_images"):
        artifact["reference_images"] = workspace_reference_images(str(artifact.get("workspace_id") or ""))
    proposal = _proposal_payload(artifact)
    image_prompt = _image_prompt_from_proposal(proposal)
    audio_text = _concise_narration_from_proposal(proposal)
    reference_image_urls = _reference_image_urls(proposal.get("reference_images") or [])
    with concurrent.futures.ThreadPoolExecutor(max_workers=3, thread_name_prefix="dataforge-producer") as pool:
        pdf_future = pool.submit(render_pdf_report, proposal, "project_proposal")
        image_future = pool.submit(generate_image, image_prompt, "1024x1024", reference_image_urls)
        audio_future = pool.submit(narrate_summary, audio_text, "zh-CN-XiaoxiaoNeural")
        pdf = pdf_future.result()
        image = image_future.result()
        audio = audio_future.result()
    return {
        "opportunity_id": proposal["opportunity_id"],
        "proposal": proposal,
        "pdf": pdf,
        "concept_image": image,
        "audio_summary": audio,
        "artifact_urls": {
            "pdf": pdf.get("artifact_url"),
            "concept_image": image.get("artifact_url"),
            "audio_summary": audio.get("artifact_url"),
        },
    }


def _reference_image_urls(reference_images: list[dict[str, Any]]) -> list[str]:
    ordered = sorted(
        [item for item in reference_images if isinstance(item, dict)],
        key=lambda item: {"logo": 0, "activity": 1, "reference": 2}.get(str(item.get("role") or "reference"), 3),
    )
    urls: list[str] = []
    for item in ordered[:3]:
        value = item.get("blob_url") or item.get("url")
        if value:
            urls.append(str(value))
    return urls


def produce_from_existing_report(payload: dict[str, Any]) -> dict[str, Any]:
    existing = _existing_proposal(payload)
    if existing:
        return existing
    artifact = {
        "workspace_id": payload.get("workspace_id") or "demo-corpus",
        "conversation_id": payload.get("conversation_id"),
        "feasibility": payload.get("feasibility") or {},
        "corpus": payload.get("corpus") or {},
        "market": payload.get("market") or {},
        "audit": payload.get("audit") or {},
        "answer": payload.get("answer") or {},
        "reference_images": payload.get("reference_images") or [],
        "narrative": payload.get("narrative") or payload.get("text"),
    }
    return _run_producer(artifact)


def _existing_proposal(payload: dict[str, Any]) -> dict[str, Any] | None:
    direct = payload.get("proposal")
    if _complete_proposal(direct):
        return {**direct, "reused": True}
    run_id = payload.get("conversation_id") or payload.get("run_id")
    if not run_id:
        return None
    try:
        run = get_run(str(run_id))
    except FileNotFoundError:
        return None
    for source in (run.get("artifact"), (run.get("final") or {}).get("artifact")):
        proposal = (source or {}).get("proposal") if isinstance(source, dict) else None
        if _complete_proposal(proposal):
            return {**proposal, "reused": True, "reused_from_run": str(run_id)}
    return None


def _complete_proposal(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    urls = value.get("artifact_urls")
    if not isinstance(urls, dict):
        return False
    return all(urls.get(key) for key in ("pdf", "concept_image", "audio_summary"))


def _image_prompt_from_proposal(proposal: dict[str, Any]) -> str:
    feasibility = proposal.get("feasibility") or {}
    dimensions = feasibility.get("dimensions") or []
    dimension_bits = ", ".join(
        f"{item.get('name')} score {item.get('score')} confidence {item.get('confidence')}" for item in dimensions[:5]
    )
    market = proposal.get("market") or {}
    market_bits = "; ".join(str(item.get("claim") or "")[:160] for item in (market.get("external_findings") or [])[:3])
    gaps = "; ".join(str(item)[:120] for item in (feasibility.get("gap_list") or [])[:3])
    return (
        f"Opportunity: {proposal.get('title')}. Verdict: {feasibility.get('verdict')} "
        f"overall confidence {feasibility.get('overall_confidence')}. Dimensions: {dimension_bits}. "
        f"Market signals: {market_bits}. Gaps: {gaps}."
    )


def _narration_from_proposal(proposal: dict[str, Any]) -> str:
    summary = _clean_text(proposal.get("executive_summary"), 1600)
    feasibility = proposal.get("feasibility") or {}
    if not summary:
        summary = (
            f"DataForge 已完成项目建议书。结论是 {feasibility.get('verdict', 'unknown')}，"
            f"整体置信度为 {feasibility.get('overall_confidence', 'unknown')}。"
        )
    dimensions = feasibility.get("dimensions") or []
    scores = "；".join(
        f"{item.get('name')} {item.get('score')}分 {item.get('confidence')}" for item in dimensions[:5]
    )
    gaps = "；".join(_clean_sentence_end(gap) for gap in (feasibility.get("gap_list") or [])[:3])
    tail = f"\n\n维度评分：{scores or '暂无可评分维度'}。主要缺口：{gaps or '暂无明确缺口'}。"
    return _clean_text(summary + tail, 2400)


def _concise_narration_from_proposal(proposal: dict[str, Any]) -> str:
    feasibility = proposal.get("feasibility") or {}
    summary = _clean_text(proposal.get("executive_summary"), 620)
    if not summary:
        summary = (
            f"DataForge has generated a grounded project proposal. "
            f"Verdict: {feasibility.get('verdict', 'unknown')}. "
            f"Overall confidence: {feasibility.get('overall_confidence', 'unknown')}."
        )
    dimensions = feasibility.get("dimensions") or []
    scores = "; ".join(
        f"{item.get('name')} {item.get('score')}/5 {item.get('confidence')}"
        for item in dimensions[:3]
    )
    gaps = "; ".join(_clean_sentence_end(gap) for gap in (feasibility.get("gap_list") or [])[:2])
    tail = (
        f"\n\nScores: {scores or 'no scored dimensions yet'}. "
        f"Main gaps: {gaps or 'no explicit gaps'}."
    )
    return _clean_text(summary + tail, 950)


def _audit_artifact(req: ChatRequest, artifact: dict[str, Any]) -> tuple[AuditVerdict, dict[str, Any]]:
    if not _evidence_catalog(artifact):
        return (
            AuditVerdict(
                verdict="pass",
                issues=[],
                target_expert=None,
            ),
            {"mode": "empty_evidence_deterministic", "response_id": None, "usage": {}},
        )
    payload = {
        "workspace_id": req.workspace_id,
        "user_request": req.message,
        "feasibility": {key: value for key, value in artifact.get("feasibility", {}).items() if key != "_llm"},
        "evidence_verification": {
            "mode": "code_prevalidated",
            "warnings": (artifact.get("feasibility", {}).get("_llm") or {}).get("evidence_warnings", []),
        },
        "evidence_catalog": _evidence_catalog(artifact),
        "market": artifact.get("market", {}),
        "market_provenance_policy": "External market claims must remain market_inferred and must not be treated as workspace data_confirmed facts.",
    }
    result = run_agent(
        "df-auditor",
        json.dumps(payload, ensure_ascii=False, indent=2),
        response_schema=AuditVerdict.model_json_schema(),
        max_output_tokens=700,
    )
    audit = AuditVerdict.model_validate(result["structured"])
    audit, adjustment = _normalize_audit_revision_gate(audit)
    meta = _model_meta(result)
    if adjustment:
        meta["audit_adjustment"] = adjustment
    return audit, meta


def _normalize_audit_revision_gate(audit: AuditVerdict) -> tuple[AuditVerdict, str | None]:
    if audit.verdict != "revise":
        return audit, None
    blocking_terms = (
        "ref outside",
        "outside the catalog",
        "not in the catalog",
        "cannot be traced",
        "lacks evidence",
        "missing evidence",
        "no evidence item",
        "empty evidence",
        "clinical diagnosis",
        "medical decision",
        "safety-critical",
        "marked feasible",
        "too strongly",
        "invent",
        "fabricat",
    )
    joined = " ".join(audit.issues).lower()
    if any(term in joined for term in blocking_terms):
        return audit, None
    adjusted = AuditVerdict(verdict="pass", issues=audit.issues, target_expert=None)
    return adjusted, "nonblocking_audit_warnings_no_revision"


def _compact_hits(hits: list[dict[str, Any]], limit: int = 6) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for hit in hits[:limit]:
        compact.append(
            {
                "id": hit.get("id"),
                "title": hit.get("title"),
                "source_file": hit.get("source_file"),
                "sheet": hit.get("sheet"),
                "row": hit.get("row"),
                "chunk_id": hit.get("chunk_id"),
                "content": _clean_text(hit.get("content"), 700),
            }
        )
    return compact


def _answer_payload(req: ChatRequest, decision: RoutingDecision, artifact: dict[str, Any]) -> dict[str, Any]:
    corpus = artifact.get("corpus", {})
    return {
        "workspace_id": req.workspace_id,
        "user_request": req.message,
        "routing": decision.model_dump(),
        "feasibility": {
            key: value
            for key, value in artifact.get("feasibility", {}).items()
            if key != "_llm"
        },
        "audit": artifact.get("audit", {}),
        "market": artifact.get("market", {}),
        "evidence_catalog": _evidence_catalog(artifact)[:10],
        "retrieved_hits": _compact_hits(corpus.get("hits", [])),
        "corpus_profile": corpus.get("profile", {}),
        "conversation_history": artifact.get("_conversation_history", []),
        "style_nonce": uuid.uuid4().hex[:8],
    }


def _chunk_text(text: str, size: int = 28) -> list[str]:
    return [text[index : index + size] for index in range(0, len(text), size)] or [""]


def _strip_inline_refs(text: Any) -> str:
    value = _clean_text(text, 1200)
    value = re.sub(r"\[(?:raw_docs|external|profile\.json)[^\]]*\]", "", value)
    value = re.sub(r"\[[^\]]*#(?:profile|[^\]]*row[^\]]*)[^\]]*\]", "", value)
    value = re.sub(r"\b(?:data_confirmed|market_inferred|speculative)\b:?", "", value)
    value = re.sub(r"\s{2,}", " ", value)
    return value.strip()


def _build_citations(artifact: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    citations: list[dict[str, Any]] = []
    marker_by_ref: dict[str, int] = {}

    catalog = _evidence_catalog(artifact)

    def add_citation(
        ref: str,
        *,
        confidence: str = "data_confirmed",
        source_type: str = "corpus",
        matched_override: dict[str, Any] | None = None,
    ) -> int | None:
        matched = matched_override or next((item for item in catalog if _refs_match(ref, item)), None)
        if not matched and ref:
            matched = {"ref": ref, "quote": ""}
        if not matched:
            return None
        canonical_ref = str(matched.get("ref") or ref)
        normalized = _normalize_ref(canonical_ref)
        if normalized in marker_by_ref:
            return marker_by_ref[normalized]
        metadata = matched.get("metadata") or {}
        source_file = metadata.get("source_file") or _ref_source(canonical_ref)
        chunk_id = _ref_tail(canonical_ref) or matched.get("id") or canonical_ref
        marker = len(citations) + 1
        citations.append(
            {
                "marker": marker,
                "source_file": source_file,
                "chunk_id": chunk_id,
                "confidence": confidence,
                "source_type": source_type,
                "snippet": _clean_text(matched.get("quote"), 420),
            }
        )
        marker_by_ref[normalized] = marker
        return marker

    feasibility = artifact.get("feasibility") or {}
    for dimension in feasibility.get("dimensions") or []:
        confidence = str(dimension.get("confidence") or "speculative")
        for evidence in dimension.get("evidence") or []:
            ref = str(evidence.get("ref") or "")
            if ref:
                add_citation(ref, confidence=confidence, source_type=str(evidence.get("source_type") or "corpus"))

    if not citations:
        for hit in artifact.get("corpus", {}).get("hits", [])[:8]:
            evidence = _evidence_from_hit(hit)
            add_citation(
                evidence.ref,
                confidence="data_confirmed",
                source_type="corpus",
                matched_override={
                    "ref": evidence.ref,
                    "quote": evidence.quote,
                    "metadata": {
                        "title": hit.get("title"),
                        "source_file": hit.get("source_file"),
                        "sheet": hit.get("sheet"),
                        "row": hit.get("row"),
                    },
                },
            )

    for finding in artifact.get("market", {}).get("external_findings", [])[:4]:
        if not isinstance(finding, dict):
            continue
        url = str(finding.get("source_url") or "")
        claim = str(finding.get("claim") or "")
        if not url and not claim:
            continue
        key = _normalize_ref(url or claim)
        if key in marker_by_ref:
            continue
        marker = len(citations) + 1
        citations.append(
            {
                "marker": marker,
                "source_file": url or finding.get("source_title") or "market",
                "chunk_id": f"market-{marker}",
                "confidence": "market_inferred",
                "source_type": "market",
                "snippet": _clean_text(claim, 420),
                "source_url": url,
            }
        )
        marker_by_ref[key] = marker
    return citations, marker_by_ref


def _evidence_markers(evidence_items: list[dict[str, Any]], citations: list[dict[str, Any]]) -> str:
    markers: list[str] = []
    for evidence in evidence_items:
        ref = str(evidence.get("ref") or "")
        tail = _ref_tail(ref)
        source = _ref_source(ref)
        matched = next(
            (
                item
                for item in citations
                if _normalize_ref(ref)
                and (
                    (tail and _normalize_ref(item.get("chunk_id")) == tail)
                    or (tail and tail in _normalize_ref(item.get("chunk_id")))
                )
            ),
            None,
        )
        if not matched and source and not tail:
            matched = next((item for item in citations if _normalize_ref(item.get("source_file")) == source), None)
        if matched:
            marker = f"[{matched['marker']}]"
            if marker not in markers:
                markers.append(marker)
    return " ".join(markers[:3])


def _structured_answer(req: ChatRequest, decision: RoutingDecision, artifact: dict[str, Any]) -> dict[str, Any]:
    if decision.intent == "corpus_qa":
        return _structured_corpus_answer(req, artifact)
    citations, _ = _build_citations(artifact)
    feasibility = artifact.get("feasibility") or {}
    corpus = artifact.get("corpus") or {}
    market = artifact.get("market") or {}
    audit = artifact.get("audit") or {}
    opportunity = feasibility.get("opportunity_id") or ((corpus.get("opportunities") or [{}])[0] or {}).get("title") or "workspace opportunity"
    opportunity_title = _human_title_from_opportunity(opportunity)
    verdict = feasibility.get("verdict") or "unknown"
    overall = feasibility.get("overall_confidence") or "speculative"
    lines: list[str] = [
        "## 执行摘要",
        f"- 结论：**{verdict}**；整体置信度：**{overall}**。",
        f"- 本次回答基于当前工作区检索、可行性分析和审计结果生成；审计结论：{audit.get('verdict', 'not_run')}。",
        "",
        "## 机会",
        f"- 机会方向：{_strip_inline_refs(opportunity_title)}。",
    ]
    if market.get("positioning_note"):
        market_markers = " ".join(f"[{item['marker']}]" for item in citations if item.get("source_type") == "market")[:24]
        lines.append(f"- 市场补充：{_strip_inline_refs(market.get('positioning_note'))} {market_markers}".rstrip())
    lines.extend(["", "## 各维度评分与判断"])
    dimensions = feasibility.get("dimensions") or []
    if dimensions:
        for dimension in dimensions:
            markers = _evidence_markers(dimension.get("evidence") or [], citations)
            name = dimension.get("name") or "dimension"
            score = dimension.get("score", "n/a")
            confidence = dimension.get("confidence") or "speculative"
            rationale = _strip_inline_refs(dimension.get("rationale")) or "证据不足，需要补充。"
            lines.append(f"- **{name}**：{score}/5，置信度 `{confidence}`。{rationale} {markers}".rstrip())
    else:
        lines.append("- 当前没有足够的已验证证据形成维度评分。")
    lines.extend(["", "## 关键缺口"])
    gaps = feasibility.get("gap_list") or []
    if gaps:
        for gap in gaps[:5]:
            lines.append(f"- {_strip_inline_refs(gap)}")
    else:
        lines.append("- 暂无额外缺口；建议继续用新数据验证关键假设。")
    lines.extend(["", "## 建议下一步"])
    for item in _feasibility_next_steps(req, artifact, citations):
        lines.append(f"- {item}")
    if citations:
        lines.append(f"- 前端可在证据面板中查看 {len(citations)} 条结构化 citation。")
    markdown = "\n".join(lines).strip()
    markdown = _strip_raw_ref_leaks(markdown)
    return {
        "markdown": markdown,
        "citations": citations,
        "_llm": {"mode": "structured_answer_renderer", "response_id": None, "usage": {}},
    }


def _feasibility_next_steps(req: ChatRequest, artifact: dict[str, Any], citations: list[dict[str, Any]]) -> list[str]:
    dimensions = artifact.get("feasibility", {}).get("dimensions") or []
    low_dimensions = [item for item in dimensions if int(item.get("score") or 0) <= 2]
    hits = artifact.get("corpus", {}).get("hits", [])
    signals = _evidence_signals(hits, citations)
    steps: list[str] = []
    if signals:
        first = signals[0]
        second = signals[1] if len(signals) > 1 else first
        third = signals[2] if len(signals) > 2 else second
        steps.append(f"把第一轮验证场景锁定在：{first['text']}，先做小范围触达和参与记录 {first['markers']}".rstrip())
        steps.append(f"把活动、联名、赞助或权益设计绑定到：{second['text']}，避免脱离资料里的真实兴趣/痛点 {second['markers']}".rstrip())
        steps.append(f"复盘指标直接围绕：{third['text']}，记录转化、复购、成本、参与反馈和渠道来源 {third['markers']}".rstrip())
    for dimension in low_dimensions[:2]:
        name = dimension.get("name") or "低分维度"
        rationale = _strip_inline_refs(dimension.get("rationale")) or "当前证据不足"
        steps.append(f"补强 `{name}`：针对“{rationale[:72]}”补一组可量化样本或真实成本数据。")
    if not steps:
        topic = _query_action_title(req.message) or _human_title_from_opportunity(artifact.get("feasibility", {}).get("opportunity_id"))
        steps.append(f"围绕“{topic}”跑一个 1 周最小验证，至少记录用户触达、参与、转化、成本和复盘反馈。")
    return steps[:5]


def _structured_corpus_answer(req: ChatRequest, artifact: dict[str, Any]) -> dict[str, Any]:
    citations, _ = _build_citations(artifact)
    hits = artifact.get("corpus", {}).get("hits", [])
    signals = _evidence_signals(hits, citations)
    topic = _query_action_title(req.message) or "当前问题"
    lines: list[str] = [
        "## 综合回答",
        f"- 可以先围绕“{topic}”给出一版有据方案；以下内容只基于当前工作区命中的 {len(hits)} 条上传资料。",
        "",
        "## 资料里浮现的信号",
    ]
    if signals:
        for signal in signals[:6]:
            lines.append(f"- {signal['text']} {signal['markers']}".rstrip())
    else:
        lines.append("- 当前问题没有命中足够具体的工作区记录。")
    lines.extend(["", "## 建议方案"])
    if signals:
        lines.extend(_corpus_action_lines(signals, citations))
    else:
        lines.append("- 先上传或补充与该问题直接相关的记录，再让系统生成有据建议。")
    if citations:
        lines.append(f"- 本回答返回 {len(citations)} 条结构化 citation，前端可打开证据面板核验。")
    markdown = _strip_raw_ref_leaks("\n".join(lines).strip())
    return {
        "markdown": markdown,
        "citations": citations,
        "_llm": {"mode": "structured_corpus_answer_renderer", "response_id": None, "usage": {}},
    }


def _evidence_signals(hits: list[dict[str, Any]], citations: list[dict[str, Any]]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for hit in hits[:8]:
        text = _compact_hit_signal(hit)
        if not text:
            continue
        key = re.sub(r"\W+", "", text.lower())[:80]
        if key in seen:
            continue
        seen.add(key)
        markers = _evidence_markers([_evidence_from_hit(hit).model_dump()], citations)
        items.append({"text": text, "markers": markers})
    return items


def _compact_hit_signal(hit: dict[str, Any]) -> str:
    content = _clean_text(hit.get("content"), 900)
    if not content:
        return ""
    parts = [part.strip() for part in re.split(r";|\n", content) if part.strip()]
    selected: list[str] = []
    skip_names = {"collection", "id", "row", "source", "source_file", "chunk_id"}
    for part in parts:
        if ":" in part:
            name, value = part.split(":", 1)
            name = name.strip()
            value = value.strip()
            if not value or name.lower() in skip_names:
                continue
            if len(value) < 2:
                continue
            selected.append(f"{name} 是“{value[:44]}”")
        else:
            selected.append(part[:70])
        if len(selected) >= 2:
            break
    if not selected:
        return content[:100]
    text = "；".join(selected)
    return _strip_raw_ref_leaks(_strip_inline_refs(text))[:140]


def _corpus_action_lines(signals: list[dict[str, str]], citations: list[dict[str, Any]]) -> list[str]:
    first = signals[0]
    second = signals[1] if len(signals) > 1 else first
    third = signals[2] if len(signals) > 2 else second
    fourth = signals[3] if len(signals) > 3 else third
    citation_total = len(citations)
    return [
        f"- 先把方案主题绑定到最强信号：{first['text']}，不要只做泛泛传播 {first['markers']}".rstrip(),
        f"- 把第二类信号转成参与钩子或合作权益：{second['text']}，用于设计触达文案、活动机制或会员权益 {second['markers']}".rstrip(),
        f"- 用第三类信号定义最小验证指标：{third['text']}，建议记录触达人数、参与率、转化/复购、单次成本和用户反馈 {third['markers']}".rstrip(),
        f"- 复盘时对照另一条命中信号做分层：{fourth['text']}，看哪些人群、渠道或内容真正拉动结果 {fourth['markers']}".rstrip(),
        f"- 本次结构化 citations 共 {citation_total} 条；结论应随后续上传数据变化而变化。",
    ]


def _strip_raw_ref_leaks(text: str) -> str:
    text = re.sub(r"\[raw_docs/[^\]]+\]", "", text)
    text = re.sub(r"\[external/[^\]]+\]", "", text)
    text = re.sub(r"\[profile\.json[^\]]*\]", "", text)
    return text


async def _answer_event_stream(payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def worker() -> None:
        try:
            for item in stream_grounded_answer(payload):
                loop.call_soon_threadsafe(queue.put_nowait, item)
        except Exception as exc:
            loop.call_soon_threadsafe(
                queue.put_nowait,
                {"type": "error", "message": _clean_text(exc, 500)},
            )
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=worker, name="dataforge-answer-stream", daemon=True).start()
    while True:
        try:
            item = await asyncio.wait_for(queue.get(), timeout=8)
        except asyncio.TimeoutError:
            yield {
                "type": "progress",
                "agent": "df-answer-writer",
                "name": "stream_grounded_answer",
                "status": "running",
            }
            continue
        if item is None:
            break
        yield item


async def _stream_answer_frames(
    req: ChatRequest,
    decision: RoutingDecision,
    artifact: dict[str, Any],
    conversation_id: str,
    state: dict[str, Any],
) -> AsyncIterator[str]:
    answer = _structured_answer(req, decision, artifact)
    text = str(answer.get("markdown") or _final_text(decision, artifact))
    meta = dict(answer.get("_llm") or {})
    for delta in _chunk_text(text, 96):
        if delta:
            yield _frame("answer_delta", {"delta": delta}, conversation_id)
            await asyncio.sleep(0)
    state["text"] = text
    state["meta"] = meta
    artifact["answer"] = {"markdown": text, "text": text, "citations": answer.get("citations", []), "_llm": meta}
    artifact["citations"] = answer.get("citations", [])
    yield _frame("model_response", {"agent": "df-answer-writer", **meta}, conversation_id)
    return

    payload = _answer_payload(req, decision, artifact)
    parts: list[str] = []
    pending_delta = ""
    meta: dict[str, Any] = {}
    stream_error: str | None = None

    async def flush_delta() -> AsyncIterator[str]:
        nonlocal pending_delta
        if pending_delta:
            chunk = pending_delta
            pending_delta = ""
            yield _frame("answer_delta", {"delta": chunk}, conversation_id)
            await asyncio.sleep(0)

    async for item in _answer_event_stream(payload):
        item_type = item.get("type")
        if item_type == "delta":
            delta = str(item.get("delta") or "")
            if delta:
                parts.append(delta)
                pending_delta += delta
                if len(pending_delta) >= 96:
                    async for frame in flush_delta():
                        yield frame
        elif item_type == "meta":
            async for frame in flush_delta():
                yield frame
            meta = {key: value for key, value in item.items() if key != "type"}
        elif item_type == "error":
            async for frame in flush_delta():
                yield frame
            stream_error = str(item.get("message") or "answer stream failed")
        elif item_type == "progress":
            async for frame in flush_delta():
                yield frame
            yield _frame("progress", {key: value for key, value in item.items() if key != "type"}, conversation_id)
    async for frame in flush_delta():
        yield frame

    if not "".join(parts).strip():
        fallback = _final_text(decision, artifact)
        for delta in _chunk_text(fallback):
            if delta:
                parts.append(delta)
                yield _frame("answer_delta", {"delta": delta}, conversation_id)
                await asyncio.sleep(0)
        meta = {
            "mode": "answer_fallback_template",
            "response_id": None,
            "usage": {},
            "error": stream_error,
        }
    elif stream_error:
        suffix = "\n\n（流式回答中断，以上内容为已完成的模型输出；结构化产物已保留在本次结果中。）"
        for delta in _chunk_text(suffix):
            parts.append(delta)
            yield _frame("answer_delta", {"delta": delta}, conversation_id)
            await asyncio.sleep(0)
        meta = meta or {"mode": "responses_stream_partial_error", "response_id": None, "usage": {}}
        meta["error"] = stream_error

    text = "".join(parts)
    state["text"] = text
    state["meta"] = meta
    artifact["answer"] = {"text": text, "_llm": meta}
    yield _frame("model_response", {"agent": "df-answer-writer", **meta}, conversation_id)


async def _progress_frames(
    task: asyncio.Task[Any],
    conversation_id: str,
    agent: str,
    name: str,
    *,
    interval: int = 8,
    extra: dict[str, Any] | None = None,
) -> AsyncIterator[str]:
    payload = {"agent": agent, "name": name, "status": "running"}
    if extra:
        payload.update(extra)
    while not task.done():
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=interval)
        except asyncio.TimeoutError:
            yield _frame("progress", payload, conversation_id)


async def _producer_frames(artifact: dict[str, Any], conversation_id: str) -> AsyncIterator[str]:
    yield _frame("role_change", {"agent": "df-producer"}, conversation_id)
    reference_count = len(artifact.get("reference_images") or workspace_reference_images(str(artifact.get("workspace_id") or "")))
    yield _frame("tool_call", {"agent": "df-producer", "name": "render_pdf_report", "args": {"template": "project_proposal"}}, conversation_id)
    yield _frame("tool_call", {"agent": "df-producer", "name": "generate_image", "args": {"size": "1024x1024", "reference_count": reference_count}}, conversation_id)
    yield _frame("tool_call", {"agent": "df-producer", "name": "narrate_summary", "args": {"voice": "zh-CN-XiaoxiaoNeural"}}, conversation_id)
    producer_task = asyncio.create_task(run_in_threadpool(_run_producer, artifact))
    while not producer_task.done():
        try:
            artifact["proposal"] = await asyncio.wait_for(asyncio.shield(producer_task), timeout=8)
        except asyncio.TimeoutError:
            yield _frame(
                "progress",
                {"agent": "df-producer", "name": "produce_artifacts", "status": "running"},
                conversation_id,
            )
    if "proposal" not in artifact:
        artifact["proposal"] = await producer_task
    yield _frame(
        "tool_result",
        {
            "agent": "df-producer",
            "name": "render_pdf_report",
            "bytes": artifact["proposal"]["pdf"].get("bytes"),
            "mode": artifact["proposal"]["pdf"].get("mode"),
            "artifact_url": artifact["proposal"]["artifact_urls"].get("pdf"),
        },
        conversation_id,
    )
    yield _frame(
        "tool_result",
        {
            "agent": "df-producer",
            "name": "generate_image",
            "bytes": artifact["proposal"]["concept_image"].get("bytes"),
            "mode": artifact["proposal"]["concept_image"].get("mode"),
            "artifact_url": artifact["proposal"]["artifact_urls"].get("concept_image"),
        },
        conversation_id,
    )
    yield _frame(
        "tool_result",
        {
            "agent": "df-producer",
            "name": "narrate_summary",
            "bytes": artifact["proposal"]["audio_summary"].get("bytes"),
            "mode": artifact["proposal"]["audio_summary"].get("mode"),
            "artifact_url": artifact["proposal"]["artifact_urls"].get("audio_summary"),
        },
        conversation_id,
    )


def _last_assistant_text(history: list[dict[str, Any]]) -> str:
    for item in reversed(history):
        if item.get("role") == "assistant":
            text = _clean_text(item.get("text"), 1600)
            if text:
                return text
    return ""


def _lightweight_reply(req: ChatRequest, decision: RoutingDecision, history: list[dict[str, Any]]) -> dict[str, Any]:
    context = workspace_context(req.workspace_id)
    payload = {
        "workspace_id": req.workspace_id,
        "workspace_context": context,
        "current_message": req.message,
        "conversation_history": _compact_history(history),
        "routing": decision.model_dump(),
        "style_nonce": uuid.uuid4().hex[:8],
    }
    if decision.intent == "followup_edit":
        previous = _last_assistant_text(history)
        if not previous:
            return {
                "text": "我需要先有上一轮分析结果，才能按你的要求改写。请先完成一次分析，或把要改写的内容贴给我。",
                "mode": "followup_missing_context",
                "response_id": None,
                "usage": {},
            }
        payload["previous_assistant_answer"] = previous
        return run_followup_rewrite(payload)
    return run_coordinator_direct_reply(payload)


async def _emit_lightweight_final(
    req: ChatRequest,
    decision: RoutingDecision,
    artifact: dict[str, Any],
    conv_id: str,
    history: list[dict[str, Any]],
) -> AsyncIterator[str]:
    result = await run_in_threadpool(_lightweight_reply, req, decision, history)
    text = _strip_raw_ref_leaks(str(result.get("text") or "").strip() or "我可以继续帮你处理当前工作区。")
    for delta in _chunk_text(text, 96):
        yield _frame("answer_delta", {"delta": delta}, conv_id)
        await asyncio.sleep(0)
    meta = {key: result.get(key) for key in ("mode", "response_id", "usage", "error") if key in result}
    artifact["answer"] = {"markdown": text, "text": text, "citations": [], "_llm": meta}
    final_payload = {"text": text, "routing": decision.model_dump(), "artifact": artifact}
    yield _frame("model_response", {"agent": "df-coordinator", **meta}, conv_id)
    await run_in_threadpool(
        _persist_assistant_message,
        conv_id,
        req.workspace_id,
        text,
        decision.intent,
    )
    complete_run(conv_id, status=decision.intent, final=final_payload, artifact=artifact)
    yield _frame("final", final_payload, conv_id)


async def orchestrate_chat(req: ChatRequest) -> AsyncIterator[str]:
    conv_id = req.conversation_id or str(uuid.uuid4())
    history = conversation_context(req.conversation_id) if req.conversation_id else []
    working_req = _request_with_history(req, history)
    artifact: dict[str, Any] = {
        "workspace_id": req.workspace_id,
        "conversation_id": conv_id,
        "_conversation_history": _compact_history(history),
    }
    start_run(conv_id, req.workspace_id, req.message)
    yield _frame("ready", {"conversation_id": conv_id, "workspace_id": req.workspace_id}, conv_id)
    yield _frame("user", {"text": req.message}, conv_id)
    await run_in_threadpool(_persist_user_message, conv_id, req.workspace_id, req.message)

    decision, route_meta = await run_in_threadpool(_coordinator, req, history)
    artifact["routing"] = decision.model_dump()
    artifact["routing_meta"] = route_meta
    producer_requested = "df-producer" in decision.experts
    yield _frame(
        "route",
        {
            "intent": decision.intent,
            "reason": decision.reason,
            "experts": decision.experts,
            "output_mode": decision.output_mode,
            "needs_clarification": decision.needs_clarification,
            **route_meta,
        },
        conv_id,
    )
    yield _frame("plan", decision.model_dump(), conv_id)
    if decision.needs_clarification:
        guidance = await run_in_threadpool(_clarify_guidance, req, decision, conv_id)
        decision.clarifying_question = guidance.get("question")
        clarify_payload = {
            "question": decision.clarifying_question,
            "reason": decision.reason,
            "mode": guidance.get("mode"),
            "response_id": guidance.get("response_id"),
            "usage": guidance.get("usage") or {},
        }
        frame = _frame("clarify", clarify_payload, conv_id)
        await run_in_threadpool(
            _persist_assistant_message,
            conv_id,
            req.workspace_id,
            str(decision.clarifying_question or ""),
            "clarify",
        )
        complete_run(
            conv_id,
            status="clarify",
            final={"clarify": clarify_payload, "routing": decision.model_dump(), "artifact": artifact},
            artifact=artifact,
        )
        yield frame
        return

    if decision.intent in {"smalltalk_or_meta", "followup_edit"}:
        async for frame in _emit_lightweight_final(req, decision, artifact, conv_id, history):
            yield frame
        return

    if "df-corpus-analyst" in decision.experts:
        yield _frame("role_change", {"agent": "df-corpus-analyst"}, conv_id)
        yield _frame(
            "tool_call",
            {"agent": "df-corpus-analyst", "name": "search_pack_context", "args": {"workspace_id": req.workspace_id, "query": working_req.message, "top_k": 8}},
            conv_id,
        )
        artifact["corpus"] = await run_in_threadpool(_run_corpus_analyst, working_req)
        retrieval_modes = sorted({str(hit.get("retrieval_mode") or "unknown") for hit in artifact["corpus"]["hits"]})
        yield _frame(
            "tool_result",
            {
                "agent": "df-corpus-analyst",
                "name": "search_pack_context",
                "count": len(artifact["corpus"]["hits"]),
                "retrieval_modes": retrieval_modes,
            },
            conv_id,
        )

    early_market_task: asyncio.Task[Any] | None = None
    if "df-market-researcher" in decision.experts and artifact.get("corpus", {}).get("hits"):
        market_seed = {
            "workspace_id": artifact.get("workspace_id"),
            "conversation_id": artifact.get("conversation_id"),
            "corpus": artifact.get("corpus", {}),
        }
        early_market_task = asyncio.create_task(run_in_threadpool(_run_market_researcher, market_seed))

    if "df-feasibility-analyst" in decision.experts:
        yield _frame("role_change", {"agent": "df-feasibility-analyst"}, conv_id)
        try:
            feasibility_task = asyncio.create_task(run_in_threadpool(_run_feasibility_analyst, working_req, artifact))
            async for frame in _progress_frames(
                feasibility_task,
                conv_id,
                "df-feasibility-analyst",
                "analyze_feasibility",
            ):
                yield frame
            artifact["feasibility"] = await feasibility_task
            cache_info = (artifact["feasibility"].get("_llm") or {}).get("cache")
            if cache_info:
                yield _frame("cache", {"agent": "df-feasibility-analyst", **cache_info}, conv_id)
            for event, data in _agent_tool_events("df-feasibility-analyst", artifact["feasibility"].get("_llm", {})):
                yield _frame(event, data, conv_id)
            if artifact["feasibility"]["_llm"].get("response_id"):
                yield _frame("model_response", {"agent": "df-feasibility-analyst", **artifact["feasibility"]["_llm"]}, conv_id)
        except Exception as exc:
            error_payload = {"agent": "df-feasibility-analyst", "message": str(exc)}
            frame = _frame("error", error_payload, conv_id)
            await run_in_threadpool(_persist_assistant_message, conv_id, req.workspace_id, str(exc), "error")
            complete_run(conv_id, status="error", final=error_payload, artifact=artifact)
            yield frame
            return

    if "df-market-researcher" in decision.experts:
        yield _frame("role_change", {"agent": "df-market-researcher"}, conv_id)
        feasibility = artifact.get("feasibility", {})
        category = str(feasibility.get("opportunity_id") or "workspace product").replace("-", " ")
        if feasibility.get("_llm", {}).get("mode") == "empty_evidence_deterministic":
            artifact["market"] = {
                "competitors": [],
                "positioning_note": "Market lookup skipped because no workspace evidence matched the request.",
                "mode": "skipped_empty_evidence",
            }
            yield _frame("tool_result", {"agent": "df-market-researcher", "name": "market_lookup", "count": 0, "skipped": "empty_evidence"}, conv_id)
            category = ""
        if not category:
            pass
        else:
            yield _frame(
                "tool_call",
                {"agent": "df-market-researcher", "name": "market_lookup", "args": {"category": category, "keywords": category.split()[:4]}},
                conv_id,
            )
            yield _frame(
                "tool_call",
                {
                    "agent": "df-market-researcher",
                    "name": "foundry_native_web_search",
                    "args": {
                        "query": category,
                        "preferred": "bing_grounding",
                        "fallbacks": ["web_search_preview", "web_search"],
                        "confidence": "market_inferred",
                    },
                },
                conv_id,
            )
            try:
                market_task = early_market_task or asyncio.create_task(run_in_threadpool(_run_market_researcher, artifact))
                async for frame in _progress_frames(
                    market_task,
                    conv_id,
                    "df-market-researcher",
                    "research_market",
                ):
                    yield frame
                artifact["market"] = await market_task
                yield _frame("tool_result", {"agent": "df-market-researcher", "name": "market_lookup", "count": len(artifact["market"]["competitors"])}, conv_id)
                yield _frame(
                    "tool_result",
                    {
                        "agent": "df-market-researcher",
                        "name": "foundry_native_web_search",
                        "count": len(artifact["market"].get("external_findings", [])),
                        "sources": artifact["market"].get("sources", []),
                        "mode": artifact["market"].get("_llm", {}).get("mode"),
                        "verification": artifact["market"].get("_llm", {}).get("verification"),
                        "error": artifact["market"].get("_llm", {}).get("error"),
                    },
                    conv_id,
                )
            except Exception as exc:
                artifact["market"] = {"competitors": [], "positioning_note": "Market lookup unavailable.", "error": str(exc)}
                yield _frame("tool_result", {"agent": "df-market-researcher", "name": "market_lookup", "count": 0, "error": str(exc)}, conv_id)
                yield _frame("tool_result", {"agent": "df-market-researcher", "name": "foundry_native_web_search", "count": 0, "error": str(exc)}, conv_id)

    if "df-auditor" in decision.experts:
        yield _frame("role_change", {"agent": "df-auditor"}, conv_id)
        try:
            audit_task = asyncio.create_task(run_in_threadpool(_audit_artifact, working_req, artifact))
            async for frame in _progress_frames(
                audit_task,
                conv_id,
                "df-auditor",
                "audit_report",
            ):
                yield frame
            audit, audit_meta = await audit_task
            for event, data in _agent_tool_events("df-auditor", audit_meta):
                yield _frame(event, data, conv_id)
            if audit_meta.get("response_id"):
                yield _frame("model_response", {"agent": "df-auditor", **audit_meta}, conv_id)
        except Exception as exc:
            error_payload = {"agent": "df-auditor", "message": str(exc)}
            frame = _frame("error", error_payload, conv_id)
            await run_in_threadpool(_persist_assistant_message, conv_id, req.workspace_id, str(exc), "error")
            complete_run(conv_id, status="error", final=error_payload, artifact=artifact)
            yield frame
            return
        yield _frame("audit", audit.model_dump(), conv_id)
    else:
        audit = AuditVerdict(verdict="pass", issues=[], target_expert=None)

    if audit.verdict == "revise" and audit.target_expert == "df-feasibility-analyst":
        yield _frame("role_change", {"agent": "df-feasibility-analyst", "revision": 1}, conv_id)
        try:
            previous_feasibility = artifact.get("feasibility") or {}
            revision_task = asyncio.create_task(run_in_threadpool(_run_feasibility_analyst, working_req, artifact, audit.model_dump()))
            async for frame in _progress_frames(
                revision_task,
                conv_id,
                "df-feasibility-analyst",
                "revise_feasibility",
                extra={"revision": 1},
            ):
                yield frame
            revision_feasibility = await revision_task
            revision_meta = revision_feasibility.get("_llm", {})
            for event, data in _agent_tool_events("df-feasibility-analyst", revision_meta):
                yield _frame(event, {"revision": 1, **data}, conv_id)
            if revision_meta.get("response_id"):
                yield _frame("model_response", {"agent": "df-feasibility-analyst", "revision": 1, **revision_meta}, conv_id)
            if (
                revision_meta.get("mode") == "fallback_after_agent_error"
                and previous_feasibility
                and previous_feasibility.get("_llm", {}).get("mode") != "fallback_after_agent_error"
            ):
                preserved = {**previous_feasibility}
                preserved_meta = dict(preserved.get("_llm") or {})
                warnings = list(preserved_meta.get("evidence_warnings") or [])
                warnings.append("revision_failed_preserved_previous_feasibility")
                preserved_meta["evidence_warnings"] = warnings
                preserved_meta["revision_warning"] = _clean_text(revision_meta.get("error"), 500)
                preserved["_llm"] = preserved_meta
                artifact["feasibility"] = preserved
            else:
                artifact["feasibility"] = revision_feasibility
            yield _frame("role_change", {"agent": "df-auditor", "revision": 1}, conv_id)
            revision_audit_task = asyncio.create_task(run_in_threadpool(_audit_artifact, working_req, artifact))
            async for frame in _progress_frames(
                revision_audit_task,
                conv_id,
                "df-auditor",
                "audit_revision",
                extra={"revision": 1},
            ):
                yield frame
            audit, audit_meta = await revision_audit_task
            for event, data in _agent_tool_events("df-auditor", audit_meta):
                yield _frame(event, {"revision": 1, **data}, conv_id)
            if audit_meta.get("response_id"):
                yield _frame("model_response", {"agent": "df-auditor", "revision": 1, **audit_meta}, conv_id)
            yield _frame("audit", audit.model_dump(), conv_id)
        except Exception as exc:
            yield _frame("error", {"agent": "revision-loop", "message": str(exc)}, conv_id)
            artifact["audit"] = audit.model_dump()
            answer_state: dict[str, Any] = {}
            async for frame in _stream_answer_frames(working_req, decision, artifact, conv_id, answer_state):
                yield frame
            if producer_requested:
                async for frame in _producer_frames(artifact, conv_id):
                    yield frame
            summary = answer_state.get("text") or _final_text(decision, artifact)
            final_payload = {"text": summary, "routing": decision.model_dump(), "artifact": artifact}
            frame = _frame("final", final_payload, conv_id)
            await run_in_threadpool(
                _persist_assistant_message,
                conv_id,
                req.workspace_id,
                summary,
                _artifact_verdict(artifact, "completed_with_revision_error"),
            )
            complete_run(conv_id, status="completed_with_revision_error", final=final_payload, artifact=artifact)
            yield frame
            return

    artifact["audit"] = audit.model_dump()
    answer_state: dict[str, Any] = {}
    async for frame in _stream_answer_frames(working_req, decision, artifact, conv_id, answer_state):
        yield frame
    if producer_requested:
        async for frame in _producer_frames(artifact, conv_id):
            yield frame
    summary = answer_state.get("text") or _final_text(decision, artifact)
    final_payload = {"text": summary, "routing": decision.model_dump(), "artifact": artifact}
    frame = _frame("final", final_payload, conv_id)
    await run_in_threadpool(
        _persist_assistant_message,
        conv_id,
        req.workspace_id,
        summary,
        _artifact_verdict(artifact, "completed"),
    )
    complete_run(conv_id, status="completed", final=final_payload, artifact=artifact)
    yield frame


def _final_text(decision: RoutingDecision, artifact: dict[str, Any]) -> str:
    if decision.intent == "corpus_qa":
        hits = artifact.get("corpus", {}).get("hits", [])
        titles = ", ".join(hit.get("title", "Untitled") for hit in hits[:3])
        return f"\u8d44\u6599\u68c0\u7d22\u5b8c\u6210\uff0c\u547d\u4e2d {len(hits)} \u6761\u3002\u4e3b\u8981\u6765\u6e90\uff1a{titles}\u3002"
    feasibility = artifact.get("feasibility", {})
    if feasibility.get("_llm", {}).get("mode") == "empty_evidence_deterministic":
        gaps = "; ".join(_clean_sentence_end(gap) for gap in feasibility.get("gap_list", [])[:2])
        gap_text = _clean_sentence_end(gaps or "\u8bc1\u636e\u4e0d\u8db3")
        return (
            f"\u8d44\u6599\u68c0\u7d22\u5b8c\u6210\uff0c\u4f46\u672a\u627e\u5230\u8db3\u4ee5\u652f\u6491\u8be5\u8bf7\u6c42\u7684\u5de5\u4f5c\u533a\u8bc1\u636e\u3002"
            f"\u7ed3\u8bba\uff1a{feasibility.get('verdict', 'unknown')}\uff1b\u4e3b\u8981\u7f3a\u53e3\uff1a{gap_text}\u3002"
        )
    verdict = feasibility.get("verdict", "unknown")
    gaps = "; ".join(_clean_sentence_end(gap) for gap in feasibility.get("gap_list", [])[:2])
    confidence = feasibility.get("overall_confidence", "unknown")
    return (
        f"\u5df2\u5b8c\u6210\u8bed\u6599\u68c0\u7d22\u3001\u6a21\u578b\u53ef\u884c\u6027\u8bc4\u4f30\u548c\u5ba1\u8ba1\u3002"
        f"\u7ed3\u8bba\uff1a{verdict}\uff1b\u7f6e\u4fe1\u5ea6\uff1a{confidence}\u3002"
        f"\u4e3b\u8981\u7f3a\u53e3\uff1a{gaps or '\u8bf7\u67e5\u770b\u7ed3\u6784\u5316\u8bc1\u636e'}\u3002"
    )
