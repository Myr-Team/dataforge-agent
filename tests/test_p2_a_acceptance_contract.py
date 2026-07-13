from __future__ import annotations

from eval import run_p2_a_acceptance
from eval.run_p2_a_acceptance import build_acceptance_report, component_reports


def _component_reports() -> dict[str, dict]:
    return {
        "baseline": {
            "evidence_kind": "fixture",
            "sample_count": 6,
            "production_claim_allowed": False,
            "lineage": [{"source": "eval/p2_reference_cases.json"}],
        },
        "market_relevance": {
            "evidence_kind": "fixture",
            "sample_count": 2,
            "production_claim_allowed": False,
            "passed": True,
            "failed_reasons": [],
            "inputs": [{"source": "market-gate-fixture", "accepted_sources": 1}],
        },
        "maf_quality": {
            "evidence_kind": "fixture",
            "sample_count": 7,
            "production_claim_allowed": False,
            "passed": True,
            "failed_reasons": [],
            "inputs": [{"source": "eval/maf_runtime_cases.json"}],
            "metrics": {"latency_ms": 12.5, "tokens": None},
        },
        "tasks": {
            "evidence_kind": "fixture",
            "sample_count": 1,
            "production_claim_allowed": False,
            "passed": True,
            "failed_reasons": [],
            "inputs": [{"source": "task-store-smoke"}],
        },
        "connectors": {
            "evidence_kind": "fixture",
            "sample_count": 1,
            "production_claim_allowed": False,
            "passed": True,
            "failed_reasons": [],
            "inputs": [{"source": "connector-store-smoke"}],
        },
    }


def test_acceptance_requires_every_gate_and_evidence_label() -> None:
    report = build_acceptance_report(_component_reports())

    assert set(report["gates"]) >= {
        "market_relevance",
        "maf_quality",
        "tasks",
        "connectors",
    }
    assert all(
        gate["evidence_kind"] in {"fixture", "observed"}
        for gate in report["gates"].values()
    )
    assert all("sample_count" in gate for gate in report["gates"].values())
    assert all("production_claim_allowed" in gate for gate in report["gates"].values())
    assert all("failed_reasons" in gate for gate in report["gates"].values())
    assert all("inputs_lineage" in gate for gate in report["gates"].values())


def test_fixture_evidence_cannot_measure_latency_or_tokens_as_production() -> None:
    report = build_acceptance_report(_component_reports())

    for gate in report["gates"].values():
        assert gate["production_claim_allowed"] is False
        assert gate["metrics"]["latency_ms"] == "unmeasured"
        assert gate["metrics"]["tokens"] == "unmeasured"


def test_observed_zero_metrics_remain_measured() -> None:
    reports = _component_reports()
    reports["tasks"] = {
        "evidence_kind": "observed",
        "sample_count": 1,
        "production_claim_allowed": True,
        "passed": True,
        "failed_reasons": [],
        "inputs": [{"build_id": "build-1", "run_id": "run-1"}],
        "metrics": {"latency_ms": 0, "tokens": 0},
    }

    report = build_acceptance_report(reports)

    assert report["gates"]["tasks"]["metrics"] == {"latency_ms": 0, "tokens": 0}


def test_acceptance_preserves_failed_reasons_and_input_lineage() -> None:
    reports = _component_reports()
    reports["connectors"]["passed"] = False
    reports["connectors"]["failed_reasons"] = ["secret_store_unavailable"]

    report = build_acceptance_report(reports)

    assert report["passed"] is False
    assert report["gates"]["connectors"]["failed_reasons"] == [
        "secret_store_unavailable"
    ]
    assert report["gates"]["connectors"]["inputs_lineage"] == [
        {"source": "connector-store-smoke"}
    ]


def test_component_reports_run_existing_component_checks() -> None:
    reports = component_reports()

    assert set(reports) == {
        "baseline",
        "market_relevance",
        "maf_quality",
        "tasks",
        "connectors",
    }
    assert all(report["passed"] for report in reports.values())


def test_component_report_exception_becomes_a_failed_gate(monkeypatch) -> None:
    def fail_maf() -> dict:
        raise RuntimeError("private provider failure")

    monkeypatch.setattr(run_p2_a_acceptance, "_maf_report", fail_maf)

    report = build_acceptance_report(component_reports())

    assert report["passed"] is False
    assert report["gates"]["maf_quality"]["failed_reasons"] == [
        "component_execution_failed"
    ]
