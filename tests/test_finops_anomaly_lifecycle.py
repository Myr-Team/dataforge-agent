from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.finops.anomalies import AnomalyEvaluationInput, DetectedAnomaly, evaluate_default_anomalies
from backend.finops.models import FinOpsRequestEvent
from backend.finops.anomaly_store import (
    AnomalyConflict,
    AnomalyNotFound,
    FinOpsAnomalyService,
    InMemoryAnomalyRepository,
)


def _finding(*, anomaly_id: str = "anomaly_error_ws-a") -> DetectedAnomaly:
    return DetectedAnomaly(
        anomaly_id=anomaly_id,
        policy_type="error_rate",
        severity="warning",
        observed_value=8.0,
        threshold_value=5.0,
        sample_count=25,
        workspace_ids=["ws-a"],
        recommendation="Inspect the failed request source.",
    )


def test_anomaly_lifecycle_acknowledge_suppress_and_auto_resolve() -> None:
    repository = InMemoryAnomalyRepository()
    service = FinOpsAnomalyService(repository)
    current = service.reconcile(tenant_ref="tenant-a", findings=[_finding()])
    assert current[0].status == "open"

    acknowledged = service.acknowledge(
        tenant_ref="tenant-a",
        anomaly_id=current[0].anomaly_id,
        actor_ref="actor-admin",
    )
    assert acknowledged.status == "acknowledged"
    assert acknowledged.acknowledged_by == "actor-admin"

    suppressed = service.suppress(
        tenant_ref="tenant-a",
        anomaly_id=current[0].anomaly_id,
        actor_ref="actor-admin",
        reason="known maintenance",
        until=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    )
    assert suppressed.status == "suppressed"
    assert suppressed.suppression_reason == "known maintenance"

    resolved = service.reconcile(tenant_ref="tenant-a", findings=[])
    assert resolved[0].status == "resolved"
    assert resolved[0].resolved_at is not None


def test_anomaly_lifecycle_is_tenant_scoped_and_rejects_invalid_transitions() -> None:
    service = FinOpsAnomalyService(InMemoryAnomalyRepository())
    anomaly = service.reconcile(tenant_ref="tenant-a", findings=[_finding()])[0]

    with pytest.raises(AnomalyNotFound):
        service.acknowledge(
            tenant_ref="tenant-b",
            anomaly_id=anomaly.anomaly_id,
            actor_ref="actor-admin",
        )

    service.acknowledge(
        tenant_ref="tenant-a",
        anomaly_id=anomaly.anomaly_id,
        actor_ref="actor-admin",
    )
    with pytest.raises(AnomalyConflict):
        service.acknowledge(
            tenant_ref="tenant-a",
            anomaly_id=anomaly.anomaly_id,
            actor_ref="actor-admin",
        )


def test_anomaly_reopens_after_resolution_when_condition_returns() -> None:
    service = FinOpsAnomalyService(InMemoryAnomalyRepository())
    anomaly = service.reconcile(tenant_ref="tenant-a", findings=[_finding()])[0]
    assert service.reconcile(tenant_ref="tenant-a", findings=[])[0].status == "resolved"

    reopened = service.reconcile(tenant_ref="tenant-a", findings=[_finding()])[0]
    assert reopened.anomaly_id == anomaly.anomaly_id
    assert reopened.status == "open"
    assert reopened.resolved_at is None


def test_detected_anomaly_identity_is_stable_when_observed_value_changes() -> None:
    def event(index: int, *, failed: bool) -> FinOpsRequestEvent:
        return FinOpsRequestEvent.model_validate(
            {
                "request_ref": f"req_{index:020d}",
                "occurred_at": datetime(
                    2026,
                    7,
                    24,
                    1,
                    0,
                    index % 60,
                    tzinfo=timezone.utc,
                ),
                "call_class": "model",
                "tenant_ref": "tenant-a",
                "workspace_id": "ws-a",
                "status": "failed" if failed else "succeeded",
                "tokens": {"total": 10},
                "gateway_coverage": "apim_governed",
                "estimated_cost": {"amount": 0.001, "currency": "USD", "status": "estimated"},
                "evidence_state": "observed",
            }
        )

    first = evaluate_default_anomalies(
        AnomalyEvaluationInput(events=[event(index, failed=index < 2) for index in range(20)])
    )
    second = evaluate_default_anomalies(
        AnomalyEvaluationInput(events=[event(index, failed=index < 3) for index in range(20)])
    )
    first_error = next(item for item in first if item.policy_type == "error_rate")
    second_error = next(item for item in second if item.policy_type == "error_rate")

    assert first_error.observed_value != second_error.observed_value
    assert first_error.anomaly_id == second_error.anomaly_id
