from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from threading import Event, Lock

import pytest
from fastapi.testclient import TestClient

import backend.model_provider_router as provider_router
from backend.app import app
from backend.deepseek_provider import ProviderHttpResponse
from backend.aws_bedrock_provider import AwsBedrockCredential
from backend.model_provider_repository import InMemoryModelProviderRepository
from backend.model_provider_service import ModelProviderService
from backend.model_providers import ModelProviderRecord, ProviderModel
from auth_fixtures import trusted_headers


class _Secrets:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}
        self.put_calls = 0
        self.rotate_calls = 0

    def put(self, tenant_ref: str, provider_id: str, api_key: str) -> str:
        self.put_calls += 1
        self.values[(tenant_ref, provider_id)] = api_key
        return f"kv:provider-{provider_id}"

    def get(self, tenant_ref: str, provider_id: str, secret_ref: str) -> str:
        return self.values[(tenant_ref, provider_id)]

    def status(self, tenant_ref: str, provider_id: str, secret_ref: str) -> str:
        return "stored" if (tenant_ref, provider_id) in self.values else "missing"

    def rotate(self, tenant_ref: str, provider_id: str, api_key: str) -> str:
        self.rotate_calls += 1
        return self.put(tenant_ref, provider_id, api_key)


class _Transport:
    def get_json(self, **values: object) -> ProviderHttpResponse:
        if values.get("path") == "/user/balance":
            return ProviderHttpResponse(
                status_code=200,
                headers={},
                json_body={"is_available": True},
            )
        return ProviderHttpResponse(
            status_code=200,
            headers={},
            json_body={
                "data": [
                    {"id": "deepseek-v4-flash"},
                    {"id": "deepseek-v4-pro"},
                ]
            },
        )

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


class _CapturingTransport(_Transport):
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def post_json(self, **values: object) -> ProviderHttpResponse:
        self.calls.append(dict(values))
        return super().post_json(**values)


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


