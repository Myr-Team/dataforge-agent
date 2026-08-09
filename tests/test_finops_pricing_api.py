from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

import backend.finops.router as finops_router
from backend.app import app
from backend.finops.models import FinOpsRequestEvent, TokenUsage
from backend.finops.sql_pricing import InMemoryPriceMappingRepository
from auth_fixtures import trusted_headers


def _client(
    monkeypatch,
    *,
    role: str = "owner",
    roles: dict[str, str] | None = None,
    audit_events: list[dict] | None = None,
) -> TestClient:
    repository = InMemoryPriceMappingRepository()
    monkeypatch.setenv("DF_WEB_PROXY_SECRET", "test-proxy-secret")
    monkeypatch.setenv("DF_FINOPS_HMAC_SECRET", "finops-test-secret")
    monkeypatch.setenv("DF_FINOPS_READ_ENABLED", "1")
    monkeypatch.setenv("DF_FINOPS_SQL_ENABLED", "0")
    monkeypatch.delenv("DF_FINOPS_TENANT_OWNER_OIDS", raising=False)
    monkeypatch.setattr(
        finops_router,
        "get_finops_price_mapping_repository",
        lambda: repository,
    )
    monkeypatch.setattr(
        finops_router,
        "_authorized_workspace_roles",
        lambda _actor: dict(roles) if roles is not None else {"ws-a": role},
    )
    monkeypatch.setattr(finops_router, "_tenant_ref", lambda _actor: "tenantref-a")
    monkeypatch.setattr(finops_router, "_actor_ref", lambda _actor: "actorref-a")
    captured = audit_events if audit_events is not None else []
    monkeypatch.setattr(
        finops_router,
        "record_audit_event",
        lambda actor, action, resource, **metadata: captured.append(
            {
                "actor": actor,
                "action": action,
                "resource": resource,
                **metadata,
            }
        ) or {"event_id": "event_test"},
    )
    return TestClient(app)


def test_pricing_catalog_and_owner_mapping_round_trip(monkeypatch) -> None:
    client = _client(monkeypatch)
    bumps: list[tuple[str, str, tuple[str, ...]]] = []

    class _Namespace:
        def bump(self, tenant_ref, workspace_id, domains):
            bumps.append((tenant_ref, workspace_id, tuple(domains)))

    monkeypatch.setattr(finops_router, "get_finops_cache_namespace", _Namespace)
    headers = trusted_headers(actor_id="owner-a", tenant_id="tenant-a")

    catalog = client.get("/api/finops/pricing/catalog", headers=headers)
    created = client.put(
        "/api/finops/pricing/mappings/gpt-5.6-terra?workspace_id=ws-a",
        headers=headers,
        json={
            "official_price_key": "azure-openai:gpt-5.1:global-standard:global",
            "base_revision": 0,
        },
    )
    mappings = client.get("/api/finops/pricing/mappings", headers=headers)

    assert catalog.status_code == 200
    assert catalog.json()["items"][0]["source_url"].startswith(
        "https://prices.azure.com/"
    )
    assert created.status_code == 200
    assert created.json()["mapping"]["mapping_revision"] == 1
    assert mappings.json()["items"][0]["deployment"] == "gpt-5.6-terra"
    assert bumps == [
        ("tenantref-a", "ws-a", ("cost", "roi", "risk", "overview"))
    ]


def test_pricing_mapping_rejects_arbitrary_rate_and_stale_revision(monkeypatch) -> None:
    client = _client(monkeypatch)
    bumps: list[tuple[str, str, tuple[str, ...]]] = []

    class _Namespace:
        def bump(self, tenant_ref, workspace_id, domains):
            bumps.append((tenant_ref, workspace_id, tuple(domains)))

    monkeypatch.setattr(finops_router, "get_finops_cache_namespace", _Namespace)
    headers = trusted_headers(actor_id="owner-a", tenant_id="tenant-a")
    url = "/api/finops/pricing/mappings/gpt-5.6-terra?workspace_id=ws-a"
    body = {
        "official_price_key": "azure-openai:gpt-5.1:global-standard:global",
        "base_revision": 0,
    }

    assert client.put(url, headers=headers, json={**body, "input_rate": 0}).status_code == 422
    assert client.put(url, headers=headers, json=body).status_code == 200
    assert client.put(url, headers=headers, json=body).status_code == 409
    assert bumps == [
        ("tenantref-a", "ws-a", ("cost", "roi", "risk", "overview"))
    ]


