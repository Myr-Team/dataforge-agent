from __future__ import annotations

import pytest

from eval.run_p2_baseline import build_report, load_runs, reference_cases


REQUIRED_CASE_SHAPES = {
    "site_channel_selection",
    "growth_retention",
    "pricing_productization",
    "operations",
    "campaign_service",
    "risk_data_readiness",
}


def test_reference_cases_cover_required_domain_neutral_shapes() -> None:
    cases = reference_cases()

    assert {case["shape"] for case in cases} == REQUIRED_CASE_SHAPES
    for case in cases:
        assert case["goal"]
        assert case["schema_roles"]
        assert case["evidence_strength"]
        assert case["expected_required_agents"]
        assert case["known_unrelated_source_topics"]


def test_baseline_report_separates_observed_and_fixture_metrics() -> None:
    report = build_report(reference_cases(), observed_runs=[])

    assert report["evidence_kind"] == "fixture"
    assert report["production_claim_allowed"] is False
    assert report["sample_count"] == 6
    assert report["metrics"]["market_relevance"] is None


def test_observed_report_requires_build_and_run_lineage() -> None:
    with pytest.raises(ValueError, match="build_id"):
        build_report(reference_cases(), observed_runs=[{"run_id": "r1"}])


def test_observed_report_only_aggregates_reported_measurements() -> None:
    report = build_report(
        reference_cases(),
        observed_runs=[
            {
                "build_id": "build-123",
                "run_id": "run-1",
                "metrics": {"latency_ms": 42, "market_relevance": 0.8},
            }
        ],
    )

    assert report["evidence_kind"] == "observed"
    assert report["production_claim_allowed"] is True
    assert report["sample_count"] == 1
    assert report["metrics"]["latency_ms"] == 42.0
    assert report["metrics"]["market_relevance"] == 0.8
    assert report["metrics"]["tokens"] is None


def test_saved_observed_run_json_accepts_a_utf8_bom(tmp_path) -> None:
    capture = tmp_path / "observed-runs.json"
    capture.write_text(
        '[{"build_id": "build-123", "run_id": "run-1"}]',
        encoding="utf-8-sig",
    )

    assert load_runs(capture) == [{"build_id": "build-123", "run_id": "run-1"}]
