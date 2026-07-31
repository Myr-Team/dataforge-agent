from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.finops.anomalies import AnomalyEvaluationInput, evaluate_default_anomalies
from backend.finops.governance import (
    ActionConflict,
    ActionPermissionDenied,
    FinOpsActionService,
    InMemoryActionRepository,
    RecordingExecutor,
)
from backend.finops.executors import ApimPolicyExecutor
from backend.finops.executors import PriceCardActivationExecutor
from backend.finops.dataforge_clients import (
    DataForgeCachePolicyClient,
    DataForgeModelRouteClient,
)
from backend.finops.management import FinOpsManagementService, InMemoryManagementRepository
from backend.finops.price_card_client import ManagementPriceCardClient
from backend.finops.models import FinOpsRequestEvent, TokenUsage


def _events(
    count: int,
    *,
    failed: int = 0,
    latency_ms: int = 1000,
    governed: int | None = None,
    priced: int | None = None,
    cache_hits: int = 0,
    cache_eligible: int = 0,
) -> list[FinOpsRequestEvent]:
    now = datetime(2026, 7, 24, 2, 0, tzinfo=timezone.utc)
    governed = count if governed is None else governed
    priced = count if priced is None else priced
    rows: list[FinOpsRequestEvent] = []
    for index in range(count):
        rows.append(
            FinOpsRequestEvent.model_validate(
                {
                    "request_ref": f"req_{index:012x}",
                    "occurred_at": now + timedelta(seconds=index),
                    "call_class": "model",
                    "tenant_ref": "tenant-a",
                    "workspace_id": "ws-a",
                    "status": "failed" if index < failed else "succeeded",
                    "tokens": TokenUsage(total=100),
                    "latency_ms": latency_ms,
                    "cache": {
                        "state": "hit" if index < cache_hits else "miss",
                        "eligible": index < cache_eligible,
                    },
                    "gateway_coverage": "apim_governed" if index < governed else "app_observed",
                    "estimated_cost": {
                        "amount": 0.01 if index < priced else None,
                        "currency": "USD",
                        "status": "estimated" if index < priced else "unavailable",
                        "price_card_revision": "price-1" if index < priced else None,
                    },
                    "evidence_state": "observed",
                }
            )
        )
    return rows


def test_anomaly_rules_do_not_emit_when_required_sample_is_insufficient() -> None:
    results = evaluate_default_anomalies(
        AnomalyEvaluationInput(events=_events(19, failed=10, latency_ms=5000))
    )
    assert not any(item.policy_type in {"error_rate", "p95_latency"} for item in results)


def test_anomaly_rules_emit_error_latency_coverage_unpriced_and_cache_findings() -> None:
    results = evaluate_default_anomalies(
        AnomalyEvaluationInput(
            events=_events(
                20,
                failed=2,
                latency_ms=2500,
                governed=18,
                priced=18,
                cache_hits=2,
                cache_eligible=20,
            ),
            cache_hit_rate_threshold_pct=30,
        )
    )
    by_type = {item.policy_type: item for item in results}
    assert by_type["error_rate"].severity == "critical"
    assert by_type["p95_latency"].observed_value == 2500
    assert by_type["apim_coverage"].observed_value == 90
    assert by_type["unpriced_requests"].observed_value == 10
    assert by_type["cache_hit_rate"].observed_value == 10


def test_anomaly_evaluator_honors_configured_thresholds_and_minimum_samples() -> None:
    results = evaluate_default_anomalies(
        AnomalyEvaluationInput(
            events=_events(
                20,
                failed=2,
                latency_ms=2500,
                governed=18,
                priced=18,
                cache_hits=2,
                cache_eligible=20,
            ),
            error_rate_threshold_pct=15,
            error_rate_minimum_requests=30,
            p95_latency_threshold_ms=3000,
            p95_latency_minimum_requests=30,
            apim_coverage_threshold_pct=80,
            unpriced_threshold_pct=20,
            cache_hit_rate_threshold_pct=5,
            cache_minimum_requests=30,
        )
    )

    assert results == []


