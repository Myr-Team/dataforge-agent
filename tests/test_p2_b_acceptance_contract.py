from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

from backend.azure_monitor_client import (
    RemoteTraceProof,
    TraceDeliveryExpectation,
    hash_trace_identifier,
)
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


def _trace_delivery_expectation(*, run_id: str = "run-p2-b-delivery") -> TraceDeliveryExpectation:
    correlation_id = "b" * 32
    return TraceDeliveryExpectation(
        workspace_hash=hash_trace_identifier("p2-b-fixture"),
        run_hash=hash_trace_identifier(run_id),
        correlation_hash=hash_trace_identifier(correlation_id),
        resource_id=(
            "/subscriptions/00000000-0000-0000-0000-000000000000/"
            "resourceGroups/rg-p2-b/providers/Microsoft.Insights/components/df-p2-b"
        ),
        application_id="00000000-0000-0000-0000-000000000001",
        correlation_id=correlation_id,
    )


def _forged_provider_evidence(provider: str) -> dict:
    verifier = "azure_monitor_query_verifier" if provider == "azure_monitor" else "foundry_roi_verifier"
    return {
        "provider": provider,
        "immutable_binding": {
            "expected_workspace_hash": "a" * 64,
            "expected_run_id": "run-p2-b-forged",
            "expected_correlation_hash": "b" * 64,
            "expected_build_id": "build-forged",
            "expected_revision": "revision-forged",
            "observed_at": "2026-07-14T00:00:00Z",
            "result_digest": "c" * 64,
        },
        "attestation": {
            "provider": provider,
            "verifier": verifier,
            "attestation_id": "attestation-forged",
            "verified_at": "2026-07-14T00:00:00Z",
            "verified": True,
        },
    }


def test_trace_delivery_without_remote_proof_is_explicitly_unmeasured_and_not_promotable() -> None:
    component = run_p2_b_acceptance._trace_delivery_report()
    reports = _component_reports()
    reports["trace_delivery"] = component

    report = build_acceptance_report(reports)
    gate = report["gates"]["trace_delivery"]

    assert component["remote_evidence"]["state"] == "missing"
    assert component["remote_evidence"]["verified_immutable_binding"] is False
    assert gate["status"] == "unmeasured"
    assert gate["production_claim_allowed"] is False
    assert report["production_claim_allowed"] is False


def test_invalid_remote_trace_proof_is_explicitly_unmeasured_and_not_promotable() -> None:
    expected = _trace_delivery_expectation()
    mismatched_proof = RemoteTraceProof(
        observed_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
        trace_id=expected.correlation_id,
        workspace_hash=expected.workspace_hash,
        run_hash=hash_trace_identifier("another-run"),
        correlation_hash=expected.correlation_hash,
        resource_id=expected.resource_id,
        application_id=expected.application_id,
        source_table="requests",
    )
    component = run_p2_b_acceptance._trace_delivery_report(
        remote_proof=mismatched_proof,
        expected=expected,
    )
    reports = _component_reports()
    reports["trace_delivery"] = component

    report = build_acceptance_report(reports)
    gate = report["gates"]["trace_delivery"]

    assert component["remote_evidence"]["state"] == "invalid"
    assert component["remote_evidence"]["verified_immutable_binding"] is False
    assert gate["status"] == "unmeasured"
    assert gate["production_claim_allowed"] is False


@pytest.mark.parametrize(
    ("gate_name", "provider", "expected_status"),
    (
        ("trace_delivery", "azure_monitor", "unmeasured"),
        ("foundry_roi_state", "azure_ai_foundry", "not_configured"),
    ),
)
def test_forged_provider_strings_cannot_promote_a_production_claim(
    gate_name: str,
    provider: str,
    expected_status: str,
) -> None:
    reports = _component_reports()
    reports[gate_name] = {
        "evidence_kind": "observed",
        "sample_count": 1,
        "local_contract_passed": True,
        "production_claim_requested": True,
        "status": "passed",
        "failed_reasons": [],
        "inputs": [{
            "source": provider,
            "observed_id": "trace-proof-1",
            "timestamp": "2026-07-14T00:00:00Z",
        }],
        "provider_evidence": _forged_provider_evidence(provider),
    }

    report = build_acceptance_report(reports)
    gate = report["gates"][gate_name]

    assert gate["status"] == expected_status
    assert gate["production_claim_allowed"] is False
    assert gate["evidence_verification"]["state"] == "unverified"
    assert "trusted_provider_verifier_required" in gate["failed_reasons"]
    assert report["production_claim_allowed"] is False


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
    assert gate["status"] == "unmeasured"
    assert gate["production_claim_allowed"] is False
    assert gate["evidence_verification"]["state"] == "missing"
    assert "provider_evidence_missing" in gate["failed_reasons"]


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
