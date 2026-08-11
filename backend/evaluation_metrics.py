"""Pure, offline metrics for deterministic local agent evaluation."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any


class MetricInputError(ValueError):
    """Raised when a metric cannot be computed truthfully from its inputs."""


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MetricInputError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise MetricInputError(f"{field} must be a finite number")
    return result


def _non_negative_number(value: Any, field: str) -> float:
    result = _finite_number(value, field)
    if result < 0:
        raise MetricInputError(f"{field} must be non-negative")
    return result


def _positive_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise MetricInputError(f"{field} must be a positive integer")
    return value


def _non_negative_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MetricInputError(f"{field} must be a non-negative integer")
    return value


def _paired_floats(
    targets: Sequence[Any], predictions: Sequence[Any]
) -> tuple[list[float], list[float]]:
    if not targets or not predictions:
        raise MetricInputError("continuous samples must not be empty")
    if len(targets) != len(predictions):
        raise MetricInputError("targets and predictions must have the same length")
    return (
        [_finite_number(value, f"targets[{index}]") for index, value in enumerate(targets)],
        [
            _finite_number(value, f"predictions[{index}]")
            for index, value in enumerate(predictions)
        ],
    )


def continuous_regression(
    targets: Sequence[Any], predictions: Sequence[Any], *, huber_delta: float = 1.0
) -> dict[str, Any]:
    """Compute MAE, MSE, RMSE, and mean Huber loss."""

    normalized_targets, normalized_predictions = _paired_floats(targets, predictions)
    delta = _finite_number(huber_delta, "huber_delta")
    if delta <= 0:
        raise MetricInputError("huber_delta must be greater than zero")

    absolute_errors: list[float] = []
    squared_errors: list[float] = []
    huber_losses: list[float] = []
    for target, prediction in zip(normalized_targets, normalized_predictions):
        absolute_error = abs(prediction - target)
        absolute_errors.append(absolute_error)
        squared_errors.append(absolute_error * absolute_error)
        if absolute_error <= delta:
            huber_losses.append(0.5 * absolute_error * absolute_error)
        else:
            huber_losses.append(delta * (absolute_error - 0.5 * delta))

    sample_count = len(normalized_targets)
    mse = sum(squared_errors) / sample_count
    return {
        "status": "measured",
        "sample_count": sample_count,
        "invalid_count": 0,
        "mae": sum(absolute_errors) / sample_count,
        "mse": mse,
        "rmse": math.sqrt(mse),
        "huber": sum(huber_losses) / sample_count,
        "huber_delta": delta,
    }


def binary_probability(
    labels: Sequence[Any], probabilities: Sequence[Any], *, epsilon: float = 1e-15
) -> dict[str, Any]:
    """Compute binary cross-entropy and Brier score."""

    if not labels or not probabilities:
        raise MetricInputError("binary samples must not be empty")
    if len(labels) != len(probabilities):
        raise MetricInputError("labels and probabilities must have the same length")
    clipping = _finite_number(epsilon, "epsilon")
    if not 0 < clipping < 0.5:
        raise MetricInputError("epsilon must be between zero and 0.5")

    normalized_labels: list[int] = []
    normalized_probabilities: list[float] = []
    for index, label in enumerate(labels):
        if isinstance(label, bool) or not isinstance(label, int) or label not in (0, 1):
            raise MetricInputError(f"labels[{index}] must be 0 or 1")
        normalized_labels.append(label)
    for index, probability in enumerate(probabilities):
        value = _finite_number(probability, f"probabilities[{index}]")
        if value < 0 or value > 1:
            raise MetricInputError(f"probabilities[{index}] must be between 0 and 1")
        normalized_probabilities.append(value)

    bce_total = 0.0
    brier_total = 0.0
    for label, probability in zip(normalized_labels, normalized_probabilities):
        clipped = min(max(probability, clipping), 1.0 - clipping)
        bce_total -= label * math.log(clipped) + (1 - label) * math.log(1.0 - clipped)
        brier_total += (probability - label) ** 2

    sample_count = len(normalized_labels)
    return {
        "status": "measured",
        "sample_count": sample_count,
        "invalid_count": 0,
        "bce": bce_total / sample_count,
        "brier": brier_total / sample_count,
    }


def _stable_unique_strings(values: Any, field: str) -> list[str]:
    if not isinstance(values, list):
        raise MetricInputError(f"{field} must be a list")
    result: list[str] = []
    seen: set[str] = set()
    for index, value in enumerate(values):
        if not isinstance(value, str) or not value.strip():
            raise MetricInputError(f"{field}[{index}] must be a non-empty string")
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def retrieval_ranking(
    queries: Sequence[Mapping[str, Any]], *, k_values: Iterable[int] = (1, 3, 5, 10)
) -> dict[str, Any]:
    """Compute binary-relevance Recall@K, MRR, and nDCG@K.

    Queries with an empty relevant set are reported as not applicable and are not
    included in metric averages. Duplicate hits are removed before ranking.
    """

    if not queries:
        raise MetricInputError("retrieval queries must not be empty")
    normalized_k = sorted({_positive_integer(value, "k") for value in k_values})
    if not normalized_k:
        raise MetricInputError("k_values must not be empty")

    recall_totals = {value: 0.0 for value in normalized_k}
    ndcg_totals = {value: 0.0 for value in normalized_k}
    reciprocal_rank_total = 0.0
    applicable_count = 0
    not_applicable_count = 0
    permission_violations: list[str] = []
    query_results: list[dict[str, Any]] = []

    for index, query in enumerate(queries):
        if not isinstance(query, Mapping):
            raise MetricInputError(f"queries[{index}] must be an object")
        relevant = _stable_unique_strings(query.get("relevant_ids"), f"queries[{index}].relevant_ids")
        ranked = _stable_unique_strings(query.get("ranked_ids"), f"queries[{index}].ranked_ids")
        allowed_raw = query.get("allowed_ids")
        allowed = set(_stable_unique_strings(allowed_raw, f"queries[{index}].allowed_ids")) if allowed_raw is not None else None
        if allowed is not None:
            permission_violations.extend(hit for hit in ranked if hit not in allowed)

        if not relevant:
            not_applicable_count += 1
            query_results.append({"status": "not_applicable", "top_ids": ranked})
            continue

        applicable_count += 1
        relevant_set = set(relevant)
        first_relevant_rank = next(
            (rank for rank, hit in enumerate(ranked, start=1) if hit in relevant_set), None
        )
        reciprocal_rank_total += 0.0 if first_relevant_rank is None else 1.0 / first_relevant_rank
        per_query: dict[str, Any] = {"status": "measured", "top_ids": ranked}
        for cutoff in normalized_k:
            top_hits = ranked[:cutoff]
            recall = len(relevant_set.intersection(top_hits)) / len(relevant_set)
            recall_totals[cutoff] += recall
            dcg = sum(
                1.0 / math.log2(rank + 1)
                for rank, hit in enumerate(top_hits, start=1)
                if hit in relevant_set
            )
            ideal_count = min(len(relevant_set), cutoff)
            ideal_dcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_count + 1))
            ndcg = dcg / ideal_dcg
            ndcg_totals[cutoff] += ndcg
            per_query[f"recall_at_{cutoff}"] = recall
            per_query[f"ndcg_at_{cutoff}"] = ndcg
        per_query["reciprocal_rank"] = (
            0.0 if first_relevant_rank is None else 1.0 / first_relevant_rank
        )
        query_results.append(per_query)

    status = "measured" if applicable_count else "not_applicable"
    divisor = applicable_count or 1
    return {
        "status": status,
        "sample_count": applicable_count,
        "invalid_count": 0,
        "not_applicable_count": not_applicable_count,
        "permission_violation_count": len(permission_violations),
        "permission_violation_ids": sorted(set(permission_violations)),
        "mrr": reciprocal_rank_total / divisor if applicable_count else None,
        "recall_at_k": {
            str(cutoff): recall_totals[cutoff] / divisor if applicable_count else None
            for cutoff in normalized_k
        },
        "ndcg_at_k": {
            str(cutoff): ndcg_totals[cutoff] / divisor if applicable_count else None
            for cutoff in normalized_k
        },
        "queries": query_results,
    }


def grounding_contract(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Evaluate deterministic claim-to-authorized-evidence reference propagation."""

    if not cases:
        raise MetricInputError("grounding cases must not be empty")
    claim_count = 0
    supported_count = 0
    unsupported_count = 0
    invalid_refs: set[str] = set()

    for case_index, case in enumerate(cases):
        if not isinstance(case, Mapping):
            raise MetricInputError(f"cases[{case_index}] must be an object")
        allowed = set(
            _stable_unique_strings(
                case.get("allowed_evidence_refs"),
                f"cases[{case_index}].allowed_evidence_refs",
            )
        )
        claims = case.get("claims")
        if not isinstance(claims, list) or not claims:
            raise MetricInputError(f"cases[{case_index}].claims must not be empty")
        for claim_index, claim in enumerate(claims):
            if not isinstance(claim, Mapping):
                raise MetricInputError(
                    f"cases[{case_index}].claims[{claim_index}] must be an object"
                )
            refs = _stable_unique_strings(
                claim.get("evidence_refs"),
                f"cases[{case_index}].claims[{claim_index}].evidence_refs",
            )
            claim_count += 1
            invalid_for_claim = [reference for reference in refs if reference not in allowed]
            if refs and not invalid_for_claim:
                supported_count += 1
            else:
                unsupported_count += 1
                invalid_refs.update(invalid_for_claim)

    if claim_count == 0:
        raise MetricInputError("grounding claims must not be empty")
    return {
        "status": "reference_contract",
        "measurement_scope": "reference_contract",
        "sample_count": claim_count,
        "invalid_count": 0,
        "supported_claim_count": supported_count,
        "unsupported_claim_count": unsupported_count,
        "claim_coverage": supported_count / claim_count,
        "unsupported_claim_rate": unsupported_count / claim_count,
        "invalid_evidence_refs": sorted(invalid_refs),
        "reference_propagation_completeness": supported_count / claim_count,
    }


