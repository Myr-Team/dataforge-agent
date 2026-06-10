from __future__ import annotations

import json
import os
import urllib.request
import uuid
from collections.abc import AsyncIterator
from typing import Any

try:
    from .chat_loop_primitives import sse
    from .rag import search
    from .schemas import AuditVerdict, ChatRequest, Evidence, RoutingDecision
    from .tools.generate_image import generate_image
    from .tools.narrate_summary import narrate_summary
    from .tools.render_pdf import render_pdf_report
except ImportError:
    from chat_loop_primitives import sse
    from rag import search
    from schemas import AuditVerdict, ChatRequest, Evidence, RoutingDecision
    from tools.generate_image import generate_image
    from tools.narrate_summary import narrate_summary
    from tools.render_pdf import render_pdf_report


PRODUCT_TERMS = ("product", "saas", "business", "proposal", "package", "产品", "商业", "项目书", "方案", "变现")
FULL_PACKAGE_TERMS = ("pdf", "image", "audio", "voice", "概念图", "语音", "三件套", "full package")
CORPUS_QA_TERMS = ("what is in", "what do the docs", "资料里", "文档里", "只问资料", "有哪些资料", "包含什么")
VAGUE_TERMS = ("something", "anything", "东西", "随便", "搞一个")
MEDICAL_TERMS = ("diagnosis", "treatment", "clinical", "medical", "health", "诊断", "治疗", "医疗", "健康")


def _contains(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in terms)


def _evidence_from_hit(hit: dict[str, Any]) -> Evidence:
    quote = str(hit.get("content", "")).strip().replace("\n", " ")
    return Evidence(
        source_type="corpus",
        ref=f"{hit.get('source_file', 'unknown')}#{hit.get('chunk_id', hit.get('id', 'chunk'))}",
        quote=quote[:260] or None,
    )


def _coordinator(req: ChatRequest) -> RoutingDecision:
    message = req.message.strip()
    wants_full_package = _contains(message, FULL_PACKAGE_TERMS)
    wants_product = wants_full_package or _contains(message, PRODUCT_TERMS)
    asks_corpus_only = _contains(message, CORPUS_QA_TERMS) and not wants_product
    is_vague = _contains(message, VAGUE_TERMS) and not (_contains(message, MEDICAL_TERMS) or _contains(message, PRODUCT_TERMS))

    if is_vague or len(message) < 6:
        return RoutingDecision(
            workspace_id=req.workspace_id,
            intent="clarify",
            experts=[],
            output_mode="chat",
            needs_clarification=True,
            clarifying_question="请补充你想面向哪个用户、做哪类产品或只想查询资料内容。",
            reason="The request is too broad to route without inventing a target outcome.",
        )

    if asks_corpus_only:
        return RoutingDecision(
            workspace_id=req.workspace_id,
            intent="corpus_qa",
            experts=["df-corpus-analyst"],
            output_mode="chat",
            needs_clarification=False,
            reason="The user is asking what the workspace documents contain.",
        )

    experts = ["df-corpus-analyst", "df-feasibility-analyst", "df-market-researcher", "df-auditor"]
    if wants_full_package:
        experts.insert(-1, "df-producer")
    return RoutingDecision(
        workspace_id=req.workspace_id,
        intent="product_feasibility",
        experts=experts,
        output_mode="full_package" if wants_full_package else "report",
        needs_clarification=False,
        reason="The user is asking DataForge to identify or evaluate product opportunities.",
    )


