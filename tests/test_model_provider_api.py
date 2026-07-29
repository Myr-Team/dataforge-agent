from __future__ import annotations

from fastapi.testclient import TestClient

import backend.model_provider_router as provider_router
from backend.app import app
from backend.deepseek_provider import ProviderHttpResponse
from backend.aws_bedrock_provider import AwsBedrockCredential
from backend.model_provider_repository import InMemoryModelProviderRepository
from backend.model_provider_service import ModelProviderService
from backend.model_providers import ProviderModel
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


class _BedrockControlPlane:
    def __init__(self) -> None:
        self.calls: list[tuple[str, AwsBedrockCredential]] = []

    def list_models(
        self,
        region: str,
        credential: AwsBedrockCredential,
    ) -> list[ProviderModel]:
        self.calls.append((region, credential))
        return [
            ProviderModel(
                model_id="anthropic.claude-sonnet-4-20250514-v1:0",
                display_name="Claude Sonnet 4",
                capabilities=["text", "streaming"],
                support_state="unsupported",
                price_key=None,
            )
        ]


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


def test_owner_creates_masked_bedrock_provider(monkeypatch) -> None:
    client, _repository, secrets, audits = _client(monkeypatch)
    monkeypatch.setenv("DF_AWS_BEDROCK_CONNECTOR_ENABLED", "1")
    monkeypatch.setattr(
        "backend.aws_bedrock_provider.Boto3BedrockControlPlane.list_models",
        lambda _self, _region, _credential: [],
    )

    response = client.post(
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

    assert response.status_code == 201
    assert response.json()["region"] == "ap-southeast-1"
    assert "access_key" not in response.text.lower()
    assert "secret-marker" not in response.text
    assert "secret-marker" not in str(audits)
    assert "secret-marker" in next(iter(secrets.values.values()))


def test_bedrock_create_is_hidden_when_specific_flag_is_off(monkeypatch) -> None:
    client, _repository, secrets, audits = _client(monkeypatch)
    monkeypatch.setenv("DF_AWS_BEDROCK_CONNECTOR_ENABLED", "0")

    response = client.post(
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

    assert response.status_code == 404
    assert secrets.values == {}
    assert audits == []


def test_invalid_bedrock_request_does_not_echo_secret_values(monkeypatch) -> None:
    client, _repository, _secrets, _audits = _client(monkeypatch)
    monkeypatch.setenv("DF_AWS_BEDROCK_CONNECTOR_ENABLED", "1")

    response = client.post(
        "/api/model-providers",
        headers=trusted_headers(actor_id="owner-a", tenant_id="tenant-a"),
        json={
            "provider_type": "aws_bedrock",
            "display_name": "AWS Bedrock",
            "region": "ap-southeast-1",
            "access_key_id": "AKIAEXAMPLE",
            "secret_access_key": "secret-marker-too-short",
            "session_token": "session-marker-value",
            "unexpected": "value",
        },
    )

    assert response.status_code == 422
    assert "secret-marker" not in response.text
    assert "session-marker" not in response.text
    assert "AKIAEXAMPLE" not in response.text


def test_bedrock_test_and_rotation_are_hidden_when_specific_flag_is_off(
    monkeypatch,
) -> None:
    client, _repository, secrets, audits = _client(monkeypatch)
    monkeypatch.setenv("DF_AWS_BEDROCK_CONNECTOR_ENABLED", "1")
    monkeypatch.setattr(
        "backend.aws_bedrock_provider.Boto3BedrockControlPlane.list_models",
        lambda _self, _region, _credential: [],
    )
    headers = trusted_headers(actor_id="owner-a", tenant_id="tenant-a")
    created = client.post(
        "/api/model-providers",
        headers=headers,
        json={
            "provider_type": "aws_bedrock",
            "display_name": "AWS Bedrock",
            "region": "ap-southeast-1",
            "access_key_id": "AKIAEXAMPLE",
            "secret_access_key": "secret-marker-value",
        },
    ).json()
    secret_before = next(iter(secrets.values.values()))
    audit_count = len(audits)
    monkeypatch.setenv("DF_AWS_BEDROCK_CONNECTOR_ENABLED", "0")

    tested = client.post(
        f"/api/model-providers/{created['provider_id']}/test",
        headers=headers,
    )
    rotated = client.post(
        f"/api/model-providers/{created['provider_id']}/rotate-secret",
        headers=headers,
        json={
            "provider_type": "aws_bedrock",
            "access_key_id": "AKIAREPLACED",
            "secret_access_key": "replacement-secret-marker",
            "base_revision": created["revision"],
        },
    )

    assert tested.status_code == 404
    assert rotated.status_code == 404
    assert next(iter(secrets.values.values())) == secret_before
    assert len(audits) == audit_count


def test_owner_rotates_masked_bedrock_provider(monkeypatch) -> None:
    client, _repository, secrets, audits = _client(monkeypatch)
    monkeypatch.setenv("DF_AWS_BEDROCK_CONNECTOR_ENABLED", "1")
    monkeypatch.setattr(
        "backend.aws_bedrock_provider.Boto3BedrockControlPlane.list_models",
        lambda _self, _region, _credential: [],
    )
    headers = trusted_headers(actor_id="owner-a", tenant_id="tenant-a")
    created = client.post(
        "/api/model-providers",
        headers=headers,
        json={
            "provider_type": "aws_bedrock",
            "display_name": "AWS Bedrock",
            "region": "ap-southeast-1",
            "access_key_id": "AKIAEXAMPLE",
            "secret_access_key": "secret-marker-value",
        },
    ).json()

    response = client.post(
        f"/api/model-providers/{created['provider_id']}/rotate-secret",
        headers=headers,
        json={
            "provider_type": "aws_bedrock",
            "access_key_id": "AKIAREPLACED",
            "secret_access_key": "replacement-secret-marker",
            "base_revision": created["revision"],
        },
    )

    assert response.status_code == 200
    assert response.json()["region"] == "ap-southeast-1"
    assert "access_key" not in response.text.lower()
    assert "replacement-secret" not in response.text
    assert "replacement-secret" not in str(audits)
    assert "replacement-secret" in next(iter(secrets.values.values()))


def test_stale_deepseek_rotation_does_not_write_audit_or_secret(monkeypatch) -> None:
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
    ).json()
    audit_count = len(audits)
    secret_before = next(iter(secrets.values.values()))

    response = client.post(
        f"/api/model-providers/{created['provider_id']}/rotate-secret",
        headers=headers,
        json={"api_key": "replacement-marker", "base_revision": 1},
    )

    assert response.status_code == 409
    assert len(audits) == audit_count
    assert next(iter(secrets.values.values())) == secret_before


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


def test_service_dispatches_bedrock_connection_test_as_unmanaged_discovery() -> None:
    repository = InMemoryModelProviderRepository()
    secrets = _Secrets()
    bedrock = _BedrockControlPlane()
    service = ModelProviderService(
        repository=repository,
        secret_store=secrets,
        transport=_Transport(),
        bedrock_control_plane=bedrock,
    )
    credential = AwsBedrockCredential(
        access_key_id="AKIAEXAMPLE",
        secret_access_key="secret-marker-value",
    )

    result = service.create(
        tenant_ref="tenant-safe",
        actor_ref="actor-safe",
        provider_type="aws_bedrock",
        display_name="AWS Bedrock",
        base_url="https://bedrock.us-east-1.amazonaws.com",
        region="us-east-1",
        secret_value=credential.to_secret_value(),
        provider_id="provider_bedrock",
    )

    assert result["connection_state"] == "connected"
    assert result["governance_state"] == "unmanaged"
    assert result["available_models"] == [{
        "model_id": "anthropic.claude-sonnet-4-20250514-v1:0",
        "display_name": "Claude Sonnet 4",
        "capabilities": ["text", "streaming"],
        "support_state": "unsupported",
        "price_key": None,
    }]
    assert bedrock.calls == [("us-east-1", credential)]
    assert list(secrets.values.values()) == [credential.to_secret_value()]
