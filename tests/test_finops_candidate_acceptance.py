from __future__ import annotations

import pytest

from backend.finops.candidate_acceptance import (
    CandidateAcceptanceError,
    summarize_candidate_payloads,
)


def _payloads() -> dict:
    return {
        "bootstrap": {
            "overview": {
                "metrics": {
                    "requests": 153,
                    "tokens": {"total": 880_000},
                    "estimated_cost": {"amount": 1.87},
                    "budget": {"amount": 670, "used_amount": 1.87, "usage_pct": 0.279},
                    "latency": {"p50_ms": 1480, "p95_ms": 5140},
                    "error_rate_pct": 5.2,
                    "success_rate_pct": 94.8,
                    "cache_hit_rate_pct": 12.5,
                    "cache": {
                        "eligible_requests": 64,
                        "hit": 8,
                        "miss": 56,
                        "avoided_tokens": 18_400,
                        "estimated_savings": 0.084,
                    },
                    "apim_coverage_pct": 86.9,
                }
            },
            "trend": {
                "items": [
                    {"requests": 30, "tokens": {"total": 120_000}, "estimated_cost": 0.31},
                    {"requests": 52, "tokens": {"total": 360_000}, "estimated_cost": 0.73},
                    {"requests": 71, "tokens": {"total": 400_000}, "estimated_cost": 0.83},
                ]
            },
            "departments": {
                "items": [
                    {"requests": 80, "tokens": 500_000, "estimated_cost": 1.1},
                    {"requests": 73, "tokens": 380_000, "estimated_cost": 0.77},
                ]
            },
        },
        "workspace_breakdown": {"items": [{"key": "demo", "estimated_cost": 1.87}]},
        "agents": {
            "items": [
                {"key": "a", "requests": 40, "tokens": 300_000, "estimated_cost": 0.91, "p95_latency_ms": 2200},
                {"key": "b", "requests": 35, "tokens": 250_000, "estimated_cost": 0.56, "p95_latency_ms": 1800},
                {"key": "c", "requests": 30, "tokens": 210_000, "estimated_cost": 0.40, "p95_latency_ms": 1500},
            ]
        },
        "budgets": {
            "items": [{
                "progress": {
                    "amount": 670,
                    "spent_amount": 1.87,
                    "usage_pct": 0.279,
                    "forecast_amount": 28.98,
                }
            }]
        },
        "roi": {
            "metrics": [
                {"id": "monthly_benefit", "value": 3_000},
                {"id": "monthly_total_cost", "value": 800},
                {"id": "monthly_net_benefit", "value": 2_200},
                {"id": "roi_ratio", "value": 2.75},
            ],
            "value_bridge": {
                "formula_revision": "roi-scenario-v1",
                "scenario_id": "scenario-1",
                "payback_months": 3.4,
                "items": [
                    {"id": "monthly_benefit", "value": 3_000, "unit": "USD"},
                    {"id": "monthly_total_cost", "value": -800, "unit": "USD"},
                    {"id": "monthly_net_benefit", "value": 2_200, "unit": "USD"},
                ],
            },
            "evidence_maturity": {
                "stages": [
                    {"id": "investment", "evidence_refs": ["req_investment"]},
                    {"id": "usage", "evidence_refs": ["req_usage"]},
                    {"id": "output", "evidence_refs": ["req_output"]},
                    {"id": "outcome", "evidence_refs": ["req_outcome"]},
                ]
            },
            "unit_economics_trend": [{"value": 0.1}, {"value": 0.2}, {"value": 0.4}],
            "scenarios": [{"title": "运营自动化测算"}],
        },
        "risk": {
            "risk_domains": [{"id": value} for value in ("cost", "reliability", "performance", "governance")],
            "risk_matrix": [{"id": str(index)} for index in range(4)],
            "priorities": [{"id": str(index)} for index in range(4)],
            "optimization_portfolio": [{"id": str(index)} for index in range(4)],
            "selected_evidence_summaries": [
                {"request_ref": f"req_risk_{index}"} for index in range(7)
            ],
            "evidence_sets": [
                {
                    "policy_type": policy_type,
                    "items": [{"request_ref": request_ref, "signal": signal}],
                }
                for policy_type, request_ref, signal in (
                    ("p95_latency", "req_risk_0", {"metric": "latency_ms", "value": 6200, "unit": "ms"}),
                    ("cache_hit_rate", "req_risk_1", {"metric": "cache_state", "value": "miss", "unit": "state"}),
                    ("unpriced_requests", "req_risk_2", {"metric": "pricing_status", "value": "unpriced", "unit": "status"}),
                    ("error_rate", "req_risk_3", {"metric": "request_status", "value": "failed", "unit": "status"}),
                    ("daily_cost_budget", "req_risk_4", {"metric": "estimated_cost", "value": 1.87, "unit": "USD"}),
                    ("token_spike", "req_risk_5", {"metric": "tokens_total", "value": 31580, "unit": "token"}),
                    ("apim_coverage", "req_risk_6", {"metric": "gateway_coverage", "value": "unmanaged", "unit": "state"}),
                )
            ],
            "insight": {"summary": "风险来自不同调用与缓存证据。"},
            "governance_capability": {
                "read_enabled": True,
                "draft_enabled": True,
                "actions_enabled": False,
            },
        },
        "request_detail": {
            "display": {
                "name": "演示工作区 · 分析数据",
                "operation": "批量分析",
            },
            "status": "succeeded",
            "metrics": {
                "latency_ms": 1800,
                "tokens": {"total": 8000},
                "cache": {"hit": True},
                "estimated_cost": {"amount": 0.01},
            },
            "business_request": {"text": "分析当前工作区"},
            "business_response": {"text": "分析已完成"},
        },
        "pricing": {
            "items": [
                {"official_model": "gpt-5.1"},
                {"official_model": "gpt-5.6-terra"},
            ]
        },
        "price_mappings": {"items": []},
        "roi_request_details": {
            request_ref: {"request_ref": request_ref, "status": "succeeded"}
            for request_ref in ("req_investment", "req_usage", "req_output", "req_outcome")
        },
        "assistant_check": {
            "requested_evidence_refs": ["req_risk_0"],
            "response": {
                "status": "ready",
                "evidence_refs": ["req_risk_0"],
                "evidence_labels": ["高时延证据"],
            },
        },
        "model_routing": {
            "policy": {
                "agent_assignments": {
                    "df-finops-analyst": {
                        "primary_route_id": "terra",
                        "fallback_route_id": "analysis",
                    },
                    "df-roi-analyst": {
                        "primary_route_id": "terra",
                        "fallback_route_id": "analysis",
                    },
                }
            },
            "routes": [
                {"id": "terra", "deployment": "gpt-5.6-terra"},
                {"id": "analysis", "deployment": "gpt-5.1"},
            ],
        },
    }


