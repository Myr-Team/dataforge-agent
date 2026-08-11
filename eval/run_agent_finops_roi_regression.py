"""Deterministic, local-only Agent FinOps and ROI regression runner."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.evaluation_metrics import (  # noqa: E402
    MetricInputError,
    binary_probability,
    continuous_regression,
    grounding_contract,
    retrieval_ranking,
    unit_economics,
)

DATASET_SCHEMA = "dataforge.agent-eval-dataset.v1"
CASE_SCHEMA = "dataforge.agent-eval-case.v1"
REPORT_SCHEMA = "dataforge.agent-eval-report.v1"
EXIT_PASS = 0
EXIT_REGRESSION = 1
EXIT_INVALID = 2
ALLOWED_TASK_TYPES = {
    "continuous_regression",
    "binary_probability",
    "retrieval_ranking",
    "grounded_generation",
    "unit_economics",
}
DEFAULT_CASES = ROOT / "eval" / "agent_finops_roi_cases.json"
DEFAULT_OUTPUT = Path(tempfile.gettempdir()) / "dataforge-agent-finops-local.json"


class DatasetValidationError(ValueError):
    """Raised when a dataset is malformed or not comparable."""


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise DatasetValidationError("dataset contains non-canonical values") from exc


def _sha256(value: Any) -> str:
    return f"sha256:{hashlib.sha256(_canonical_json(value)).hexdigest()}"


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DatasetValidationError(f"{field} must be an object")
    return value


def _list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise DatasetValidationError(f"{field} must be a list")
    return value


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DatasetValidationError(f"{field} must be a non-empty string")
    return value


def _number(value: Any, field: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DatasetValidationError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise DatasetValidationError(f"{field} must be finite and at least {minimum}")
    return result


def _digest_payload(dataset: Mapping[str, Any]) -> dict[str, Any]:
    dataset_meta = _mapping(dataset.get("dataset"), "dataset")
    baseline = _mapping(dataset.get("baseline"), "baseline")
    candidate = _mapping(dataset.get("candidate"), "candidate")
    return {
        "schema_version": dataset.get("schema_version"),
        "measurement_scope": dataset.get("measurement_scope"),
        "dataset": {
            "id": dataset_meta.get("id"),
            "version": dataset_meta.get("version"),
        },
        "baseline_version": baseline.get("version"),
        "candidate_version": candidate.get("version"),
        "huber_delta": dataset.get("huber_delta"),
        "tolerances": dataset.get("tolerances"),
        "cases": dataset.get("cases"),
    }


def compute_dataset_digest(dataset: Mapping[str, Any]) -> str:
    """Return the digest bound to versions, tolerances, and all evaluation cases."""

    return _sha256(_digest_payload(dataset))


def load_dataset(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
        value = json.loads(
            text,
            parse_constant=lambda token: (_ for _ in ()).throw(
                DatasetValidationError(f"non-finite JSON constant: {token}")
            ),
        )
    except DatasetValidationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DatasetValidationError("dataset could not be read as UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise DatasetValidationError("dataset root must be an object")
    return value


def validate_dataset(
    dataset: Mapping[str, Any], *, baseline_version: str, candidate_version: str
) -> str:
    if dataset.get("schema_version") != DATASET_SCHEMA:
        raise DatasetValidationError("unsupported dataset schema_version")
    if dataset.get("measurement_scope") != "sanitized_fixture":
        raise DatasetValidationError("dataset measurement_scope must be sanitized_fixture")
    metadata = _mapping(dataset.get("dataset"), "dataset")
    _text(metadata.get("id"), "dataset.id")
    _text(metadata.get("version"), "dataset.version")
    declared_digest = _text(metadata.get("digest"), "dataset.digest")

    baseline = _mapping(dataset.get("baseline"), "baseline")
    candidate = _mapping(dataset.get("candidate"), "candidate")
    if _text(baseline.get("version"), "baseline.version") != baseline_version:
        raise DatasetValidationError("baseline version does not match CLI selection")
    if _text(candidate.get("version"), "candidate.version") != candidate_version:
        raise DatasetValidationError("candidate version does not match CLI selection")

    tolerances = _mapping(dataset.get("tolerances"), "tolerances")
    for name, value in tolerances.items():
        _text(name, "tolerance name")
        _number(value, f"tolerances.{name}")

    cases = _list(dataset.get("cases"), "cases")
    if not cases:
        raise DatasetValidationError("cases must not be empty")
    seen_ids: set[str] = set()
    for index, case_value in enumerate(cases):
        case = _mapping(case_value, f"cases[{index}]")
        if case.get("schema_version") != CASE_SCHEMA:
            raise DatasetValidationError(f"cases[{index}] has an unsupported schema_version")
        case_id = _text(case.get("case_id"), f"cases[{index}].case_id")
        if case_id in seen_ids:
            raise DatasetValidationError(f"duplicate case_id: {case_id}")
        seen_ids.add(case_id)
        task_type = _text(case.get("task_type"), f"cases[{index}].task_type")
        if task_type not in ALLOWED_TASK_TYPES:
            raise DatasetValidationError(f"unsupported task_type: {task_type}")
        _text(case.get("capability"), f"cases[{index}].capability")
        _text(case.get("input_ref"), f"cases[{index}].input_ref")
        _mapping(case.get("expected"), f"cases[{index}].expected")
        _mapping(case.get("baseline"), f"cases[{index}].baseline")
        _mapping(case.get("candidate"), f"cases[{index}].candidate")

    computed_digest = compute_dataset_digest(dataset)
    compared_digests = {
        declared_digest,
        _text(baseline.get("dataset_digest"), "baseline.dataset_digest"),
        _text(candidate.get("dataset_digest"), "candidate.dataset_digest"),
    }
    if compared_digests != {computed_digest}:
        raise DatasetValidationError("dataset digest mismatch")
    return computed_digest


def _tolerance(dataset: Mapping[str, Any], name: str) -> float:
    tolerances = _mapping(dataset.get("tolerances"), "tolerances")
    if name not in tolerances:
        raise DatasetValidationError(f"missing tolerance: {name}")
    return _number(tolerances[name], f"tolerances.{name}")


def _gate(name: str, passed: bool, **evidence: Any) -> dict[str, Any]:
    return {"name": name, "status": "pass" if passed else "regression", **evidence}


def _lower_is_better_gate(
    name: str, baseline: float, candidate: float, tolerance: float, *, relative: bool
) -> dict[str, Any]:
    limit = baseline * (1.0 + tolerance) if relative else baseline + tolerance
    return _gate(
        name,
        candidate <= limit,
        baseline=baseline,
        candidate=candidate,
        tolerance=tolerance,
        limit=limit,
    )


def _higher_is_better_gate(
    name: str, baseline: float, candidate: float, tolerance: float
) -> dict[str, Any]:
    floor = baseline - tolerance
    return _gate(
        name,
        candidate >= floor,
        baseline=baseline,
        candidate=candidate,
        tolerance=tolerance,
        floor=floor,
    )


def _task_cases(dataset: Mapping[str, Any], task_type: str) -> list[Mapping[str, Any]]:
    return [
        _mapping(case, "case")
        for case in _list(dataset.get("cases"), "cases")
        if _mapping(case, "case").get("task_type") == task_type
    ]


def _evaluate_continuous(
    dataset: Mapping[str, Any], cases: list[Mapping[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    targets: list[Any] = []
    baseline_values: list[Any] = []
    candidate_values: list[Any] = []
    units: set[str] = set()
    for case in cases:
        expected = _mapping(case.get("expected"), "continuous.expected")
        baseline = _mapping(case.get("baseline"), "continuous.baseline")
        candidate = _mapping(case.get("candidate"), "continuous.candidate")
        expected_unit = _text(expected.get("unit"), "continuous.expected.unit")
        if baseline.get("unit") != expected_unit or candidate.get("unit") != expected_unit:
            raise DatasetValidationError("continuous baseline/candidate units are not comparable")
        units.add(expected_unit)
        targets.append(expected.get("value"))
        baseline_values.append(baseline.get("value"))
        candidate_values.append(candidate.get("value"))
    if len(units) != 1:
        raise DatasetValidationError("continuous cases must use one comparable unit")
    delta = _number(dataset.get("huber_delta", 1.0), "huber_delta", minimum=0.000000001)
    baseline_metrics = continuous_regression(targets, baseline_values, huber_delta=delta)
    candidate_metrics = continuous_regression(targets, candidate_values, huber_delta=delta)
    gates = [
        _lower_is_better_gate(
            "continuous.mae",
            baseline_metrics["mae"],
            candidate_metrics["mae"],
            _tolerance(dataset, "mae_relative"),
            relative=True,
        ),
        _lower_is_better_gate(
            "continuous.rmse",
            baseline_metrics["rmse"],
            candidate_metrics["rmse"],
            _tolerance(dataset, "rmse_relative"),
            relative=True,
        ),
    ]
    return {
        "unit": next(iter(units)),
        "baseline": baseline_metrics,
        "candidate": candidate_metrics,
    }, gates


def _evaluate_binary(
    dataset: Mapping[str, Any], cases: list[Mapping[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    labels = [_mapping(case.get("expected"), "binary.expected").get("label") for case in cases]
    baseline_values = [
        _mapping(case.get("baseline"), "binary.baseline").get("probability") for case in cases
    ]
    candidate_values = [
        _mapping(case.get("candidate"), "binary.candidate").get("probability") for case in cases
    ]
    baseline_metrics = binary_probability(labels, baseline_values)
    candidate_metrics = binary_probability(labels, candidate_values)
    gates = [
        _lower_is_better_gate(
            "binary.bce",
            baseline_metrics["bce"],
            candidate_metrics["bce"],
            _tolerance(dataset, "bce_absolute"),
            relative=False,
        ),
        _lower_is_better_gate(
            "binary.brier",
            baseline_metrics["brier"],
            candidate_metrics["brier"],
            _tolerance(dataset, "brier_absolute"),
            relative=False,
        ),
    ]
    return {"baseline": baseline_metrics, "candidate": candidate_metrics}, gates


def _evaluate_retrieval(
    dataset: Mapping[str, Any], cases: list[Mapping[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    baseline_queries: list[dict[str, Any]] = []
    candidate_queries: list[dict[str, Any]] = []
    traces: list[dict[str, Any]] = []
    for case in cases:
        expected = _mapping(case.get("expected"), "retrieval.expected")
        baseline = _mapping(case.get("baseline"), "retrieval.baseline")
        candidate = _mapping(case.get("candidate"), "retrieval.candidate")
        relevant_ids = expected.get("relevant_ids")
        allowed_ids = expected.get("allowed_ids")
        if not isinstance(relevant_ids, list):
            raise DatasetValidationError("retrieval.expected.relevant_ids must be a list")
        if not isinstance(allowed_ids, list):
            raise DatasetValidationError("retrieval.expected.allowed_ids must be a list")
        if not set(relevant_ids).issubset(set(allowed_ids)):
            raise DatasetValidationError("retrieval relevant IDs must be authorized")
        baseline_adapter = _text(baseline.get("adapter"), "retrieval.baseline.adapter")
        candidate_adapter = _text(candidate.get("adapter"), "retrieval.candidate.adapter")
        baseline_trace = _mapping(
            baseline.get("retrieval_trace"), "retrieval.baseline.retrieval_trace"
        )
        candidate_trace = _mapping(
            candidate.get("retrieval_trace"), "retrieval.candidate.retrieval_trace"
        )
        baseline_queries.append(
            {"relevant_ids": relevant_ids, "allowed_ids": allowed_ids, "ranked_ids": baseline.get("hit_ids")}
        )
        candidate_queries.append(
            {"relevant_ids": relevant_ids, "allowed_ids": allowed_ids, "ranked_ids": candidate.get("hit_ids")}
        )
        traces.append(
            {
                "case_id": case.get("case_id"),
                "baseline_adapter": baseline_adapter,
                "candidate_adapter": candidate_adapter,
                "baseline_trace": baseline_trace,
                "candidate_trace": candidate_trace,
            }
        )
    baseline_metrics = retrieval_ranking(baseline_queries)
    candidate_metrics = retrieval_ranking(candidate_queries)
    if baseline_metrics["status"] != "measured" or candidate_metrics["status"] != "measured":
        raise DatasetValidationError("retrieval dataset has no applicable relevant sets")
    gates = [
        _higher_is_better_gate(
            "retrieval.recall_at_5",
            baseline_metrics["recall_at_k"]["5"],
            candidate_metrics["recall_at_k"]["5"],
            _tolerance(dataset, "recall_at_5_drop"),
        ),
        _higher_is_better_gate(
            "retrieval.mrr",
            baseline_metrics["mrr"],
            candidate_metrics["mrr"],
            _tolerance(dataset, "mrr_drop"),
        ),
        _higher_is_better_gate(
            "retrieval.ndcg_at_5",
            baseline_metrics["ndcg_at_k"]["5"],
            candidate_metrics["ndcg_at_k"]["5"],
            _tolerance(dataset, "ndcg_at_5_drop"),
        ),
        _gate(
            "retrieval.permission_violations",
            baseline_metrics["permission_violation_count"] == 0
            and candidate_metrics["permission_violation_count"] == 0,
            baseline_count=baseline_metrics["permission_violation_count"],
            candidate_count=candidate_metrics["permission_violation_count"],
        ),
    ]
    return {
        "baseline": baseline_metrics,
        "candidate": candidate_metrics,
        "traces": traces,
    }, gates


def _evaluate_grounding(
    dataset: Mapping[str, Any], cases: list[Mapping[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    baseline_cases: list[dict[str, Any]] = []
    candidate_cases: list[dict[str, Any]] = []
    for case in cases:
        expected = _mapping(case.get("expected"), "grounding.expected")
        baseline = _mapping(case.get("baseline"), "grounding.baseline")
        candidate = _mapping(case.get("candidate"), "grounding.candidate")
        baseline_cases.append(
            {"allowed_evidence_refs": expected.get("allowed_evidence_refs"), "claims": baseline.get("claims")}
        )
        candidate_cases.append(
            {"allowed_evidence_refs": expected.get("allowed_evidence_refs"), "claims": candidate.get("claims")}
        )
    baseline_metrics = grounding_contract(baseline_cases)
    candidate_metrics = grounding_contract(candidate_cases)
    gate = _lower_is_better_gate(
        "grounding.unsupported_claim_rate",
        baseline_metrics["unsupported_claim_rate"],
        candidate_metrics["unsupported_claim_rate"],
        _tolerance(dataset, "unsupported_claim_rate_increase"),
        relative=False,
    )
    return {"baseline": baseline_metrics, "candidate": candidate_metrics}, [gate]


def _evaluate_unit_economics(
    dataset: Mapping[str, Any], cases: list[Mapping[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    case_results: list[dict[str, Any]] = []
    gates: list[dict[str, Any]] = []
    for case in cases:
        case_id = _text(case.get("case_id"), "unit_economics.case_id")
        baseline_input = _mapping(case.get("baseline"), "unit_economics.baseline")
        candidate_input = _mapping(case.get("candidate"), "unit_economics.candidate")
        baseline_cost = _mapping(baseline_input.get("cost"), "unit_economics.baseline.cost")
        candidate_cost = _mapping(candidate_input.get("cost"), "unit_economics.candidate.cost")
        if baseline_cost.get("currency") != candidate_cost.get("currency"):
            raise DatasetValidationError("unit economics currencies are not comparable")
        if baseline_cost.get("window") != candidate_cost.get("window"):
            raise DatasetValidationError("unit economics windows are not comparable")
        for side, source, source_cost in (
            ("baseline", baseline_input, baseline_cost),
            ("candidate", candidate_input, candidate_cost),
        ):
            benefit = source.get("monetized_benefit")
            if benefit is not None:
                benefit_mapping = _mapping(benefit, f"unit_economics.{side}.monetized_benefit")
                if benefit_mapping.get("currency") != source_cost.get("currency"):
                    raise DatasetValidationError("unit economics benefit currency is not comparable")
                if benefit_mapping.get("window") != source_cost.get("window"):
                    raise DatasetValidationError("unit economics benefit window is not comparable")
        baseline_metrics = unit_economics(
            cost=baseline_cost,
            successful_requests=baseline_input.get("successful_requests"),
            verified_outcomes=baseline_input.get("verified_outcomes"),
            outcome_evidence_status=baseline_input.get("outcome_evidence_status"),
            monetized_benefit=baseline_input.get("monetized_benefit"),
        )
        candidate_metrics = unit_economics(
            cost=candidate_cost,
            successful_requests=candidate_input.get("successful_requests"),
            verified_outcomes=candidate_input.get("verified_outcomes"),
            outcome_evidence_status=candidate_input.get("outcome_evidence_status"),
            monetized_benefit=candidate_input.get("monetized_benefit"),
        )
        case_results.append(
            {"case_id": case_id, "baseline": baseline_metrics, "candidate": candidate_metrics}
        )
        baseline_cps = baseline_metrics["cost_per_success"]
        candidate_cps = candidate_metrics["cost_per_success"]
        if baseline_cps is None or candidate_cps is None:
            gates.append(_gate(f"unit_economics.{case_id}.cost_per_success", False, reason="incomplete_cost_evidence"))
        else:
            gates.append(
                _lower_is_better_gate(
                    f"unit_economics.{case_id}.cost_per_success",
                    baseline_cps,
                    candidate_cps,
                    _tolerance(dataset, "cost_per_success_relative"),
                    relative=True,
                )
            )
        for side, metrics in (("baseline", baseline_metrics), ("candidate", candidate_metrics)):
            source = baseline_input if side == "baseline" else candidate_input
            status = source.get("outcome_evidence_status")
            benefit = source.get("monetized_benefit")
            benefit_status = benefit.get("status") if isinstance(benefit, Mapping) else None
            if status in {"estimated", "scenario"} or benefit_status in {"estimated", "scenario"}:
                gates.append(
                    _gate(
                        f"unit_economics.{case_id}.{side}.no_evidence_upgrade",
                        metrics["verified_roi"] is None and metrics["verified_status"] != "verified",
                        evidence_status=status,
                        benefit_status=benefit_status,
                    )
                )
    return {"cases": case_results}, gates


def evaluate_dataset(
    dataset: Mapping[str, Any], *, baseline_version: str, candidate_version: str
) -> dict[str, Any]:
    digest = validate_dataset(
        dataset, baseline_version=baseline_version, candidate_version=candidate_version
    )
    metrics: dict[str, Any] = {}
    gates: list[dict[str, Any]] = []
    evaluators = (
        ("continuous_regression", _evaluate_continuous),
        ("binary_probability", _evaluate_binary),
        ("retrieval_ranking", _evaluate_retrieval),
        ("grounded_generation", _evaluate_grounding),
        ("unit_economics", _evaluate_unit_economics),
    )
    try:
        for task_type, evaluator in evaluators:
            cases = _task_cases(dataset, task_type)
            if not cases:
                continue
            task_metrics, task_gates = evaluator(dataset, cases)
            metrics[task_type] = task_metrics
            gates.extend(task_gates)
    except MetricInputError as exc:
        raise DatasetValidationError(str(exc)) from exc

    if not metrics:
        raise DatasetValidationError("dataset has no evaluable cases")
    passed = all(gate["status"] == "pass" for gate in gates)
    case_count = len(_list(dataset.get("cases"), "cases"))
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA,
        "mode": "deterministic_local",
        "measurement_scope": "sanitized_fixture",
        "production_quality_claim": False,
        "network_access": False,
        "dataset": {
            "id": _mapping(dataset.get("dataset"), "dataset").get("id"),
            "version": _mapping(dataset.get("dataset"), "dataset").get("version"),
            "digest": digest,
        },
        "baseline": {"version": baseline_version, "metrics": {key: value.get("baseline") for key, value in metrics.items() if "baseline" in value}},
        "candidate": {"version": candidate_version, "metrics": {key: value.get("candidate") for key, value in metrics.items() if "candidate" in value}},
        "metrics": metrics,
        "gates": gates,
        "sample_count": case_count,
        "invalid_count": 0,
        "not_applicable_count": sum(
            int(task.get("candidate", {}).get("not_applicable_count", 0))
            for task in metrics.values()
            if isinstance(task, Mapping) and isinstance(task.get("candidate"), Mapping)
        ),
        "result": "pass" if passed else "regression",
    }
    report["report_digest"] = _sha256(report)
    return report


def _invalid_report(message: str) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA,
        "mode": "deterministic_local",
        "measurement_scope": "sanitized_fixture",
        "production_quality_claim": False,
        "network_access": False,
        "dataset": None,
        "baseline": None,
        "candidate": None,
        "metrics": {},
        "gates": [],
        "sample_count": 0,
        "invalid_count": 1,
        "not_applicable_count": 0,
        "result": "invalid",
        "error": {"code": "invalid_dataset", "message": message},
    }
    report["report_digest"] = _sha256(report)
    return report


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, allow_nan=False, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("deterministic",), default="deterministic")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--baseline", default="baseline-v1")
    parser.add_argument("--candidate", default="candidate-v1")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        dataset = load_dataset(args.cases)
        report = evaluate_dataset(
            dataset,
            baseline_version=args.baseline,
            candidate_version=args.candidate,
        )
        exit_code = EXIT_PASS if report["result"] == "pass" else EXIT_REGRESSION
    except DatasetValidationError as exc:
        report = _invalid_report(str(exc))
        exit_code = EXIT_INVALID
    _write_report(args.output, report)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "result": report["result"],
                "report_digest": report["report_digest"],
            },
            sort_keys=True,
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
