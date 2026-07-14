"""Generate a machine-readable P2-B Azure governance acceptance report.

This evaluator is intentionally deterministic and offline.  It validates the
local contracts that protect governance data, but it never promotes an Azure
Monitor delivery or native Foundry ROI claim without separate observed evidence.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


REQUIRED_GATES = (
    "trace_configuration",
    "trace_delivery",
    "local_roi_state",
    "foundry_roi_state",
    "chargeback_lineage",
    "invitation_claim_matching",
    "audit_redaction",
    "authorization",
)
EVIDENCE_KINDS = frozenset({"fixture", "observed"})
GATE_STATES = frozenset({"passed", "failed", "unmeasured", "not_configured"})
LOCAL_EVALUATOR_PRODUCTION_PROMOTION = False
REMOTE_PROVIDER_GATES = {
    "trace_delivery": {
        "provider": "azure_monitor",
        "fallback_status": "unmeasured",
        "binding_fields": (
            "expected_workspace_hash",
            "expected_run_id",
            "expected_correlation_hash",
            "expected_build_id",
            "expected_revision",
            "observed_at",
            "result_digest",
        ),
        "attestation_fields": (
            "provider",
            "verifier",
            "attestation_id",
            "verified_at",
        ),
        "verifier": "azure_monitor_query_verifier",
    },
    "foundry_roi_state": {
        "provider": "azure_ai_foundry",
        "fallback_status": "not_configured",
        "binding_fields": (
            "expected_workspace_hash",
            "expected_run_id",
            "expected_correlation_hash",
            "expected_build_id",
            "expected_revision",
            "observed_at",
            "result_digest",
        ),
        "attestation_fields": (
            "provider",
            "verifier",
            "attestation_id",
            "verified_at",
        ),
        "verifier": "foundry_roi_verifier",
    },
}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _provider_evidence_verification(name: str, component: Mapping[str, Any]) -> dict[str, Any]:
    """Describe why caller-provided provider evidence cannot be trusted offline.

    This evaluator has no Azure credential, signed verifier, or provider query
    result.  It can describe the immutable binding required for a production
    claim, but must never elevate caller-supplied strings into such a claim.
    """
    requirement = REMOTE_PROVIDER_GATES.get(name)
    if requirement is None:
        return {"state": "not_applicable", "verified_immutable_binding": False}

    required = {
        "provider": requirement["provider"],
        "binding_fields": list(requirement["binding_fields"]),
        "attestation_fields": list(requirement["attestation_fields"]),
        "verifier": requirement["verifier"],
    }
    evidence = component.get("provider_evidence")
    if not isinstance(evidence, Mapping):
        return {
            "state": "missing",
            "verified_immutable_binding": False,
            "required": required,
            "reasons": ["provider_evidence_missing"],
        }

    binding = evidence.get("immutable_binding")
    attestation = evidence.get("attestation")
    invalid_fields: list[str] = []
    if evidence.get("provider") != requirement["provider"]:
        invalid_fields.append("provider")
    if not isinstance(binding, Mapping):
        invalid_fields.append("immutable_binding")
    else:
        invalid_fields.extend(
            field for field in requirement["binding_fields"] if not _text(binding.get(field))
        )
    if not isinstance(attestation, Mapping):
        invalid_fields.append("attestation")
    else:
        invalid_fields.extend(
            field for field in requirement["attestation_fields"] if not _text(attestation.get(field))
        )
        if attestation.get("provider") != requirement["provider"]:
            invalid_fields.append("attestation.provider")
        if attestation.get("verifier") != requirement["verifier"]:
            invalid_fields.append("attestation.verifier")
    if invalid_fields:
        return {
            "state": "invalid",
            "verified_immutable_binding": False,
            "required": required,
            "invalid_fields": sorted(set(invalid_fields)),
            "reasons": ["provider_evidence_invalid"],
        }

    return {
        "state": "unverified",
        "verified_immutable_binding": False,
        "required": required,
        "reasons": [
            "trusted_provider_verifier_required",
            "offline_evaluator_cannot_verify_provider_evidence",
        ],
    }


def _gate(name: str, component: Mapping[str, Any]) -> dict[str, Any]:
    evidence_kind = str(component.get("evidence_kind") or "")
    if evidence_kind not in EVIDENCE_KINDS:
        raise ValueError(f"{name} evidence_kind must be fixture or observed")
    sample_count = component.get("sample_count")
    if not isinstance(sample_count, int) or isinstance(sample_count, bool) or sample_count < 0:
        raise ValueError(f"{name} sample_count must be a non-negative integer")
    status = str(component.get("status") or "")
    if status not in GATE_STATES:
        raise ValueError(f"{name} status is invalid")

    local_contract_passed = bool(component.get("local_contract_passed"))
    requested = bool(component.get("production_claim_requested"))
    failed_reasons = [str(value) for value in _as_list(component.get("failed_reasons")) if str(value)]
    inputs = _as_list(component.get("inputs"))
    evidence_verification = _provider_evidence_verification(name, component)
    if name in REMOTE_PROVIDER_GATES:
        fallback_status = REMOTE_PROVIDER_GATES[name]["fallback_status"]
        if status == "passed" or evidence_kind == "observed":
            status = fallback_status
        if requested:
            local_contract_passed = False
            failed_reasons.extend(evidence_verification.get("reasons", []))
            failed_reasons.append("offline_evaluator_cannot_authorize_production_claim")
    if status == "failed":
        local_contract_passed = False
    if not local_contract_passed and not failed_reasons:
        failed_reasons.append("local_contract_failed")

    return {
        "status": status,
        "evidence_kind": evidence_kind,
        "sample_count": sample_count,
        "local_contract_passed": local_contract_passed,
        "production_claim_allowed": False,
        "failed_reasons": sorted(set(failed_reasons)),
        "inputs_lineage": inputs,
        "evidence_verification": evidence_verification,
    }


def build_acceptance_report(component_reports: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Normalize local and observed evidence without hiding unmeasured Azure gates."""
    missing = [name for name in REQUIRED_GATES if name not in component_reports]
    if missing:
        raise ValueError(f"missing required component reports: {', '.join(missing)}")
    unexpected = sorted(set(component_reports) - set(REQUIRED_GATES))
    if unexpected:
        raise ValueError(f"unexpected component reports: {', '.join(unexpected)}")

    gates = {name: _gate(name, component_reports[name]) for name in REQUIRED_GATES}
    failed_gates = [name for name, gate in gates.items() if not gate["local_contract_passed"]]
    unmeasured_gates = [
        name for name, gate in gates.items()
        if gate["status"] in {"unmeasured", "not_configured"}
    ]
    return {
        "schema_version": 1,
        "report_kind": "p2_b_azure_governance_acceptance",
        "measurement_scope": "deterministic_local_governance_contract",
        "passed": not failed_gates,
        "failed_gates": failed_gates,
        "unmeasured_gates": unmeasured_gates,
        "production_claim_allowed": False,
        "production_claim_policy": {
            "local_evaluator_can_promote": LOCAL_EVALUATOR_PRODUCTION_PROMOTION,
            "reason": "offline_evaluator_has_no_trusted_provider_verifier",
        },
        "gates": gates,
    }


