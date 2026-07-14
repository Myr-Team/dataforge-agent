from __future__ import annotations

import json

import pytest

from eval import run_p2_b_acceptance
from eval.run_p2_b_acceptance import (
    REQUIRED_GATES,
    build_acceptance_report,
    component_reports,
)


def _component_reports() -> dict[str, dict]:
    reports: dict[str, dict] = {}
    for gate in REQUIRED_GATES:
        reports[gate] = {
            "evidence_kind": "fixture",
            "sample_count": 1,
            "local_contract_passed": True,
            "production_claim_requested": False,
            "status": "passed",
            "failed_reasons": [],
            "inputs": [{"source": f"p2-b-{gate}-fixture"}],
        }
    reports["trace_delivery"]["status"] = "unmeasured"
    reports["foundry_roi_state"]["status"] = "not_configured"
    return reports


def test_acceptance_requires_all_governance_gates_and_preserves_unmeasured_states() -> None:
    report = build_acceptance_report(_component_reports())

    assert set(report["gates"]) == set(REQUIRED_GATES)
    assert report["passed"] is True
    assert report["production_claim_allowed"] is False
    assert report["gates"]["trace_delivery"]["status"] == "unmeasured"
    assert report["gates"]["foundry_roi_state"]["status"] == "not_configured"
    assert set(report["unmeasured_gates"]) == {"trace_delivery", "foundry_roi_state"}


def test_observed_evidence_needs_stable_lineage_before_it_can_support_a_production_gate() -> None:
    reports = _component_reports()
    reports["trace_delivery"] = {
        "evidence_kind": "observed",
        "sample_count": 1,
        "local_contract_passed": True,
        "production_claim_requested": True,
        "status": "passed",
        "failed_reasons": [],
        "inputs": [{
            "source": "azure_monitor",
            "observed_id": "trace-proof-1",
            "timestamp": "2026-07-14T00:00:00Z",
        }],
    }

    report = build_acceptance_report(reports)

    assert report["gates"]["trace_delivery"]["production_claim_allowed"] is True


def test_observed_evidence_without_lineage_cannot_promote_a_production_claim() -> None:
    reports = _component_reports()
    reports["trace_delivery"] = {
        "evidence_kind": "observed",
        "sample_count": 1,
        "local_contract_passed": True,
        "production_claim_requested": True,
        "status": "passed",
        "failed_reasons": [],
        "inputs": [{"source": "azure_monitor"}],
    }

    report = build_acceptance_report(reports)

    gate = report["gates"]["trace_delivery"]
    assert gate["local_contract_passed"] is False
    assert gate["production_claim_allowed"] is False
    assert "observed_lineage_required" in gate["failed_reasons"]


def test_component_reports_are_deterministic_and_do_not_claim_azure_delivery_or_native_roi() -> None:
    report = build_acceptance_report(component_reports())

    assert report["passed"] is True
    assert report["production_claim_allowed"] is False
    assert report["gates"]["trace_delivery"]["status"] == "unmeasured"
    assert report["gates"]["foundry_roi_state"]["status"] == "not_configured"
    assert all(gate["evidence_kind"] == "fixture" for gate in report["gates"].values())


def test_acceptance_rejects_missing_or_unknown_governance_gate() -> None:
    reports = _component_reports()
    reports.pop("authorization")
    with pytest.raises(ValueError, match="missing required component reports"):
        build_acceptance_report(reports)

    reports = _component_reports()
    reports["audit_redaction"]["status"] = "invented"
    with pytest.raises(ValueError, match="status"):
        build_acceptance_report(reports)


def test_cli_writes_machine_readable_report(tmp_path) -> None:
    output = tmp_path / "p2-b-acceptance.json"

    assert run_p2_b_acceptance.main(["--output", str(output)]) == 0

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["report_kind"] == "p2_b_azure_governance_acceptance"
    assert report["passed"] is True
    assert report["production_claim_allowed"] is False
