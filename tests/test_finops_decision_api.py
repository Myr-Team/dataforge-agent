from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import backend.finops.router as finops_router
from auth_fixtures import trusted_headers
from backend.app import app
from backend.finops.models import FinOpsRequestEvent
from backend.finops.query import FinOpsQueryService
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


def test_member_cannot_read_admin_decision_scope(
    client: TestClient, member_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(finops_router, "_authorized_workspace_roles", lambda _actor: {"ws-a": "member"})
    response = client.get("/api/finops/risk/decision", params={"workspace_id": "ws-a"}, headers=member_headers)
    assert response.status_code == 403


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