def _fixture_gate(
    *,
    passed: bool,
    source: str,
    case: str,
    status: str = "passed",
    failed_reason: str | None = None,
) -> dict[str, Any]:
    return {
        "evidence_kind": "fixture",
        "sample_count": 1,
        "local_contract_passed": passed,
        "production_claim_requested": False,
        "status": status,
        "failed_reasons": [] if passed or not failed_reason else [failed_reason],
        "inputs": [{"source": source, "case": case}],
    }


def _trace_configuration_report() -> dict[str, Any]:
    from backend.azure_monitor_client import build_trace_status

    status = build_trace_status(
        configured=True,
        local_emit_at=None,
        local_exporter_callback_at=None,
        remote_proof=None,
        expected=None,
        correlation_id="a" * 32,
    )
    return _fixture_gate(
        passed=status.state == "partial" and status.correlation_id == "a" * 32,
        source="backend.azure_monitor_client.build_trace_status",
        case="configured_exporter_without_remote_proof_is_partial",
        failed_reason="trace_configuration_contract_failed",
    )


def _trace_delivery_report(*, remote_proof: Any = None, expected: Any = None) -> dict[str, Any]:
    """Exercise the local Azure Monitor proof matcher without claiming a query ran."""
    from backend.azure_monitor_client import (
        TraceDeliveryExpectation,
        build_trace_status,
        hash_trace_identifier,
    )

    expectation = expected or TraceDeliveryExpectation(
        workspace_hash=hash_trace_identifier("p2-b-fixture"),
        run_hash=hash_trace_identifier("run-p2-b-delivery"),
        correlation_hash=hash_trace_identifier("b" * 32),
        resource_id=(
            "/subscriptions/00000000-0000-0000-0000-000000000000/"
            "resourceGroups/rg-p2-b/providers/Microsoft.Insights/components/df-p2-b"
        ),
        application_id="00000000-0000-0000-0000-000000000001",
        correlation_id="b" * 32,
    )
    status = build_trace_status(
        configured=True,
        local_emit_at=None,
        local_exporter_callback_at=None,
        remote_proof=remote_proof,
        expected=expectation,
        correlation_id=expectation.correlation_id,
    )
    if remote_proof is None:
        evidence_state = "missing"
        expected_state = "partial"
    elif status.state == "connected":
        evidence_state = "synthetic_fixture"
        expected_state = "connected"
    else:
        evidence_state = "invalid"
        expected_state = "partial"
    gate = _fixture_gate(
        passed=status.state == expected_state,
        source="backend.azure_monitor_client.build_trace_status",
        case=f"remote_trace_proof_{evidence_state}_is_not_a_production_claim",
        status="unmeasured",
        failed_reason="trace_delivery_contract_failed",
    )
    gate["remote_evidence"] = {
        "provider": "azure_monitor",
        "state": evidence_state,
        "verified_immutable_binding": False,
        "reason": "offline_fixture_does_not_execute_or_attest_an_azure_monitor_query",
    }
    return gate


