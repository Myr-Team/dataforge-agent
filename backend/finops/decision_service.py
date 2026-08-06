from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from .decision_models import DecisionStatement, RiskDecision, RoiDecision


_LEVEL = {"low": 1, "medium": 2, "high": 3}
_COMPLETE_STAGE = {
    "investment": {"observed", "estimated", "verified", "complete"},
    "usage": {"observed", "verified", "complete"},
    "output": {"observed", "verified", "complete"},
    "outcome": {"verified", "complete"},
}
_DOMAIN = {
    "daily_cost_budget": "cost", "token_spike": "cost", "unpriced_requests": "cost",
    "p95_latency": "experience", "error_rate": "experience", "cache_hit_rate": "efficiency",
    "apim_coverage": "governance",
}
_CAPABILITY_EXPLANATION = {
    "平台自动确认": ["调用、Token 与模型成本", "成功率、时延与缓存节省", "运行、分析与产物关联"],
    "业务侧补充验证": ["节省工时与小时价值", "避免损失或新增收益", "结果负责人审核与确认"],
}
_SAFE_REF_PREFIXES = ("req_", "run-", "outcome-", "outcome_", "event_")
_SAFE_POLICIES = frozenset((*_DOMAIN, "cache_hit_rate", "error_rate", "token_spike", "daily_cost_budget", "unpriced_requests", "apim_coverage"))
_ANOMALY_ACTIONS = {
    "open": ["acknowledge", "suppress"],
    "acknowledged": ["suppress"],
    "suppressed": [],
    "resolved": [],
}
_SCENARIO_RESULT_KEYS = (
    "monthly_benefit", "monthly_total_cost", "monthly_net_benefit", "roi_ratio", "payback_months", "formula_revision",
)


def _refs(values: Any, limit: int = 20) -> list[str]:
    return [
        ref for value in values or []
        if (ref := str(value).strip()) and ref.startswith(_SAFE_REF_PREFIXES)
    ][:limit]


def _safe_scenario(item: Mapping[str, Any]) -> dict[str, Any]:
    result = item.get("result") if isinstance(item.get("result"), Mapping) else {}
    scenario_id = str(item.get("scenario_id") or "").strip()
    return {
        "scenario_id": scenario_id[:80] if scenario_id.replace("-", "").replace("_", "").isalnum() else None,
        "status": str(item.get("status") or "unavailable") if str(item.get("status") or "") in {"estimated", "observed", "verified", "partial", "unavailable", "not_recorded"} else "unavailable",
        "result": {key: result.get(key) for key in _SCENARIO_RESULT_KEYS},
    }