def test_candidate_acceptance_requires_complete_numeric_display_data() -> None:
    summary = summarize_candidate_payloads(_payloads())

    assert summary["ok"] is True
    assert summary["overview"]["requests"] == 153
    assert summary["trend_distinct_values"] == {
        "requests": {"distinct": 3, "known": 3, "missing": 0},
        "tokens": {"distinct": 3, "known": 3, "missing": 0},
        "cost": {"distinct": 3, "known": 3, "missing": 0},
    }
    assert summary["roi"]["metric_count"] == 4
    assert summary["roi"]["bridge_subtraction_verified"] is True
    assert summary["roi"]["openable_stage_count"] == 4
    assert summary["risk"]["distinct_evidence"] == 7
    assert summary["risk"]["distinct_evidence_sets"] == 7
    assert summary["risk"]["localized_string_signal_cases"] == 4
    assert summary["assistant"]["selected_item_evidence_verified"] is True
    assert summary["model_routing"]["operations_analysts_on_terra"] is True
    assert summary["request_detail_complete"] is True


def test_candidate_acceptance_rejects_equal_chart_values() -> None:
    payloads = _payloads()
    for item in payloads["bootstrap"]["trend"]["items"]:
        item["tokens"]["total"] = 120_000

    with pytest.raises(CandidateAcceptanceError, match="token trend chart geometry"):
        summarize_candidate_payloads(payloads)


