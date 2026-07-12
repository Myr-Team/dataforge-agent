from __future__ import annotations

import json
import importlib
import base64
from urllib.parse import quote

import pytest

import backend.workspace_authz as workspace_authz
from backend.app import app
from fastapi.testclient import TestClient


def _trusted_easy_auth_headers(email: str, *, actor_id: str = "oid-user", tenant_id: str = "tenant-1") -> dict[str, str]:
    principal = {
        "userDetails": email,
        "claims": [
            {"typ": "preferred_username", "val": email},
            {"typ": "oid", "val": actor_id},
            {"typ": "tid", "val": tenant_id},
        ],
    }
    encoded = base64.urlsafe_b64encode(json.dumps(principal).encode("utf-8")).decode("ascii")
    return {
        "x-ms-client-principal": encoded,
        "x-dataforge-proxy-secret": "server-only-secret",
    }


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
        {"email": "OWNER@contoso.com", "actor_id": "oid-owner", "source": "easy_auth"},
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

    assert workspace_authz.workspace_role("ws-roles", {"actor_id": "oid-editor", "source": "easy_auth"}) == "editor"
    assert workspace_authz.workspace_role("ws-roles", {"email": "viewer@contoso.com"}) is None
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


@pytest.mark.parametrize(
    ("method", "path", "kwargs", "action"),
    [
        ("delete", "/api/workspaces/ws-roles", {}, "workspace.delete"),
        ("post", "/api/workspaces/ws-roles/auto-analyze", {"json": {}}, "analysis.run"),
        (
            "post",
            "/api/chat",
            {"json": {"workspace_id": "ws-roles", "message": "analyze this"}},
            "analysis.run",
        ),
        (
            "post",
            "/api/workspaces/ws-roles/flagship",
            {"json": {"run_id": "run-1"}},
            "analysis.run",
        ),
        (
            "post",
            "/api/produce",
            {"json": {"workspace_id": "ws-roles", "conversation_id": "run-1", "feasibility": {}}},
            "artifact.generate",
        ),
        (
            "post",
            "/api/upload",
            {
                "data": {"workspace_id": "ws-roles"},
                "files": {"file": ("data.csv", b"a,b\n1,2\n", "text/csv")},
            },
            "file.create",
        ),
        (
            "post",
            "/api/workspaces/ws-roles/action-plan",
            {"json": {"method": "pilot"}},
            "analysis.run",
        ),
    ],
)
def test_viewer_cannot_reach_workspace_mutation_routes(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    kwargs: dict,
    action: str,
) -> None:
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
    headers = {"x-dataforge-actor": quote(json.dumps({"email": "viewer@contoso.com"}))}

    response = TestClient(app).request(method, path, headers=headers, **kwargs)

    assert response.status_code == 403
    assert action in response.json()["detail"]


def test_pending_editor_cannot_mutate_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DF_WORKSPACE_RBAC_ENFORCED", "1")
    monkeypatch.setattr(
        workspace_authz,
        "_load_workspace_meta",
        lambda _workspace_id: {
            "workspace_members": [
                {"email": "pending@contoso.com", "role": "editor", "status": "pending"}
            ]
        },
    )

    with pytest.raises(PermissionError, match="analysis.run"):
        workspace_authz.require_workspace_permission(
            "ws-roles",
            {"email": "pending@contoso.com"},
            "analysis.run",
        )


def test_easy_auth_login_activates_matching_pending_invitation(monkeypatch: pytest.MonkeyPatch) -> None:
    saved: list[dict] = []
    meta = {
        "workspace_members": [
            {"email": "invited@contoso.com", "role": "editor", "status": "pending"}
        ]
    }
    monkeypatch.setattr(workspace_authz, "_load_workspace_meta", lambda _workspace_id: meta)
    monkeypatch.setattr(
        workspace_authz,
        "_save_workspace_meta",
        lambda _workspace_id, value: saved.append(value),
        raising=False,
    )

    role = workspace_authz.workspace_role(
        "ws-roles",
        {
            "email": "invited@contoso.com",
            "actor_id": "oid-invited",
            "tenant_id": "tenant-1",
            "source": "easy_auth",
        },
    )

    assert role == "editor"
    assert saved[0]["workspace_members"][0]["status"] == "active"
    assert saved[0]["workspace_members"][0]["actor_id"] == "oid-invited"


