from __future__ import annotations

from backend.finops.roi_economics import build_roi_economics


def test_unit_economics_and_verified_funnel_require_complete_evidence() -> None:
    payload = build_roi_economics(
        cost_evidence={"status": "complete", "total": 12, "currency": "USD"},
        outcome_evidence={
            "status": "verified",
            "outcome_event_ids": ["outcome-a"],
            "verified_outcome_event_ids": ["outcome-a"],
        },
        realized_roi={
            "status": "verified",
            "value": 30,
            "currency": "USD",
            "net_value": 18,
            "roi_ratio": 1.5,
        },
        requests=10,
        successful_requests=8,
        analyses=4,
        artifacts=3,
        scenarios=[{"scenario_id": "roi_scenario_aaaaaaaaaaaaaaaa", "status": "estimated"}],
    )

    assert payload["unit_economics"]["cost_per_successful_request"]["value"] == 1.5
    assert payload["unit_economics"]["cost_per_analysis"]["value"] == 3
    assert payload["unit_economics"]["cost_per_artifact"]["value"] == 4
    assert [stage["id"] for stage in payload["funnel"]] == [
        "investment", "usage", "output", "outcome",
    ]
    assert payload["funnel"][-1]["status"] == "verified"
    assert payload["scenarios"][0]["status"] == "estimated"


def test_partial_cost_and_zero_denominators_never_emit_unsupported_values() -> None:
    payload = build_roi_economics(
        cost_evidence={"status": "incomplete", "total": None, "currency": None},
        outcome_evidence={"status": "observed", "outcome_event_ids": ["outcome-a"]},
        realized_roi={"status": "incomplete", "roi_ratio": None},
        requests=0,
        successful_requests=0,
        analyses=0,
        artifacts=0,
        scenarios=[],
    )

    assert all(item["value"] is None for item in payload["unit_economics"].values())
    assert payload["verified_roi"]["value"] is None
    assert payload["verified_roi"]["status"] == "incomplete"
    assert "完整成本证据" in payload["evidence_gaps"]
    assert "独立验证的业务结果" in payload["evidence_gaps"]