def test_candidate_acceptance_rejects_missing_budget_spend() -> None:
    payloads = _payloads()
    payloads["budgets"]["items"][0]["progress"]["spent_amount"] = None

    with pytest.raises(CandidateAcceptanceError, match="budget progress spend"):
        summarize_candidate_payloads(payloads)


def test_candidate_acceptance_counts_an_explicitly_unpriced_agent_row() -> None:
    payloads = _payloads()
    payloads["agents"]["items"].append(
        {
            "key": "unpriced",
            "requests": 2,
            "tokens": None,
            "estimated_cost": None,
            "p95_latency_ms": None,
        }
    )

    summary = summarize_candidate_payloads(payloads)

    assert summary["agent_rows"] == 4
    assert summary["agent_cost_values"] == {
        "distinct": 3,
        "known": 3,
        "missing": 1,
    }


def test_candidate_acceptance_rejects_repeated_risk_evidence() -> None:
    payloads = _payloads()
    for item in payloads["risk"]["selected_evidence_summaries"]:
        item["request_ref"] = "req_same"

    with pytest.raises(CandidateAcceptanceError, match="risk evidence is not sufficiently distinct"):
        summarize_candidate_payloads(payloads)


def test_candidate_acceptance_rejects_roi_bridge_that_adds_cost() -> None:
    payloads = _payloads()
    payloads["roi"]["value_bridge"]["items"][1]["value"] = 800

    with pytest.raises(CandidateAcceptanceError, match="ROI value bridge must subtract cost"):
        summarize_candidate_payloads(payloads)


def test_candidate_acceptance_rejects_non_openable_roi_evidence() -> None:
    payloads = _payloads()
    payloads["roi"]["evidence_maturity"]["stages"][2]["evidence_refs"] = ["run-output"]

    with pytest.raises(CandidateAcceptanceError, match="ROI stage evidence is not request-level"):
        summarize_candidate_payloads(payloads)


def test_candidate_acceptance_rejects_reused_or_missing_risk_evidence_sets() -> None:
    payloads = _payloads()
    payloads["risk"]["evidence_sets"] = payloads["risk"]["evidence_sets"][:5]

    with pytest.raises(CandidateAcceptanceError, match="risk policy evidence lacks distinct coverage"):
        summarize_candidate_payloads(payloads)


def test_candidate_acceptance_rejects_missing_string_signal_examples() -> None:
    payloads = _payloads()
    for evidence_set in payloads["risk"]["evidence_sets"]:
        for item in evidence_set["items"]:
            if isinstance(item["signal"]["value"], str):
                item["signal"]["value"] = None

    with pytest.raises(CandidateAcceptanceError, match="localized string evidence is incomplete"):
        summarize_candidate_payloads(payloads)


def test_candidate_acceptance_rejects_assistant_evidence_drift() -> None:
    payloads = _payloads()
    payloads["assistant_check"]["response"]["evidence_refs"] = ["req_risk_1"]

    with pytest.raises(CandidateAcceptanceError, match="assistant evidence does not match selected item"):
        summarize_candidate_payloads(payloads)


def test_candidate_acceptance_rejects_missing_terra_analyst_assignment() -> None:
    payloads = _payloads()
    payloads["model_routing"]["policy"]["agent_assignments"]["df-roi-analyst"] = {
        "primary_route_id": "analysis",
        "fallback_route_id": "terra",
    }

    with pytest.raises(CandidateAcceptanceError, match="operations analyst routing is not Terra-first"):
        summarize_candidate_payloads(payloads)
