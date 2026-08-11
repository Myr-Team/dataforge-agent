from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.finops.anomalies import AnomalyEvaluationInput, evaluate_default_anomalies
from backend.finops.synthetic_demo import (
    DEMO_ANCHOR,
    DEMO_BATCH_ID,
    DEMO_SCENARIO_ID,
    build_synthetic_demo_bundle,
    reconcile_synthetic_demo,
)


def _bundle():
    return build_synthetic_demo_bundle(
        workspace_id="demo-corpus",
        batch_id=DEMO_BATCH_ID,
        anchor_at=DEMO_ANCHOR,
        seed="shenzhen-finops-v1",
    )


def test_shenzhen_bundle_is_deterministic_and_has_the_declared_scale() -> None:
    first = _bundle()
    second = _bundle()

    assert first.canonical_digest == second.canonical_digest
    assert first.anchor_at == datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
    assert first.scenario_id == DEMO_SCENARIO_ID
    assert first.batch_id == DEMO_BATCH_ID
    assert len(first.analysis_tasks) == 96
    assert len(first.request_facts) == 2480
    assert len(first.reports) == 78
    assert len(first.evidence_review_tasks) == 18
    assert first.monthly_ai_operating_cost_usd == pytest.approx(206.40)
    assert all(item.provenance == "synthetic_demo" for item in first.request_facts)
    assert all("客户" not in item.title and "销售" not in item.title for item in first.analysis_tasks)


def test_bundle_reconciles_request_run_correlation_attempt_and_safe_trace() -> None:
    bundle = _bundle()
    report = reconcile_synthetic_demo(bundle)

    assert report.ok is True
    assert report.request_count == report.run_count == report.attempt_count == 2480
    assert len({item.request_ref for item in bundle.request_facts}) == 2480
    assert len({item.run_id for item in bundle.request_facts}) == 2480
    assert len({item.correlation_ref for item in bundle.request_facts}) == 2480
    assert len({item.attempt_ref for item in bundle.request_facts}) == 2480
    assert all(item.steps and item.model_attempts for item in bundle.runs)
    assert all(item.route_evidence == "synthetic" for item in bundle.model_attempts)


def test_bundle_prices_only_exact_catalog_mappings_and_keeps_cache_layers_separate() -> None:
    bundle = _bundle()

    assert {item.provider_type for item in bundle.model_attempts} == {"azure_foundry", "deepseek"}
    assert all(
        item.official_price_key and item.price_card_revision
        for item in bundle.model_attempts
        if item.cost_usd is not None
    )
    repeat = list(bundle.request_facts[:2])
    assert [item.result_cache.state for item in repeat] == ["miss", "hit"]
    assert repeat[1].result_cache.source_result_version == repeat[0].result_id
    assert repeat[0].provider_cache.evidence_state != repeat[0].result_cache.state


def test_bundle_keeps_scenario_measured_and_demo_verified_process_evidence_distinct() -> None:
    bundle = _bundle()

    assert bundle.roi.scenario_monthly_benefit_usd == pytest.approx(6240)
    assert bundle.roi.monthly_operating_input_usd == pytest.approx(1586.40)
    assert bundle.roi.scenario_roi_pct == pytest.approx(293.3)
    assert bundle.roi.measured_paired_evaluations == 18
    assert bundle.roi.measured_historical_hours == pytest.approx(17.8)
    assert bundle.roi.measured_assisted_hours == pytest.approx(8.1)
    assert bundle.roi.demo_reviewed_savings_hours == pytest.approx(174.6)
    assert bundle.roi.production_quality_claim is False
    assert bundle.roi.demo_verified_label == "演示验证结果 · 合成数据"
    assert bundle.roi.outcome_actor_ref != bundle.roi.reviewer_actor_ref


def test_generated_signals_are_scanned_by_existing_rules_with_real_request_refs() -> None:
    bundle = _bundle()

    findings = evaluate_default_anomalies(
        AnomalyEvaluationInput(
            events=list(bundle.events),
            trailing_token_median=1120,
            daily_budget_usd=5,
        )
    )
    policies = {item.policy_type for item in findings}
    assert {
        "error_rate",
        "p95_latency",
        "daily_cost_budget",
        "token_spike",
        "apim_coverage",
        "unpriced_requests",
        "cache_hit_rate",
    } <= policies
    event_refs = {event.request_ref for event in bundle.events}
    assert all(ref in event_refs for item in findings for ref in item.evidence_refs)


def test_bundle_refuses_non_allowlisted_workspace() -> None:
    with pytest.raises(PermissionError, match="allowlisted"):
        build_synthetic_demo_bundle(
            workspace_id="other-workspace",
            batch_id=DEMO_BATCH_ID,
            anchor_at=DEMO_ANCHOR,
            seed="shenzhen-finops-v1",
        )
