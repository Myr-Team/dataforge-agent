from __future__ import annotations

import json

from backend.context_evaluation import (
    EvaluationSummary,
    candidate_route_eligible,
    evaluate_context_candidate,
    load_evaluation_gate,
)


def test_candidate_route_is_ineligible_when_evidence_coverage_regresses() -> None:
    summary = EvaluationSummary(
        sample_count=20,
        baseline={"evidence_coverage": 0.90, "completion": 0.85},
        candidate={"evidence_coverage": 0.75, "completion": 0.90},
        evaluator_version="context-v1",
    )

    assert candidate_route_eligible(summary) is False


def test_candidate_route_is_ineligible_when_sample_count_is_too_small() -> None:
    summary = EvaluationSummary(
        sample_count=19,
        baseline={"evidence_coverage": 0.90, "completion": 0.85},
        candidate={"evidence_coverage": 0.90, "completion": 0.85},
        evaluator_version="context-v1",
    )

    assert candidate_route_eligible(summary) is False


def test_candidate_route_is_ineligible_when_completion_regresses() -> None:
    summary = EvaluationSummary(
        sample_count=20,
        baseline={"evidence_coverage": 0.90, "completion": 0.85},
        candidate={"evidence_coverage": 0.90, "completion": 0.80},
        evaluator_version="context-v1",
    )

    assert candidate_route_eligible(summary) is False


def test_candidate_route_is_eligible_when_thresholds_hold() -> None:
    summary = EvaluationSummary(
        sample_count=24,
        baseline={"evidence_coverage": 0.90, "completion": 0.85},
        candidate={"evidence_coverage": 0.91, "completion": 0.87},
        evaluator_version="context-v1",
    )

    assert candidate_route_eligible(summary) is True


def test_evaluate_context_candidate_returns_safe_aggregate_summary() -> None:
    cases = [
        {"case_id": "ctx-001", "scenario": "site-selection follow-up"},
        {"case_id": "ctx-002", "scenario": "pricing follow-up"},
    ]

    def runner(case: dict[str, str], *, variant: str) -> dict[str, object]:
        _ = variant
        if case["case_id"] == "ctx-001":
            return {
                "baseline": {"evidence_coverage": 0.80, "completion": 0.82, "raw_answer": "never persist"},
                "candidate": {"evidence_coverage": 0.88, "completion": 0.91, "prompt": "never persist"},
                "workspace_id": "customer-a",
            }
        return {
            "baseline": {"evidence_coverage": 0.86, "completion": 0.84},
            "candidate": {"evidence_coverage": 0.90, "completion": 0.93, "raw_evidence": "never persist"},
            "customer_name": "Confidential Corp",
        }

    summary = evaluate_context_candidate(
        cases,
        runner,
        route_id="followup",
        evaluator_version="context-v1",
        generated_at="2026-07-22T00:00:00Z",
    )

    assert summary.to_payload() == {
        "route_id": "followup",
        "status": "evaluated",
        "generated_at": "2026-07-22T00:00:00Z",
        "sample_count": 2,
        "evaluator_version": "context-v1",
        "baseline": {"evidence_coverage": 0.83, "completion": 0.83},
        "candidate": {"evidence_coverage": 0.89, "completion": 0.92},
    }


def test_load_evaluation_gate_marks_stale_summary_ineligible(tmp_path) -> None:
    path = tmp_path / "context-summary.json"
    path.write_text(
        json.dumps(
            {
                "route_id": "followup",
                "status": "evaluated",
                "generated_at": "2026-06-01T00:00:00Z",
                "sample_count": 24,
                "evaluator_version": "context-v1",
                "baseline": {"evidence_coverage": 0.90, "completion": 0.85},
                "candidate": {"evidence_coverage": 0.92, "completion": 0.88},
            }
        ),
        encoding="utf-8",
    )

    assert load_evaluation_gate(path, route_id="followup", now="2026-07-22T00:00:00Z") == {
        "status": "stale",
        "sample_count": 24,
        "evaluator_version": "context-v1",
        "eligible": False,
    }


def test_load_evaluation_gate_rejects_malformed_summary_without_leaking_fields(tmp_path) -> None:
    path = tmp_path / "context-summary.json"
    path.write_text(
        json.dumps(
            {
                "route_id": "followup",
                "status": "evaluated",
                "generated_at": "2026-07-20T00:00:00Z",
                "sample_count": "twenty-four",
                "evaluator_version": "context-v1",
                "baseline": {"evidence_coverage": 0.90, "completion": 0.85},
                "candidate": {"evidence_coverage": 0.92, "completion": 0.88},
                "prompt": "never persist",
                "customer_name": "Confidential Corp",
            }
        ),
        encoding="utf-8",
    )

    assert load_evaluation_gate(path, route_id="followup", now="2026-07-22T00:00:00Z") == {
        "status": "malformed",
        "sample_count": None,
        "evaluator_version": None,
        "eligible": False,
    }
