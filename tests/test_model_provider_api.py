from __future__ import annotations

from fastapi.testclient import TestClient

import backend.model_provider_router as provider_router
from backend.app import app
from backend.deepseek_provider import ProviderHttpResponse
from backend.model_provider_repository import InMemoryModelProviderRepository
from auth_fixtures import trusted_headers


class _Secrets:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def put(self, tenant_ref: str, provider_id: str, api_key: str) -> str:
        self.values[(tenant_ref, provider_id)] = api_key
        return f"kv:provider-{provider_id}"

    def get(self, tenant_ref: str, provider_id: str, secret_ref: str) -> str:
        return self.values[(tenant_ref, provider_id)]

    def rotate(self, tenant_ref: str, provider_id: str, api_key: str) -> str:
        return self.put(tenant_ref, provider_id, api_key)


class _Transport:
    def post_json(self, **_: object) -> ProviderHttpResponse:
        return ProviderHttpResponse(
            status_code=200,
            headers={},
            json_body={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {
                    "prompt_tokens": 2,
                    "completion_tokens": 1,
                    "total_tokens": 3,
                    "prompt_cache_hit_tokens": 0,
                    "prompt_cache_miss_tokens": 2,
                },
            },
        )


def _client(monkeypatch, *, roles: dict[str, str] | None = None):
    repository = InMemoryModelProviderRepository()
    secrets = _Secrets()
    audits: list[dict[str, object]] = []
    monkeypatch.setenv("DF_WEB_PROXY_SECRET", "test-proxy-secret")
    monkeypatch.setenv("DF_FINOPS_HMAC_SECRET", "finops-test-secret")
    monkeypatch.setenv("DF_PROVIDER_CONNECTORS_ENABLED", "1")
    monkeypatch.setattr(
        provider_router,
        "get_model_provider_repository",
        lambda: repository,
    )
    monkeypatch.setattr(
        provider_router,
        "get_model_provider_secret_store",
        lambda: secrets,
    )
    monkeypatch.setattr(
        provider_router,
        "get_provider_transport",
        lambda: _Transport(),
    )
    monkeypatch.setattr(
        provider_router,
        "_authorized_workspace_roles",
        lambda _actor: dict(roles or {"ws-a": "owner"}),
    )
    monkeypatch.setattr(
        provider_router,
        "record_audit_event",
        lambda actor, action, resource, **metadata: audits.append(
            {
                "actor": actor,
                "action": action,
                "resource": resource,
                "metadata": metadata,
            }
        )
        or {"event_id": "event-safe"},
    )
    return TestClient(app), repository, secrets, audits


def test_owner_creates_tests_and_lists_masked_deepseek_provider(monkeypatch) -> None:
    client, _repository, secrets, audits = _client(monkeypatch)
    headers = trusted_headers(actor_id="owner-a", tenant_id="tenant-a")

    created = client.post(
        "/api/model-providers",
        headers=headers,
        json={
            "provider_type": "deepseek",
            "display_name": "DeepSeek",
            "base_url": "https://api.deepseek.com",
            "api_key": "secret-marker",
        },
    )
    listed = client.get("/api/model-providers", headers=headers)

    assert created.status_code == 201
    assert created.json()["connection_state"] == "connected"
    assert created.json()["secret_status"] == "stored"
    assert listed.status_code == 200
    assert listed.json()["count"] == 1
    assert "api_key" not in str(created.json())
    assert "secret_ref" not in str(created.json())
    assert "secret-marker" not in str(created.json())
    assert list(secrets.values.values()) == ["secret-marker"]
    assert audits[0]["action"] == "model_provider.manage"
    assert "secret-marker" not in str(audits)


def test_provider_management_requires_owner_or_admin_across_workspaces(
    monkeypatch,
) -> None:
    client, _repository, _secrets, _audits = _client(
        monkeypatch,
        roles={"ws-a": "owner", "ws-b": "viewer"},
    )

    response = client.get(
        "/api/model-providers",
        headers=trusted_headers(actor_id="viewer-a", tenant_id="tenant-a"),
    )

    assert response.status_code == 403


def test_provider_reads_are_tenant_scoped(monkeypatch) -> None:
    client, _repository, _secrets, _audits = _client(monkeypatch)
    owner_a = trusted_headers(actor_id="owner-a", tenant_id="tenant-a")
    owner_b = trusted_headers(actor_id="owner-b", tenant_id="tenant-b")

    assert client.post(
        "/api/model-providers",
        headers=owner_a,
        json={
            "provider_type": "deepseek",
            "display_name": "DeepSeek A",
            "base_url": "https://api.deepseek.com",
            "api_key": "secret-marker-a",
        },
    ).status_code == 201

    assert client.get("/api/model-providers", headers=owner_b).json()["count"] == 0


def test_provider_patch_rejects_stale_revision(monkeypatch) -> None:
    client, _repository, _secrets, _audits = _client(monkeypatch)
    headers = trusted_headers(actor_id="owner-a", tenant_id="tenant-a")
    created = client.post(
        "/api/model-providers",
        headers=headers,
        json={
            "provider_type": "deepseek",
            "display_name": "DeepSeek",
            "base_url": "https://api.deepseek.com",
            "api_key": "secret-marker",
        },
    ).json()
    url = f"/api/model-providers/{created['provider_id']}"

    assert client.patch(
        url,
        headers=headers,
        json={"base_revision": 1, "display_name": "stale"},
    ).status_code == 409


def test_provider_api_is_hidden_when_feature_is_disabled(monkeypatch) -> None:
    client, _repository, _secrets, _audits = _client(monkeypatch)
    monkeypatch.setenv("DF_PROVIDER_CONNECTORS_ENABLED", "0")

    response = client.get(
        "/api/model-providers",
        headers=trusted_headers(actor_id="owner-a", tenant_id="tenant-a"),
    )

    assert response.status_code == 404


def test_invalid_provider_endpoint_is_rejected_before_secret_write(
    monkeypatch,
) -> None:
    client, _repository, secrets, _audits = _client(monkeypatch)

    response = client.post(
        "/api/model-providers",
        headers=trusted_headers(actor_id="owner-a", tenant_id="tenant-a"),
        json={
            "provider_type": "deepseek",
            "display_name": "Invalid",
            "base_url": "https://evil.example",
            "api_key": "secret-marker",
        },
    )

    assert response.status_code == 422
    assert secrets.values == {}
