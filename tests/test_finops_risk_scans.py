from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import backend.finops.risk_scans as risk_scans
from backend.finops.anomalies import AnomalyEvaluationInput
from backend.finops.models import FinOpsRequestEvent
from backend.finops.risk_scans import (
    InMemoryRiskScanRepository,
    RiskScanScope,
    RiskScanService,
)


_NOW = datetime(2026, 8, 3, 2, 0, tzinfo=timezone.utc)


def _event(
    index: int,
    *,
    failed: bool = False,
    latency_ms: int = 500,
    cache_state: str = "hit",
    gateway_coverage: str = "apim_governed",
    priced: bool = True,
) -> FinOpsRequestEvent:
    return FinOpsRequestEvent.model_validate(
        {
            "request_ref": f"req_scan_{index:012d}",
            "occurred_at": _NOW + timedelta(seconds=index),
            "call_class": "model",
            "tenant_ref": "tenant-a",
            "workspace_id": "ws-a",
            "agent_id": "agent-a",
            "deployment": "model-a",
            "status": "failed" if failed else "succeeded",
            "latency_ms": latency_ms,
            "tokens": {"total": 100 + index},
            "cache": {"state": cache_state, "eligible": True},
            "gateway_coverage": gateway_coverage,
            "estimated_cost": {
                "amount": 0.01 if priced else None,
                "currency": "USD",
                "status": "estimated" if priced else "unavailable",
            },
            "evidence_state": "observed" if priced else "partial",
        }
    )


def _scope() -> RiskScanScope:
    return RiskScanScope(
        workspace_id="ws-a",
        from_value="2026-08-02T02:00:00Z",
        to_value="2026-08-03T02:15:00Z",
    )


def test_scan_persists_all_rule_outcomes_and_policy_specific_evidence() -> None:
    events = [
        _event(
            index,
            failed=index in {17, 18, 19},
            latency_ms=6_000 if index == 16 else 500,
            cache_state="miss" if index in {14, 15} else "hit",
            gateway_coverage="unmanaged" if index == 19 else "apim_governed",
            priced=index != 13,
        )
        for index in range(20)
    ]
    repository = InMemoryRiskScanRepository()
    service = RiskScanService(repository)

    scan = service.run(
        tenant_ref="tenant-a",
        scope=_scope(),
        evaluation=AnomalyEvaluationInput(
            events=events,
            apim_coverage_threshold_pct=100,
            unpriced_threshold_pct=0,
            cache_hit_rate_threshold_pct=95,
        ),
        policy_revision="policy-7",
        ledger_revision="ledger-42",
        initiated_by_ref="actor-admin",
        now=_NOW,
    )

    assert scan.status == "completed"
    assert scan.rules_evaluated == 7
    assert len(scan.findings) == 7
    assert scan.rules_triggered == 5
    assert scan.policy_revision == "policy-7"
    assert scan.ledger_revision == "ledger-42"
    assert scan.request_sample_count == 20
    by_policy = {item.policy_type: item for item in scan.findings}
    assert by_policy["error_rate"].status == "triggered"
    assert by_policy["error_rate"].evidence_refs[0] == "req_scan_000000000019"
    assert by_policy["p95_latency"].status == "triggered"
    assert by_policy["p95_latency"].evidence_refs[0] == "req_scan_000000000016"
    assert by_policy["cache_hit_rate"].evidence_refs[:2] == [
        "req_scan_000000000015",
        "req_scan_000000000014",
    ]
    assert by_policy["daily_cost_budget"].status == "unavailable"
    assert by_policy["token_spike"].status == "unavailable"

    persisted = repository.get("tenant-a", scan.scan_ref)
    assert persisted == scan
    assert repository.get("tenant-b", scan.scan_ref) is None


def test_scan_marks_small_samples_insufficient_instead_of_creating_risk() -> None:
    service = RiskScanService(InMemoryRiskScanRepository())

    scan = service.run(
        tenant_ref="tenant-a",
        scope=_scope(),
        evaluation=AnomalyEvaluationInput(events=[_event(1)]),
        policy_revision="policy-7",
        ledger_revision="ledger-42",
        initiated_by_ref="actor-admin",
        now=_NOW,
    )

    by_policy = {item.policy_type: item for item in scan.findings}
    assert by_policy["error_rate"].status == "insufficient_data"
    assert by_policy["p95_latency"].status == "insufficient_data"
    assert by_policy["cache_hit_rate"].status == "insufficient_data"
    assert by_policy["apim_coverage"].status == "clear"
    assert by_policy["unpriced_requests"].status == "clear"
    assert scan.rules_triggered == 0
    assert scan.rules_insufficient == 3


