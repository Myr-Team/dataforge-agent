from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import backend.finops.router as finops_router
from backend.app import app
from backend.finops.gateway_unmatched import InMemoryGatewayUnmatchedRepository
from backend.finops.models import FinOpsRequestEvent, TokenUsage
from backend.finops.query import FinOpsQueryService
from backend.finops.repository import InMemoryFinOpsRepository
from auth_fixtures import trusted_headers


def _event(request_ref: str) -> FinOpsRequestEvent:
    return FinOpsRequestEvent.model_validate(
        {
            "request_ref": request_ref,
            "occurred_at": datetime.now(timezone.utc) - timedelta(hours=1),
            "call_class": "model",
            "tenant_ref": "tenantref-a",
            "workspace_id": "ws-a",
            "actor_ref": "actorref-a",
            "status": "succeeded",
            "tokens": TokenUsage(total=100),
            "gateway_coverage": "apim_governed",
            "estimated_cost": {
                "amount": 0.01,
                "currency": "USD",
                "status": "estimated",
                "price_card_revision": "price-1",
            },
            "evidence_state": "observed",
        }
    )


def _client(monkeypatch, *, roles: dict[str, str]) -> TestClient:
    repository = InMemoryFinOpsRepository()
    repository.upsert_events([_event("req_aaaaaaaaaaaa")])
    gateway = InMemoryGatewayUnmatchedRepository()
    gateway.record_gateway_errors(
        [
            {
                "occurred_at": (
                    datetime.now(timezone.utc) - timedelta(hours=1)
                ).isoformat(),
                "status_code": 503,
            }
        ]
    )
    service = FinOpsQueryService(repository, gateway_unmatched_repository=gateway)
    monkeypatch.setenv("DF_WEB_PROXY_SECRET", "test-proxy-secret")
    monkeypatch.setenv("DF_FINOPS_HMAC_SECRET", "finops-test-secret")
    monkeypatch.setenv("DF_FINOPS_READ_ENABLED", "1")
    monkeypatch.setenv("DF_FINOPS_SQL_ENABLED", "0")
    monkeypatch.setattr(finops_router, "get_finops_query_service", lambda: service)
    monkeypatch.setattr(
        finops_router, "_authorized_workspace_roles", lambda _actor: dict(roles)
    )
    monkeypatch.setattr(finops_router, "_tenant_ref", lambda _actor: "tenantref-a")
    monkeypatch.setattr(finops_router, "_actor_ref", lambda _actor: "actorref-a")
    return TestClient(app)


def test_owner_overview_exposes_unattributed_gateway_evidence(monkeypatch) -> None:
    client = _client(monkeypatch, roles={"ws-a": "owner"})
    headers = trusted_headers(actor_id="owner-a", tenant_id="tenant-a")

    apim = client.get("/api/finops/overview", headers=headers).json()["trust"]["apim"]

    assert apim["unmatched_metric_records"] == 1
    assert apim["gateway_unmatched"]["scope"] == "unattributed"
    assert apim["gateway_unmatched"]["unmatched_gateway_errors"]["total"] == 1


def test_member_overview_redacts_unattributed_gateway_evidence(monkeypatch) -> None:
    client = _client(monkeypatch, roles={"ws-a": "member"})
    headers = trusted_headers(actor_id="member-a", tenant_id="tenant-a")

    apim = client.get("/api/finops/overview", headers=headers).json()["trust"]["apim"]

    assert apim["unmatched_metric_records"] is None
    assert apim.get("gateway_unmatched") is None
