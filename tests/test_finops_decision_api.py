from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import backend.finops.router as finops_router
from auth_fixtures import trusted_headers
from backend.app import app
from backend.finops.anomalies import DetectedAnomaly
from backend.finops.anomaly_store import FinOpsAnomalyService, InMemoryAnomalyRepository
from backend.finops.models import FinOpsRequestEvent
from backend.finops.query import FinOpsQueryService
from backend.finops.query_cache import FinOpsCacheBusy
from backend.finops.repository import InMemoryFinOpsRepository


@pytest.fixture
def repository() -> InMemoryFinOpsRepository:
    value = InMemoryFinOpsRepository()
    value.upsert_events(
        [
            FinOpsRequestEvent.model_validate(
                {
                    "request_ref": "req_aaaaaaaaaaaa",
                    "occurred_at": datetime(2026, 7, 24, 2, 0, tzinfo=timezone.utc),
                    "call_class": "model",
                    "tenant_ref": "tenant-a",
                    "workspace_id": "ws-a",
                    "agent_id": "coordinator",
                    "deployment": "gpt-5-mini",
                    "status": "succeeded",
                    "tokens": {"total": 10},
                    "gateway_coverage": "apim_governed",
                    "estimated_cost": {"amount": 0.001, "currency": "USD", "status": "estimated"},
                    "evidence_state": "observed",
                }
            )
        ]
    )
    return value


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, repository: InMemoryFinOpsRepository) -> TestClient:
    monkeypatch.setenv("DF_WEB_PROXY_SECRET", "test-proxy-secret")
    monkeypatch.setenv("DF_FINOPS_HMAC_SECRET", "finops-test-secret")
    monkeypatch.setenv("DF_FINOPS_READ_ENABLED", "1")
    monkeypatch.setenv("DF_FINOPS_SQL_ENABLED", "0")
    monkeypatch.setattr(finops_router, "get_finops_query_service", lambda: FinOpsQueryService(repository))
    monkeypatch.setattr(finops_router, "_authorized_workspace_roles", lambda _actor: {"ws-a": "owner"})
    monkeypatch.setattr(finops_router, "_tenant_ref", lambda _actor: "tenant-a")
    monkeypatch.setattr(
        finops_router,
        "workspace_roi_snapshot",
        lambda workspace_id, from_value, to_value: {
            "workspace_id": workspace_id,
            "usage": {"runs": 1},
            "cost_evidence": {"status": "incomplete", "total": None, "currency": None},
            "observed_run_ids": [],
        },
        raising=False,
    )
    monkeypatch.setattr(
        finops_router,
        "workspace_cost_value_snapshot",
        lambda workspace_id, from_value, to_value: {
            "workspace_id": workspace_id,
            "cost_evidence": {"status": "incomplete", "total": None, "currency": None},
            "outcome_evidence": {"status": "not_recorded", "outcome_event_ids": [], "verified_outcome_event_ids": []},
            "realized_roi": {"status": "not_recorded", "value": None, "currency": None, "net_value": None},
            "artifact_count": 0,
            "output_trend": [],
            "scenarios": [],
        },
        raising=False,
    )
    return TestClient(app)


@pytest.fixture
def owner_headers() -> dict[str, str]:
    return trusted_headers(actor_id="owner-a", tenant_id="tenant-a")


@pytest.fixture
def member_headers() -> dict[str, str]:
    return trusted_headers(actor_id="member-a", tenant_id="tenant-a")