def test_latest_scan_is_scoped_by_tenant_workspace_and_filter_fingerprint() -> None:
    repository = InMemoryRiskScanRepository()
    service = RiskScanService(repository)
    first = service.run(
        tenant_ref="tenant-a",
        scope=_scope(),
        evaluation=AnomalyEvaluationInput(events=[_event(1)]),
        policy_revision="policy-7",
        ledger_revision="ledger-41",
        initiated_by_ref="actor-admin",
        now=_NOW,
    )
    second_scope = _scope().model_copy(update={"model": "model-a"})
    second = service.run(
        tenant_ref="tenant-a",
        scope=second_scope,
        evaluation=AnomalyEvaluationInput(events=[_event(2)]),
        policy_revision="policy-8",
        ledger_revision="ledger-42",
        initiated_by_ref="actor-admin",
        now=_NOW + timedelta(minutes=1),
    )

    assert service.latest(tenant_ref="tenant-a", scope=_scope()) == first
    assert service.latest(tenant_ref="tenant-a", scope=second_scope) == second
    assert service.latest(tenant_ref="tenant-b", scope=_scope()) is None


def test_scan_service_has_no_governance_action_execution_dependency() -> None:
    service = RiskScanService(InMemoryRiskScanRepository())

    assert not hasattr(service, "execute")
    assert not hasattr(service, "governance_actions")


def test_scan_persists_running_before_completed_and_lists_history() -> None:
    class RecordingRepository(InMemoryRiskScanRepository):
        def __init__(self) -> None:
            super().__init__()
            self.statuses: list[str] = []

        def save(self, value):
            self.statuses.append(value.status)
            return super().save(value)

    repository = RecordingRepository()
    service = RiskScanService(repository)
    first = service.run(
        tenant_ref="tenant-a",
        scope=_scope(),
        evaluation=AnomalyEvaluationInput(events=[_event(1)]),
        policy_revision="policy-7",
        ledger_revision="ledger-41",
        initiated_by_ref="actor-admin",
        now=_NOW,
    )
    second = service.run(
        tenant_ref="tenant-a",
        scope=_scope(),
        evaluation=AnomalyEvaluationInput(events=[_event(2)]),
        policy_revision="policy-8",
        ledger_revision="ledger-42",
        initiated_by_ref="actor-admin",
        now=_NOW + timedelta(minutes=1),
    )

    assert repository.statuses == ["running", "completed", "running", "completed"]
    assert service.list(tenant_ref="tenant-a", workspace_id="ws-a", limit=5) == [second, first]
    assert service.list(tenant_ref="tenant-b", workspace_id="ws-a", limit=5) == []


def test_scan_persists_safe_failed_state_when_evaluation_raises(monkeypatch) -> None:
    repository = InMemoryRiskScanRepository()
    service = RiskScanService(repository)
    monkeypatch.setattr(
        risk_scans,
        "evaluate_default_anomalies",
        lambda _evaluation: (_ for _ in ()).throw(RuntimeError("secret provider detail")),
    )

    with pytest.raises(RuntimeError, match="secret provider detail"):
        service.run(
            tenant_ref="tenant-a",
            scope=_scope(),
            evaluation=AnomalyEvaluationInput(events=[_event(1)]),
            policy_revision="policy-7",
            ledger_revision="ledger-42",
            initiated_by_ref="actor-admin",
            now=_NOW,
            scan_ref="rscan_00000000000000000000000000000001",
        )

    failed = repository.get("tenant-a", "rscan_00000000000000000000000000000001")
    assert failed is not None
    assert failed.status == "failed"
    assert failed.safe_error_category == "risk_scan_evaluation_failed"
    assert "secret" not in failed.model_dump_json()


def test_scan_coverage_counts_bound_request_evidence_not_available_rules() -> None:
    events = [_event(index) for index in range(20)]
    scan = RiskScanService(InMemoryRiskScanRepository()).run(
        tenant_ref="tenant-a",
        scope=_scope(),
        evaluation=AnomalyEvaluationInput(events=events),
        policy_revision="policy-7",
        ledger_revision="ledger-42",
        initiated_by_ref="actor-admin",
        now=_NOW,
    )

    assert scan.rules_evaluated == 7
    assert scan.rules_unavailable == 2
    assert scan.evidence_bound_findings == 7
    assert scan.evidence_coverage_pct == 100
    assert all(finding.evidence_refs for finding in scan.findings)