def unit_economics(
    *,
    cost: Mapping[str, Any],
    successful_requests: Any,
    verified_outcomes: Any,
    outcome_evidence_status: Any,
    monetized_benefit: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute comparable unit economics without upgrading evidence authority."""

    if not isinstance(cost, Mapping):
        raise MetricInputError("cost must be an object")
    amount = _non_negative_number(cost.get("amount"), "cost.amount")
    currency = cost.get("currency")
    status = cost.get("status")
    window = cost.get("window")
    if not isinstance(currency, str) or not currency:
        raise MetricInputError("cost.currency must be a non-empty string")
    if not isinstance(window, str) or not window:
        raise MetricInputError("cost.window must be a non-empty string")
    successes = _non_negative_integer(successful_requests, "successful_requests")
    outcomes = _non_negative_integer(verified_outcomes, "verified_outcomes")
    if not isinstance(outcome_evidence_status, str):
        raise MetricInputError("outcome_evidence_status must be a string")

    cost_complete = status == "complete"
    evidence_verified = outcome_evidence_status == "verified"
    result: dict[str, Any] = {
        "status": "measured" if cost_complete else "incomplete",
        "currency": currency,
        "window": window,
        "cost_status": status,
        "outcome_evidence_status": outcome_evidence_status,
        "cost_per_success": amount / successes if cost_complete and successes > 0 else None,
        "cost_per_verified_outcome": (
            amount / outcomes if cost_complete and evidence_verified and outcomes > 0 else None
        ),
        "net_verified_value": None,
        "verified_roi": None,
        "verified_status": "unavailable",
    }

    if monetized_benefit is not None:
        if not isinstance(monetized_benefit, Mapping):
            raise MetricInputError("monetized_benefit must be an object")
        benefit_amount = _non_negative_number(
            monetized_benefit.get("amount"), "monetized_benefit.amount"
        )
        benefit_currency = monetized_benefit.get("currency")
        benefit_status = monetized_benefit.get("status")
        benefit_window = monetized_benefit.get("window")
        comparable = benefit_currency == currency and benefit_window == window
        benefit_verified = benefit_status == "verified"
        if cost_complete and evidence_verified and benefit_verified and comparable:
            net_value = benefit_amount - amount
            result["net_verified_value"] = net_value
            result["verified_roi"] = net_value / amount if amount > 0 else None
            result["verified_status"] = "verified"
        elif benefit_status in {"estimated", "scenario"}:
            result["verified_status"] = "unavailable"

    return result
