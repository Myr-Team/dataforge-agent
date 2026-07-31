from __future__ import annotations

import json

from backend.finops.decision_service import build_risk_decision, build_roi_decision


def test_roi_decision_keeps_scenario_separate_from_verified_value() -> None:
    result = build_roi_decision(
        economics={
            "funnel": [
                {"id": "investment", "label": "投入", "value": 700.03, "unit": "USD", "status": "estimated"},
                {"id": "usage", "label": "使用", "value": 60, "unit": "调用", "status": "observed"},
                {"id": "output", "label": "产出", "value": 60, "unit": "分析", "status": "observed"},
                {"id": "outcome", "label": "业务结果", "value": None, "unit": "结果", "status": "not_recorded"},
            ],
            "scenarios": [{
                "scenario_id": "roi_scenario_demo",
                "status": "estimated",
                "result": {
                    "monthly_benefit": 3000,
                    "monthly_total_cost": 700.03,
                    "monthly_net_benefit": 2299.97,
                    "roi_ratio": 3.2856,
                    "payback_months": 2.1,
                    "formula_revision": "dataforge-roi-v1",
                },
            }],
            "verified_roi": {"status": "not_recorded", "value": None},
        },
        roi_snapshot={
            "lineage_complete": True,
            "usage": {"runs": 60},
            "observed_run_ids": ["run-001", "run-002"],
            "cost_evidence": {"status": "complete", "observed_run_ids": ["run-001", "run-002"]},
        },
        cost_value={
            "artifact_count": 12,
            "outcome_evidence": {
                "status": "not_recorded",
                "outcome_event_ids": [],
                "verified_outcome_event_ids": [],
            },
        },
        unit_trend=[],
    )

    assert result["decision"]["state"] == "scenario_positive_unverified"
    assert result["decision"]["title"] == "测算显示具备投入价值，业务结果仍需验证"
    assert result["metrics"][0]["status"] == "estimated"
    assert result["value_bridge"]["formula_revision"] == "dataforge-roi-v1"
    assert result["verified_roi"]["value"] is None
    assert result["evidence_maturity"]["score_pct"] == 75
    assert result["evidence_maturity"]["stages"][1]["evidence_count"] == 60
    assert result["evidence_maturity"]["stages"][2]["value"] == 12
    assert result["evidence_maturity"]["stages"][3]["evidence_gap"] == "业务结果尚未独立验证"


def test_risk_decision_uses_impact_and_evidence_without_composite_score() -> None:
    result = build_risk_decision(
        anomalies=[{
            "anomaly_id": "anom_latency", "policy_type": "p95_latency", "severity": "warning",
            "sample_count": 60, "evidence_state": "observed", "evidence_refs": ["req_slow_000001"],
        }],
        opportunities=[{
            "opportunity_id": "opp_latency", "policy_type": "p95_latency", "impact": "high",
            "confidence": "high", "effort": "high", "sample_count": 60,
            "evidence_refs": ["req_slow_000001"], "estimated_savings": None, "currency": None,
        }],
        evidence_summaries=[{
            "request_ref": "req_slow_000001", "request_name": "演示工作区 · 自动分析 · 慢响应",
            "signal": {"metric": "latency_ms", "value": 6200, "unit": "ms"}, "latency_ms": 6200,
            "cache_state": "miss", "status": "succeeded", "error_category": None,
            "technical_refs": {"request_ref": "req_slow_000001"},
        }],
        insight=None, drafts=[],
        governance_capability={"read_enabled": True, "draft_enabled": True, "actions_enabled": False, "typed_executors": ["cache_policy"]},
    )

    assert "risk_score" not in result
    assert result["risk_matrix"][0]["x_confidence"] == 3
    assert result["risk_matrix"][0]["y_impact"] == 3
    assert result["priorities"][0]["evidence_refs"] == ["req_slow_000001"]
    assert result["priorities"][0]["expected_impact"]["status"] == "unavailable"
    assert result["selected_evidence_summaries"][0]["request_name"].endswith("慢响应")
    assert result["portfolio_metadata"]["x_axis"] == "effort"
    assert result["governance_capability"]["actions_enabled"] is False


def test_decision_projections_do_not_leak_upstream_sensitive_fields() -> None:
    poison = "PROMPT_SECRET provider-response provider-123 alice@example.com api-key internal-error Azure API Management"
    roi = build_roi_decision(
        economics={
            "funnel": [],
            "scenarios": [{"scenario_id": "roi-safe", "status": "estimated", "result": {"monthly_benefit": 1, "formula_revision": "v1", "raw_prompt": poison}, "provider_id": poison}],
            "verified_roi": {"status": "verified", "value": 1, "provider_response": poison, "identity": poison},
        },
        roi_snapshot={"usage": {"runs": 1}, "observed_run_ids": ["run-001", poison], "cost_evidence": {"status": "complete", "observed_run_ids": ["run-001"]}},
        cost_value={"artifact_count": 1, "outcome_evidence": {"status": "verified", "outcome_event_ids": ["outcome-001"], "verified_outcome_event_ids": ["outcome-001"]}},
        unit_trend=[{"label": "每次调用成本", "value": 1, "provider_id": poison, "raw_response": poison}],
    )
    risk = build_risk_decision(
        anomalies=[],
        opportunities=[{"opportunity_id": "opp-safe", "policy_type": "p95_latency", "impact": "high", "confidence": "high", "effort": "high", "sample_count": 1, "evidence_refs": ["req_safe_001", poison], "provider_id": poison, "raw_prompt": poison}],
        evidence_summaries=[{"request_ref": "req_safe_001", "request_name": "慢响应", "signal": {"metric": "latency_ms", "value": 1, "unit": "ms"}, "technical_refs": {"request_ref": "req_safe_001", "provider_id": poison, "trace_ref": poison}, "internal_error": poison}],
        insight={"title": "安全洞察", "provider_response": poison, "secret": poison},
        drafts=[{"title": "安全草稿", "raw_prompt": poison, "actor_id": poison}],
        governance_capability={"read_enabled": True, "draft_enabled": True, "actions_enabled": False, "typed_executors": ["cache_policy"], "gateway_product_name": poison},
    )

    payload = json.dumps({"roi": roi, "risk": risk}, ensure_ascii=False)
    assert poison not in payload
    assert "provider_id" not in payload
    assert "raw_prompt" not in payload
    assert "technical_refs" in payload
    assert risk["selected_evidence_summaries"][0]["technical_refs"] == {"request_ref": "req_safe_001"}


def test_risk_matrix_marks_unknown_levels_unavailable_instead_of_low() -> None:
    result = build_risk_decision(
        anomalies=[],
        opportunities=[{"opportunity_id": "opp-unknown", "policy_type": "p95_latency", "impact": "unknown", "confidence": None, "effort": "high", "sample_count": 1, "evidence_refs": []}],
        evidence_summaries=[], insight=None, drafts=[], governance_capability={},
    )

    item = result["risk_matrix"][0]
    assert item["x_confidence"] is None
    assert item["y_impact"] is None
    assert item["x_confidence_state"] == "unavailable"
    assert item["y_impact_state"] == "unavailable"