def _local_roi_report() -> dict[str, Any]:
    from backend.roi_service import build_roi_snapshot

    workspace_id = "p2-b-fixture"
    snapshot = build_roi_snapshot(
        workspace_id,
        {"from": "2026-07-14T00:00:00Z", "to": "2026-07-15T00:00:00Z"},
        runs=[{
            "workspace_id": workspace_id,
            "run_id": "run_p2_b_fixture",
            "completed_at": "2026-07-14T12:00:00Z",
            "models": [{"model": "gpt-5", "usage": {"input_tokens": 3, "output_tokens": 2}}],
        }],
        outcomes=[],
        prices=[],
        source_validator=lambda _workspace_id, _source: True,
    )
    return _fixture_gate(
        passed=bool(
            snapshot["lineage_complete"]
            and snapshot["observed_run_ids"] == ["run_p2_b_fixture"]
            and snapshot["status"] == "estimated"
        ),
        source="backend.roi_service.build_roi_snapshot",
        case="local_run_lineage_without_invented_business_value",
        failed_reason="local_roi_contract_failed",
    )


def _foundry_roi_report() -> dict[str, Any]:
    from backend.foundry_roi import FoundryRoiStatus

    status = FoundryRoiStatus(
        state="not_configured",
        reason="No native Foundry ROI observation supplied to the deterministic evaluator",
    )
    return _fixture_gate(
        passed=status.state == "not_configured" and status.configured is False,
        source="backend.foundry_roi.FoundryRoiStatus",
        case="native_roi_requires_external_discovery_and_attestation",
        status="not_configured",
        failed_reason="foundry_roi_state_contract_failed",
    )


def _chargeback_lineage_report() -> dict[str, Any]:
    from backend.invitation_store import member_subject_label
    from backend.roi_service import member_chargeback

    workspace_id = "p2-b-fixture"
    actor = {"actor_id": "fixture-actor", "tenant_id": "fixture-tenant", "source": "easy_auth"}
    chargeback = member_chargeback(
        workspace_id,
        {"from": "2026-07-14T00:00:00Z", "to": "2026-07-15T00:00:00Z"},
        runs=[{
            "workspace_id": workspace_id,
            "run_id": "run_p2_b_chargeback",
            "completed_at": "2026-07-14T12:00:00Z",
            "trusted_identity": True,
            "actor": actor,
            "models": [{"model": "gpt-5", "usage": {"input_tokens": 3, "output_tokens": 2}}],
        }],
        messages=[],
        tasks=[],
        memberships=[{**actor, "status": "active"}],
        prices=[],
        pseudonym_salt="p2-b-acceptance-fixture-salt",
    )
    expected = member_subject_label(workspace_id, actor, pseudonym_salt="p2-b-acceptance-fixture-salt")
    serialized = json.dumps(chargeback, sort_keys=True)
    return _fixture_gate(
        passed=bool(
            chargeback["members"]
            and chargeback["members"][0]["member"]["subject_label"] == expected
            and actor["actor_id"] not in serialized
            and actor["tenant_id"] not in serialized
        ),
        source="backend.roi_service.member_chargeback",
        case="tenant_scoped_member_pseudonym_is_preserved",
        failed_reason="chargeback_lineage_contract_failed",
    )


