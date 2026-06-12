from __future__ import annotations

from typing import Any

try:
    from .schemas import RoutingDecision
except ImportError:
    from schemas import RoutingDecision


PRODUCT_TERMS = ("product", "saas", "business", "proposal", "package", "\u4ea7\u54c1", "\u5546\u4e1a", "\u9879\u76ee\u4e66", "\u65b9\u6848", "\u53d8\u73b0")
FULL_PACKAGE_TERMS = ("pdf", "image", "audio", "voice", "full package", "\u6982\u5ff5\u56fe", "\u8bed\u97f3", "\u4e09\u4ef6\u5957")
CORPUS_QA_TERMS = ("what is in", "what do the docs", "documents contain", "docs contain", "main evidence", "\u8d44\u6599\u91cc", "\u6587\u6863\u91cc", "\u53ea\u95ee\u8d44\u6599", "\u6709\u54ea\u4e9b\u8d44\u6599", "\u5305\u542b\u4ec0\u4e48")
VAGUE_TERMS = ("something", "anything", "whatever", "some idea", "\u4e1c\u897f", "\u968f\u4fbf", "\u641e\u4e00\u4e2a")
SOLUTION_TERMS = ("how", "plan", "strategy", "recommend", "advice", "campaign", "\u600e\u4e48", "\u5982\u4f55", "\u65b9\u6848", "\u5efa\u8bae", "\u63a8\u8350", "\u7b56\u7565", "\u6d3b\u52a8", "\u63a8\u5e7f")


def _contains(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in terms)


def deterministic_route(message: str, workspace_id: str = "demo-corpus", metadata: dict[str, Any] | None = None) -> RoutingDecision:
    text = message.strip()
    wants_full_package = _contains(text, FULL_PACKAGE_TERMS)
    wants_product = wants_full_package or _contains(text, PRODUCT_TERMS)
    wants_solution = _contains(text, SOLUTION_TERMS)
    asks_corpus_only = _contains(text, CORPUS_QA_TERMS) and not wants_product
    is_vague = _contains(text, VAGUE_TERMS) and not wants_product
    doc_count = int((metadata or {}).get("doc_count", 1))

    if is_vague or len(text) < 6 or doc_count <= 0:
        return RoutingDecision(
            workspace_id=workspace_id,
            intent="clarify",
            experts=[],
            output_mode="chat",
            needs_clarification=True,
            clarifying_question="我会根据当前工作区生成中文引导，请补充目标用户、产品范围或你想先做资料问答。",
            reason="请求过短、过泛，或当前工作区缺少可检索资料。",
        )

    if asks_corpus_only:
        return RoutingDecision(
            workspace_id=workspace_id,
            intent="corpus_qa",
            experts=["df-corpus-analyst"],
            output_mode="chat",
            needs_clarification=False,
            reason="The user is asking what the workspace documents contain.",
        )

    if wants_solution and doc_count > 0 and not wants_full_package:
        return RoutingDecision(
            workspace_id=workspace_id,
            intent="corpus_qa",
            experts=["df-corpus-analyst"],
            output_mode="chat",
            needs_clarification=False,
            reason="The user is asking for grounded advice that can be answered from workspace evidence.",
        )

    experts = ["df-corpus-analyst", "df-feasibility-analyst", "df-market-researcher", "df-auditor"]
    if wants_full_package:
        experts.insert(-1, "df-producer")
    return RoutingDecision(
        workspace_id=workspace_id,
        intent="feasibility_analysis",
        experts=experts,
        output_mode="full_package" if wants_full_package else "report",
        needs_clarification=False,
        reason="The user is asking DataForge to identify or evaluate product opportunities.",
    )
