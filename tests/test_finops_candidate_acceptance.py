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
                {"value": 1.87}, {"value": 145}, {"value": 42}, {"value": 0.31}
            ],
            "value_bridge": {
                "formula_revision": "roi-scenario-v1",
                "scenario_id": "scenario-1",
                "payback_months": 3.4,
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
                {"request_ref": f"req_{index}"} for index in range(4)
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
    assert summary["risk"]["distinct_evidence"] == 4
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
