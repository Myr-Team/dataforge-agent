from __future__ import annotations

import json

from backend.finops.decision_service import build_risk_decision, build_roi_decision


def test_roi_decision_keeps_scenario_separate_from_verified_value() -> None:
    result = build_roi_decision(
        economics={
            "funnel": [
                {"id": "investment", "label": "投入", "value": 800, "unit": "USD", "status": "estimated"},
                {"id": "usage", "label": "使用", "value": 60, "unit": "调用", "status": "observed"},
                {"id": "output", "label": "产出", "value": 60, "unit": "分析", "status": "observed"},
                {"id": "outcome", "label": "业务结果", "value": None, "unit": "结果", "status": "not_recorded"},
            ],
            "scenarios": [{
                "scenario_id": "roi_scenario_demo",
                "title": "运营自动化测算",
                "status": "estimated",
                "inputs": {
                    "currency": "USD",
                    "hours_saved": 40,
                    "hourly_value": 50,
                    "avoided_loss_or_revenue": 1000,
                    "implementation_cost": 6000,
                    "monthly_fixed_cost": 200,
                    "model_cost": 100,
                    "evaluation_months": 12,
                },
                "result": {
                    "monthly_benefit": 3000,
                    "monthly_total_cost": 800,
                    "monthly_net_benefit": 2200,
                    "roi_ratio": 2.75,
                    "payback_months": 2.2,
                    "formula_revision": "dataforge-roi-v1",
                },
            }],
            "verified_roi": {"status": "not_recorded", "value": None},
        },
        roi_snapshot={
            "lineage_complete": True,
            "usage": {"runs": 60},
            "observed_run_ids": ["run-001", "run-002"],
            "cost_evidence": {
                "status": "complete",
                "observed_run_ids": ["run-001", "run-002"],
                "priced_run_ids": ["run-001", "run-002"],
            },
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
        request_refs_by_run={
            "run-001": ["req_run_000001"],
            "run-002": ["req_run_000002"],
        },
        artifact_run_ids=["run-001", "run-002"],
        artifact_source_count=12,
    )

    assert result["decision"]["state"] == "scenario_positive_unverified"
    assert result["decision"]["title"] == "测算显示具备投入价值，业务结果仍需验证"
    assert result["case_story"]["title"] == "运营自动化测算"
    assert result["case_story"]["assumptions"][0] == {
        "id": "hours_saved",
        "label": "每月节省工时",
        "value": 40.0,
        "unit": "小时/月",
    }
    assert "40 小时" in result["case_story"]["summary"]
    assert result["case_story"]["boundary"] == "情景参数用于展示投入价值，业务结果验证前不计为已实现 ROI。"
    assert result["metrics"][0]["status"] == "estimated"
    assert result["value_bridge"]["formula_revision"] == "dataforge-roi-v1"
    assert result["value_bridge"]["items"] == [
        {"id": "monthly_benefit", "label": "月度收益", "value": 3000, "unit": "USD", "status": "estimated", "explanation": "情景测算中的月度收益。"},
        {"id": "monthly_total_cost", "label": "AI 运营总投入", "value": -800, "unit": "USD", "status": "estimated", "explanation": "价值桥中的成本扣减项。"},
        {"id": "monthly_net_benefit", "label": "月度净收益", "value": 2200, "unit": "USD", "status": "estimated", "explanation": "月度收益减去 AI 运营总投入。"},
    ]
    assert result["verified_roi"]["value"] is None
    assert result["evidence_maturity"]["score_pct"] == 75
    assert result["evidence_maturity"]["stages"][1]["evidence_count"] == 60
    assert result["evidence_maturity"]["stages"][2]["value"] == 12
    assert result["evidence_maturity"]["stages"][3]["evidence_gap"] == "业务结果尚未独立验证"


def test_roi_decision_adds_holdout_regression_error_without_promoting_roi() -> None:
    trend = [
        {
            "bucket_at": f"2026-08-{day:02d}",
            "cost_per_successful_request": value,
            "data_status": "available",
        }
        for day, value in enumerate(
            (0.18, 0.175, 0.171, 0.166, 0.162, 0.157, 0.153, 0.149, 0.145, 0.142),
            start=1,
        )
    ]
    result = build_roi_decision(
        economics={
            "funnel": [],
            "scenarios": [{
                "scenario_id": "roi_regression_demo",
                "status": "estimated",
                "inputs": {"currency": "USD"},
                "result": {"roi_ratio": 1.5, "formula_revision": "dataforge-roi-v1"},
            }],
            "verified_roi": {"status": "not_recorded", "value": None},
        },
        roi_snapshot={"usage": {"runs": 10}, "cost_evidence": {}},
        cost_value={"outcome_evidence": {}},
        unit_trend=trend,
    )

    validation = result["forecast_validation"]
    assert validation["status"] == "estimated"
    assert validation["target"] == "cost_per_successful_request"
    assert validation["sample_count"] == 10
    assert validation["train_count"] == 7
    assert validation["validation_count"] == 3
    assert validation["mse"] is not None
    assert validation["baseline_mse"] is not None
    assert validation["method_revision"] == "linear-holdout-v1"
    assert "优于朴素基线" in result["decision"]["summary"]
    assert result["decision"]["evidence_state"] == "estimated"
    assert result["verified_roi"]["value"] is None


def test_roi_regression_reports_insufficient_data_instead_of_inventing_accuracy() -> None:
    result = build_roi_decision(
        economics={"funnel": [], "scenarios": [], "verified_roi": {}},
        roi_snapshot={"usage": {"runs": 2}, "cost_evidence": {}},
        cost_value={"outcome_evidence": {}},
        unit_trend=[
            {"bucket_at": "2026-08-01", "cost_per_successful_request": 0.1, "data_status": "available"},
            {"bucket_at": "2026-08-02", "cost_per_successful_request": 0.2, "data_status": "available"},
        ],
    )

    assert result["forecast_validation"] == {
        "status": "insufficient_data",
        "target": "cost_per_successful_request",
        "unit": "USD/次成功调用",
        "sample_count": 2,
        "train_count": 0,
        "validation_count": 0,
        "mse": None,
        "rmse": None,
        "mae": None,
        "r2": None,
        "baseline_mse": None,
        "improvement_pct": None,
        "method_revision": "linear-holdout-v1",
    }


def test_roi_decision_does_not_call_a_negative_scenario_investment_worthy() -> None:
    result = build_roi_decision(
        economics={
            "funnel": [],
            "scenarios": [{
                "scenario_id": "roi-negative",
                "status": "estimated",
                "inputs": {"currency": "USD"},
                "result": {
                    "monthly_benefit": 400,
                    "monthly_total_cost": 800,
                    "monthly_net_benefit": -400,
                    "roi_ratio": -0.5,
                    "formula_revision": "dataforge-roi-v1",
                },
            }],
            "verified_roi": {"status": "not_recorded", "value": None},
        },
        roi_snapshot={"usage": {"runs": 0}, "cost_evidence": {}},
        cost_value={"outcome_evidence": {}},
        unit_trend=[],
    )

    assert result["decision"] == {
        "state": "scenario_not_positive",
        "title": "当前测算尚未达到正向回报",
        "summary": "当前情景中的净收益或 ROI 不为正；应先优化投入、提升可验证收益，再重新测算。",
        "evidence_state": "estimated",
    }


def test_roi_decision_projects_each_stage_to_openable_request_evidence() -> None:
    result = build_roi_decision(
        economics={
            "funnel": [
                {"id": "investment", "value": 2, "status": "estimated"},
                {"id": "usage", "value": 2, "status": "observed"},
                {"id": "output", "value": 1, "status": "observed"},
                {"id": "outcome", "value": 1, "status": "verified"},
            ],
            "scenarios": [],
            "verified_roi": {"status": "not_recorded", "value": None},
        },
        roi_snapshot={
            "usage": {"runs": 2},
            "observed_run_ids": ["run-usage", "run-output"],
            "cost_evidence": {
                "status": "complete",
                "observed_run_ids": ["run-usage", "run-output"],
                "priced_run_ids": ["run-usage"],
            },
        },
        cost_value={
            "artifact_count": 1,
            "outcome_evidence": {
                "status": "verified",
                "outcome_event_ids": ["outcome-second"],
                "verified_outcome_event_ids": ["outcome-second"],
            },
        },
        unit_trend=[],
        request_refs_by_run={
            "run-usage": ["req_usage_000001"],
            "run-output": ["req_output_000001"],
            "run-other-workspace": ["req_other_workspace"],
        },
        artifact_run_ids=["run-output"],
        artifact_source_count=1,
        outcome_source_run_ids={"outcome-second": "run-output"},
    )

    stages = {
        item["id"]: item for item in result["evidence_maturity"]["stages"]
    }
    assert stages["investment"]["evidence_refs"] == ["req_usage_000001"]
    assert stages["usage"]["evidence_refs"] == [
        "req_usage_000001",
        "req_output_000001",
    ]
    assert stages["output"]["evidence_refs"] == ["req_output_000001"]
    assert stages["outcome"]["evidence_refs"] == ["req_output_000001"]
    assert "req_other_workspace" not in json.dumps(stages)


def test_roi_decision_preserves_counts_and_gaps_when_request_mapping_is_missing() -> None:
    result = build_roi_decision(
        economics={
            "funnel": [
                {"id": "investment", "value": 1, "status": "estimated"},
                {"id": "usage", "value": 1, "status": "observed"},
                {"id": "output", "value": 1, "status": "observed"},
                {"id": "outcome", "value": 1, "status": "verified"},
            ],
            "scenarios": [],
            "verified_roi": {"status": "not_recorded", "value": None},
        },
        roi_snapshot={
            "usage": {"runs": 1},
            "observed_run_ids": ["run-missing"],
            "cost_evidence": {
                "status": "complete",
                "observed_run_ids": ["run-missing"],
                "priced_run_ids": ["run-missing"],
            },
        },
        cost_value={
            "artifact_count": 1,
            "outcome_evidence": {
                "status": "verified",
                "outcome_event_ids": ["outcome-missing"],
                "verified_outcome_event_ids": ["outcome-missing"],
            },
        },
        unit_trend=[],
        request_refs_by_run={},
        artifact_run_ids=["run-missing"],
        artifact_source_count=1,
        outcome_source_run_ids={"outcome-missing": "run-missing"},
    )

    stages = {
        item["id"]: item for item in result["evidence_maturity"]["stages"]
    }
    for stage in stages.values():
        assert stage["evidence_count"] == 1
        assert stage["evidence_refs"] == []
        assert "请求级证据" in stage["evidence_gap"]


def test_roi_maturity_never_leaks_run_or_outcome_ids_as_openable_evidence() -> None:
    result = build_roi_decision(
        economics={
            "funnel": [
                {
                    "id": "output",
                    "value": 1,
                    "status": "observed",
                    "evidence_refs": ["run-output", "outcome-output", "event_output"],
                }
            ],
            "scenarios": [],
            "verified_roi": {"status": "not_recorded", "value": None},
        },
        roi_snapshot={"usage": {"runs": 0}, "observed_run_ids": []},
        cost_value={
            "artifact_count": 1,
            "outcome_evidence": {
                "status": "not_recorded",
                "outcome_event_ids": [],
                "verified_outcome_event_ids": [],
            },
        },
        unit_trend=[],
        request_refs_by_run={},
        artifact_run_ids=["run-output"],
        artifact_source_count=1,
    )

    output = next(
        stage
        for stage in result["evidence_maturity"]["stages"]
        if stage["id"] == "output"
    )
    assert output["evidence_refs"] == []
    assert "请求级证据" in output["evidence_gap"]


def test_roi_decision_marks_output_incomplete_when_one_artifact_lacks_source_lineage() -> None:
    result = build_roi_decision(
        economics={
            "funnel": [{"id": "output", "value": 2, "status": "observed"}],
            "scenarios": [],
            "verified_roi": {"status": "not_recorded", "value": None},
        },
        roi_snapshot={"usage": {"runs": 0}, "observed_run_ids": []},
        cost_value={
            "artifact_count": 2,
            "outcome_evidence": {
                "status": "not_recorded",
                "outcome_event_ids": [],
                "verified_outcome_event_ids": [],
            },
        },
        unit_trend=[],
        request_refs_by_run={"run-output": ["req_output_000001"]},
        artifact_run_ids=["run-output"],
        artifact_source_count=1,
    )

    output = next(
        item
        for item in result["evidence_maturity"]["stages"]
        if item["id"] == "output"
    )
    assert output["evidence_count"] == 2
    assert output["evidence_refs"] == ["req_output_000001"]
    assert output["complete"] is False
    assert "部分产出证据" in output["evidence_gap"]


def test_roi_decision_allows_two_artifacts_to_share_one_mapped_run() -> None:
    result = build_roi_decision(
        economics={
            "funnel": [{"id": "output", "value": 2, "status": "observed"}],
            "scenarios": [],
            "verified_roi": {"status": "not_recorded", "value": None},
        },
        roi_snapshot={"usage": {"runs": 0}, "observed_run_ids": []},
        cost_value={
            "artifact_count": 2,
            "outcome_evidence": {
                "status": "not_recorded",
                "outcome_event_ids": [],
                "verified_outcome_event_ids": [],
            },
        },
        unit_trend=[],
        request_refs_by_run={"run-shared": ["req_output_000001"]},
        artifact_run_ids=["run-shared"],
        artifact_source_count=2,
    )

    output = next(
        item
        for item in result["evidence_maturity"]["stages"]
        if item["id"] == "output"
    )
    assert output["evidence_count"] == 2
    assert output["evidence_refs"] == ["req_output_000001"]
    assert output["complete"] is True
    assert output["evidence_gap"] == ""


def test_roi_decision_does_not_treat_old_observed_lineage_as_priced() -> None:
    result = build_roi_decision(
        economics={
            "funnel": [{"id": "investment", "value": 1, "status": "estimated"}],
            "scenarios": [],
            "verified_roi": {"status": "not_recorded", "value": None},
        },
        roi_snapshot={
            "usage": {"runs": 1},
            "observed_run_ids": ["run-observed-only"],
            "cost_evidence": {
                "status": "complete",
                "observed_run_ids": ["run-observed-only"],
            },
        },
        cost_value={"artifact_count": 0, "outcome_evidence": {}},
        unit_trend=[],
        request_refs_by_run={
            "run-observed-only": ["req_observed_000001"]
        },
    )

    investment = next(
        item
        for item in result["evidence_maturity"]["stages"]
        if item["id"] == "investment"
    )
    assert investment["evidence_count"] == 0
    assert investment["evidence_refs"] == []
    assert investment["complete"] is False
    assert "计价来源" in investment["evidence_gap"]


def test_roi_decision_accepts_one_request_as_evidence_for_two_outcomes() -> None:
    result = build_roi_decision(
        economics={
            "funnel": [
                {"id": "outcome", "value": 2, "status": "verified"},
            ],
            "scenarios": [],
            "verified_roi": {"status": "not_recorded", "value": None},
        },
        roi_snapshot={"usage": {"runs": 0}, "observed_run_ids": []},
        cost_value={
            "artifact_count": 0,
            "outcome_evidence": {
                "status": "verified",
                "outcome_event_ids": ["outcome-one", "outcome-two"],
                "verified_outcome_event_ids": ["outcome-one", "outcome-two"],
            },
        },
        unit_trend=[],
        request_refs_by_run={"run-shared": ["req_shared_000001"]},
        outcome_source_run_ids={
            "outcome-one": "run-shared",
            "outcome-two": "run-shared",
        },
    )

    outcome = next(
        item
        for item in result["evidence_maturity"]["stages"]
        if item["id"] == "outcome"
    )
    assert outcome["evidence_count"] == 2
    assert outcome["evidence_refs"] == ["req_shared_000001"]
    assert outcome["evidence_gap"] == ""


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
    assert result["portfolio_metadata"]["size"] == "sample_count"
    assert "评估样本量" in result["decision"]["summary"]
    assert "业务影响" not in result["decision"]["summary"]
    assert result["governance_capability"]["actions_enabled"] is False


def test_risk_decision_projects_managed_actions_versions_and_portfolio_coordinates() -> None:
    result = build_risk_decision(
        anomalies=[{
            "anomaly_id": "anomaly_cache",
            "policy_type": "cache_hit_rate",
            "status": "acknowledged",
            "severity": "warning",
            "sample_count": 25,
        }],
        opportunities=[{
            "opportunity_id": "opp_cache",
            "anomaly_id": "anomaly_cache",
            "anomaly_status": "acknowledged",
            "policy_type": "cache_hit_rate",
            "title": "缓存效率优化",
            "recommendation": "检查缓存资格与失效窗口。",
            "impact": "medium",
            "confidence": "medium",
            "effort": "low",
            "sample_count": 25,
            "evidence_refs": ["req_cache_001"],
            "base_version": "cache-policy-v7",
            "actor_ref": "must-not-project",
            "suppression_reason": "must-not-project",
            "internal_error": "must-not-project",
        }],
        evidence_summaries=[],
        insight=None,
        drafts=[],
        governance_capability={},
    )

    priority = result["priorities"][0]
    assert priority["anomaly_id"] == "anomaly_cache"
    assert priority["anomaly_status"] == "acknowledged"
    assert priority["applicable_actions"] == ["suppress"]
    assert priority["base_version"] == "cache-policy-v7"
    assert priority["title"] == "缓存效率优化"
    assert priority["recommendation"] == "检查缓存资格与失效窗口。"
    portfolio = result["optimization_portfolio"][0]
    assert portfolio["x_effort"] == 1
    assert portfolio["y_value_impact"] == 2
    assert portfolio["bubble_size"] == 25
    assert portfolio["x_effort_state"] == "observed"
    assert portfolio["y_value_impact_state"] == "observed"
    assert "actor_ref" not in priority
    assert "suppression_reason" not in priority
    assert "internal_error" not in priority


def test_risk_decision_server_owns_anomaly_action_allowlist() -> None:
    def projected(status: str) -> list[str]:
        result = build_risk_decision(
            anomalies=[],
            opportunities=[{
                "opportunity_id": f"opp_{status}",
                "anomaly_id": f"anomaly_{status}",
                "anomaly_status": status,
                "policy_type": "error_rate",
                "impact": "high",
                "confidence": "high",
                "effort": "high",
                "sample_count": 20,
                "applicable_actions": ["execute", "approve"],
            }],
            evidence_summaries=[], insight=None, drafts=[], governance_capability={},
        )
        return result["priorities"][0]["applicable_actions"]

    assert projected("open") == ["acknowledge", "suppress"]
    assert projected("acknowledged") == ["suppress"]
    assert projected("suppressed") == []
    assert projected("resolved") == []


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