def _run_corpus_analyst(req: ChatRequest) -> dict[str, Any]:
    hits = search(req.workspace_id, req.message, 5)
    evidence = [_evidence_from_hit(hit).model_dump() for hit in hits[:3]]
    opportunities: list[dict[str, Any]] = []
    if evidence:
        opportunities.append(
            {
                "id": "outdoor-analytics-copilot",
                "title": "Outdoor Analytics Copilot",
                "description": "A SaaS assistant that turns sensor manuals, feedback, and operations notes into coaching and product-packaging insights.",
                "supporting_evidence": evidence,
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


def _run_feasibility_analyst(req: ChatRequest, artifact: dict[str, Any], revision: bool = False) -> dict[str, Any]:
    is_medical = _contains(req.message, MEDICAL_TERMS)
    evidence = artifact.get("corpus", {}).get("profile", {}).get("asset_evidence", [])
    if is_medical:
        if not revision:
            return {
                "draft_issue": "Medical diagnosis needs clinical validation, consent, and labeled outcomes before feasibility can pass.",
                "requires_revision": True,
                "opportunity_id": "medical-diagnosis",
            }
        medical_hits = search(req.workspace_id, "medical consent clinical validation labeled outcomes", 3)
        medical_evidence = [_evidence_from_hit(hit).model_dump() for hit in medical_hits[:2]] or evidence
        return {
            "opportunity_id": "medical-diagnosis",
            "verdict": "not_yet_feasible",
            "overall_confidence": "data_confirmed" if medical_evidence else "speculative",
            "gap_list": [
                "No clinical validation evidence in the corpus.",
                "No labeled diagnosis outcomes in the corpus.",
                "Consent material is insufficient for a regulated medical product.",
            ],
            "dimensions": [
                {
                    "name": "asset_data",
                    "score": 1,
                    "rationale": "The corpus can discuss health-adjacent data, but does not support diagnosis claims.",
                    "evidence": medical_evidence,
                    "confidence": "data_confirmed" if medical_evidence else "speculative",
                }
            ],
        }

    if not evidence:
        return {
            "opportunity_id": "unknown",
            "verdict": "not_yet_feasible",
            "overall_confidence": "speculative",
            "gap_list": ["No corpus evidence found."],
            "dimensions": [],
        }
    return {
        "opportunity_id": "outdoor-analytics-copilot",
        "verdict": "conditional",
        "overall_confidence": "data_confirmed",
        "gap_list": ["Needs pricing validation and integration scoping."],
        "dimensions": [
            {
                "name": "asset_data",
                "score": 4,
                "rationale": "The corpus contains manuals, feedback, sensor fields, and operations notes that can ground a focused analytics SaaS.",
                "evidence": evidence,
                "confidence": "data_confirmed",
            },
            {
                "name": "technical",
                "score": 3,
                "rationale": "Search, summarization, and packaged reporting are straightforward; live device integrations remain a later step.",
                "evidence": evidence[:1],
                "confidence": "data_confirmed",
            },
        ],
    }


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


def _run_market_researcher() -> dict[str, Any]:
    data = _market_lookup("outdoor analytics", ["coaching", "sensor", "training"])
    competitors = data.get("competitors") or data.get("results") or (data if isinstance(data, list) else [])
    return {
        "opportunity_id": "outdoor-analytics-copilot",
        "competitors": competitors[:5],
        "positioning_note": "Position around corpus-grounded product packaging rather than raw activity tracking.",
    }


def _proposal_payload(artifact: dict[str, Any]) -> dict[str, Any]:
    feasibility = artifact.get("feasibility", {})
    corpus = artifact.get("corpus", {})
    market = artifact.get("market", {})
    return {
        "opportunity_id": feasibility.get("opportunity_id", "outdoor-analytics-copilot"),
        "title": "Outdoor Analytics Copilot",
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
        "feasibility": feasibility,
        "corpus_profile": corpus.get("profile", {}),
        "opportunities": corpus.get("opportunities", []),
        "market": market,
    }


def _run_producer(artifact: dict[str, Any]) -> dict[str, Any]:
    proposal = _proposal_payload(artifact)
    pdf = render_pdf_report(proposal, "project_proposal")
    image_prompt = (
        "A product concept dashboard for Outdoor Analytics Copilot: sensor evidence, "
        "coaching insights, feasibility scoring, and enterprise proposal packaging."
    )
    image = generate_image(image_prompt, "1024x1024")
    audio_text = (
        "DataForge has generated a grounded product package for Outdoor Analytics Copilot. "
        f"The feasibility verdict is {proposal.get('feasibility', {}).get('verdict', 'conditional')}. "
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


def _audit_artifact(req: ChatRequest, artifact: dict[str, Any]) -> AuditVerdict:
    feasibility = artifact.get("feasibility", {})
    if feasibility.get("requires_revision"):
        return AuditVerdict(
            verdict="revise",
            issues=[feasibility.get("draft_issue", "Feasibility draft requires revision.")],
            target_expert="df-feasibility-analyst",
        )
    if _contains(req.message, MEDICAL_TERMS) and feasibility.get("verdict") != "not_yet_feasible":
        return AuditVerdict(
            verdict="revise",
            issues=["Medical product was not honestly marked not_yet_feasible."],
            target_expert="df-feasibility-analyst",
        )
    missing = [
        dim.get("name", "dimension")
        for dim in feasibility.get("dimensions", [])
        if not dim.get("evidence")
    ]
    if missing:
        return AuditVerdict(
            verdict="revise",
            issues=[f"Feasibility dimensions missing evidence: {', '.join(missing)}"],
            target_expert="df-feasibility-analyst",
        )
    return AuditVerdict(verdict="pass", issues=[], target_expert=None)


async def orchestrate_chat(req: ChatRequest) -> AsyncIterator[str]:
    conv_id = req.conversation_id or str(uuid.uuid4())
    artifact: dict[str, Any] = {"workspace_id": req.workspace_id, "conversation_id": conv_id}
    yield sse("ready", {"conversation_id": conv_id, "workspace_id": req.workspace_id})
    yield sse("user", {"text": req.message})

    decision = _coordinator(req)
    yield sse("plan", decision.model_dump())
    if decision.needs_clarification:
        yield sse("clarify", {"question": decision.clarifying_question, "reason": decision.reason})
        return

    if "df-corpus-analyst" in decision.experts:
        yield sse("role_change", {"agent": "df-corpus-analyst"})
        yield sse("tool_call", {"agent": "df-corpus-analyst", "name": "search_pack_context", "args": {"workspace_id": req.workspace_id, "query": req.message, "top_k": 5}})
        artifact["corpus"] = _run_corpus_analyst(req)
        yield sse("tool_result", {"agent": "df-corpus-analyst", "name": "search_pack_context", "count": len(artifact["corpus"]["hits"])})

    if "df-feasibility-analyst" in decision.experts:
        yield sse("role_change", {"agent": "df-feasibility-analyst"})
        artifact["feasibility"] = _run_feasibility_analyst(req, artifact)

    if "df-market-researcher" in decision.experts and not _contains(req.message, MEDICAL_TERMS):
        yield sse("role_change", {"agent": "df-market-researcher"})
        yield sse("tool_call", {"agent": "df-market-researcher", "name": "market_lookup", "args": {"category": "outdoor analytics", "keywords": ["coaching", "sensor", "training"]}})
        try:
            artifact["market"] = _run_market_researcher()
            yield sse("tool_result", {"agent": "df-market-researcher", "name": "market_lookup", "count": len(artifact["market"]["competitors"])})
        except Exception as exc:
            artifact["market"] = {"competitors": [], "positioning_note": "Market lookup unavailable.", "error": str(exc)}
            yield sse("tool_result", {"agent": "df-market-researcher", "name": "market_lookup", "count": 0, "error": str(exc)})

    if "df-producer" in decision.experts:
        yield sse("role_change", {"agent": "df-producer"})
        yield sse("tool_call", {"agent": "df-producer", "name": "render_pdf_report", "args": {"template": "project_proposal"}})
        yield sse("tool_call", {"agent": "df-producer", "name": "generate_image", "args": {"size": "1024x1024"}})
        yield sse("tool_call", {"agent": "df-producer", "name": "narrate_summary", "args": {"voice": "zh-CN-XiaoxiaoNeural"}})
        artifact["proposal"] = _run_producer(artifact)
        yield sse(
            "tool_result",
            {
                "agent": "df-producer",
                "name": "render_pdf_report",
                "bytes": artifact["proposal"]["pdf"].get("bytes"),
                "artifact_url": artifact["proposal"]["artifact_urls"].get("pdf"),
            },
        )
        yield sse(
            "tool_result",
            {
                "agent": "df-producer",
                "name": "generate_image",
                "bytes": artifact["proposal"]["concept_image"].get("bytes"),
                "artifact_url": artifact["proposal"]["artifact_urls"].get("concept_image"),
            },
        )
        yield sse(
            "tool_result",
            {
                "agent": "df-producer",
                "name": "narrate_summary",
                "bytes": artifact["proposal"]["audio_summary"].get("bytes"),
                "mode": artifact["proposal"]["audio_summary"].get("mode"),
                "artifact_url": artifact["proposal"]["artifact_urls"].get("audio_summary"),
            },
        )

    yield sse("role_change", {"agent": "df-auditor"})
    audit = _audit_artifact(req, artifact)
    yield sse("audit", audit.model_dump())
    if audit.verdict == "revise" and audit.target_expert == "df-feasibility-analyst":
        yield sse("role_change", {"agent": "df-feasibility-analyst", "revision": 1})
        artifact["feasibility"] = _run_feasibility_analyst(req, artifact, revision=True)
        yield sse("role_change", {"agent": "df-auditor", "revision": 1})
        audit = _audit_artifact(req, artifact)
        yield sse("audit", audit.model_dump())

    artifact["audit"] = audit.model_dump()
    summary = _final_text(decision, artifact)
    yield sse("final", {"text": summary, "routing": decision.model_dump(), "artifact": artifact})


def _final_text(decision: RoutingDecision, artifact: dict[str, Any]) -> str:
    if decision.intent == "corpus_qa":
        hits = artifact.get("corpus", {}).get("hits", [])
        titles = ", ".join(hit.get("title", "Untitled") for hit in hits[:3])
        return f"资料检索完成，命中 {len(hits)} 条。主要来源：{titles}。"
    feasibility = artifact.get("feasibility", {})
    verdict = feasibility.get("verdict", "unknown")
    if verdict == "not_yet_feasible":
        return "当前资料不足以支撑这个产品方向，尤其不能支持医疗诊断类承诺；建议先补齐验证、 consent 和标注结果。"
    return "已完成产品机会分流、语料证据抽取、可行性评估和审计；建议优先推进 Outdoor Analytics Copilot 的受控试点。"
