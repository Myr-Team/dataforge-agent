from __future__ import annotations

from backend.finops.opportunities import build_opportunity_queue


def test_opportunities_rank_by_impact_confidence_and_effort() -> None:
    queue = build_opportunity_queue(
        anomalies=[
            {
                "anomaly_id": "a-low",
                "policy_type": "cache_hit_rate",
                "severity": "warning",
                "sample_count": 50,
                "status": "open",
                "evidence_state": "observed",
            },
            {
                "anomaly_id": "a-high",
                "policy_type": "daily_cost_budget",
                "severity": "critical",
                "sample_count": 80,
                "status": "open",
                "evidence_state": "observed",
            },
        ],
        recommendations=[
            {"policy_type": "daily_cost_budget", "recommendation": "复核高成本模型路由"},
            {"policy_type": "cache_hit_rate", "recommendation": "检查缓存资格与键策略"},
        ],
        priced_cost=100,
        priced_coverage_pct=100,
    )

    assert queue[0]["policy_type"] == "daily_cost_budget"
    assert queue[0]["impact"] == "high"
    assert queue[0]["confidence"] == "high"
    assert queue[0]["estimated_savings"] is not None
    assert queue[0]["action_status"] == "suggested"


def test_insufficient_sample_is_observing_and_unpriced_never_estimates_savings() -> None:
    queue = build_opportunity_queue(
        anomalies=[{
            "anomaly_id": "a-watch",
            "policy_type": "cache_hit_rate",
            "severity": "warning",
            "sample_count": 8,
            "status": "open",
            "evidence_state": "partial",
        }],
        recommendations=[],
        priced_cost=None,
        priced_coverage_pct=0,
    )

    assert queue[0]["queue_state"] == "observing"
    assert queue[0]["estimated_savings"] is None
    assert queue[0]["action_status"] == "suggested"


def test_cache_opportunity_preserves_clicked_evidence_references() -> None:
    queue = build_opportunity_queue(
        anomalies=[{
            "anomaly_id": "a-cache",
            "policy_type": "cache_hit_rate",
            "severity": "warning",
            "sample_count": 30,
            "status": "open",
            "evidence_state": "observed",
            "evidence_refs": ["req_miss_0001", "req_bypass_0002"],
        }],
        recommendations=[],
        priced_cost=1.0,
        priced_coverage_pct=100,
    )

    assert queue[0]["evidence_refs"] == [
        "req_miss_0001",
        "req_bypass_0002",
    ]
