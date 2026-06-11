from __future__ import annotations

import json
import os
import re
import urllib.request
import uuid
from collections.abc import AsyncIterator
from typing import Any

from starlette.concurrency import run_in_threadpool

try:
    from .chat_loop_primitives import sse
    from .foundry_client import run_agent
    from .rag import search
    from .router import deterministic_route
    from .schemas import AuditVerdict, ChatRequest, Evidence, FeasibilityReport, RoutingDecision
    from .tracing import trace_event
    from .tools.generate_image import generate_image
    from .tools.narrate_summary import narrate_summary
    from .tools.render_pdf import render_pdf_report
except ImportError:
    from chat_loop_primitives import sse
    from foundry_client import run_agent
    from rag import search
    from router import deterministic_route
    from schemas import AuditVerdict, ChatRequest, Evidence, FeasibilityReport, RoutingDecision
    from tracing import trace_event
    from tools.generate_image import generate_image
    from tools.narrate_summary import narrate_summary
    from tools.render_pdf import render_pdf_report


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
    return deterministic_route(req.message, req.workspace_id, {"doc_count": 8})


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


def _verify_evidence(report: FeasibilityReport, catalog: list[dict[str, Any]]) -> None:
    allowed_refs = {str(item.get("ref")) for item in catalog if item.get("ref")}
    allowed_quotes = [_clean_text(item.get("quote")) for item in catalog if _clean_text(item.get("quote"))]
    failures: list[str] = []
    for dimension in report.dimensions:
        for evidence in dimension.evidence:
            ref_ok = evidence.ref in allowed_refs
            quote = _clean_text(evidence.quote)
            quote_ok = bool(quote) and any(quote in allowed or allowed in quote for allowed in allowed_quotes)
            if not ref_ok and not quote_ok:
                failures.append(f"{dimension.name}:{evidence.ref}")
    if failures:
        raise ValueError("Model cited evidence outside the retrieved catalog: " + ", ".join(failures))


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
    result = run_agent(
        "df-feasibility-analyst",
        json.dumps(payload, ensure_ascii=False, indent=2),
        response_schema=FeasibilityReport.model_json_schema(),
    )
    report = FeasibilityReport.model_validate(result["structured"])
    _verify_evidence(report, catalog)
    data = report.model_dump()
    data["_llm"] = _model_meta(result)
    return data


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
    data = _market_lookup(category, keywords)
    competitors = data.get("competitors") or data.get("results") or (data if isinstance(data, list) else [])
    return {
        "opportunity_id": opportunity,
        "competitors": competitors[:5],
        "positioning_note": "Compare the workspace-backed concept against adjacent commercial offerings before packaging.",
    }


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
    }
    result = run_agent(
        "df-auditor",
        json.dumps(payload, ensure_ascii=False, indent=2),
        response_schema=AuditVerdict.model_json_schema(),
        max_output_tokens=1000,
    )
    audit = AuditVerdict.model_validate(result["structured"])
    return audit, _model_meta(result)


async def orchestrate_chat(req: ChatRequest) -> AsyncIterator[str]:
    conv_id = req.conversation_id or str(uuid.uuid4())
    artifact: dict[str, Any] = {"workspace_id": req.workspace_id, "conversation_id": conv_id}
    yield _frame("ready", {"conversation_id": conv_id, "workspace_id": req.workspace_id}, conv_id)
    yield _frame("user", {"text": req.message}, conv_id)

    decision = _coordinator(req)
    yield _frame("plan", decision.model_dump(), conv_id)
    if decision.needs_clarification:
        yield _frame("clarify", {"question": decision.clarifying_question, "reason": decision.reason}, conv_id)
        return

    if "df-corpus-analyst" in decision.experts:
        yield _frame("role_change", {"agent": "df-corpus-analyst"}, conv_id)
        yield _frame(
            "tool_call",
            {"agent": "df-corpus-analyst", "name": "search_pack_context", "args": {"workspace_id": req.workspace_id, "query": req.message, "top_k": 8}},
            conv_id,
        )
        artifact["corpus"] = await run_in_threadpool(_run_corpus_analyst, req)
        yield _frame("tool_result", {"agent": "df-corpus-analyst", "name": "search_pack_context", "count": len(artifact["corpus"]["hits"])}, conv_id)

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
            try:
                artifact["market"] = await run_in_threadpool(_run_market_researcher, artifact)
                yield _frame("tool_result", {"agent": "df-market-researcher", "name": "market_lookup", "count": len(artifact["market"]["competitors"])}, conv_id)
            except Exception as exc:
                artifact["market"] = {"competitors": [], "positioning_note": "Market lookup unavailable.", "error": str(exc)}
                yield _frame("tool_result", {"agent": "df-market-researcher", "name": "market_lookup", "count": 0, "error": str(exc)}, conv_id)

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
            artifact["feasibility"] = await run_in_threadpool(_run_feasibility_analyst, req, artifact, audit.model_dump())
            for event, data in _agent_tool_events("df-feasibility-analyst", artifact["feasibility"].get("_llm", {})):
                yield _frame(event, {"revision": 1, **data}, conv_id)
            if artifact["feasibility"]["_llm"].get("response_id"):
                yield _frame("model_response", {"agent": "df-feasibility-analyst", "revision": 1, **artifact["feasibility"]["_llm"]}, conv_id)
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
            summary = _final_text(decision, artifact)
            yield _frame("final", {"text": summary, "routing": decision.model_dump(), "artifact": artifact}, conv_id)
            return

    artifact["audit"] = audit.model_dump()
    summary = _final_text(decision, artifact)
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