def test_action_requires_different_human_approver_and_rejects_untyped_payload() -> None:
    service = FinOpsActionService(
        repository=InMemoryActionRepository(),
        executors={"model_route": RecordingExecutor(current_version="v1")},
    )
    action = service.create(
        tenant_ref="tenant-a",
        action_type="model_route",
        payload={
            "workspace_id": "ws-a",
            "route_id": "analysis",
            "deployment": "gpt-5-mini",
            "base_version": "v1",
        },
        actor_ref="actor-proposer",
        actor_kind="human",
    )
    pending = service.submit(action.action_id, tenant_ref="tenant-a", actor_ref="actor-proposer")
    assert pending.status == "pending_approval"

    with pytest.raises(ActionPermissionDenied):
        service.approve(action.action_id, tenant_ref="tenant-a", actor_ref="actor-proposer")
    with pytest.raises(ActionPermissionDenied):
        service.create(
            tenant_ref="tenant-a",
            action_type="model_route",
            payload={
                "workspace_id": "ws-a",
                "route_id": "analysis",
                "deployment": "gpt-5-mini",
                "base_version": "v1",
            },
            actor_ref="df-agent",
            actor_kind="agent",
        )
    with pytest.raises(ValueError):
        service.create(
            tenant_ref="tenant-a",
            action_type="apim_token_limit",
            payload={
                "workspace_id": "ws-a",
                "quota_tokens": 1000,
                "window_seconds": 60,
                "base_version": "v1",
                "xml": "<policies />",
            },
            actor_ref="actor-proposer",
            actor_kind="human",
        )


def test_action_create_reuses_matching_caller_supplied_id_and_rejects_conflicts() -> None:
    service = FinOpsActionService(repository=InMemoryActionRepository(), executors={})
    payload = {
        "workspace_id": "ws-a",
        "enabled": True,
        "ttl_seconds": 1800,
        "base_version": "cache-v1",
    }
    first = service.create(
        tenant_ref="tenant-a",
        action_type="cache_policy",
        payload=payload,
        actor_ref="owner-a",
        action_id="remediation_cache_opaque_123",
    )
    retry = service.create(
        tenant_ref="tenant-a",
        action_type="cache_policy",
        payload=payload,
        actor_ref="owner-a",
        action_id="remediation_cache_opaque_123",
    )

    assert retry == first
    assert len(service.list(tenant_ref="tenant-a")) == 1
    with pytest.raises(ActionConflict, match="idempotency"):
        service.create(
            tenant_ref="tenant-a",
            action_type="cache_policy",
            payload={**payload, "ttl_seconds": 3600},
            actor_ref="owner-a",
            action_id="remediation_cache_opaque_123",
        )


def test_action_execute_detects_drift_and_supports_verify_and_rollback() -> None:
    executor = RecordingExecutor(current_version="v2")
    service = FinOpsActionService(
        repository=InMemoryActionRepository(),
        executors={"cache_policy": executor},
    )
    action = service.create(
        tenant_ref="tenant-a",
        action_type="cache_policy",
        payload={
            "workspace_id": "ws-a",
            "enabled": True,
            "ttl_seconds": 600,
            "base_version": "v1",
        },
        actor_ref="actor-proposer",
        actor_kind="human",
    )
    service.submit(action.action_id, tenant_ref="tenant-a", actor_ref="actor-proposer")
    service.approve(action.action_id, tenant_ref="tenant-a", actor_ref="actor-approver")

    with pytest.raises(ActionConflict):
        service.execute(action.action_id, tenant_ref="tenant-a", actor_ref="actor-operator", actions_enabled=True)

    executor.current_version = "v1"
    executing = service.execute(
        action.action_id,
        tenant_ref="tenant-a",
        actor_ref="actor-operator",
        actions_enabled=True,
    )
    assert executing.status == "verifying"
    succeeded = service.verify(action.action_id, tenant_ref="tenant-a", actor_ref="actor-operator")
    assert succeeded.status == "succeeded"
    rolled_back = service.rollback(
        action.action_id,
        tenant_ref="tenant-a",
        actor_ref="actor-owner",
        reason="candidate validation completed",
        owner=True,
    )
    assert rolled_back.status == "rolled_back"
    assert executor.calls == ["execute", "verify", "rollback"]


