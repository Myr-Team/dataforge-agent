from __future__ import annotations

from pathlib import Path

import backend.roi_scenario_store as roi_scenario_store
from backend.finops.decision_service import build_roi_decision
from backend.finops.demo_workspace_seed import _shenzhen_roi_scenario
from backend.finops.synthetic_demo import (
    DEMO_ANCHOR,
    DEMO_BATCH_ID,
    build_synthetic_demo_bundle,
)
from backend.finops.synthetic_demo_projection import build_shenzhen_browser_projection


def test_store_projection_reaches_roi_decision_with_complete_synthetic_evidence(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(roi_scenario_store, "SCENARIO_DIR", tmp_path / "scenarios")
    monkeypatch.setattr(roi_scenario_store, "blob_configured", lambda: False)
    monkeypatch.setattr(roi_scenario_store, "upload_blob_json", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(roi_scenario_store, "download_blob_json_strict", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(roi_scenario_store, "_linked_run_is_valid", lambda *_args, **_kwargs: True)
    bundle = build_synthetic_demo_bundle(
        workspace_id="demo-corpus",
        batch_id=DEMO_BATCH_ID,
        anchor_at=DEMO_ANCHOR,
        seed="shenzhen-finops-v1",
    )
    stored = roi_scenario_store.upsert_demo_roi_scenario(
        "demo-corpus",
        _shenzhen_roi_scenario(bundle),
        actor=None,
        seed_key=DEMO_BATCH_ID,
    )
    scenario = roi_scenario_store.scenario_projection("demo-corpus", stored)

    decision = build_roi_decision(
        economics={"funnel": [], "scenarios": [scenario], "verified_roi": {}},
        roi_snapshot={"usage": {"runs": 2480}, "cost_evidence": {}},
        cost_value={"outcome_evidence": {}},
        unit_trend=[],
    )

    evidence = decision["scenarios"][0]["demo_evidence"]
    assert evidence["measured"] == {
        "paired_evaluations": 18,
        "historical_hours": 17.8,
        "assisted_hours": 8.1,
    }
    assert evidence["process"] == {
        "analysis_tasks": 96,
        "reports": 78,
        "evidence_reviews": 18,
        "reviewed_savings_hours": 174.6,
    }
    assert evidence["actors"]["outcome_actor_ref"] != evidence["actors"]["reviewer_actor_ref"]
    assert evidence["window"] == {
        "from": "2026-07-12T12:00:00Z",
        "to": "2026-08-11T12:00:00Z",
        "currency": "USD",
    }
    assert set(evidence["source_refs"]) == {"run_id", "request_ref", "correlation_ref", "attempt_ref"}
    assert len(evidence["evidence_items"]["analysis_tasks"]) == 96
    assert len(evidence["evidence_items"]["reports"]) == 78
    assert len(evidence["evidence_items"]["evidence_reviews"]) == 18


def test_roi_decision_accepts_bounded_dynamic_demo_measurements_without_fixed_values() -> None:
    evidence_items = {
        "analysis_tasks": [{"id": f"task_{index:03d}", "title": f"分析任务 {index:03d}"} for index in range(1, 98)],
        "reports": [{"id": f"report_{index:03d}", "title": f"报告 {index:03d}"} for index in range(1, 80)],
        "evidence_reviews": [{"id": f"review_{index:03d}", "title": f"审阅 {index:03d}"} for index in range(1, 21)],
    }
    decision = build_roi_decision(
        economics={
            "funnel": [],
            "verified_roi": {},
            "scenarios": [{
                "scenario_id": "dynamic-demo",
                "status": "estimated",
                "inputs": {"currency": "USD"},
                "result": {},
                "demo_evidence": {
                    "provenance": "synthetic_demo",
                    "production_quality_claim": False,
                    "label": "演示验证结果 · 合成数据",
                    "measured": {"paired_evaluations": 20, "historical_hours": 20, "assisted_hours": 10},
                    "process": {"analysis_tasks": 97, "reports": 79, "evidence_reviews": 20, "reviewed_savings_hours": 200},
                    "actors": {"outcome_actor_ref": "synthetic_outcome_reviewer", "reviewer_actor_ref": "synthetic_finance_reviewer"},
                    "window": {"from": "2026-07-12T12:00:00Z", "to": "2026-08-11T12:00:00Z", "currency": "USD"},
                    "source_refs": {"run_id": "run-dynamic", "request_ref": "req_dynamic_0001", "correlation_ref": "corr_dynamic_0001", "attempt_ref": "attempt_dynamic_0001"},
                    "evidence_items": evidence_items,
                },
            }],
        },
        roi_snapshot={"usage": {"runs": 0}, "cost_evidence": {}},
        cost_value={"outcome_evidence": {}},
        unit_trend=[],
    )

    evidence = decision["scenarios"][0]["demo_evidence"]
    assert evidence["measured"]["paired_evaluations"] == 20
    assert evidence["process"] == {"analysis_tasks": 97, "reports": 79, "evidence_reviews": 20, "reviewed_savings_hours": 200.0}


def test_browser_projection_is_derived_from_bundle_scanner_and_decision_service() -> None:
    projection = build_shenzhen_browser_projection()
    bundle = build_synthetic_demo_bundle(
        workspace_id="demo-corpus",
        batch_id=DEMO_BATCH_ID,
        anchor_at=DEMO_ANCHOR,
        seed="shenzhen-finops-v1",
    )
    first = bundle.request_facts[0]
    second = bundle.request_facts[1]

    assert projection["canonical_digest"] == bundle.canonical_digest
    assert projection["refs"]["request"] == first.request_ref
    assert projection["refs"]["run"] == first.run_id
    assert projection["refs"]["correlation"] == first.correlation_ref
    assert projection["refs"]["attempt"] == first.attempt_ref
    assert projection["refs"]["hit_request"] == second.request_ref
    assert projection["refs"]["hit_run"] == second.run_id
    assert projection["summary"] == {
        "analysis_tasks": 96,
        "requests": 2480,
        "reports": 78,
        "evidence_reviews": 18,
        "monthly_cost_usd": 206.4,
    }
    assert projection["gateway_counts"] == {"apim_governed": 2349, "app_observed": 131}
    assert projection["model_counts"] == {
        "deepseek-v4-flash": 5,
        "gpt-5.6-terra": 2315,
        "site-selection-unpriced-adapter": 160,
    }
    assert set(projection["policy_refs"]) == {
        "error_rate",
        "p95_latency",
        "daily_cost_budget",
        "token_spike",
        "apim_coverage",
        "unpriced_requests",
        "cache_hit_rate",
    }
    assert len({ref for refs in projection["policy_refs"].values() for ref in refs}) == 29
    assert all(projection["assistant_by_policy"][policy]["evidence_refs"] == refs[:3] for policy, refs in projection["policy_refs"].items())
    demo = projection["endpoints"]["roi"]["scenarios"][0]["demo_evidence"]
    assert demo["process"]["reviewed_savings_hours"] == 174.6
    assert len(demo["evidence_items"]["analysis_tasks"]) == 96
