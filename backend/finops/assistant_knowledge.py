from __future__ import annotations

from dataclasses import dataclass
from typing import Final


_VERSION: Final = "finops-knowledge-v1"


@dataclass(frozen=True)
class _KnowledgeEntry:
    id: str
    title: str
    definition: str
    formula: str
    judgement_boundary: str
    metric_ids: tuple[str, ...]
    policy_types: tuple[str, ...]
    terms: tuple[str, ...]

    def public(self) -> dict[str, str]:
        return {
            "id": self.id,
            "version": _VERSION,
            "title": self.title,
            "definition": self.definition,
            "formula": self.formula,
            "judgement_boundary": self.judgement_boundary,
        }


_CATALOG: Final = (
    _KnowledgeEntry(
        id="estimated-cost",
        title="请求级估算成本",
        definition="按模型价目修订版和已记录 Token 分类计算的运营估算值。",
        formula="输入、输出、缓存输入和推理 Token 分别乘以对应单价后汇总。",
        judgement_boundary="估算成本用于归因和预算判断，不等于云平台实际账单。",
        metric_ids=("estimated_cost", "cost", "operations_overview"),
        policy_types=("daily_cost_budget",),
        terms=("成本", "费用", "花费", "构成", "贡献", "模型"),
    ),
    _KnowledgeEntry(
        id="price-coverage",
        title="价目覆盖率",
        definition="可可靠匹配已激活价目修订版的请求占全部请求的比例。",
        formula="已计价请求数 ÷ 全部请求数。",
        judgement_boundary="未计价请求保留为未计价，不以零成本代替。",
        metric_ids=("estimated_cost", "unpriced_requests", "operations_overview"),
        policy_types=("unpriced_requests",),
        terms=("价目", "计价", "覆盖", "可信", "未计价", "价格"),
    ),
    _KnowledgeEntry(
        id="cache-savings",
        title="缓存节省",
        definition="相同分析复用结果后减少的输入处理、等待时间和估算成本。",
        formula="可避免 Token × 对应输入单价；只有可复核的命中证据才计入。",
        judgement_boundary="命中率不是节省金额，需结合可避免 Token 和模型价目解释。",
        metric_ids=("cache_hit_rate", "estimated_cost", "operations_overview"),
        policy_types=("cache_hit_rate",),
        terms=("缓存", "命中", "节省", "复用", "避免"),
    ),
    _KnowledgeEntry(
        id="token-usage",
        title="Token 用量",
        definition="模型调用中已记录的输入、输出、缓存输入和推理 Token。",
        formula="总 Token 为存在分类的加总；缺失分类保持未记录。",
        judgement_boundary="Token 用量不能在缺少对应价目时直接推导成本。",
        metric_ids=("tokens_total", "operations_overview", "estimated_cost"),
        policy_types=("token_spike",),
        terms=("token", "用量", "输入", "输出", "推理"),
    ),
    _KnowledgeEntry(
        id="latency-percentiles",
        title="响应延迟分位数",
        definition="P50 表示中位体验，P95 表示大多数请求之外的长尾体验。",
        formula="对筛选范围内已记录延迟排序后取对应分位点。",
        judgement_boundary="样本不足时不生成稳定的异常判断。",
        metric_ids=("p50_latency", "p95_latency", "operations_overview"),
        policy_types=("p95_latency",),
        terms=("延迟", "响应", "p50", "p95", "慢请求", "长尾"),
    ),
    _KnowledgeEntry(
        id="budget-threshold",
        title="预算阈值",
        definition="对成员或工作区的估算支出设置提醒区间。",
        formula="预算消耗率为已计价估算成本 ÷ 预算金额。",
        judgement_boundary="提醒不自动限流，也不替代财务账单审核。",
        metric_ids=("budget", "estimated_cost", "operations_overview"),
        policy_types=("daily_cost_budget",),
        terms=("预算", "阈值", "提醒", "成员", "消耗"),
    ),
    _KnowledgeEntry(
        id="roi-boundary",
        title="ROI 证据边界",
        definition="将情景测算、已观测使用和已验证业务结果分开呈现。",
        formula="ROI 为月度净收益 ÷ 月度总成本；回收期使用实施投入 ÷ 月度净收益。",
        judgement_boundary="情景参数不代表已实现收益，只有经复核的业务结果可标记已验证。",
        metric_ids=("roi_ratio", "monthly_net_benefit", "operations_overview"),
        policy_types=(),
        terms=("roi", "收益", "回收", "价值", "投入", "测算"),
    ),
    _KnowledgeEntry(
        id="risk-evidence",
        title="风险与证据",
        definition="风险项由规则、样本量、代表请求和置信度共同构成。",
        formula="规则只在满足最小样本量后比较观测值与阈值。",
        judgement_boundary="AI 解释和治理草案不等于批准或执行生产变更。",
        metric_ids=("risk_summary", "error_rate", "operations_overview"),
        policy_types=(
            "error_rate",
            "p95_latency",
            "daily_cost_budget",
            "token_spike",
            "apim_coverage",
            "unpriced_requests",
            "cache_hit_rate",
        ),
        terms=("风险", "异常", "证据", "规则", "置信", "治理"),
    ),
)


def retrieve_finops_knowledge(
    *,
    metric_id: str,
    policy_type: str | None,
    question: str,
    limit: int = 4,
) -> list[dict[str, str]]:
    """Return closed explanatory entries; user text is never copied to output."""
    safe_limit = max(1, min(int(limit), 4))
    metric = str(metric_id or "").strip().lower()[:96]
    policy = str(policy_type or "").strip().lower()[:96]
    normalized_question = " ".join(str(question or "").lower().split())[:600]
    ranked: list[tuple[int, int, _KnowledgeEntry]] = []
    for index, entry in enumerate(_CATALOG):
        score = 0
        if metric and metric in entry.metric_ids:
            score += 12
        if policy and policy in entry.policy_types:
            score += 10
        score += sum(2 for term in entry.terms if term in normalized_question)
        ranked.append((score, -index, entry))
    ranked.sort(reverse=True, key=lambda item: (item[0], item[1]))
    positive = [entry for score, _index, entry in ranked if score > 0]
    selected = positive[:safe_limit]
    if not selected:
        selected = [
            next(entry for entry in _CATALOG if entry.id == "risk-evidence"),
        ]
    return [entry.public() for entry in selected]