def test_roi_decision_returns_one_composed_payload(client: TestClient, owner_headers: dict[str, str]) -> None:
    response = client.get(
        "/api/finops/roi/decision",
        params={"workspace_id": "ws-a", "from": "2026-07-01T00:00:00Z", "to": "2026-08-01T00:00:00Z"},
        headers=owner_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["scope"]["workspace_ids"] == ["ws-a"]
    assert body["window"]["from"] == "2026-07-01T00:00:00Z"
    assert "decision" in body and "value_bridge" in body
    assert "provider_response_id" not in response.text


def test_risk_decision_does_not_trigger_agent(
    client: TestClient, owner_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    called = False

    def fail_agent(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("read endpoint must not run an agent")

    monkeypatch.setattr("backend.finops.router.run_finops_analysis", fail_agent, raising=False)
    response = client.get("/api/finops/risk/decision", params={"workspace_id": "ws-a"}, headers=owner_headers)
    assert response.status_code == 200
    assert called is False


def _managed_finding(
    *,
    anomaly_id: str,
    workspace_id: str,
    policy_type: str = "cache_hit_rate",
) -> DetectedAnomaly:
    return DetectedAnomaly(
        anomaly_id=anomaly_id,
        policy_type=policy_type,
        severity="warning",
        observed_value=10,
        threshold_value=5,
        sample_count=25,
        workspace_ids=[workspace_id],
        recommendation="Use the bounded server recommendation.",
        evidence_refs=["req_aaaaaaaaaaaa"],
    )


def test_risk_decision_preserves_managed_anomaly_state_and_server_contract(
    client: TestClient,
    owner_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anomaly_service = FinOpsAnomalyService(InMemoryAnomalyRepository())
    anomaly_service.reconcile(
        tenant_ref="tenant-b",
        findings=[_managed_finding(anomaly_id="anomaly_other_tenant", workspace_id="ws-a")],
        scope_workspace_ids=("ws-a",),
    )
    anomaly_service.reconcile(
        tenant_ref="tenant-a",
        findings=[_managed_finding(anomaly_id="anomaly_other_workspace", workspace_id="ws-b")],
        scope_workspace_ids=("ws-b",),
    )
    anomaly_service.reconcile(
        tenant_ref="tenant-a",
        findings=[_managed_finding(anomaly_id="anomaly_unscoped", workspace_id="")],
    )
    monkeypatch.setattr(finops_router, "get_finops_anomaly_service", lambda: anomaly_service)
    monkeypatch.setattr(
        finops_router,
        "evaluate_default_anomalies",
        lambda _value: [_managed_finding(anomaly_id="anomaly_cache_ws_a", workspace_id="ws-a")],
    )
    monkeypatch.setattr(
        finops_router,
        "current_remediation_base_version",
        lambda tenant_ref, workspace_id, action_kind: "cache-policy-v7",
    )

    first = client.get(
        "/api/finops/risk/decision",
        params={"workspace_id": "ws-a", "refresh": "1"},
        headers=owner_headers,
    )
    assert first.status_code == 200, first.text
    first_priority = first.json()["priorities"][0]
    assert first_priority["anomaly_id"] == "anomaly_cache_ws_a"
    assert first_priority["anomaly_status"] == "open"
    assert first_priority["applicable_actions"] == ["acknowledge", "suppress"]
    assert first_priority["base_version"] == "cache-policy-v7"
    assert first.json()["optimization_portfolio"][0]["x_effort"] in {1, 2, 3}
    assert "anomaly_other_tenant" not in first.text
    assert "anomaly_other_workspace" not in first.text
    assert "anomaly_unscoped" not in first.text

    anomaly_service.acknowledge(
        tenant_ref="tenant-a",
        anomaly_id="anomaly_cache_ws_a",
        actor_ref="secret-actor",
    )
    acknowledged = client.get(
        "/api/finops/risk/decision",
        params={"workspace_id": "ws-a", "refresh": "1"},
        headers=owner_headers,
    )
    assert acknowledged.status_code == 200, acknowledged.text
    priority = acknowledged.json()["priorities"][0]
    assert priority["anomaly_status"] == "acknowledged"
    assert priority["applicable_actions"] == ["suppress"]
    assert "secret-actor" not in acknowledged.text


def test_risk_decision_degrades_only_failed_base_version_resolution(
    client: TestClient,
    owner_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anomaly_service = FinOpsAnomalyService(InMemoryAnomalyRepository())
    monkeypatch.setattr(finops_router, "get_finops_anomaly_service", lambda: anomaly_service)
    monkeypatch.setattr(
        finops_router,
        "evaluate_default_anomalies",
        lambda _value: [
            _managed_finding(anomaly_id="anomaly_cache", workspace_id="ws-a"),
            _managed_finding(
                anomaly_id="anomaly_latency",
                workspace_id="ws-a",
                policy_type="p95_latency",
            ),
        ],
    )

    def unavailable(*_args: object) -> str:
        raise RuntimeError("private resolver failure")

    monkeypatch.setattr(finops_router, "current_remediation_base_version", unavailable)
    response = client.get(
        "/api/finops/risk/decision",
        params={"workspace_id": "ws-a", "refresh": "1"},
        headers=owner_headers,
    )

    assert response.status_code == 200, response.text
    by_policy = {item["policy_type"]: item for item in response.json()["priorities"]}
    assert by_policy["cache_hit_rate"]["base_version"] is None
    assert by_policy["p95_latency"]["base_version"] == "remediation-template-v1"
    assert "private resolver failure" not in response.text


def test_member_cannot_read_admin_decision_scope(
    client: TestClient, member_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(finops_router, "_authorized_workspace_roles", lambda _actor: {"ws-a": "member"})
    response = client.get("/api/finops/risk/decision", params={"workspace_id": "ws-a"}, headers=member_headers)
    assert response.status_code == 403


def test_refresh_is_forwarded_only_after_authorization_and_uses_role_scope(
    client: TestClient,
    owner_headers: dict[str, str],
    repository: InMemoryFinOpsRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ComposedService:
        def __init__(self) -> None:
            self.delegate = FinOpsQueryService(repository)
            self.compose_calls: list[tuple[str, object, bool]] = []

        def __getattr__(self, name: str):
            return getattr(self.delegate, name)

        def compose(self, operation, query, compute, *, force_refresh=False):
            self.compose_calls.append((operation, query, force_refresh))
            return compute()

    service = _ComposedService()
    monkeypatch.setattr(finops_router, "get_finops_query_service", lambda: service)

    response = client.get(
        "/api/finops/roi/decision",
        params={"workspace_id": "ws-a", "refresh": "1"},
        headers=owner_headers,
    )

    assert response.status_code == 200
    assert len(service.compose_calls) == 1
    operation, query, force_refresh = service.compose_calls[0]
    expected_scope = hashlib.sha256(
        json.dumps(
            [["ws-a", "owner"]],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:16]
    assert operation == "roi_decision"
    assert force_refresh is True
    assert query.permission_scope == expected_scope


def test_unauthorized_refresh_never_reaches_cache_or_compute(
    client: TestClient,
    member_headers: dict[str, str],
    repository: InMemoryFinOpsRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _GuardedService:
        def __init__(self) -> None:
            self.delegate = FinOpsQueryService(repository)
            self.compose_calls = 0

        def __getattr__(self, name: str):
            return getattr(self.delegate, name)

        def compose(self, operation, query, compute, *, force_refresh=False):
            self.compose_calls += 1
            raise AssertionError("authorization must run before cache refresh")

    service = _GuardedService()
    monkeypatch.setattr(finops_router, "get_finops_query_service", lambda: service)
    monkeypatch.setattr(
        finops_router,
        "_authorized_workspace_roles",
        lambda _actor: {"ws-a": "member"},
    )

    response = client.get(
        "/api/finops/risk/decision",
        params={"workspace_id": "ws-a", "refresh": "1"},
        headers=member_headers,
    )

    assert response.status_code == 403
    assert service.compose_calls == 0


def test_cache_single_flight_timeout_returns_safe_retryable_503(
    client: TestClient,
    owner_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BusyService:
        def compose(self, operation, query, compute, *, force_refresh=False):
            raise FinOpsCacheBusy("internal cache key must not escape")

    monkeypatch.setattr(
        finops_router,
        "get_finops_query_service",
        _BusyService,
    )

    response = client.get(
        "/api/finops/roi/decision",
        params={"workspace_id": "ws-a"},
        headers=owner_headers,
    )

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert response.json() == {
        "detail": "FinOps query refresh is busy; retry shortly"
    }
    assert "internal cache key" not in response.text


def test_roi_decision_uses_rollup_unit_economics_when_sql_is_enabled(
    client: TestClient,
    owner_headers: dict[str, str],
    repository: InMemoryFinOpsRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeRollupRepository:
        read_calls: list[tuple[object, ...]] = []

        def read(self, query, bucket):
            self.read_calls.append((query.tenant_ref, query.authorized_workspace_ids, query.from_value, query.to_value, bucket))
            return []

    fake = FakeRollupRepository()
    monkeypatch.setattr(
        finops_router,
        "get_finops_query_service",
        lambda: FinOpsQueryService(repository, rollup_repository=fake),
    )
    response = client.get(
        "/api/finops/roi/decision",
        params={"workspace_id": "ws-a", "from": "2026-07-01T00:00:00Z", "to": "2026-08-01T00:00:00Z"},
        headers=owner_headers,
    )
    assert response.status_code == 200
    current_day = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).isoformat().replace("+00:00", "Z")
    assert fake.read_calls == [("tenant-a", ("ws-a",), "2026-07-01T00:00:00Z", current_day, "day")]