def _safe_verified(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    return {key: raw.get(key) for key in ("status", "value", "currency", "net_value")}


def _safe_trend(item: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item.get(key) for key in ("label", "value", "unit", "currency", "status", "period")}


def _safe_policy(value: Any) -> str:
    policy = str(value or "")
    return policy if policy in _SAFE_POLICIES else "other"


def _safe_identifier(value: Any, limit: int = 128) -> str | None:
    clean = str(value or "").strip()
    return clean[:limit] if clean and clean.replace("-", "").replace("_", "").isalnum() else None


def _safe_revision(value: Any) -> str | None:
    clean = str(value or "").strip()
    if not clean or len(clean) > 128:
        return None
    return clean if all(character.isalnum() or character in "._:-" for character in clean) else None


def _bounded_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _safe_opportunity(item: Mapping[str, Any], *, domain: str, refs: list[str]) -> dict[str, Any]:
    opportunity_id = _safe_identifier(item.get("opportunity_id"), 80)
    anomaly_id = _safe_identifier(item.get("anomaly_id"))
    anomaly_status = str(item.get("anomaly_status") or "")
    if anomaly_status not in _ANOMALY_ACTIONS:
        anomaly_status = ""
    return {
        "opportunity_id": opportunity_id,
        "anomaly_id": anomaly_id,
        "anomaly_status": anomaly_status or None,
        "applicable_actions": list(_ANOMALY_ACTIONS.get(anomaly_status, ())) if anomaly_id else [],
        "policy_type": _safe_policy(item.get("policy_type")),
        "title": _bounded_text(item.get("title"), 120),
        "recommendation": _bounded_text(item.get("recommendation"), 400),
        "impact": str(item.get("impact") or "unavailable") if str(item.get("impact") or "") in _LEVEL else "unavailable",
        "confidence": str(item.get("confidence") or "unavailable") if str(item.get("confidence") or "") in _LEVEL else "unavailable",
        "effort": str(item.get("effort") or "unavailable") if str(item.get("effort") or "") in _LEVEL else "unavailable",
        "sample_count": max(0, int(item.get("sample_count") or 0)),
        "evidence_refs": refs,
        "estimated_savings": item.get("estimated_savings"),
        "currency": item.get("currency"),
        "risk_domain": domain,
        "base_version": _safe_revision(item.get("base_version")),
    }


def _safe_text_projection(item: Mapping[str, Any]) -> dict[str, Any]:
    return {key: str(item.get(key))[:200] for key in ("title", "label", "summary", "state", "status") if item.get(key) is not None}


def _expected_impact(opportunity: Mapping[str, Any]) -> dict[str, Any]:
    value = opportunity.get("estimated_savings")
    try:
        amount = float(value)
    except (TypeError, ValueError):
        amount = None
    if amount is not None and math.isfinite(amount) and amount >= 0:
        return {"status": "estimated", "value": amount, "currency": opportunity.get("currency")}
    return {"status": "unavailable", "value": None, "currency": None}


def _maturity(funnel: list[dict[str, Any]]) -> dict[str, Any]:
    stages = []
    score = 0
    for expected in ("investment", "usage", "output", "outcome"):
        raw = next((item for item in funnel if item.get("id") == expected), {})
        status = str(raw.get("status") or "unavailable")
        complete = status in _COMPLETE_STAGE[expected]
        score += 25 if complete else 0
        stages.append({
            "id": expected, "label": raw.get("label") or expected, "value": raw.get("value"),
            "unit": raw.get("unit") or "", "status": status,
            "evidence_count": max(0, int(raw.get("evidence_count") or 0)),
            "evidence_gap": str(raw.get("evidence_gap") or ""),
            "evidence_refs": _refs(raw.get("evidence_refs")), "complete": complete,
        })
    return {"score_pct": score, "formula_revision": "roi-evidence-maturity-v1", "stages": stages}


def _roi_statement(scenario: dict[str, Any] | None, verified: dict[str, Any]) -> DecisionStatement:
    if verified.get("status") == "verified" and verified.get("value") is not None:
        return DecisionStatement(state="verified", title="已形成可复核 ROI", summary="成本与业务结果证据均已完成验证。", evidence_state="verified")
    if scenario:
        return DecisionStatement(state="scenario_positive_unverified", title="测算显示具备投入价值，业务结果仍需验证", summary="情景参数与运行事实严格分开；验证完成前不显示已实现 ROI。", evidence_state="estimated")
    return DecisionStatement(state="evidence_incomplete", title="已建立投入与使用证据，仍需补充价值假设", summary="当前范围不足以形成可复核 ROI。", evidence_state="partial")


def build_roi_decision(*, economics: Mapping[str, Any], roi_snapshot: Mapping[str, Any], cost_value: Mapping[str, Any], unit_trend: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    scenarios = [_safe_scenario(item) for item in economics.get("scenarios") or [] if isinstance(item, Mapping)]
    scenario = max(scenarios, key=lambda item: str(item.get("revision") or item.get("scenario_id") or ""), default=None)
    result = dict(scenario.get("result") or {}) if scenario else {}
    observed_runs = _refs(roi_snapshot.get("observed_run_ids"))
    cost_evidence = roi_snapshot.get("cost_evidence") if isinstance(roi_snapshot.get("cost_evidence"), Mapping) else {}
    priced_runs = _refs(cost_evidence.get("observed_run_ids")) or observed_runs
    outcome = cost_value.get("outcome_evidence") if isinstance(cost_value.get("outcome_evidence"), Mapping) else {}
    outcome_ids = _refs(outcome.get("outcome_event_ids"))
    verified_ids = _refs(outcome.get("verified_outcome_event_ids"))
    funnel_by_id = {str(item.get("id")): dict(item) for item in economics.get("funnel") or [] if isinstance(item, Mapping)}
    investment = funnel_by_id.get("investment", {"id": "investment", "label": "投入", "unit": "USD"})
    investment["label"], investment["unit"] = "投入", "USD"
    investment.update({"evidence_count": len(priced_runs), "evidence_refs": priced_runs})
    if str(cost_evidence.get("status") or "") != "complete":
        investment["evidence_gap"] = "模型计价未完整覆盖或成本证据不完整"
    usage = funnel_by_id.get("usage", {"id": "usage", "label": "使用", "unit": "次调用"})
    usage["label"], usage["unit"] = "使用", "次调用"
    runs = int((roi_snapshot.get("usage") or {}).get("runs") or 0) if isinstance(roi_snapshot.get("usage"), Mapping) else 0
    usage.update({"value": max(0, runs), "evidence_count": max(0, runs), "evidence_refs": observed_runs})
    output = funnel_by_id.get("output", {"id": "output", "label": "产出", "unit": "个产物"})
    output["label"], output["unit"] = "产出", "个产物"
    if "artifact_count" in cost_value:
        output.update({"value": max(0, int(cost_value.get("artifact_count") or 0)), "status": "observed"})
    output.update({"evidence_count": max(0, int(output.get("value") or 0)), "evidence_refs": observed_runs})
    outcome_stage = funnel_by_id.get("outcome", {"id": "outcome", "label": "业务结果", "unit": "项结果"})
    outcome_stage["label"], outcome_stage["unit"] = "业务结果", "项结果"
    all_verified = bool(outcome_ids) and set(outcome_ids) == set(verified_ids)
    outcome_stage.update({"value": len(outcome_ids), "evidence_count": len(outcome_ids), "evidence_refs": outcome_ids})
    if not all_verified:
        outcome_stage["evidence_gap"] = "业务结果尚未独立验证"
    funnel = [investment, usage, output, outcome_stage]
    metrics = [{"id": key, "label": label, "value": result.get(key), "unit": unit, "status": "estimated", "explanation": "来自情景测算，非已验证业务结果。"} for key, label, unit in (
        ("monthly_benefit", "月度收益", "USD"), ("monthly_total_cost", "月度总成本", "USD"),
        ("monthly_net_benefit", "月度净收益", "USD"), ("roi_ratio", "ROI 比率", "ratio"),
    )]
    total_cost = result.get("monthly_total_cost")
    bridge_items = [
        {"id": "monthly_benefit", "label": "月度收益", "value": result.get("monthly_benefit"), "unit": "USD", "status": "estimated", "explanation": "情景测算中的月度收益。"},
        {"id": "monthly_total_cost", "label": "AI 运营总投入", "value": -total_cost if isinstance(total_cost, (int, float)) and not isinstance(total_cost, bool) else None, "unit": "USD", "status": "estimated", "explanation": "价值桥中的成本扣减项。"},
        {"id": "monthly_net_benefit", "label": "月度净收益", "value": result.get("monthly_net_benefit"), "unit": "USD", "status": "estimated", "explanation": "月度收益减去 AI 运营总投入。"},
    ] if scenario else []
    payload = {"decision": _roi_statement(scenario, dict(economics.get("verified_roi") or {})), "metrics": metrics,
        "value_bridge": {"formula_revision": result.get("formula_revision"), "scenario_id": scenario.get("scenario_id") if scenario else None, "payback_months": result.get("payback_months"), "items": bridge_items},
        "evidence_maturity": _maturity(funnel), "unit_economics_trend": [_safe_trend(item) for item in unit_trend],
        "verified_roi": _safe_verified(economics.get("verified_roi")), "capability_explanation": _CAPABILITY_EXPLANATION,
        "scenarios": scenarios, "evidence_gaps": [stage["evidence_gap"] for stage in funnel if stage.get("evidence_gap")]}
    validated = RoiDecision.model_validate(payload)
    return validated.model_dump(mode="json")


def build_risk_decision(*, anomalies: Sequence[Mapping[str, Any]], opportunities: Sequence[Mapping[str, Any]], evidence_summaries: Sequence[Mapping[str, Any]], insight: Mapping[str, Any] | None, drafts: Sequence[Mapping[str, Any]], governance_capability: Mapping[str, Any], evidence_sets: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    domains = {name: 0 for name in ("cost", "experience", "efficiency", "governance")}
    for anomaly in anomalies:
        domains[_DOMAIN.get(str(anomaly.get("policy_type") or ""), "governance")] += 1
    matrix, priorities, portfolio = [], [], []
    refs_needed: set[str] = set()
    for opportunity in opportunities:
        item = dict(opportunity); policy = _safe_policy(item.get("policy_type"))
        domain = _DOMAIN.get(policy, "governance")
        refs = _refs(item.get("evidence_refs"), 20); refs_needed.update(refs)
        confidence = str(item.get("confidence") or "")
        impact = str(item.get("impact") or "")
        effort = str(item.get("effort") or "")
        safe_item = _safe_opportunity(item, domain=domain, refs=refs)
        base = {"opportunity_id": safe_item["opportunity_id"], "policy_type": policy, "risk_domain": domain,
                "title": safe_item["title"],
                "x_confidence": _LEVEL.get(confidence), "y_impact": _LEVEL.get(impact),
                "x_confidence_state": "observed" if confidence in _LEVEL else "unavailable",
                "y_impact_state": "observed" if impact in _LEVEL else "unavailable",
                "bubble_size": max(0, int(item.get("sample_count") or 0)), "evidence_refs": refs}
        matrix.append(base)
        priority = {**safe_item, "expected_impact": _expected_impact(item)}
        priorities.append(priority)
        portfolio.append({
            **priority,
            "x_effort": _LEVEL.get(effort),
            "y_value_impact": _LEVEL.get(impact),
            "bubble_size": max(0, int(item.get("sample_count") or 0)),
            "x_effort_state": "observed" if effort in _LEVEL else "unavailable",
            "y_value_impact_state": "observed" if impact in _LEVEL else "unavailable",
        })
    selected = []
    for summary in evidence_summaries:
        if str(summary.get("request_ref") or "") not in refs_needed:
            continue
        signal = summary.get("signal") if isinstance(summary.get("signal"), Mapping) else {}
        technical = summary.get("technical_refs") if isinstance(summary.get("technical_refs"), Mapping) else {}
        selected.append({"request_ref": summary.get("request_ref"), "request_name": summary.get("request_name"),
            "operation": summary.get("operation"), "model_label": summary.get("model_label"),
            "signal": {"metric": signal.get("metric"), "value": signal.get("value"), "unit": signal.get("unit")},
            "latency_ms": summary.get("latency_ms"), "cache_state": summary.get("cache_state"), "status": summary.get("status"),
            "error_category": summary.get("error_category"), "visible_answer_summary": _bounded_text(summary.get("visible_answer_summary"), 400),
            "technical_refs": {"request_ref": str(technical.get("request_ref") or summary.get("request_ref") or "")} if str(technical.get("request_ref") or summary.get("request_ref") or "").startswith(_SAFE_REF_PREFIXES) else {}})
    statement = DecisionStatement(state="prioritized" if priorities else "no_current_risk", title="已按影响与证据确定优化优先级" if priorities else "当前没有可排序的风险证据", summary="风险以影响、置信度、影响范围和可追溯证据展示，不使用复合风险分数。", evidence_state="observed" if priorities else "unavailable")
    payload = {"decision": statement, "risk_domains": [{"id": key, "count": value} for key, value in domains.items()],
        "risk_matrix": matrix, "priorities": priorities, "optimization_portfolio": portfolio,
        "portfolio_metadata": {"x_axis": "effort", "y_axis": "value_impact", "size": "affected_scope", "color": "risk_domain"},
        "selected_evidence_summaries": selected, "evidence_sets": [dict(item) for item in evidence_sets], "insight": _safe_text_projection(insight) if insight else None,
        "drafts": [_safe_text_projection(item) for item in drafts], "governance_capability": {"read_enabled": bool(governance_capability.get("read_enabled")), "draft_enabled": bool(governance_capability.get("draft_enabled")), "actions_enabled": bool(governance_capability.get("actions_enabled")), "typed_executors": [value for value in (str(item).strip() for item in governance_capability.get("typed_executors") or []) if value in {"cache_policy", "budget_policy", "routing_policy", "pricing_policy"}]}}
    validated = RiskDecision.model_validate(payload)
    return validated.model_dump(mode="json")
