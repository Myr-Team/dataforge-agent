from __future__ import annotations

import math

import pytest

from backend.evaluation_metrics import (
    MetricInputError,
    binary_probability,
    continuous_regression,
    grounding_contract,
    retrieval_ranking,
    unit_economics,
)


def test_continuous_metrics_have_known_values() -> None:
    metrics = continuous_regression([1.0, 3.0], [2.0, 5.0], huber_delta=1.0)

    assert metrics["sample_count"] == 2
    assert metrics["mae"] == pytest.approx(1.5)
    assert metrics["mse"] == pytest.approx(2.5)
    assert metrics["rmse"] == pytest.approx(math.sqrt(2.5))
    assert metrics["huber"] == pytest.approx(1.0)


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf"), True])
def test_continuous_metrics_reject_non_finite_and_boolean_values(invalid: object) -> None:
    with pytest.raises(MetricInputError, match="finite number"):
        continuous_regression([1.0], [invalid])


def test_continuous_metrics_reject_empty_samples() -> None:
    with pytest.raises(MetricInputError, match="must not be empty"):
        continuous_regression([], [])


def test_binary_metrics_have_known_values() -> None:
    metrics = binary_probability([1, 0], [0.8, 0.2])

    assert metrics["sample_count"] == 2
    assert metrics["bce"] == pytest.approx(-math.log(0.8))
    assert metrics["brier"] == pytest.approx(0.04)


@pytest.mark.parametrize("probability", [-0.01, 1.01, float("nan"), float("inf"), True])
def test_binary_metrics_reject_invalid_probabilities(probability: object) -> None:
    with pytest.raises(MetricInputError):
        binary_probability([1], [probability])


@pytest.mark.parametrize("label", [-1, 2, 0.5, True, "1"])
def test_binary_metrics_reject_invalid_labels(label: object) -> None:
    with pytest.raises(MetricInputError, match="must be 0 or 1"):
        binary_probability([label], [0.5])


def test_retrieval_metrics_deduplicate_and_mark_empty_relevant_set_not_applicable() -> None:
    metrics = retrieval_ranking(
        [
            {
                "relevant_ids": ["a", "b"],
                "ranked_ids": ["a", "a", "c", "b"],
                "allowed_ids": ["a", "b", "c"],
            },
            {
                "relevant_ids": [],
                "ranked_ids": ["c"],
                "allowed_ids": ["c"],
            },
        ],
        k_values=(1, 3),
    )

    expected_dcg = 1.0 + 1.0 / math.log2(4)
    ideal_dcg = 1.0 + 1.0 / math.log2(3)
    assert metrics["status"] == "measured"
    assert metrics["sample_count"] == 1
    assert metrics["not_applicable_count"] == 1
    assert metrics["queries"][1]["status"] == "not_applicable"
    assert metrics["queries"][0]["top_ids"] == ["a", "c", "b"]
    assert metrics["recall_at_k"] == {"1": 0.5, "3": 1.0}
    assert metrics["mrr"] == pytest.approx(1.0)
    assert metrics["ndcg_at_k"]["3"] == pytest.approx(expected_dcg / ideal_dcg)


def test_retrieval_with_only_empty_relevant_sets_is_not_applicable() -> None:
    metrics = retrieval_ranking(
        [{"relevant_ids": [], "ranked_ids": ["a"], "allowed_ids": ["a"]}]
    )

    assert metrics["status"] == "not_applicable"
    assert metrics["sample_count"] == 0
    assert metrics["mrr"] is None
    assert metrics["recall_at_k"]["5"] is None


def test_retrieval_permission_violations_are_explicit() -> None:
    metrics = retrieval_ranking(
        [{"relevant_ids": ["a"], "ranked_ids": ["outside", "a"], "allowed_ids": ["a"]}]
    )

    assert metrics["permission_violation_count"] == 1
    assert metrics["permission_violation_ids"] == ["outside"]


def test_grounding_contract_counts_supported_and_unsupported_claims() -> None:
    metrics = grounding_contract(
        [
            {
                "allowed_evidence_refs": ["evidence-1"],
                "claims": [
                    {"evidence_refs": ["evidence-1"]},
                    {"evidence_refs": []},
                    {"evidence_refs": ["outside"]},
                ],
            }
        ]
    )

    assert metrics["measurement_scope"] == "reference_contract"
    assert metrics["claim_coverage"] == pytest.approx(1 / 3)
    assert metrics["unsupported_claim_rate"] == pytest.approx(2 / 3)
    assert metrics["invalid_evidence_refs"] == ["outside"]


def test_grounding_contract_rejects_empty_claim_samples() -> None:
    with pytest.raises(MetricInputError, match="claims must not be empty"):
        grounding_contract([{"allowed_evidence_refs": [], "claims": []}])


def test_unit_economics_computes_verified_roi_only_for_complete_verified_evidence() -> None:
    metrics = unit_economics(
        cost={"amount": 10.0, "currency": "USD", "status": "complete", "window": "w1"},
        successful_requests=5,
        verified_outcomes=2,
        outcome_evidence_status="verified",
        monetized_benefit={
            "amount": 30.0,
            "currency": "USD",
            "status": "verified",
            "window": "w1",
        },
    )

    assert metrics["cost_per_success"] == pytest.approx(2.0)
    assert metrics["cost_per_verified_outcome"] == pytest.approx(5.0)
    assert metrics["net_verified_value"] == pytest.approx(20.0)
    assert metrics["verified_roi"] == pytest.approx(2.0)
    assert metrics["verified_status"] == "verified"


@pytest.mark.parametrize("evidence_status", ["estimated", "scenario"])
def test_estimated_or_scenario_evidence_never_upgrades_to_verified(
    evidence_status: str,
) -> None:
    metrics = unit_economics(
        cost={"amount": 10.0, "currency": "USD", "status": "complete", "window": "w1"},
        successful_requests=5,
        verified_outcomes=1,
        outcome_evidence_status=evidence_status,
        monetized_benefit={
            "amount": 30.0,
            "currency": "USD",
            "status": evidence_status,
            "window": "w1",
        },
    )

    assert metrics["cost_per_verified_outcome"] is None
    assert metrics["net_verified_value"] is None
    assert metrics["verified_roi"] is None
    assert metrics["verified_status"] == "unavailable"
