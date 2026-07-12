from __future__ import annotations

import json
from urllib.parse import quote

import pytest

import backend.workspace_authz as workspace_authz
from backend.app import app
from fastapi.testclient import TestClient


def test_role_capabilities_are_enforced_by_action() -> None:
    assert workspace_authz.authorize("owner", "member.manage") is True
    assert workspace_authz.authorize("admin", "outcome.verify") is True
    assert workspace_authz.authorize("editor", "analysis.run") is True
    assert workspace_authz.authorize("editor", "file.edit") is True
    assert workspace_authz.authorize("editor", "member.manage") is False
    assert workspace_authz.authorize("viewer", "workspace.read") is True
    assert workspace_authz.authorize("viewer", "file.edit") is False
    assert workspace_authz.authorize(None, "workspace.read") is False


def test_default_workspace_owner_resolves_without_stored_member(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(workspace_authz, "_load_workspace_meta", lambda _workspace_id: {})
    monkeypatch.setenv("DF_WORKSPACE_OWNER_EMAIL", "owner@contoso.com")

    role = workspace_authz.workspace_role(
        "ws-roles",
        {"email": "OWNER@contoso.com", "actor_id": "oid-owner"},
    )

    assert role == "owner"


def test_stored_member_role_resolves_by_actor_id_or_email(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        workspace_authz,
        "_load_workspace_meta",
        lambda _workspace_id: {
            "workspace_members": [
                {
                    "email": "editor@contoso.com",
                    "actor_id": "oid-editor",
                    "role": "editor",
                    "status": "active",
                },
                {
                    "email": "viewer@contoso.com",
                    "role": "viewer",
                    "status": "pending",
                },
            ]
        },
    )

    assert workspace_authz.workspace_role("ws-roles", {"actor_id": "oid-editor"}) == "editor"
    assert workspace_authz.workspace_role("ws-roles", {"email": "viewer@contoso.com"}) == "viewer"
    assert workspace_authz.workspace_role("ws-roles", {"email": "unknown@contoso.com"}) is None


def test_permission_gate_is_disabled_until_explicitly_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DF_WORKSPACE_RBAC_ENFORCED", raising=False)

    assert workspace_authz.require_workspace_permission(
        "ws-roles",
        {"email": "unknown@contoso.com"},
        "file.edit",
    ) == "compatibility"


def test_enabled_permission_gate_rejects_viewer_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DF_WORKSPACE_RBAC_ENFORCED", "1")
    monkeypatch.setattr(
        workspace_authz,
        "workspace_role",
        lambda _workspace_id, _actor: "viewer",
    )

    with pytest.raises(PermissionError, match="file.edit"):
        workspace_authz.require_workspace_permission(
            "ws-roles",
            {"email": "viewer@contoso.com"},
            "file.edit",
        )


def test_viewer_mutation_endpoint_returns_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DF_WORKSPACE_RBAC_ENFORCED", "1")
    monkeypatch.setenv("DF_WORKSPACE_OWNER_EMAIL", "owner@contoso.com")
    monkeypatch.setattr(
        workspace_authz,
        "_load_workspace_meta",
        lambda _workspace_id: {
            "workspace_members": [
                {"email": "viewer@contoso.com", "role": "viewer", "status": "active"}
            ]
        },
    )
    headers = {
        "x-dataforge-actor": quote(json.dumps({"email": "viewer@contoso.com"})),
    }

    response = TestClient(app).put(
        "/api/workspaces/ws-roles/files/file-1/cells",
        json={"edits": [{"row": 0, "column": 0, "value": "blocked"}]},
        headers=headers,
    )

    assert response.status_code == 403
    assert "file.edit" in response.json()["detail"]