def test_pricing_mapping_write_requires_owner(monkeypatch) -> None:
    client = _client(monkeypatch, role="member")

    response = client.put(
        "/api/finops/pricing/mappings/gpt-5.6-terra?workspace_id=ws-a",
        headers=trusted_headers(actor_id="member-a", tenant_id="tenant-a"),
        json={
            "official_price_key": "azure-openai:gpt-5.1:global-standard:global",
            "base_revision": 0,
        },
    )

    assert response.status_code == 403


def test_pricing_mapping_delete_removes_wrong_mapping(monkeypatch) -> None:
    client = _client(monkeypatch)
    bumps: list[tuple[str, str, tuple[str, ...]]] = []

    class _Namespace:
        def bump(self, tenant_ref, workspace_id, domains):
            bumps.append((tenant_ref, workspace_id, tuple(domains)))

    monkeypatch.setattr(finops_router, "get_finops_cache_namespace", _Namespace)
    headers = trusted_headers(actor_id="owner-a", tenant_id="tenant-a")
    url = "/api/finops/pricing/mappings/gpt-5.6-terra?workspace_id=ws-a"

    client.put(
        url,
        headers=headers,
        json={
            "official_price_key": "azure-openai:gpt-5.1:global-standard:global",
            "base_revision": 0,
        },
    )
    delete_url = f"{url}&base_revision=1"
    deleted = client.delete(delete_url, headers=headers)
    missing = client.delete(delete_url, headers=headers)
    mappings = client.get("/api/finops/pricing/mappings", headers=headers)

    assert deleted.status_code == 204
    assert missing.status_code == 404
    assert mappings.json()["count"] == 0
    assert bumps == [
        ("tenantref-a", "ws-a", ("cost", "roi", "risk", "overview")),
        ("tenantref-a", "ws-a", ("cost", "roi", "risk", "overview")),
    ]


def test_pricing_mapping_delete_requires_owner(monkeypatch) -> None:
    client = _client(monkeypatch, role="admin")

    response = client.delete(
        "/api/finops/pricing/mappings/gpt-5.6-terra?workspace_id=ws-a&base_revision=1",
        headers=trusted_headers(actor_id="admin-a", tenant_id="tenant-a"),
    )

    assert response.status_code == 403


def test_pricing_mapping_requires_owner_across_all_workspaces(monkeypatch) -> None:
    # Owner of one workspace but only a member of another must not be able to
    # write the tenant-level official price mapping.
    client = _client(monkeypatch, roles={"ws-a": "owner", "ws-b": "member"})

    response = client.put(
        "/api/finops/pricing/mappings/gpt-5.6-terra?workspace_id=ws-a&base_revision=1",
        headers=trusted_headers(actor_id="owner-a", tenant_id="tenant-a"),
        json={
            "official_price_key": "azure-openai:gpt-5.1:global-standard:global",
            "base_revision": 0,
        },
    )

    assert response.status_code == 403


def test_pricing_mapping_delete_requires_owner_across_all_workspaces(monkeypatch) -> None:
    client = _client(monkeypatch, roles={"ws-a": "owner", "ws-b": "admin"})

    response = client.delete(
        "/api/finops/pricing/mappings/gpt-5.6-terra?workspace_id=ws-a&base_revision=1",
        headers=trusted_headers(actor_id="owner-a", tenant_id="tenant-a"),
    )

    assert response.status_code == 403


def test_policy_write_requires_admin_across_all_workspaces(monkeypatch) -> None:
    client = _client(monkeypatch, roles={"ws-a": "owner", "ws-b": "member"})

    response = client.post(
        "/api/finops/policies",
        headers=trusted_headers(actor_id="owner-a", tenant_id="tenant-a"),
        json={
            "policy_type": "unpriced_requests",
            "configuration": {"threshold_pct": 5},
        },
    )

    assert response.status_code == 403


def _deployment_event(call_class: str, deployment: str) -> FinOpsRequestEvent:
    return FinOpsRequestEvent.model_validate(
        {
            "request_ref": "req_aaaaaaaaaaaa",
            "occurred_at": datetime(2026, 7, 24, 2, 0, tzinfo=timezone.utc),
            "call_class": call_class,
            "tenant_ref": "tenantref-a",
            "workspace_id": "ws-a",
            "deployment": deployment,
            "status": "succeeded",
            "tokens": TokenUsage(input=10, output=2, total=12),
        }
    )


