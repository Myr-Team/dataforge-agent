from __future__ import annotations

from fastapi.testclient import TestClient

import backend.entra_group_router as group_router
from auth_fixtures import trusted_headers
from backend.app import app
from backend.entra_group_mapping import InMemoryEntraGroupMappingRepository


def _client(monkeypatch, *, roles: dict[str, str] | None = None):
    repository = InMemoryEntraGroupMappingRepository()
    audits: list[dict[str, object]] = []
    monkeypatch.setenv("DF_WEB_PROXY_SECRET", "test-proxy-secret")
    monkeypatch.setenv("DF_FINOPS_HMAC_SECRET", "finops-test-secret")
    monkeypatch.setenv("DF_ENTRA_GROUP_GOVERNANCE_ENABLED", "1")
    monkeypatch.setattr(
        group_router,
        "get_entra_group_mapping_repository",
        lambda: repository,
    )
    monkeypatch.setattr(
        group_router,
        "_authorized_workspace_roles",
        lambda _actor: dict(roles or {"ws-a": "owner"}),
    )
    monkeypatch.setattr(
        group_router,
        "record_audit_event",
        lambda actor, action, resource, **metadata: audits.append(
            {
                "actor": actor,
                "action": action,
                "resource": resource,
                "metadata": metadata,
            }
        )
        or {"event_id": "audit-safe"},
    )
    monkeypatch.setattr(
        group_router,
        "search_entra_groups",
        lambda query, _request, limit=8: {
            "connected": True,
            "source": "microsoft_graph",
            "permission_state": "granted",
            "groups": [
                {
                    "id": "raw-group-finance",
                    "display_name": "Finance",
                    "mail": None,
                    "security_enabled": True,
                    "group_type": "security",
                }
            ],
        },
    )
    return TestClient(app), repository, audits


def test_owner_searches_and_creates_friendly_group_mapping(monkeypatch) -> None:
    client, _repository, audits = _client(monkeypatch)
    headers = trusted_headers(actor_id="owner-a", tenant_id="tenant-a")

    groups = client.get(
        "/api/identity-governance/groups?query=finance",
        headers=headers,
    )
    created = client.post(
        "/api/identity-governance/group-mappings",
        headers=headers,
        json={
            "group_id": "raw-group-finance",
            "display_name": "Finance",
            "role": "viewer",
            "workspace_ids": ["ws-a"],
            "priority": 100,
        },
    )
    listed = client.get("/api/identity-governance", headers=headers)

    assert groups.status_code == 200
    assert groups.json()["groups"][0]["display_name"] == "Finance"
    assert created.status_code == 201
    assert created.json()["display_name"] == "Finance"
    assert created.json()["role"] == "viewer"
    assert "raw-group-finance" not in str(created.json())
    assert "raw-group-finance" not in str(listed.json())
    assert audits[0]["action"] == "entra_group_mapping.manage"


def test_group_mapping_rejects_owner_and_stale_revision(monkeypatch) -> None:
    client, _repository, _audits = _client(monkeypatch)
    headers = trusted_headers(actor_id="owner-a", tenant_id="tenant-a")

    owner = client.post(
        "/api/identity-governance/group-mappings",
        headers=headers,
        json={
            "group_id": "raw-group-finance",
            "display_name": "Finance",
            "role": "owner",
            "workspace_ids": ["ws-a"],
        },
    )
    assert owner.status_code == 422

    created = client.post(
        "/api/identity-governance/group-mappings",
        headers=headers,
        json={
            "group_id": "raw-group-finance",
            "display_name": "Finance",
            "role": "viewer",
            "workspace_ids": ["ws-a"],
        },
    ).json()
    conflict = client.patch(
        f"/api/identity-governance/group-mappings/{created['mapping_id']}",
        headers=headers,
        json={"base_revision": 2, "role": "editor"},
    )

    assert conflict.status_code == 409


def test_group_mapping_is_tenant_scoped_and_requires_admin(monkeypatch) -> None:
    client, _repository, _audits = _client(monkeypatch)
    owner_a = trusted_headers(actor_id="owner-a", tenant_id="tenant-a")
    owner_b = trusted_headers(actor_id="owner-b", tenant_id="tenant-b")

    assert client.post(
        "/api/identity-governance/group-mappings",
        headers=owner_a,
        json={
            "group_id": "raw-group-finance",
            "display_name": "Finance",
            "role": "viewer",
            "workspace_ids": ["ws-a"],
        },
    ).status_code == 201
    assert client.get(
        "/api/identity-governance",
        headers=owner_b,
    ).json()["mapping_count"] == 0

    denied_client, _repository, _audits = _client(
        monkeypatch,
        roles={"ws-a": "viewer"},
    )
    assert denied_client.get(
        "/api/identity-governance",
        headers=owner_a,
    ).status_code == 403


def test_configured_tenant_owner_can_manage_groups_with_mixed_workspace_roles(monkeypatch) -> None:
    monkeypatch.setenv("DF_FINOPS_TENANT_OWNER_OIDS", "owner-a")
    client, _repository, _audits = _client(
        monkeypatch,
        roles={"ws-a": "owner", "ws-b": "viewer"},
    )

    response = client.get(
        "/api/identity-governance",
        headers=trusted_headers(actor_id="owner-a", tenant_id="tenant-a"),
    )

    assert response.status_code == 200


def test_group_mapping_requires_durable_audit(monkeypatch) -> None:
    client, repository, _audits = _client(monkeypatch)
    monkeypatch.setattr(
        group_router,
        "record_audit_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("audit unavailable")
        ),
    )

    response = client.post(
        "/api/identity-governance/group-mappings",
        headers=trusted_headers(actor_id="owner-a", tenant_id="tenant-a"),
        json={
            "group_id": "raw-group-finance",
            "display_name": "Finance",
            "role": "viewer",
            "workspace_ids": ["ws-a"],
        },
    )

    assert response.status_code == 503
    assert repository.list("tenant_a") == []
