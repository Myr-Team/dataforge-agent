from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Final


_VERSION: Final = "finops-knowledge-v2"
_ROOT: Final = Path(__file__).resolve().parent / "data" / "knowledge"
_HEADING: Final = re.compile(r"^##\s+(?P<title>.+?)\s+\{#(?P<id>[a-z0-9-]+)\}\s*$")


@dataclass(frozen=True)
class _Document:
    document_id: str
    filename: str
    title: str
    metric_ids: tuple[str, ...]
    policy_types: tuple[str, ...]
    terms: tuple[str, ...]


@dataclass(frozen=True)
class _Chunk:
    id: str
    document: _Document
    section: str
    excerpt: str

    def public(self) -> dict[str, str]:
        formula = _prefixed_line(self.excerpt, "公式：")
        boundary = _prefixed_line(self.excerpt, "判断边界：") or _prefixed_line(
            self.excerpt,
            "适用边界：",
        )
        return {
            "id": self.id,
            "version": _VERSION,
            "title": self.document.title,
            "document_id": self.document.document_id,
            "section": self.section,
            "citation": f"内部知识：{self.document.title} / {self.section}",
            "excerpt": self.excerpt[:1000],
            "definition": self.excerpt.split("。", 1)[0][:320],
            "formula": formula[:320],
            "judgement_boundary": boundary[:400],
        }


_DOCUMENTS: Final = (
    _Document(
        document_id="cost-pricing-handbook",
        filename="cost-pricing-handbook.zh-CN.md",
        title="DataForge 成本与计价方法",
        metric_ids=("estimated_cost", "cost", "unpriced_requests", "operations_overview"),
        policy_types=("daily_cost_budget", "unpriced_requests"),
        terms=("成本", "费用", "价格", "价目", "计价", "未计价", "预算", "模型"),
    ),
    _Document(
        document_id="cache-performance-playbook",
        filename="cache-performance-playbook.zh-CN.md",
        title="DataForge 缓存与性能优化手册",
        metric_ids=("cache_hit_rate", "cache_estimated_savings", "cache_avoided_tokens", "p95_latency"),
        policy_types=("cache_hit_rate", "p95_latency"),
        terms=("缓存", "命中", "节省", "Token", "延迟", "性能", "复用"),
    ),
    _Document(
        document_id="roi-regression-guide",
        filename="roi-regression-guide.zh-CN.md",
        title="DataForge ROI 与回归证据指南",
        metric_ids=("roi", "roi_ratio", "monthly_net_benefit", "roi_forecast_validation"),
        policy_types=(),
        terms=("ROI", "收益", "回归", "MSE", "RMSE", "R2", "预测", "回收", "证据"),
    ),
    _Document(
        document_id="risk-evidence-playbook",
        filename="risk-evidence-playbook.zh-CN.md",
        title="DataForge 风险判定与证据手册",
        metric_ids=("risk_summary", "error_rate", "token_spike", "operations_overview"),
        policy_types=(
            "error_rate",
            "p95_latency",
            "daily_cost_budget",
            "token_spike",
            "apim_coverage",
            "unpriced_requests",
            "cache_hit_rate",
        ),
        terms=("风险", "异常", "证据", "规则", "阈值", "样本", "治理", "扫描"),
    ),
)

_SECTION_HINTS: Final = {
    "estimated-cost": (("estimated_cost", "cost", "operations_overview"), ("daily_cost_budget",), ("成本", "费用", "模型")),
    "price-coverage": (("unpriced_requests", "estimated_cost"), ("unpriced_requests",), ("未计价", "价目", "覆盖", "价格")),
    "budget-threshold": (("budget", "estimated_cost"), ("daily_cost_budget",), ("预算", "阈值", "提醒")),
    "cache-savings": (("cache_hit_rate", "cache_estimated_savings", "cache_avoided_tokens"), ("cache_hit_rate",), ("缓存", "命中", "节省", "Token")),
    "latency-percentiles": (("p95_latency", "p50_latency"), ("p95_latency",), ("延迟", "P95", "长尾")),
    "roi-boundary": (("roi", "roi_ratio", "monthly_net_benefit"), (), ("ROI", "收益", "回收", "验证")),
    "roi-regression": (("roi_forecast_validation",), (), ("回归", "MSE", "RMSE", "R2", "预测")),
    "risk-evidence": (("risk_summary", "error_rate"), ("error_rate", "token_spike", "apim_coverage"), ("风险", "异常", "证据", "样本")),
    "governance-boundary": (("risk_summary", "operations_overview"), (), ("治理", "批准", "执行", "草案")),
}


def _prefixed_line(text: str, prefix: str) -> str:
    for line in text.splitlines():
        clean = line.strip()
        if clean.startswith(prefix):
            return clean[len(prefix):].strip()
    return ""


@lru_cache(maxsize=1)
def _catalog() -> tuple[_Chunk, ...]:
    chunks: list[_Chunk] = []
    for document in _DOCUMENTS:
        text = (_ROOT / document.filename).read_text(encoding="utf-8")
        current_title = ""
        current_id = ""
        body: list[str] = []

        def append_current() -> None:
            if not current_id:
                return
            excerpt = "\n".join(line.strip() for line in body if line.strip()).strip()
            if excerpt:
                chunks.append(_Chunk(current_id, document, current_title, excerpt[:1800]))

        for line in text.splitlines():
            match = _HEADING.match(line.strip())
            if match:
                append_current()
                current_title = match.group("title").strip()
                current_id = match.group("id")
                body = []
                continue
            if current_id and not line.startswith("# "):
                body.append(line)
        append_current()
    return tuple(chunks)


def retrieve_finops_knowledge(
    *,
    metric_id: str,
    policy_type: str | None,
    question: str,
    limit: int = 4,
) -> list[dict[str, str]]:
    """Retrieve bounded, allowlisted internal guidance without creating evidence."""
    safe_limit = max(1, min(int(limit), 4))
    metric = str(metric_id or "").strip().lower()[:96]
    policy = str(policy_type or "").strip().lower()[:96]
    normalized_question = " ".join(str(question or "").split())[:600]
    lower_question = normalized_question.lower()
    ranked: list[tuple[int, int, _Chunk]] = []
    for index, chunk in enumerate(_catalog()):
        document = chunk.document
        hint_metrics, hint_policies, hint_terms = _SECTION_HINTS.get(chunk.id, ((), (), ()))
        score = 0
        if metric and metric in hint_metrics:
            score += 20
        elif metric and metric in document.metric_ids:
            score += 8
        if policy and policy in hint_policies:
            score += 18
        elif policy and policy in document.policy_types:
            score += 7
        score += sum(4 for term in hint_terms if term.lower() in lower_question)
        score += sum(1 for term in document.terms if term.lower() in lower_question)
        ranked.append((score, -index, chunk))
    ranked.sort(reverse=True, key=lambda item: (item[0], item[1]))
    selected = [chunk for score, _index, chunk in ranked if score > 0][:safe_limit]
    if not selected:
        selected = [next(chunk for chunk in _catalog() if chunk.id == "risk-evidence")]
    return [chunk.public() for chunk in selected]
