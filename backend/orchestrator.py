from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import urllib.request
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from starlette.concurrency import run_in_threadpool

try:
    from .chat_loop_primitives import sse
    from .foundry_client import run_agent, run_coordinator_guidance, run_market_web_research, stream_grounded_answer
    from .rag import search
    from .router import deterministic_route
    from .schemas import AuditVerdict, ChatRequest, Evidence, FeasibilityReport, RoutingDecision
    from .tracing import trace_event
    from .tools.generate_image import generate_image
    from .tools.narrate_summary import narrate_summary
    from .tools.render_pdf import render_pdf_report
    from .workspace_store import workspace_context
except ImportError:
    from chat_loop_primitives import sse
    from foundry_client import run_agent, run_coordinator_guidance, run_market_web_research, stream_grounded_answer
    from rag import search
    from router import deterministic_route
    from schemas import AuditVerdict, ChatRequest, Evidence, FeasibilityReport, RoutingDecision
    from tracing import trace_event
    from tools.generate_image import generate_image
    from tools.narrate_summary import narrate_summary
    from tools.render_pdf import render_pdf_report
    from workspace_store import workspace_context


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


def _contains(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in terms)


def _clean_text(value: Any, limit: int | None = None) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit] if limit else text


def _slug(value: str, fallback: str = "generated-data-product") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:72] or fallback


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
    trace_event(event, data, conversation_id)
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


def _coordinator(req: ChatRequest) -> RoutingDecision:
    context = workspace_context(req.workspace_id)
    return deterministic_route(req.message, req.workspace_id, {"doc_count": context.get("doc_count", 0)})


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
    titles = [str(hit.get("title") or "").strip() for hit in hits if hit.get("title")]
    if titles:
        first = titles[0].replace("_", " ").replace("-", " ").title()
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
        data = report.model_dump()
        data["_llm"] = {"mode": "empty_evidence_deterministic", "response_id": None, "usage": {}}
        return data
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
        )
        report = FeasibilityReport.model_validate(result["structured"])
        report, evidence_warnings = _verify_evidence(report, catalog)
        data = report.model_dump()
        data["_llm"] = _model_meta(result)
        data["_llm"]["evidence_warnings"] = evidence_warnings
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
    opportunity = str(feasibility.get("opportunity_id") or "workspace-product")
    category = opportunity.replace("-", " ")
    keywords = [word for word in category.split() if len(word) > 2][:4] or ["analytics", "workflow"]
    competitors: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    try:
        data = _market_lookup(category, keywords)
        raw_competitors = data.get("competitors") or data.get("results") or (data if isinstance(data, list) else [])
        competitors = [_market_inferred_item(item) for item in raw_competitors[:5] if isinstance(item, dict)]
    except Exception as exc:
        errors["market_lookup"] = _clean_text(exc, 300)

    web_payload = {
        "opportunity_id": opportunity,
        "category": category,
        "keywords": keywords,
        "feasibility": {key: value for key, value in feasibility.items() if key != "_llm"},
        "evidence_catalog": _evidence_catalog(artifact)[:6],
    }
    try:
        web_result = run_market_web_research(web_payload)
    except Exception as exc:
        web_result = {
            "external_findings": [],
            "sources": [],
            "positioning_note": "",
            "_llm": {"mode": "web_search_unavailable", "response_id": None, "usage": {}, "error": _clean_text(exc, 300)},
        }
    return {
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
    return {
        "opportunity_id": opportunity_id,
        "title": title,
        "executive_summary": _final_text(
            RoutingDecision(
                workspace_id=artifact.get("workspace_id", "demo-corpus"),
                intent="product_feasibility",
                experts=[],
                output_mode="report",
                needs_clarification=False,
                reason="producer payload",
            ),
            artifact,
        ),
        "feasibility": {key: value for key, value in feasibility.items() if key != "_llm"},
        "corpus_profile": corpus.get("profile", {}),
        "opportunities": corpus.get("opportunities", []),
        "market": market,
    }


def _run_producer(artifact: dict[str, Any]) -> dict[str, Any]:
    proposal = _proposal_payload(artifact)
    pdf = render_pdf_report(proposal, "project_proposal")
    image_prompt = (
        "A product concept dashboard grounded in workspace evidence, with feasibility scoring, "
        "source-backed insights, and executive proposal packaging."
    )
    image = generate_image(image_prompt, "1024x1024")
    audio_text = (
        f"DataForge generated a grounded product package for {proposal.get('title', 'the workspace opportunity')}. "
        f"The feasibility verdict is {proposal.get('feasibility', {}).get('verdict', 'unknown')}. "
        "The package includes a proposal PDF, a concept image, and this spoken summary."
    )
    audio = narrate_summary(audio_text, "zh-CN-XiaoxiaoNeural")
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


def produce_from_existing_report(payload: dict[str, Any]) -> dict[str, Any]:
    artifact = {
        "workspace_id": payload.get("workspace_id") or "demo-corpus",
        "conversation_id": payload.get("conversation_id"),
        "feasibility": payload.get("feasibility") or {},
        "corpus": payload.get("corpus") or {},
        "market": payload.get("market") or {},
    }
    return _run_producer(artifact)


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
        "evidence_catalog": _evidence_catalog(artifact),
        "market": artifact.get("market", {}),
        "market_provenance_policy": "External market claims must remain market_inferred and must not be treated as workspace data_confirmed facts.",
    }
    result = run_agent(
        "df-auditor",
        json.dumps(payload, ensure_ascii=False, indent=2),
        response_schema=AuditVerdict.model_json_schema(),
        max_output_tokens=1000,
    )
    audit = AuditVerdict.model_validate(result["structured"])
    return audit, _model_meta(result)


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
        "style_nonce": uuid.uuid4().hex[:8],
    }


