from __future__ import annotations

import hashlib
import math
from typing import Any, Mapping


_SEVERITY_IMPACT = {"critical": "high", "warning": "medium", "info": "low"}
_IMPACT_SCORE = {"high": 3, "medium": 2, "low": 1}
_CONFIDENCE_SCORE = {"high": 3, "medium": 2, "low": 1}
_EFFORT_SCORE = {"low": 3, "medium": 2, "high": 1}
_EFFORT = {
    "cache_hit_rate": "medium",
    "daily_cost_budget": "medium",
    "unpriced_requests": "low",
    "apim_coverage": "medium",
    "p95_latency": "high",
    "error_rate": "high",
    "token_spike": "medium",
}
_SAVINGS_RATE = {
    "daily_cost_budget": 0.10,
    "cache_hit_rate": 0.05,
    "token_spike": 0.08,
}


def build_opportunity_queue(
    *,
    anomalies: list[Mapping[str, Any]],
    recommendations: list[Mapping[str, Any]],
    priced_cost: float | None,
    priced_coverage_pct: float | None,
) -> list[dict[str, Any]]:
    recommendations_by_policy = {
        str(item.get("policy_type") or ""): str(item.get("recommendation") or "")
        for item in recommendations
    }
    cost_is_usable = (
        _finite_nonnegative(priced_cost)
        and _finite_nonnegative(priced_coverage_pct)
        and float(priced_coverage_pct) >= 95
    )
    result = []
    for anomaly in anomalies:
        policy_type = str(anomaly.get("policy_type") or "unknown")[:64]
        sample_count = max(0, int(anomaly.get("sample_count") or 0))
        evidence_state = str(anomaly.get("evidence_state") or "unavailable")
        confidence = (
            "high"
            if sample_count >= 50 and evidence_state in {"observed", "complete"}
            else "medium"
            if sample_count >= 20
            else "low"
        )
        impact = _SEVERITY_IMPACT.get(str(anomaly.get("severity") or ""), "low")
        savings_rate = _SAVINGS_RATE.get(policy_type)
        estimated_savings = (
            round(float(priced_cost) * savings_rate, 8)
            if cost_is_usable and savings_rate is not None
            else None
        )
        anomaly_id = str(anomaly.get("anomaly_id") or "")
        evidence_refs = [
            str(value).strip()
            for value in anomaly.get("evidence_refs") or []
            if str(value).strip()
        ][:5]
        digest = hashlib.sha256(f"{anomaly_id}:{policy_type}".encode("utf-8")).hexdigest()[:16]
        result.append({
            "opportunity_id": f"opp_{digest}",
            "anomaly_id": anomaly_id or None,
            "policy_type": policy_type,
            "title": _title(policy_type),
            "recommendation": recommendations_by_policy.get(policy_type) or _recommendation(policy_type),
            "impact": impact,
            "confidence": confidence,
            "effort": _EFFORT.get(policy_type, "medium"),
            "queue_state": "ready" if sample_count >= 20 else "observing",
            "sample_count": sample_count,
            "evidence_state": evidence_state,
            "evidence_refs": evidence_refs,
            "estimated_savings": estimated_savings,
            "currency": "USD" if estimated_savings is not None else None,
            "action_status": "suggested",
        })
    return sorted(
        result,
        key=lambda item: (
            -_IMPACT_SCORE[item["impact"]],
            -_CONFIDENCE_SCORE[item["confidence"]],
            -_EFFORT_SCORE[item["effort"]],
            item["opportunity_id"],
        ),
    )


def _finite_nonnegative(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number >= 0


def _title(policy_type: str) -> str:
    return {
        "daily_cost_budget": "预算消耗优化",
        "cache_hit_rate": "缓存效率优化",
        "token_spike": "Token 用量优化",
        "unpriced_requests": "计价覆盖补齐",
        "apim_coverage": "统一入口治理覆盖",
        "p95_latency": "响应时延优化",
        "error_rate": "调用成功率改善",
    }.get(policy_type, "运营指标优化")


def _recommendation(policy_type: str) -> str:
    return {
        "daily_cost_budget": "复核主要成本贡献来源与模型路由。",
        "cache_hit_rate": "检查缓存资格、键策略与失效窗口。",
        "token_spike": "对比上一周期定位 Token 增长来源。",
        "unpriced_requests": "补齐模型价目表与价格版本。",
        "apim_coverage": "核对未经过统一入口的调用路径。",
        "p95_latency": "定位慢请求与模型路由瓶颈。",
        "error_rate": "按错误类别和模型定位失败来源。",
    }.get(policy_type, "结合证据复核运营指标。")
