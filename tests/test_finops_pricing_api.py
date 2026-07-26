from __future__ import annotations

from fastapi.testclient import TestClient

import backend.finops.router as finops_router
from backend.app import app
from backend.finops.sql_pricing import InMemoryPriceMappingRepository
from auth_fixtures import trusted_headers


def _client(monkeypatch, *, role: str = "owner") -> TestClient:
    repository = InMemoryPriceMappingRepository()
    monkeypatch.setenv("DF_WEB_PROXY_SECRET", "test-proxy-secret")
    monkeypatch.setenv("DF_FINOPS_HMAC_SECRET", "finops-test-secret")
    monkeypatch.setenv("DF_FINOPS_READ_ENABLED", "1")
    monkeypatch.setenv("DF_FINOPS_SQL_ENABLED", "0")
    monkeypatch.setattr(
        finops_router,
        "get_finops_price_mapping_repository",
        lambda: repository,
    )
    monkeypatch.setattr(
        finops_router,
        "_authorized_workspace_roles",
        lambda _actor: {"ws-a": role},
    )
    monkeypatch.setattr(finops_router, "_tenant_ref", lambda _actor: "tenantref-a")
    monkeypatch.setattr(finops_router, "_actor_ref", lambda _actor: "actorref-a")
    return TestClient(app)


def test_pricing_catalog_and_owner_mapping_round_trip(monkeypatch) -> None:
    client = _client(monkeypatch)
    headers = trusted_headers(actor_id="owner-a", tenant_id="tenant-a")

    catalog = client.get("/api/finops/pricing/catalog", headers=headers)
    created = client.put(
        "/api/finops/pricing/mappings/gpt-5.6-terra",
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


def test_pricing_mapping_rejects_arbitrary_rate_and_stale_revision(monkeypatch) -> None:
    client = _client(monkeypatch)
    headers = trusted_headers(actor_id="owner-a", tenant_id="tenant-a")
    url = "/api/finops/pricing/mappings/gpt-5.6-terra"
    body = {
        "official_price_key": "azure-openai:gpt-5.1:global-standard:global",
        "base_revision": 0,
    }

    assert client.put(url, headers=headers, json={**body, "input_rate": 0}).status_code == 422
    assert client.put(url, headers=headers, json=body).status_code == 200
    assert client.put(url, headers=headers, json=body).status_code == 409


def test_pricing_mapping_write_requires_owner(monkeypatch) -> None:
    client = _client(monkeypatch, role="member")

    response = client.put(
        "/api/finops/pricing/mappings/gpt-5.6-terra",
        headers=trusted_headers(actor_id="member-a", tenant_id="tenant-a"),
        json={
            "official_price_key": "azure-openai:gpt-5.1:global-standard:global",
            "base_revision": 0,
        },
    )

    assert response.status_code == 403


def test_pricing_mapping_delete_removes_wrong_mapping(monkeypatch) -> None:
    client = _client(monkeypatch)
    headers = trusted_headers(actor_id="owner-a", tenant_id="tenant-a")
    url = "/api/finops/pricing/mappings/gpt-5.6-terra"

    client.put(
        url,
        headers=headers,
        json={
            "official_price_key": "azure-openai:gpt-5.1:global-standard:global",
            "base_revision": 0,
        },
    )
    deleted = client.delete(url, headers=headers)
    missing = client.delete(url, headers=headers)
    mappings = client.get("/api/finops/pricing/mappings", headers=headers)

    assert deleted.status_code == 204
    assert missing.status_code == 404
    assert mappings.json()["count"] == 0


def test_pricing_mapping_delete_requires_owner(monkeypatch) -> None:
    client = _client(monkeypatch, role="admin")

    response = client.delete(
        "/api/finops/pricing/mappings/gpt-5.6-terra",
        headers=trusted_headers(actor_id="admin-a", tenant_id="tenant-a"),
    )

    assert response.status_code == 403