def _chunk_text(text: str, size: int = 28) -> list[str]:
    return [text[index : index + size] for index in range(0, len(text), size)] or [""]


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
        item = await queue.get()
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
    payload = _answer_payload(req, decision, artifact)
    parts: list[str] = []
    meta: dict[str, Any] = {}
    stream_error: str | None = None

    async for item in _answer_event_stream(payload):
        item_type = item.get("type")
        if item_type == "delta":
            delta = str(item.get("delta") or "")
            if delta:
                parts.append(delta)
                yield _frame("answer_delta", {"delta": delta}, conversation_id)
        elif item_type == "meta":
            meta = {key: value for key, value in item.items() if key != "type"}
        elif item_type == "error":
            stream_error = str(item.get("message") or "answer stream failed")

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


async def orchestrate_chat(req: ChatRequest) -> AsyncIterator[str]:
    conv_id = req.conversation_id or str(uuid.uuid4())
    artifact: dict[str, Any] = {"workspace_id": req.workspace_id, "conversation_id": conv_id}
    yield _frame("ready", {"conversation_id": conv_id, "workspace_id": req.workspace_id}, conv_id)
    yield _frame("user", {"text": req.message}, conv_id)

    decision = _coordinator(req)
    yield _frame("plan", decision.model_dump(), conv_id)
    if decision.needs_clarification:
        guidance = await run_in_threadpool(_clarify_guidance, req, decision, conv_id)
        decision.clarifying_question = guidance.get("question")
        yield _frame(
            "clarify",
            {
                "question": decision.clarifying_question,
                "reason": decision.reason,
                "mode": guidance.get("mode"),
                "response_id": guidance.get("response_id"),
                "usage": guidance.get("usage") or {},
            },
            conv_id,
        )
        return

    if "df-corpus-analyst" in decision.experts:
        yield _frame("role_change", {"agent": "df-corpus-analyst"}, conv_id)
        yield _frame(
            "tool_call",
            {"agent": "df-corpus-analyst", "name": "search_pack_context", "args": {"workspace_id": req.workspace_id, "query": req.message, "top_k": 8}},
            conv_id,
        )
        artifact["corpus"] = await run_in_threadpool(_run_corpus_analyst, req)
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

    if "df-feasibility-analyst" in decision.experts:
        yield _frame("role_change", {"agent": "df-feasibility-analyst"}, conv_id)
        try:
            artifact["feasibility"] = await run_in_threadpool(_run_feasibility_analyst, req, artifact)
            for event, data in _agent_tool_events("df-feasibility-analyst", artifact["feasibility"].get("_llm", {})):
                yield _frame(event, data, conv_id)
            if artifact["feasibility"]["_llm"].get("response_id"):
                yield _frame("model_response", {"agent": "df-feasibility-analyst", **artifact["feasibility"]["_llm"]}, conv_id)
        except Exception as exc:
            yield _frame("error", {"agent": "df-feasibility-analyst", "message": str(exc)}, conv_id)
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
                artifact["market"] = await run_in_threadpool(_run_market_researcher, artifact)
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

    if "df-producer" in decision.experts:
        yield _frame("role_change", {"agent": "df-producer"}, conv_id)
        yield _frame("tool_call", {"agent": "df-producer", "name": "render_pdf_report", "args": {"template": "project_proposal"}}, conv_id)
        yield _frame("tool_call", {"agent": "df-producer", "name": "generate_image", "args": {"size": "1024x1024"}}, conv_id)
        yield _frame("tool_call", {"agent": "df-producer", "name": "narrate_summary", "args": {"voice": "zh-CN-XiaoxiaoNeural"}}, conv_id)
        artifact["proposal"] = await run_in_threadpool(_run_producer, artifact)
        yield _frame(
            "tool_result",
            {
                "agent": "df-producer",
                "name": "render_pdf_report",
                "bytes": artifact["proposal"]["pdf"].get("bytes"),
                "artifact_url": artifact["proposal"]["artifact_urls"].get("pdf"),
            },
            conv_id,
        )
        yield _frame(
            "tool_result",
            {
                "agent": "df-producer",
                "name": "generate_image",
                "bytes": artifact["proposal"]["concept_image"].get("bytes"),
                "artifact_url": artifact["proposal"]["artifact_urls"].get("concept_image"),
            },
            conv_id,
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
            conv_id,
        )

    yield _frame("role_change", {"agent": "df-auditor"}, conv_id)
    try:
        audit, audit_meta = await run_in_threadpool(_audit_artifact, req, artifact)
        for event, data in _agent_tool_events("df-auditor", audit_meta):
            yield _frame(event, data, conv_id)
        if audit_meta.get("response_id"):
            yield _frame("model_response", {"agent": "df-auditor", **audit_meta}, conv_id)
    except Exception as exc:
        yield _frame("error", {"agent": "df-auditor", "message": str(exc)}, conv_id)
        return
    yield _frame("audit", audit.model_dump(), conv_id)

    if audit.verdict == "revise" and audit.target_expert == "df-feasibility-analyst":
        yield _frame("role_change", {"agent": "df-feasibility-analyst", "revision": 1}, conv_id)
        try:
            previous_feasibility = artifact.get("feasibility") or {}
            revision_feasibility = await run_in_threadpool(_run_feasibility_analyst, req, artifact, audit.model_dump())
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
            audit, audit_meta = await run_in_threadpool(_audit_artifact, req, artifact)
            for event, data in _agent_tool_events("df-auditor", audit_meta):
                yield _frame(event, {"revision": 1, **data}, conv_id)
            if audit_meta.get("response_id"):
                yield _frame("model_response", {"agent": "df-auditor", "revision": 1, **audit_meta}, conv_id)
            yield _frame("audit", audit.model_dump(), conv_id)
        except Exception as exc:
            yield _frame("error", {"agent": "revision-loop", "message": str(exc)}, conv_id)
            artifact["audit"] = audit.model_dump()
            answer_state: dict[str, Any] = {}
            async for frame in _stream_answer_frames(req, decision, artifact, conv_id, answer_state):
                yield frame
            summary = answer_state.get("text") or _final_text(decision, artifact)
            yield _frame("final", {"text": summary, "routing": decision.model_dump(), "artifact": artifact}, conv_id)
            return

    artifact["audit"] = audit.model_dump()
    answer_state: dict[str, Any] = {}
    async for frame in _stream_answer_frames(req, decision, artifact, conv_id, answer_state):
        yield frame
    summary = answer_state.get("text") or _final_text(decision, artifact)
    yield _frame("final", {"text": summary, "routing": decision.model_dump(), "artifact": artifact}, conv_id)


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
