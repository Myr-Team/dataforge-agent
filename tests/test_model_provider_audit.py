from __future__ import annotations

from fastapi.testclient import TestClient

import backend.model_provider_router as provider_router
from backend.app import app
from backend.model_provider_repository import InMemoryModelProviderRepository
from backend.audit_store import ALLOWED_REASON_CODES
from auth_fixtures import trusted_headers


def test_provider_mutation_fails_before_secret_write_when_audit_is_unavailable(
    monkeypatch,
) -> None:
    repository = InMemoryModelProviderRepository()

    class _Secrets:
        writes = 0

        def put(self, tenant_ref: str, provider_id: str, api_key: str) -> str:
            self.writes += 1
            return "kv:unexpected"

    secrets = _Secrets()
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
        "_authorized_workspace_roles",
        lambda _actor: {"ws-a": "owner"},
    )
    monkeypatch.setattr(
        provider_router,
        "record_audit_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("audit unavailable")
        ),
    )

    response = TestClient(app).post(
        "/api/model-providers",
        headers=trusted_headers(actor_id="owner-a", tenant_id="tenant-a"),
        json={
            "provider_type": "deepseek",
            "display_name": "DeepSeek",
            "base_url": "https://api.deepseek.com",
            "api_key": "secret-marker",
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Audit persistence is required"
    assert secrets.writes == 0
    assert repository.list("tenant-a") == []


def test_bedrock_mutation_fails_before_secret_write_when_audit_is_unavailable(
    monkeypatch,
) -> None:
    repository = InMemoryModelProviderRepository()

    class _Secrets:
        writes = 0

        def put(self, tenant_ref: str, provider_id: str, secret_value: str) -> str:
            self.writes += 1
            return "kv:unexpected"

    secrets = _Secrets()
    monkeypatch.setenv("DF_WEB_PROXY_SECRET", "test-proxy-secret")
    monkeypatch.setenv("DF_FINOPS_HMAC_SECRET", "finops-test-secret")
    monkeypatch.setenv("DF_PROVIDER_CONNECTORS_ENABLED", "1")
    monkeypatch.setenv("DF_AWS_BEDROCK_CONNECTOR_ENABLED", "1")
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
        "_authorized_workspace_roles",
        lambda _actor: {"ws-a": "owner"},
    )
    monkeypatch.setattr(
        provider_router,
        "record_audit_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("audit unavailable")
        ),
    )

    response = TestClient(app).post(
        "/api/model-providers",
        headers=trusted_headers(actor_id="owner-a", tenant_id="tenant-a"),
        json={
            "provider_type": "aws_bedrock",
            "display_name": "AWS Bedrock",
            "region": "ap-southeast-1",
            "access_key_id": "AKIAEXAMPLE",
            "secret_access_key": "secret-marker-value",
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Audit persistence is required"
    assert secrets.writes == 0
    assert repository.list("tenant-a") == []


def test_provider_routing_transitions_use_allowlisted_audit_reason_codes() -> None:
    assert {"routing_governed", "routing_suspended"}.issubset(ALLOWED_REASON_CODES)