def _client(
    monkeypatch,
    *,
    roles: dict[str, str] | None = None,
    repository: InMemoryModelProviderRepository | None = None,
    transport: _Transport | None = None,
):
    repository = repository or InMemoryModelProviderRepository()
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
        lambda: transport or _Transport(),
    )
    from backend.provider_connection_probe import DeepSeekConnectionProbe

    monkeypatch.setattr(
        "backend.model_provider_service.DeepSeekConnectionProbe",
        lambda *, transport: DeepSeekConnectionProbe(
            transport=transport,
            resolver=lambda _host, port, **_kwargs: [
                (2, 1, 6, "", ("8.8.8.8", port))
            ],
            tls_probe=lambda _host, _port, _timeout: None,
        ),
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
    assert created.json()["connection_stage"] == "completed"
    assert list(created.json()["stage_durations_ms"]) == [
        "secret_read",
        "endpoint_resolution",
        "tls_connect",
        "provider_auth",
        "minimal_inference",
        "model_discovery",
    ]
    assert created.json()["secret_status"] == "stored"
    assert listed.status_code == 200
    assert listed.json()["count"] == 1
    assert "api_key" not in str(created.json())
    assert "secret_ref" not in str(created.json())
    assert "secret-marker" not in str(created.json())
    assert list(secrets.values.values()) == ["secret-marker"]
    assert audits[0]["action"] == "model_provider.manage"
    assert "secret-marker" not in str(audits)


def test_owner_governs_and_suspends_verified_deepseek_routes(monkeypatch) -> None:
    client, repository, secrets, audits = _client(monkeypatch)
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

    assert created["route_eligibility"] == {
        "state": "governance_required",
        "selectable": False,
        "can_govern": True,
        "reason": "governance_required",
        "eligible_model_count": 2,
    }

    governed = client.post(
        f"/api/model-providers/{created['provider_id']}/govern",
        headers=headers,
        json={"base_revision": created["revision"]},
    )

    assert governed.status_code == 200
    assert governed.json()["governance_state"] == "governed"
    assert governed.json()["revision"] == created["revision"] + 1
    assert governed.json()["route_eligibility"] == {
        "state": "selectable",
        "selectable": True,
        "can_govern": False,
        "reason": None,
        "eligible_model_count": 2,
    }
    assert audits[-1]["metadata"]["reason_code"] == "routing_governed"

    suspended = client.post(
        f"/api/model-providers/{created['provider_id']}/suspend",
        headers=headers,
        json={"base_revision": governed.json()["revision"]},
    )

    assert suspended.status_code == 200
    assert suspended.json()["governance_state"] == "pending"
    assert suspended.json()["route_eligibility"]["reason"] == "governance_required"
    tenant_ref = next(iter(secrets.values))[0]
    assert repository.get(tenant_ref, created["provider_id"]).connection_state == "connected"
    assert audits[-1]["metadata"]["reason_code"] == "routing_suspended"


def test_provider_governance_requires_current_revision_and_stored_secret(monkeypatch) -> None:
    client, repository, secrets, audits = _client(monkeypatch)
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

    stale = client.post(
        f"/api/model-providers/{created['provider_id']}/govern",
        headers=headers,
        json={"base_revision": 1},
    )
    assert stale.status_code == 409
    assert len(audits) == audit_count

    tenant_ref = next(iter(secrets.values))[0]
    secrets.values.clear()
    unavailable = client.post(
        f"/api/model-providers/{created['provider_id']}/govern",
        headers=headers,
        json={"base_revision": created["revision"]},
    )
    assert unavailable.status_code == 422
    assert unavailable.json()["detail"] == "provider_secret_unavailable"
    assert len(audits) == audit_count

    current = repository.get(tenant_ref, created["provider_id"])
    assert current.governance_state == "pending"


def test_provider_governance_requires_owner_or_admin(monkeypatch) -> None:
    client, _repository, _secrets, audits = _client(
        monkeypatch,
        roles={"ws-a": "viewer"},
    )

    response = client.post(
        "/api/model-providers/provider_unknown/govern",
        headers=trusted_headers(actor_id="viewer-a", tenant_id="tenant-a"),
        json={"base_revision": 1},
    )

    assert response.status_code == 403
    assert audits == []


def test_configured_tenant_owner_can_manage_provider_with_mixed_workspace_roles(monkeypatch) -> None:
    monkeypatch.setenv("DF_FINOPS_TENANT_OWNER_OIDS", "owner-a")
    client, _repository, _secrets, _audits = _client(
        monkeypatch,
        roles={"ws-a": "owner", "ws-b": "viewer"},
    )

    response = client.get(
        "/api/model-providers",
        headers=trusted_headers(actor_id="owner-a", tenant_id="tenant-a"),
    )

    assert response.status_code == 200


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


def test_bedrock_patch_is_hidden_when_specific_flag_is_off(monkeypatch) -> None:
    client, repository, secrets, audits = _client(monkeypatch)
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
    audit_count = len(audits)
    monkeypatch.setenv("DF_AWS_BEDROCK_CONNECTOR_ENABLED", "0")

    response = client.patch(
        f"/api/model-providers/{created['provider_id']}",
        headers=headers,
        json={
            "base_revision": created["revision"],
            "display_name": "Blocked Bedrock update",
        },
    )

    current = repository.get(
        next(iter(secrets.values))[0],
        created["provider_id"],
    )
    assert response.status_code == 404
    assert current.revision == created["revision"]
    assert current.display_name == created["display_name"]
    assert len(audits) == audit_count


def test_bedrock_disable_is_hidden_when_specific_flag_is_off(monkeypatch) -> None:
    client, repository, secrets, audits = _client(monkeypatch)
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
    audit_count = len(audits)
    monkeypatch.setenv("DF_AWS_BEDROCK_CONNECTOR_ENABLED", "0")

    response = client.post(
        f"/api/model-providers/{created['provider_id']}/disable",
        headers=headers,
        json={"base_revision": created["revision"]},
    )

    current = repository.get(
        next(iter(secrets.values))[0],
        created["provider_id"],
    )
    assert response.status_code == 404
    assert current.revision == created["revision"]
    assert current.connection_state == created["connection_state"]
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


def test_competing_revisioned_rotations_serialize_before_audit_and_secret(
    monkeypatch,
) -> None:
    class _CountingRepository(InMemoryModelProviderRepository):
        def __init__(self) -> None:
            super().__init__()
            self.update_calls = 0

        def update(self, *args: object, **kwargs: object):
            self.update_calls += 1
            return super().update(*args, **kwargs)

    repository = _CountingRepository()
    client, _repository, secrets, audits = _client(
        monkeypatch,
        repository=repository,
    )
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
    rotate_count = secrets.rotate_calls
    update_count = repository.update_calls
    first_audit_entered = Event()
    release_first_audit = Event()
    competing_guard_attempted = Event()
    guard_count = 0
    guard_count_lock = Lock()
    base_guard = getattr(repository, "mutation_guard", None)

    @contextmanager
    def coordinated_guard(tenant_ref: str, provider_id: str):
        nonlocal guard_count
        with guard_count_lock:
            guard_count += 1
            guard_index = guard_count
        if guard_index == 2:
            competing_guard_attempted.set()
        guard = (
            base_guard(tenant_ref, provider_id)
            if base_guard is not None
            else nullcontext()
        )
        with guard:
            yield

    repository.mutation_guard = coordinated_guard  # type: ignore[attr-defined]
    audit_call_count = 0
    audit_call_lock = Lock()

    def blocking_audit(actor, action, resource, **metadata):
        nonlocal audit_call_count
        with audit_call_lock:
            audit_call_count += 1
            call_index = audit_call_count
        if call_index == 1:
            first_audit_entered.set()
            assert release_first_audit.wait(timeout=5)
        audits.append(
            {
                "actor": actor,
                "action": action,
                "resource": resource,
                "metadata": metadata,
            }
        )
        return {"event_id": "event-safe"}

    monkeypatch.setattr(provider_router, "record_audit_event", blocking_audit)
    url = f"/api/model-providers/{created['provider_id']}/rotate-secret"

    def rotate(api_key: str):
        return client.post(
            url,
            headers=headers,
            json={
                "api_key": api_key,
                "base_revision": created["revision"],
            },
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(rotate, "winner-marker")
        assert first_audit_entered.wait(timeout=5)
        second = pool.submit(rotate, "loser-marker")
        guard_observed = competing_guard_attempted.wait(timeout=2)
        release_first_audit.set()
        first_response = first.result(timeout=10)
        second_response = second.result(timeout=10)

    current = repository.get(
        next(iter(secrets.values))[0],
        created["provider_id"],
    )
    assert guard_observed
    assert first_response.status_code == 200
    assert second_response.status_code == 409
    assert len(audits) == audit_count + 1
    assert secrets.rotate_calls == rotate_count + 1
    assert repository.update_calls == update_count + 2
    assert current.revision == created["revision"] + 2


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


@pytest.mark.parametrize(
    ("field_name", "forged_value"),
    [
        ("connection_state", "invalid"),
        ("governance_state", "governed"),
        (
            "available_models",
            [
                {
                    "model_id": "forged-model",
                    "display_name": "Forged model",
                    "capabilities": ["admin"],
                    "support_state": "supported",
                    "price_key": "forged:price",
                }
            ],
        ),
        ("models", []),
        ("last_tested_at", "2026-07-29T01:00:00Z"),
        ("last_success_at", "2026-07-29T01:00:00Z"),
        ("safe_error_category", "forged_error"),
        ("last_error_category", "forged_error"),
        ("secret_ref", "forged-secret-reference"),
    ],
)
def test_provider_patch_rejects_server_owned_state_without_mutation(
    monkeypatch,
    field_name: str,
    forged_value: object,
) -> None:
    client, repository, secrets, audits = _client(monkeypatch)
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
    tenant_ref = next(iter(secrets.values))[0]
    before = repository.get(tenant_ref, created["provider_id"])
    audit_count = len(audits)

    response = client.patch(
        f"/api/model-providers/{created['provider_id']}",
        headers=headers,
        json={
            "base_revision": before.revision,
            field_name: forged_value,
        },
    )

    after = repository.get(tenant_ref, created["provider_id"])
    assert {
        "status_code": response.status_code,
        "repository_mutated": after != before,
        "audit_written": len(audits) != audit_count,
    } == {
        "status_code": 422,
        "repository_mutated": False,
        "audit_written": False,
    }


def test_provider_patch_preserves_server_observations_when_editing_configuration(
    monkeypatch,
) -> None:
    client, repository, secrets, audits = _client(monkeypatch)
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
    tenant_ref = next(iter(secrets.values))[0]
    before = repository.get(tenant_ref, created["provider_id"])
    audit_count = len(audits)

    response = client.patch(
        f"/api/model-providers/{created['provider_id']}",
        headers=headers,
        json={
            "base_revision": before.revision,
            "display_name": "DeepSeek primary",
        },
    )

    after = repository.get(tenant_ref, created["provider_id"])
    assert response.status_code == 200
    assert after.display_name == "DeepSeek primary"
    assert after.revision == before.revision + 1
    assert after.connection_state == before.connection_state
    assert after.governance_state == before.governance_state
    assert after.available_models == before.available_models
    assert after.last_tested_at == before.last_tested_at
    assert after.last_success_at == before.last_success_at
    assert after.safe_error_category == before.safe_error_category
    assert len(audits) == audit_count + 1


def test_deepseek_patch_cannot_redirect_stored_secret_to_another_host(
    monkeypatch,
) -> None:
    transport = _CapturingTransport()
    client, repository, secrets, audits = _client(
        monkeypatch,
        transport=transport,
    )
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
    tenant_ref = next(iter(secrets.values))[0]
    before = repository.get(tenant_ref, created["provider_id"])
    audit_count = len(audits)
    transport.calls.clear()

    response = client.patch(
        f"/api/model-providers/{created['provider_id']}",
        headers=headers,
        json={
            "base_revision": before.revision,
            "base_url": "https://attacker.example",
        },
    )
    if response.status_code == 200:
        client.post(
            f"/api/model-providers/{created['provider_id']}/test",
            headers=headers,
        )

    assert {
        "status_code": response.status_code,
        "repository_mutated": (
            repository.get(tenant_ref, created["provider_id"]) != before
        ),
        "audit_written": len(audits) != audit_count,
        "transport_calls": [
            {
                "base_url": call.get("base_url"),
                "api_key": call.get("api_key"),
            }
            for call in transport.calls
        ],
    } == {
        "status_code": 422,
        "repository_mutated": False,
        "audit_written": False,
        "transport_calls": [],
    }


def test_deepseek_patch_rejects_irrelevant_region_before_audit(
    monkeypatch,
) -> None:
    client, repository, secrets, audits = _client(monkeypatch)
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
    tenant_ref = next(iter(secrets.values))[0]
    before = repository.get(tenant_ref, created["provider_id"])
    audit_count = len(audits)

    response = client.patch(
        f"/api/model-providers/{created['provider_id']}",
        headers=headers,
        json={
            "base_revision": before.revision,
            "region": "us-east-1",
        },
    )

    assert response.status_code == 422
    assert repository.get(tenant_ref, created["provider_id"]) == before
    assert len(audits) == audit_count


def test_bedrock_patch_rejects_user_supplied_base_url_before_audit(
    monkeypatch,
) -> None:
    client, repository, secrets, audits = _client(monkeypatch)
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
    tenant_ref = next(iter(secrets.values))[0]
    before = repository.get(tenant_ref, created["provider_id"])
    audit_count = len(audits)

    response = client.patch(
        f"/api/model-providers/{created['provider_id']}",
        headers=headers,
        json={
            "base_revision": before.revision,
            "base_url": "https://attacker.example",
        },
    )

    assert response.status_code == 422
    assert repository.get(tenant_ref, created["provider_id"]) == before
    assert len(audits) == audit_count
    assert "secret-marker" not in response.text


def test_bedrock_patch_derives_endpoint_from_valid_region(monkeypatch) -> None:
    client, repository, secrets, audits = _client(monkeypatch)
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
    tenant_ref = next(iter(secrets.values))[0]
    before = repository.get(tenant_ref, created["provider_id"])
    audit_count = len(audits)

    response = client.patch(
        f"/api/model-providers/{created['provider_id']}",
        headers=headers,
        json={
            "base_revision": before.revision,
            "region": "us-west-2",
        },
    )

    after = repository.get(tenant_ref, created["provider_id"])
    assert response.status_code == 200
    assert after.region == "us-west-2"
    assert after.base_url == "https://bedrock.us-west-2.amazonaws.com"
    assert after.connection_state == before.connection_state
    assert after.available_models == before.available_models
    assert after.revision == before.revision + 1
    assert len(audits) == audit_count + 1


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


def test_service_never_sends_secret_to_non_official_deepseek_endpoint() -> None:
    repository = InMemoryModelProviderRepository()
    secrets = _Secrets()
    transport = _CapturingTransport()
    now = datetime(2026, 7, 29, tzinfo=timezone.utc)
    provider = ModelProviderRecord(
        provider_id="provider_deepseek",
        tenant_ref="tenant-safe",
        provider_type="deepseek",
        display_name="DeepSeek",
        base_url="https://attacker.example",
        secret_ref="kv:provider-provider_deepseek",
        connection_state="connected",
        governance_state="pending",
        available_models=[],
        revision=1,
        created_by_ref="actor-safe",
        updated_by_ref="actor-safe",
        created_at=now,
        updated_at=now,
    )
    repository.create(provider)
    secrets.values[("tenant-safe", "provider_deepseek")] = "secret-marker"
    service = ModelProviderService(
        repository=repository,
        secret_store=secrets,
        transport=transport,
        clock=lambda: now,
    )

    result = service.test(
        tenant_ref="tenant-safe",
        provider_id="provider_deepseek",
        actor_ref="actor-safe",
    )

    assert result["connection_state"] == "invalid"
    assert result["safe_error_category"] == "configuration_conflict"
    assert transport.calls == []