class _ApimClient:
    def __init__(self, *, managed_status: int = 200, anonymous_status: int = 401, hash_matches: bool = True) -> None:
        self.managed_status = managed_status
        self.anonymous_status = anonymous_status
        self.hash_matches = hash_matches
        self.calls: list[str] = []
        self.active = "rev-current"

    def current_version(self, workspace_id: str) -> str:
        return "etag-v1"

    def create_candidate(self, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append("create_candidate")
        return {"revision_id": "rev-candidate", "previous_revision_id": "rev-current", "policy_hash": "hash-a"}

    def smoke_candidate(self, workspace_id: str, revision_id: str) -> dict[str, object]:
        assert workspace_id == "ws-a"
        self.calls.append("smoke_candidate")
        return {"managed_identity_status": self.managed_status, "anonymous_status": self.anonymous_status}

    def read_policy_hash(self, workspace_id: str, revision_id: str) -> str:
        assert workspace_id == "ws-a"
        self.calls.append("read_policy_hash")
        return "hash-a" if self.hash_matches else "hash-b"

    def activate_candidate(self, workspace_id: str, revision_id: str) -> None:
        assert workspace_id == "ws-a"
        self.calls.append("activate_candidate")
        self.active = revision_id

    def active_revision(self, workspace_id: str) -> str:
        assert workspace_id == "ws-a"
        return self.active

    def activate_revision(self, workspace_id: str, revision_id: str) -> None:
        assert workspace_id == "ws-a"
        self.calls.append(f"rollback:{revision_id}")
        self.active = revision_id


def test_apim_executor_requires_managed_200_anonymous_401_and_policy_hash_readback() -> None:
    client = _ApimClient()
    executor = ApimPolicyExecutor(client)
    payload = {
        "workspace_id": "ws-a",
        "quota_tokens": 1000,
        "window_seconds": 60,
        "base_version": "etag-v1",
    }
    result = executor.execute(payload)
    assert executor.verify(payload, result) is True
    assert executor.rollback(payload, result) is True
    assert client.calls == [
        "create_candidate",
        "smoke_candidate",
        "read_policy_hash",
        "activate_candidate",
        "rollback:rev-current",
    ]

    with pytest.raises(RuntimeError):
        ApimPolicyExecutor(_ApimClient(anonymous_status=200)).execute(payload)
    with pytest.raises(RuntimeError):
        ApimPolicyExecutor(_ApimClient(hash_matches=False)).execute(payload)


def test_price_card_action_executor_activates_and_restores_reviewed_revision() -> None:
    management = FinOpsManagementService(InMemoryManagementRepository())
    first = management.create_price_card(
        tenant_ref="tenant-a",
        actor_ref="author-a",
        items=[
            {
                "deployment": "gpt-a",
                "input_per_million": 1,
                "output_per_million": 2,
            }
        ],
    )
    management.review_price_card(
        tenant_ref="tenant-a",
        revision_id=first.revision_id,
        actor_ref="reviewer-a",
    )
    management.activate_price_card(
        tenant_ref="tenant-a",
        revision_id=first.revision_id,
        actor_ref="operator-a",
        actions_enabled=True,
    )
    second = management.create_price_card(
        tenant_ref="tenant-a",
        actor_ref="author-b",
        items=[
            {
                "deployment": "gpt-a",
                "input_per_million": 0.8,
                "output_per_million": 1.6,
            }
        ],
    )
    management.review_price_card(
        tenant_ref="tenant-a",
        revision_id=second.revision_id,
        actor_ref="reviewer-b",
    )
    client = ManagementPriceCardClient(lambda: management)
    service = FinOpsActionService(
        repository=InMemoryActionRepository(),
        executors={"price_card_activation": PriceCardActivationExecutor(client)},
    )
    action = service.create(
        tenant_ref="tenant-a",
        action_type="price_card_activation",
        payload={
            "revision_id": second.revision_id,
            "base_version": client.current_version("tenant-a", second.revision_id),
        },
        actor_ref="proposer",
    )
    service.submit(action.action_id, tenant_ref="tenant-a", actor_ref="proposer")
    service.approve(action.action_id, tenant_ref="tenant-a", actor_ref="approver")

    assert service.execute(
        action.action_id,
        tenant_ref="tenant-a",
        actor_ref="operator",
        actions_enabled=True,
    ).status == "verifying"
    assert service.verify(
        action.action_id,
        tenant_ref="tenant-a",
        actor_ref="operator",
    ).status == "succeeded"
    assert client.verify_active("tenant-a", second.revision_id) is True
    assert service.rollback(
        action.action_id,
        tenant_ref="tenant-a",
        actor_ref="owner",
        reason="candidate validation complete",
        owner=True,
    ).status == "rolled_back"
    assert client.verify_active("tenant-a", first.revision_id) is True


class _WorkspaceConfigStore:
    def __init__(self) -> None:
        self.model_policy = {"revision": 1, "assignments": {}}
        self.cache_policy = {"version": 1, "enabled": True, "ttl_seconds": 3600}

    def load_model_policy(self, workspace_id: str) -> dict[str, object]:
        return dict(self.model_policy)

    def save_model_policy(self, workspace_id: str, policy: dict[str, object]) -> None:
        self.model_policy = dict(policy)

    def load_cache_policy(self, workspace_id: str) -> dict[str, object]:
        return dict(self.cache_policy)

    def save_cache_policy(self, workspace_id: str, policy: dict[str, object]) -> None:
        self.cache_policy = dict(policy)


class _Route:
    route_id = "analysis"
    deployment = "gpt-5-mini"
    capabilities = frozenset({"analysis"})


def test_dataforge_model_and_cache_clients_apply_verify_and_restore_versioned_config() -> None:
    store = _WorkspaceConfigStore()
    model_client = DataForgeModelRouteClient(store=store, route_loader=lambda: [_Route()])
    model_payload = {
        "workspace_id": "ws-a",
        "route_id": "analysis",
        "deployment": "gpt-5-mini",
        "execution_kind": "full_analysis",
        "base_version": "1",
    }
    assert model_client.current_version("ws-a") == "1"
    model_result = model_client.apply(model_payload)
    assert model_client.verify(model_payload, model_result) is True
    assert store.model_policy["assignments"]["full_analysis"]["primary_route_id"] == "analysis"
    assert model_client.restore(model_payload, model_result) is True
    assert store.model_policy == {"revision": 1, "assignments": {}}

    cache_client = DataForgeCachePolicyClient(store=store)
    cache_payload = {
        "workspace_id": "ws-a",
        "enabled": False,
        "ttl_seconds": 600,
        "base_version": "1",
    }
    cache_result = cache_client.apply(cache_payload)
    assert cache_client.verify(cache_payload, cache_result) is True
    assert store.cache_policy["enabled"] is False
    assert cache_client.restore(cache_payload, cache_result) is True
    assert store.cache_policy == {"version": 1, "enabled": True, "ttl_seconds": 3600}


def test_dataforge_model_client_rejects_deployment_not_owned_by_allowlisted_route() -> None:
    client = DataForgeModelRouteClient(
        store=_WorkspaceConfigStore(),
        route_loader=lambda: [_Route()],
    )
    with pytest.raises(ValueError):
        client.apply(
            {
                "workspace_id": "ws-a",
                "route_id": "analysis",
                "deployment": "unmanaged-deployment",
                "execution_kind": "full_analysis",
                "base_version": "1",
            }
        )