def test_new_workspace_upload_passes_authenticated_owner_to_store(monkeypatch: pytest.MonkeyPatch) -> None:
    app_module = importlib.import_module("backend.app")
    captured: dict = {}

    def create_job(**kwargs):
        captured.update(kwargs)
        return {
            "workspace_id": "upload-owner-test",
            "name": "Owner test",
            "format": "csv",
            "indexed_count": 0,
            "profile_summary": "processing",
            "documents": [],
            "reference_images": [],
            "ingest_job_id": None,
            "ingest_status": {"state": "ready"},
        }

    monkeypatch.setattr(app_module, "create_workspace_upload_job", create_job)
    headers = {"x-dataforge-actor": quote(json.dumps({"email": "creator@contoso.com", "actor_id": "oid-creator"}))}

    response = TestClient(app).post(
        "/api/upload",
        files={"file": ("data.csv", b"a,b\n1,2\n", "text/csv")},
        headers=headers,
    )

    assert response.status_code == 200
    assert captured["actor"]["email"] == "creator@contoso.com"
    assert captured["actor"]["actor_id"] == "oid-creator"


def test_forged_client_owner_header_is_rejected_when_rbac_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DF_WORKSPACE_RBAC_ENFORCED", "1")
    monkeypatch.setenv("DF_WORKSPACE_OWNER_EMAIL", "owner@contoso.com")
    monkeypatch.setenv("DF_WEB_PROXY_SECRET", "server-only-secret")
    headers = {
        "x-dataforge-actor": quote(json.dumps({"email": "owner@contoso.com", "actor_id": "forged"})),
    }

    response = TestClient(app).delete("/api/workspaces/upload-forged-owner", headers=headers)

    assert response.status_code == 403
    assert "workspace.delete" in response.json()["detail"]


def test_trusted_web_proxy_easy_auth_principal_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DF_WORKSPACE_RBAC_ENFORCED", "1")
    monkeypatch.setenv("DF_WORKSPACE_OWNER_EMAIL", "owner@contoso.com")
    monkeypatch.setenv("DF_WEB_PROXY_SECRET", "server-only-secret")
    headers = _trusted_easy_auth_headers("owner@contoso.com", actor_id="oid-owner")

    response = TestClient(app).get("/api/workspaces/ws-roles/artifact-jobs", headers=headers)

    assert response.status_code == 200


def test_pending_invitation_rejects_mismatched_oid_or_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        workspace_authz,
        "_load_workspace_meta",
        lambda _workspace_id: {
            "workspace_members": [
                {
                    "email": "invited@contoso.com",
                    "actor_id": "oid-original",
                    "tenant_id": "tenant-1",
                    "role": "editor",
                    "status": "pending",
                }
            ]
        },
    )

    assert workspace_authz.workspace_role(
        "ws-roles",
        {
            "email": "invited@contoso.com",
            "actor_id": "oid-other",
            "tenant_id": "tenant-2",
            "source": "easy_auth",
        },
    ) is None