class _StubQueryService:
    def __init__(self, events: list[FinOpsRequestEvent]) -> None:
        self._events = events

    def events(self, _query) -> list[FinOpsRequestEvent]:
        return self._events


def test_pricing_mapping_rejects_incompatible_deployment(monkeypatch) -> None:
    client = _client(monkeypatch)
    monkeypatch.setattr(
        finops_router,
        "get_finops_query_service",
        lambda: _StubQueryService([_deployment_event("image", "gpt-5.6-terra")]),
    )

    response = client.put(
        "/api/finops/pricing/mappings/gpt-5.6-terra?workspace_id=ws-a",
        headers=trusted_headers(actor_id="owner-a", tenant_id="tenant-a"),
        json={
            "official_price_key": "azure-openai:gpt-5.1:global-standard:global",
            "base_revision": 0,
        },
    )

    assert response.status_code == 422
    assert "compatible" in response.json()["detail"]


def test_pricing_mapping_allows_observed_model_deployment(monkeypatch) -> None:
    client = _client(monkeypatch)
    monkeypatch.setattr(
        finops_router,
        "get_finops_query_service",
        lambda: _StubQueryService([_deployment_event("model", "gpt-5.6-terra")]),
    )

    response = client.put(
        "/api/finops/pricing/mappings/gpt-5.6-terra?workspace_id=ws-a",
        headers=trusted_headers(actor_id="owner-a", tenant_id="tenant-a"),
        json={
            "official_price_key": "azure-openai:gpt-5.1:global-standard:global",
            "base_revision": 0,
        },
    )

    assert response.status_code == 200


def test_pricing_mapping_contract_exposes_tenant_management_capability(monkeypatch) -> None:
    client = _client(monkeypatch, roles={"ws-a": "owner", "ws-b": "member"})
    monkeypatch.setenv("DF_FINOPS_TENANT_OWNER_OIDS", "owner-a,other-owner")

    response = client.get(
        "/api/finops/pricing/mappings",
        headers=trusted_headers(actor_id="owner-a", tenant_id="tenant-a"),
    )

    assert response.status_code == 200
    assert response.json()["scope"] == "tenant"
    assert response.json()["can_manage"] is True
    assert response.json()["authorization_source"] == "entra_tenant_pricing_admin"


def test_configured_tenant_pricing_admin_can_write_with_workspace_audit_scope(monkeypatch) -> None:
    audit_events: list[dict] = []
    client = _client(
        monkeypatch,
        roles={"ws-a": "admin", "ws-b": "member"},
        audit_events=audit_events,
    )
    monkeypatch.setenv("DF_FINOPS_TENANT_OWNER_OIDS", "owner-a")

    response = client.put(
        "/api/finops/pricing/mappings/gpt-5.6-terra?workspace_id=ws-a",
        headers=trusted_headers(actor_id="owner-a", tenant_id="tenant-a"),
        json={
            "official_price_key": "azure-openai:gpt-5.1:global-standard:global",
            "base_revision": 0,
        },
    )

    assert response.status_code == 200
    assert audit_events == [
        {
            "actor": audit_events[0]["actor"],
            "action": "model_price_mapping.write",
            "resource": {
                "workspace_id": "ws-a",
                "resource_type": "model_price_mapping",
                "resource_id": "gpt-5.6-terra:1",
            },
            "result": "allowed",
            "reason_code": "authorized",
        }
    ]


def test_pricing_mapping_write_fails_closed_when_audit_is_unavailable(monkeypatch) -> None:
    client = _client(monkeypatch)

    def _raise_audit(*_args, **_kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(finops_router, "record_audit_event", _raise_audit)
    response = client.put(
        "/api/finops/pricing/mappings/gpt-5.6-terra?workspace_id=ws-a",
        headers=trusted_headers(actor_id="owner-a", tenant_id="tenant-a"),
        json={
            "official_price_key": "azure-openai:gpt-5.1:global-standard:global",
            "base_revision": 0,
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Audit persistence is required"
    assert client.get(
        "/api/finops/pricing/mappings",
        headers=trusted_headers(actor_id="owner-a", tenant_id="tenant-a"),
    ).json()["count"] == 0