def _invitation_claim_report() -> dict[str, Any]:
    from backend import invitation_store

    workspace_id = "p2-b-fixture"
    accepted_actor = {
        "actor_id": "fixture-invitee",
        "tenant_id": "fixture-tenant",
        "email": "fixture-invitee@example.invalid",
        "source": "easy_auth",
    }
    meta: dict[str, Any] = {}
    with patch.object(invitation_store, "blob_configured", return_value=False):
        invitation = invitation_store.create_pending_invitation(
            meta,
            workspace_id,
            email="fixture-invitee@example.invalid",
            role="viewer",
            invited_by=accepted_actor,
        )
        invitation_store.transition_invitation(
            meta,
            invitation["invitation_id"],
            "accepted",
            identity=accepted_actor,
            workspace_id=workspace_id,
        )
        accepted = invitation_store.consume_accepted_invitation(
            meta,
            workspace_id,
            {**accepted_actor, "email": "changed-email@example.invalid"},
        )
    return _fixture_gate(
        passed=bool(accepted and accepted.get("role") == "viewer"),
        source="backend.invitation_store.consume_accepted_invitation",
        case="tenant_and_oid_claim_match_is_email_independent",
        failed_reason="invitation_claim_matching_contract_failed",
    )


def _audit_redaction_report() -> dict[str, Any]:
    from backend import audit_store

    key = b"p2-b-acceptance-fixture-audit-key"
    raw_actor = {
        "name": "Fixture Private Name",
        "email": "fixture-private@example.invalid",
        "actor_id": "fixture-private-oid",
        "tenant_id": "fixture-private-tenant",
    }
    actor_hash = audit_store._actor_hash(raw_actor, key)
    metadata = audit_store._clean_metadata(
        {"correlation": {"run_id": "run-private-correlation"}},
        key,
    )
    serialized = json.dumps({"actor_hash": actor_hash, **metadata}, sort_keys=True)
    return _fixture_gate(
        passed=bool(
            re.fullmatch(r"actor_[0-9a-f]{40}", actor_hash)
            and re.fullmatch(r"corr_[0-9a-f]{40}", metadata["correlation"]["run_id"])
            and all(value not in serialized for value in raw_actor.values())
            and "run-private-correlation" not in serialized
        ),
        source="backend.audit_store._actor_hash_and_clean_metadata",
        case="actor_and_correlation_values_are_hmac_projected",
        failed_reason="audit_redaction_contract_failed",
    )


def _authorization_report() -> dict[str, Any]:
    from backend.workspace_authz import require_sensitive_workspace_permission

    denied = False
    trusted = {"actor_id": "fixture-admin", "tenant_id": "fixture-tenant", "source": "easy_auth"}
    with patch.dict(
        os.environ,
        {"DF_ENVIRONMENT": "production", "DF_SENSITIVE_AUTH_LOCAL_DEV_BYPASS": "0"},
        clear=False,
    ):
        try:
            require_sensitive_workspace_permission(
                "p2-b-fixture",
                {},
                "member.manage",
                role_resolver=lambda _workspace_id, _actor: "owner",
            )
        except PermissionError:
            denied = True
        granted = require_sensitive_workspace_permission(
            "p2-b-fixture",
            trusted,
            "member.manage",
            role_resolver=lambda _workspace_id, _actor: "admin",
        )
    return _fixture_gate(
        passed=denied and granted == "admin",
        source="backend.workspace_authz.require_sensitive_workspace_permission",
        case="untrusted_identity_denied_even_when_compatibility_rbac_is_off",
        failed_reason="authorization_contract_failed",
    )


def component_reports() -> dict[str, dict[str, Any]]:
    """Execute deterministic local P2-B contract checks without cloud calls."""
    return {
        "trace_configuration": _trace_configuration_report(),
        "trace_delivery": _trace_delivery_report(),
        "local_roi_state": _local_roi_report(),
        "foundry_roi_state": _foundry_roi_report(),
        "chargeback_lineage": _chargeback_lineage_report(),
        "invitation_claim_matching": _invitation_claim_report(),
        "audit_redaction": _audit_redaction_report(),
        "authorization": _authorization_report(),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_acceptance_report(component_reports())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "passed": report["passed"], "unmeasured_gates": report["unmeasured_gates"]}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