@pytest.mark.parametrize(
    "path",
    [
        "/api/workspaces/ws-private",
        "/api/workspaces/ws-private/dashboard",
        "/api/workspaces/ws-private/files",
        "/api/workspaces/ws-private/experiments",
        "/api/workspaces/ws-private/governance-summary",
        "/api/runs?workspace_id=ws-private",
        "/api/conversations?workspace_id=ws-private",
    ],
)
def test_non_member_cannot_read_workspace_scoped_surfaces(monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    monkeypatch.setenv("DF_WORKSPACE_RBAC_ENFORCED", "1")
    monkeypatch.setenv("DF_WORKSPACE_OWNER_EMAIL", "owner@contoso.com")
    monkeypatch.setenv("DF_WEB_PROXY_SECRET", "server-only-secret")
    monkeypatch.setattr(workspace_authz, "_load_workspace_meta", lambda _workspace_id: {})

    response = TestClient(app).get(path, headers=_trusted_easy_auth_headers("outsider@contoso.com"))

    assert response.status_code == 403
    assert "read" in response.json()["detail"]


def test_workspace_list_only_returns_memberships_when_rbac_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    app_module = importlib.import_module("backend.app")
    monkeypatch.setenv("DF_WORKSPACE_RBAC_ENFORCED", "1")
    monkeypatch.setenv("DF_WORKSPACE_OWNER_EMAIL", "owner@contoso.com")
    monkeypatch.setenv("DF_WEB_PROXY_SECRET", "server-only-secret")
    monkeypatch.setattr(
        app_module,
        "list_workspaces",
        lambda: [
            {"workspace_id": "ws-allowed", "name": "Allowed", "doc_count": 1},
            {"workspace_id": "ws-private", "name": "Private", "doc_count": 1},
        ],
    )
    monkeypatch.setattr(
        workspace_authz,
        "_load_workspace_meta",
        lambda workspace_id: {
            "workspace_members": [
                {"email": "viewer@contoso.com", "actor_id": "oid-viewer", "tenant_id": "tenant-1", "role": "viewer", "status": "active"}
            ]
        } if workspace_id == "ws-allowed" else {},
    )

    response = TestClient(app).get(
        "/api/workspaces",
        headers=_trusted_easy_auth_headers("viewer@contoso.com", actor_id="oid-viewer"),
    )

    assert response.status_code == 200
    assert [item["workspace_id"] for item in response.json()["workspaces"]] == ["ws-allowed"]


@pytest.mark.parametrize(
    "path",
    [
        "/api/runs/run-private/summary",
        "/api/runs/run-private/trace",
        "/api/runs/run-private/pipeline",
        "/api/runs/run-private/structured-result",
        "/api/runs/run-private/log",
        "/api/conversations/conv-private/structured-result",
        "/api/conversations/conv-private/context",
        "/api/conversations/conv-private/quick-actions",
    ],
)
def test_non_member_cannot_read_run_or_conversation_objects(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    control_module = importlib.import_module("backend.control_plane")
    monkeypatch.setenv("DF_WORKSPACE_RBAC_ENFORCED", "1")
    monkeypatch.setenv("DF_WORKSPACE_OWNER_EMAIL", "owner@contoso.com")
    monkeypatch.setenv("DF_WEB_PROXY_SECRET", "server-only-secret")
    monkeypatch.setattr(workspace_authz, "_load_workspace_meta", lambda _workspace_id: {})
    monkeypatch.setattr(
        control_module,
        "get_run",
        lambda run_id: {"run_id": run_id, "workspace_id": "ws-private"},
    )
    monkeypatch.setattr(
        control_module,
        "get_conversation",
        lambda conversation_id: {"conversation_id": conversation_id, "workspace_id": "ws-private"},
    )

    response = TestClient(app).get(path, headers=_trusted_easy_auth_headers("outsider@contoso.com"))

    assert response.status_code == 403
    assert "read" in response.json()["detail"]


def test_non_member_cannot_download_workspace_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    app_module = importlib.import_module("backend.app")
    monkeypatch.setenv("DF_WORKSPACE_RBAC_ENFORCED", "1")
    monkeypatch.setenv("DF_WORKSPACE_OWNER_EMAIL", "owner@contoso.com")
    monkeypatch.setenv("DF_WEB_PROXY_SECRET", "server-only-secret")
    monkeypatch.setattr(workspace_authz, "_load_workspace_meta", lambda _workspace_id: {})
    monkeypatch.setattr(
        app_module,
        "list_artifact_jobs",
        lambda _workspace_id=None: [
            {
                "job_id": "artifact_job_private",
                "workspace_id": "ws-private",
                "artifacts": {"pdf": {"artifact_url": "/api/artifacts/private-plan.pdf"}},
            }
        ],
    )
    monkeypatch.setattr(app_module, "list_runs", lambda _workspace_id=None: [])

    response = TestClient(app).get(
        "/api/artifacts/private-plan.pdf",
        headers=_trusted_easy_auth_headers("outsider@contoso.com"),
    )

    assert response.status_code == 403
    assert "artifact.read" in response.json()["detail"]
