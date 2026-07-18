from __future__ import annotations

import json
import importlib
import base64
from urllib.parse import quote

import pytest

import backend.invitation_store as invitation_store
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


def _actor(oid: str, tid: str = "tenant-a") -> dict[str, str]:
    return {"actor_id": oid, "tenant_id": tid, "source": "easy_auth"}


def test_access_decision_normalizes_matching_legacy_owner_without_email_grant(monkeypatch: pytest.MonkeyPatch) -> None:
    saved: list[dict[str, object]] = []
    meta = {
        "workspace_owner": {"actor_id": "owner-oid", "tenant_id": "tenant-a"},
        "workspace_members": [],
    }
    monkeypatch.setenv("DF_WORKSPACE_RBAC_ENFORCED", "1")
    monkeypatch.setattr(workspace_authz, "_load_workspace_meta", lambda _id: meta)
    monkeypatch.setattr(workspace_authz, "_save_workspace_meta", lambda _id, value: saved.append(dict(value)))
    monkeypatch.setattr(workspace_authz, "_audit_active_key", lambda: ("audit-key", b"k" * 32))
    monkeypatch.setattr(workspace_authz, "_audit_actor_hash", lambda _actor, _key: "actor_audit-correlation")

    decision = workspace_authz.workspace_access_decision("ws-legacy", _actor("owner-oid"))

    assert (decision.allowed, decision.role, decision.reason_code) == (True, "owner", "owner_match")
    assert saved[0]["workspace_members"][0]["actor_id"] == "owner-oid"
    assert saved[0]["workspace_members"][0]["tenant_id"] == "tenant-a"
    normalization = saved[0]["authorization_normalizations"][0]
    assert set(normalization) == {"kind", "occurred_at", "identity_correlation"}
    assert normalization["kind"] == "owner_membership"
    assert normalization["identity_correlation"] == "corr_audit-correlation"
    assert all(value not in str(normalization) for value in ("owner-oid", "tenant-a"))

    second = workspace_authz.workspace_access_decision("ws-legacy", _actor("owner-oid"))

    assert (second.allowed, second.role, second.reason_code) == (True, "owner", "owner_match")
    assert len(saved) == 1


def test_access_decision_rejects_same_oid_from_another_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DF_WORKSPACE_RBAC_ENFORCED", "1")
    monkeypatch.setattr(workspace_authz, "_load_workspace_meta", lambda _id: {
        "workspace_owner": {"actor_id": "owner-oid", "tenant_id": "tenant-a"},
        "workspace_members": [],
    })

    decision = workspace_authz.workspace_access_decision("ws-legacy", _actor("owner-oid", "tenant-b"))

    assert (decision.allowed, decision.role, decision.reason_code) == (False, None, "tenant_mismatch")


@pytest.mark.parametrize("rbac_enforced", [False, True])
def test_access_decision_never_grants_owner_or_member_by_email(monkeypatch: pytest.MonkeyPatch, rbac_enforced: bool) -> None:
    if rbac_enforced:
        monkeypatch.setenv("DF_WORKSPACE_RBAC_ENFORCED", "1")
    else:
        monkeypatch.delenv("DF_WORKSPACE_RBAC_ENFORCED", raising=False)
    monkeypatch.setenv("DF_WORKSPACE_OWNER_EMAIL", "owner@contoso.com")
    monkeypatch.setattr(
        workspace_authz,
        "_load_workspace_meta",
        lambda _id: {
            "workspace_owner": {"email": "owner@contoso.com"},
            "workspace_members": [{"email": "member@contoso.com", "role": "editor", "status": "active"}],
        },
    )

    owner = workspace_authz.workspace_access_decision("ws-email", {"email": "owner@contoso.com", "source": "easy_auth"})
    member = workspace_authz.workspace_access_decision(
        "ws-email",
        {"email": "member@contoso.com", "actor_id": "member-oid", "tenant_id": "tenant-a", "source": "easy_auth"},
    )

    assert (owner.allowed, owner.role, owner.reason_code) == (False, None, "identity_missing")
    assert (member.allowed, member.role, member.reason_code) == (False, None, "membership_missing")


def test_owner_normalization_omits_correlation_when_audit_key_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    saved: list[dict[str, object]] = []
    monkeypatch.setenv("DF_WORKSPACE_RBAC_ENFORCED", "1")
    monkeypatch.setattr(
        workspace_authz,
        "_load_workspace_meta",
        lambda _id: {"workspace_owner": {"actor_id": "owner-oid", "tenant_id": "tenant-a"}, "workspace_members": []},
    )
    monkeypatch.setattr(workspace_authz, "_save_workspace_meta", lambda _id, value: saved.append(dict(value)))
    monkeypatch.setattr(
        workspace_authz,
        "_audit_active_key",
        lambda: (_ for _ in ()).throw(workspace_authz.AuditPersistenceError("audit key unavailable")),
    )

    decision = workspace_authz.workspace_access_decision("ws-no-audit-key", _actor("owner-oid"))

    assert (decision.allowed, decision.role, decision.reason_code) == (True, "owner", "owner_match")
    normalization = saved[0]["authorization_normalizations"][0]
    assert set(normalization) == {"kind", "occurred_at"}


@pytest.mark.parametrize(
    ("meta", "actor", "expected_reason"),
    [
        ({"workspace_members": []}, _actor("outsider-oid"), "membership_missing"),
        ({"workspace_owner": {"actor_id": "owner-oid", "tenant_id": "tenant-a"}}, _actor("owner-oid", "tenant-b"), "tenant_mismatch"),
    ],
)
def test_sensitive_role_resolver_cannot_override_canonical_denial(
    monkeypatch: pytest.MonkeyPatch,
    meta: dict[str, object],
    actor: dict[str, str],
    expected_reason: str,
) -> None:
    monkeypatch.setenv("DF_WORKSPACE_RBAC_ENFORCED", "1")
    monkeypatch.setattr(workspace_authz, "_load_workspace_meta", lambda _id: meta)

    with pytest.raises(workspace_authz.WorkspaceAuthorizationError) as error:
        workspace_authz.require_sensitive_workspace_permission(
            "ws-sensitive",
            actor,
            "member.manage",
            role_resolver=lambda _workspace_id, _actor: "owner",
        )

    assert error.value.decision.reason_code == expected_reason


def test_sensitive_role_resolver_cannot_elevate_canonical_role(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DF_WORKSPACE_RBAC_ENFORCED", "1")
    monkeypatch.setattr(
        workspace_authz,
        "_load_workspace_meta",
        lambda _id: {
            "workspace_members": [
                {"actor_id": "viewer-oid", "tenant_id": "tenant-a", "role": "viewer", "status": "active"},
            ],
        },
    )

    with pytest.raises(workspace_authz.WorkspaceAuthorizationError) as error:
        workspace_authz.require_sensitive_workspace_permission(
            "ws-sensitive",
            _actor("viewer-oid"),
            "member.manage",
            role_resolver=lambda _workspace_id, _actor: "owner",
        )

    assert (error.value.decision.role, error.value.decision.reason_code) == ("viewer", "role_denied")


def test_removed_invitation_member_denies_before_accepted_journal_role(monkeypatch: pytest.MonkeyPatch) -> None:
    actor = _actor("invited-oid")
    meta = {"workspace_members": []}
    invitation = invitation_store.create_pending_invitation(
        meta,
        "ws-removed-invite",
        email="invited@contoso.com",
        role="editor",
        invited_by=_actor("owner-oid"),
    )
    invitation_store.transition_invitation(meta, invitation["invitation_id"], "accepted", identity=actor)
    invitation_store.consume_accepted_invitation(meta, "ws-removed-invite", actor)
    meta["workspace_members"] = [
        {
            "actor_id": "invited-oid",
            "tenant_id": "tenant-a",
            "role": "editor",
            "status": "removed",
            "invitation_id": invitation["invitation_id"],
        }
    ]
    monkeypatch.setenv("DF_WORKSPACE_RBAC_ENFORCED", "1")
    monkeypatch.setattr(workspace_authz, "_load_workspace_meta", lambda _id: meta)

    decision = workspace_authz.workspace_access_decision("ws-removed-invite", actor)

    assert (decision.allowed, decision.role, decision.reason_code) == (False, None, "membership_missing")


@pytest.mark.parametrize(
    ("actor", "action", "expected"),
    [
        ({}, "workspace.read", (False, None, "identity_missing")),
        (_actor("viewer-oid"), "workspace.read", (True, "viewer", "member_match")),
        (_actor("removed-oid"), "workspace.read", (False, None, "membership_missing")),
        (_actor("viewer-oid"), "file.edit", (False, "viewer", "role_denied")),
    ],
)
def test_access_decision_is_bounded(monkeypatch: pytest.MonkeyPatch, actor, action, expected) -> None:
    monkeypatch.setenv("DF_WORKSPACE_RBAC_ENFORCED", "1")
    monkeypatch.setattr(workspace_authz, "_load_workspace_meta", lambda _id: {
        "workspace_owner": {"actor_id": "owner-oid", "tenant_id": "tenant-a"},
        "workspace_members": [
            {"actor_id": "viewer-oid", "tenant_id": "tenant-a", "role": "viewer", "status": "active"},
            {"actor_id": "removed-oid", "tenant_id": "tenant-a", "role": "editor", "status": "removed"},
        ],
    })

    decision = workspace_authz.workspace_access_decision("ws-access", actor)
    checked = workspace_authz.with_action(decision, action)

    assert (checked.allowed, checked.role, checked.reason_code) == expected


def test_role_capabilities_are_enforced_by_action() -> None:
    assert workspace_authz.authorize("owner", "member.manage") is True
    assert workspace_authz.authorize("admin", "outcome.verify") is True
    assert workspace_authz.authorize("editor", "analysis.run") is True
    assert workspace_authz.authorize("editor", "file.edit") is True
    assert workspace_authz.authorize("editor", "member.manage") is False
    assert workspace_authz.authorize("viewer", "workspace.read") is True
    assert workspace_authz.authorize("viewer", "file.edit") is False
    assert workspace_authz.authorize(None, "workspace.read") is False


def test_rbac_enabled_rejects_default_owner_and_member_email_without_oid_and_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DF_WORKSPACE_RBAC_ENFORCED", "1")
    monkeypatch.setenv("DF_WORKSPACE_OWNER_EMAIL", "owner@contoso.com")
    monkeypatch.setattr(
        workspace_authz,
        "_load_workspace_meta",
        lambda _workspace_id: {
            "workspace_members": [{"email": "editor@contoso.com", "role": "editor", "status": "active"}],
        },
    )

    assert workspace_authz.workspace_role("ws-roles", {"email": "owner@contoso.com", "source": "easy_auth"}) is None
    assert workspace_authz.workspace_role("ws-roles", {"email": "editor@contoso.com", "source": "easy_auth"}) is None


def test_stored_member_role_requires_matching_tenant_scoped_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        workspace_authz,
        "_load_workspace_meta",
        lambda _workspace_id: {
            "workspace_members": [
                {
                    "email": "editor@contoso.com",
                    "actor_id": "oid-editor",
                    "tenant_id": "tenant-a",
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

    assert workspace_authz.workspace_role("ws-roles", _actor("oid-editor")) == "editor"
    assert workspace_authz.workspace_role("ws-roles", _actor("oid-editor", "tenant-b")) is None
    assert workspace_authz.workspace_role("ws-roles", {"email": "viewer@contoso.com"}) is None
    assert workspace_authz.workspace_role("ws-roles", {"email": "unknown@contoso.com"}) is None


def test_workspace_owner_row_without_role_is_owner_but_trusted_editor_is_not_chargeback_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        workspace_authz,
        "_load_workspace_meta",
        lambda _workspace_id: {
            "workspace_owner": {"actor_id": "OWNER-OID", "tenant_id": "Tenant-A"},
            "workspace_members": [{"actor_id": "editor-oid", "tenant_id": "tenant-a", "role": "editor", "status": "active"}],
        },
    )

    assert workspace_authz.workspace_role("ws-roles", {"actor_id": "owner-oid", "tenant_id": "tenant-a", "source": "easy_auth"}) == "owner"
    assert workspace_authz.workspace_role("ws-roles", {"actor_id": "EDITOR-OID", "tenant_id": "TENANT-A", "source": "easy_auth"}) == "editor"
    assert not workspace_authz.authorize("editor", "chargeback.read")


def test_active_workspace_role_requires_trusted_tenant_and_current_membership(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        workspace_authz,
        "_load_workspace_meta",
        lambda _workspace_id: {
            "workspace_owner": {"actor_id": "owner-oid", "tenant_id": "tenant-a"},
            "workspace_members": [
                {"actor_id": "viewer-oid", "tenant_id": "tenant-a", "role": "viewer", "status": "active"},
                {"actor_id": "former-oid", "tenant_id": "tenant-a", "role": "admin", "status": "removed"},
            ],
        },
    )

    assert workspace_authz.active_workspace_role("ws-roles", {"actor_id": "OWNER-OID", "tenant_id": "TENANT-A", "source": "easy_auth"}) == "owner"
    assert workspace_authz.active_workspace_role("ws-roles", {"actor_id": "viewer-oid", "tenant_id": "tenant-a", "source": "easy_auth"}) == "viewer"
    assert workspace_authz.active_workspace_role("ws-roles", {"actor_id": "viewer-oid", "source": "easy_auth"}) is None
    assert workspace_authz.active_workspace_role("ws-roles", {"actor_id": "former-oid", "tenant_id": "tenant-a", "source": "easy_auth"}) is None


def test_permission_gate_fails_closed_for_empty_actor_when_rbac_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DF_WORKSPACE_RBAC_ENFORCED", raising=False)

    with pytest.raises(workspace_authz.WorkspaceAuthorizationError) as error:
        workspace_authz.require_workspace_permission("ws-roles", {}, "file.edit")

    assert error.value.decision.reason_code == "identity_missing"


def test_sensitive_permission_gate_is_fail_closed_when_general_rbac_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DF_WORKSPACE_RBAC_ENFORCED", raising=False)
    monkeypatch.delenv("DF_SENSITIVE_AUTH_LOCAL_DEV_BYPASS", raising=False)
    monkeypatch.delenv("DF_ENVIRONMENT", raising=False)
    monkeypatch.setattr(
        workspace_authz,
        "_load_workspace_meta",
        lambda _workspace_id: {
            "workspace_owner": {"actor_id": "owner-oid", "tenant_id": "tenant-1"},
            "workspace_members": [
                {"actor_id": "admin-oid", "tenant_id": "tenant-1", "role": "admin", "status": "active"},
                {"actor_id": "viewer-oid", "tenant_id": "tenant-1", "role": "viewer", "status": "active"},
            ],
        },
    )

    for actor in (
        {},
        {"email": "untrusted@example.com", "source": "client_actor"},
        {"actor_id": "outsider-oid", "tenant_id": "tenant-1", "source": "easy_auth"},
    ):
        with pytest.raises(PermissionError, match="member.manage"):
            workspace_authz.require_sensitive_workspace_permission("ws-roles", actor, "member.manage")

    assert workspace_authz.require_sensitive_workspace_permission(
        "ws-roles",
        {"actor_id": "owner-oid", "tenant_id": "tenant-1", "source": "easy_auth"},
        "member.manage",
    ) == "owner"
    assert workspace_authz.require_sensitive_workspace_permission(
        "ws-roles",
        {"actor_id": "admin-oid", "tenant_id": "tenant-1", "source": "easy_auth"},
        "audit.read",
    ) == "admin"
    assert workspace_authz.require_sensitive_workspace_permission(
        "ws-roles",
        {"actor_id": "viewer-oid", "tenant_id": "tenant-1", "source": "easy_auth"},
        "run.read",
    ) == "viewer"


def test_sensitive_permission_denies_empty_actor_with_local_development_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DF_WORKSPACE_RBAC_ENFORCED", raising=False)
    monkeypatch.setenv("DF_SENSITIVE_AUTH_LOCAL_DEV_BYPASS", "1")
    monkeypatch.setenv("DF_ENVIRONMENT", "development")

    with pytest.raises(workspace_authz.WorkspaceAuthorizationError) as error:
        workspace_authz.require_sensitive_workspace_permission("ws-local", {}, "outcome.read")

    assert error.value.decision.reason_code == "identity_missing"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/workspaces/ws-sensitive/settings"),
        ("GET", "/api/workspaces/ws-sensitive/members"),
        ("GET", "/api/workspaces/ws-sensitive/members/entra-users?query=user"),
        ("POST", "/api/workspaces/ws-sensitive/members/invite"),
        ("POST", "/api/workspaces/ws-sensitive/members/entra-invite"),
        ("PATCH", "/api/workspaces/ws-sensitive/members/member_0123456789abcdef0123456789abcdef01234567"),
        ("DELETE", "/api/workspaces/ws-sensitive/members/member_0123456789abcdef0123456789abcdef01234567"),
        ("GET", "/api/workspaces/ws-sensitive/usage-summary"),
        ("GET", "/api/workspaces/ws-sensitive/audit-events"),
        ("GET", "/api/workspaces/ws-sensitive/governance/audit-events"),
        ("GET", "/api/workspaces/ws-sensitive/governance/invitations"),
        ("GET", "/api/workspaces/ws-sensitive/governance-summary"),
        ("GET", "/api/workspaces/ws-sensitive/governance/roi?from=2026-07-10T00:00:00Z&to=2026-07-11T00:00:00Z"),
        ("GET", "/api/workspaces/ws-sensitive/governance/chargeback?from=2026-07-10T00:00:00Z&to=2026-07-11T00:00:00Z"),
        ("GET", "/api/workspaces/ws-sensitive/governance/trace-status"),
        ("GET", "/api/workspaces/ws-sensitive/outcomes"),
        ("POST", "/api/workspaces/ws-sensitive/outcomes"),
        ("POST", "/api/workspaces/ws-sensitive/outcomes/outcome-1/verify"),
    ],
)
def test_sensitive_routes_deny_unauthenticated_and_nonmember_when_rbac_env_is_unset(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
) -> None:
    monkeypatch.delenv("DF_WORKSPACE_RBAC_ENFORCED", raising=False)
    monkeypatch.delenv("DF_SENSITIVE_AUTH_LOCAL_DEV_BYPASS", raising=False)
    monkeypatch.setenv("DF_WEB_PROXY_SECRET", "server-only-secret")
    monkeypatch.setattr(
        workspace_authz,
        "_load_workspace_meta",
        lambda _workspace_id: {"workspace_owner": {"actor_id": "owner-oid", "tenant_id": "tenant-1"}},
    )
    client = TestClient(app)

    for headers in ({}, _trusted_easy_auth_headers("outsider@contoso.com", actor_id="outsider-oid")):
        response = client.request(method, path, headers=headers, json={} if method in {"POST", "PATCH"} else None)
        assert response.status_code == 403, (method, path, response.text)


def test_sensitive_routes_allow_persisted_owner_and_admin_when_rbac_env_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    control_module = importlib.import_module("backend.control_plane")
    monkeypatch.delenv("DF_WORKSPACE_RBAC_ENFORCED", raising=False)
    monkeypatch.delenv("DF_SENSITIVE_AUTH_LOCAL_DEV_BYPASS", raising=False)
    monkeypatch.setenv("DF_WEB_PROXY_SECRET", "server-only-secret")
    monkeypatch.setattr(
        workspace_authz,
        "_load_workspace_meta",
        lambda _workspace_id: {
            "workspace_owner": {"actor_id": "owner-oid", "tenant_id": "tenant-1"},
            "workspace_members": [
                {"actor_id": "admin-oid", "tenant_id": "tenant-1", "role": "admin", "status": "active"},
            ],
        },
    )
    monkeypatch.setattr(control_module, "workspace_member_roles", lambda workspace_id, _request=None: {"workspace_id": workspace_id, "members": []})
    monkeypatch.setattr(control_module, "workspace_experiment_ledger", lambda workspace_id: {"workspace_id": workspace_id, "versions": [], "count": 0})
    client = TestClient(app)

    owner_response = client.get(
        "/api/workspaces/ws-sensitive/members",
        headers=_trusted_easy_auth_headers("owner@contoso.com", actor_id="owner-oid"),
    )
    admin_response = client.get(
        "/api/workspaces/ws-sensitive/experiments",
        headers=_trusted_easy_auth_headers("admin@contoso.com", actor_id="admin-oid"),
    )

    assert owner_response.status_code == 200
    assert admin_response.status_code == 200


def test_experiment_ledger_uses_the_normal_run_read_policy_when_rbac_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    control_module = importlib.import_module("backend.control_plane")
    monkeypatch.delenv("DF_WORKSPACE_RBAC_ENFORCED", raising=False)
    monkeypatch.setenv("DF_WEB_PROXY_SECRET", "server-only-secret")
    monkeypatch.setattr(
        workspace_authz,
        "_load_workspace_meta",
        lambda _workspace_id: {
            "workspace_members": [
                {"actor_id": "viewer-oid", "tenant_id": "tenant-1", "role": "viewer", "status": "active"},
            ],
        },
    )
    monkeypatch.setattr(
        control_module,
        "workspace_experiment_ledger",
        lambda workspace_id: {"workspace_id": workspace_id, "versions": [], "count": 0},
    )
    client = TestClient(app)

    response = client.get(
        "/api/workspaces/ws-experiment/experiments",
        headers=_trusted_easy_auth_headers("viewer@contoso.com", actor_id="viewer-oid"),
    )

    assert response.status_code == 200
    assert response.json()["workspace_id"] == "ws-experiment"


def test_permission_gate_returns_role_denied_for_active_viewer_when_rbac_is_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DF_WORKSPACE_RBAC_ENFORCED", "false")
    monkeypatch.setattr(
        workspace_authz,
        "_load_workspace_meta",
        lambda _workspace_id: {
            "workspace_members": [
                {"actor_id": "viewer-oid", "tenant_id": "tenant-a", "role": "viewer", "status": "active"},
            ],
        },
    )

    with pytest.raises(workspace_authz.WorkspaceAuthorizationError) as error:
        workspace_authz.require_workspace_permission(
            "ws-roles",
            _actor("viewer-oid"),
            "file.edit",
        )

    assert (error.value.decision.role, error.value.decision.reason_code) == ("viewer", "role_denied")


def test_member_management_requires_owner_or_admin_when_rbac_is_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DF_WORKSPACE_RBAC_ENFORCED", "1")
    monkeypatch.setattr(
        workspace_authz,
        "_load_workspace_meta",
        lambda _workspace_id: {
            "workspace_owner": {"actor_id": "owner-oid", "tenant_id": "tenant-1"},
            "workspace_members": [{"actor_id": "editor-oid", "tenant_id": "tenant-1", "role": "editor", "status": "active"}],
        },
    )

    assert workspace_authz.require_workspace_permission(
        "ws-roles",
        {"actor_id": "owner-oid", "tenant_id": "tenant-1", "source": "easy_auth"},
        "member.manage",
    ) == "owner"
    with pytest.raises(PermissionError, match="member.manage"):
        workspace_authz.require_workspace_permission(
            "ws-roles",
            {"actor_id": "editor-oid", "tenant_id": "tenant-1", "source": "easy_auth"},
            "member.manage",
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


def test_pending_invitation_does_not_grant_access_even_when_email_matches(monkeypatch: pytest.MonkeyPatch) -> None:
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

    assert role is None
    assert saved == []


def test_accepted_invitation_activates_only_the_matching_trusted_oid_and_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    saved: list[dict] = []
    meta = {"workspace_members": []}
    pending = invitation_store.create_pending_invitation(
        meta,
        "ws-roles",
        email="invited@contoso.com",
        role="editor",
        invited_by={"actor_id": "owner-oid", "tenant_id": "tenant-1", "source": "easy_auth"},
    )
    invitation_store.transition_invitation(
        meta,
        pending["invitation_id"],
        "accepted",
        identity={"actor_id": "oid-invited", "tenant_id": "tenant-1", "source": "easy_auth"},
    )
    meta["workspace_members"].append(
        {"email": "invited@contoso.com", "role": "editor", "status": "pending", "invitation_id": pending["invitation_id"]}
    )
    monkeypatch.setattr(workspace_authz, "_load_workspace_meta", lambda _workspace_id: meta)
    monkeypatch.setattr(workspace_authz, "_save_workspace_meta", lambda _workspace_id, value: saved.append(value), raising=False)

    assert workspace_authz.workspace_role(
        "ws-roles",
        {"email": "invited@contoso.com", "actor_id": "wrong-oid", "tenant_id": "tenant-1", "source": "easy_auth"},
    ) is None
    assert workspace_authz.workspace_role(
        "ws-roles",
        {"email": "invited@contoso.com", "actor_id": "oid-invited", "tenant_id": "wrong-tenant", "source": "easy_auth"},
    ) is None
    assert workspace_authz.workspace_role(
        "ws-roles",
        {"email": "invited@contoso.com", "actor_id": "oid-invited", "tenant_id": "tenant-1", "source": "easy_auth"},
    ) == "editor"
    assert saved[-1]["workspace_members"][0]["status"] == "active"
    assert saved[-1]["workspace_members"][0]["actor_id"] == "oid-invited"


def test_accepted_bootstrap_is_consumed_once_and_cannot_overwrite_a_later_role_update(monkeypatch: pytest.MonkeyPatch) -> None:
    meta = {"workspace_members": []}
    pending = invitation_store.create_pending_invitation(
        meta,
        "ws-roles",
        email="invited@contoso.com",
        role="admin",
        invited_by={"actor_id": "owner-oid", "tenant_id": "tenant-1", "source": "easy_auth"},
    )
    invitation_store.transition_invitation(
        meta,
        pending["invitation_id"],
        "accepted",
        identity={"actor_id": "oid-invited", "tenant_id": "tenant-1", "source": "easy_auth"},
    )
    meta["workspace_members"].append(
        {"email": "invited@contoso.com", "role": "admin", "status": "pending", "invitation_id": pending["invitation_id"]}
    )
    saves = []
    monkeypatch.setattr(workspace_authz, "_load_workspace_meta", lambda _workspace_id: meta)
    monkeypatch.setattr(workspace_authz, "_save_workspace_meta", lambda _workspace_id, value: saves.append(value), raising=False)
    actor = {"actor_id": "oid-invited", "tenant_id": "tenant-1", "source": "easy_auth"}

    assert workspace_authz.workspace_role("ws-roles", actor) == "admin"
    invitation_store.update_invited_member_role(meta, "ws-roles", email="invited@contoso.com", role="viewer")

    assert workspace_authz.workspace_role("ws-roles", actor) == "viewer"
    assert len([event for event in meta["workspace_invitation_events"] if event.get("event_type") == "activation"]) == 1
    invitation_store.revoke_effective_invitations(meta, "ws-roles", email="invited@contoso.com")
    meta["workspace_members"] = []
    assert workspace_authz.workspace_role("ws-roles", actor) is None


def test_journal_role_is_authoritative_over_stale_metadata_and_interleaved_removal(monkeypatch: pytest.MonkeyPatch) -> None:
    meta = {"workspace_members": [{"actor_id": "oid", "tenant_id": "tenant", "role": "admin", "status": "active", "invitation_id": "stale"}]}
    pending = invitation_store.create_pending_invitation(meta, "ws-roles", email="alias@contoso.com", role="editor", invited_by={"actor_id": "owner", "tenant_id": "tenant", "source": "easy_auth"})
    invitation_store.transition_invitation(meta, pending["invitation_id"], "accepted", identity={"actor_id": "oid", "tenant_id": "tenant", "source": "easy_auth"})
    actor = {"actor_id": "oid", "tenant_id": "tenant", "source": "easy_auth"}
    monkeypatch.setattr(workspace_authz, "_load_workspace_meta", lambda _workspace_id: meta)
    monkeypatch.setattr(workspace_authz, "_save_workspace_meta", lambda *_args: None)

    assert workspace_authz.workspace_role("ws-roles", actor) == "editor"
    invitation_store.update_invited_member_role(meta, "ws-roles", email="alias@contoso.com", role="viewer")
    assert workspace_authz.workspace_role("ws-roles", actor) == "viewer"
    invitation_store.revoke_effective_invitations(meta, "ws-roles", email="alias@contoso.com")
    assert workspace_authz.workspace_role("ws-roles", actor) is None


@pytest.mark.parametrize("metadata_role", ["admin", "owner"])
def test_activation_uses_viewer_role_from_journal_not_stale_metadata(monkeypatch: pytest.MonkeyPatch, metadata_role: str) -> None:
    actor = {"actor_id": "oid", "tenant_id": "tenant", "source": "easy_auth"}
    meta = {"workspace_members": []}
    pending = invitation_store.create_pending_invitation(meta, "ws", email="user@contoso.com", role="admin", invited_by={"actor_id": "owner", "tenant_id": "tenant", "source": "easy_auth"})
    assert invitation_store.update_invited_member_role(meta, "ws", email="user@contoso.com", role="viewer") is True
    invitation_store.transition_invitation(meta, pending["invitation_id"], "accepted", identity=actor)
    meta["workspace_members"] = [{"email": "user@contoso.com", "role": metadata_role, "status": "pending", "invitation_id": pending["invitation_id"]}]
    monkeypatch.setattr(workspace_authz, "_load_workspace_meta", lambda _workspace_id: meta)
    monkeypatch.setattr(workspace_authz, "_save_workspace_meta", lambda *_args: None)

    assert workspace_authz.workspace_role("ws", actor) == "viewer"


def test_role_changes_before_and_after_accept_are_journal_authoritative(monkeypatch: pytest.MonkeyPatch) -> None:
    actor = {"actor_id": "oid", "tenant_id": "tenant", "source": "easy_auth"}
    for before_accept in (True, False):
        meta = {}
        pending = invitation_store.create_pending_invitation(meta, "ws", email="user@contoso.com", role="admin", invited_by={"actor_id": "owner", "tenant_id": "tenant", "source": "easy_auth"})
        if before_accept:
            assert invitation_store.update_invited_member_role(meta, "ws", email="user@contoso.com", role="viewer") is True
        invitation_store.transition_invitation(meta, pending["invitation_id"], "accepted", identity=actor)
        if not before_accept:
            assert invitation_store.update_invited_member_role(meta, "ws", email="user@contoso.com", role="viewer") is True
        monkeypatch.setattr(workspace_authz, "_load_workspace_meta", lambda _workspace_id, value=meta: value)
        monkeypatch.setattr(workspace_authz, "_save_workspace_meta", lambda *_args: None)
        assert workspace_authz.workspace_role("ws", actor) == "viewer"


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


def test_trusted_web_proxy_easy_auth_principal_matches_persisted_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DF_WORKSPACE_RBAC_ENFORCED", "1")
    monkeypatch.setenv("DF_WEB_PROXY_SECRET", "server-only-secret")
    monkeypatch.setattr(
        workspace_authz,
        "_load_workspace_meta",
        lambda _workspace_id: {
            "workspace_owner": {"actor_id": "oid-owner", "tenant_id": "tenant-1"},
            "workspace_members": [{"actor_id": "oid-owner", "tenant_id": "tenant-1", "role": "owner", "status": "active"}],
        },
    )
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


def test_workspace_list_does_not_enumerate_anonymous_callers_when_rbac_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    app_module = importlib.import_module("backend.app")
    monkeypatch.delenv("DF_WORKSPACE_RBAC_ENFORCED", raising=False)
    monkeypatch.setenv("DF_WEB_PROXY_SECRET", "server-only-secret")
    monkeypatch.setattr(
        app_module,
        "list_workspaces",
        lambda: [
            {"workspace_id": "ws-private-a", "name": "Private A", "doc_count": 1},
            {"workspace_id": "ws-private-b", "name": "Private B", "doc_count": 1},
        ],
    )

    response = TestClient(app).get("/api/workspaces")

    assert response.status_code == 200
    assert response.json()["workspaces"] == []


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


def test_artifact_download_denies_anonymous_callers_when_rbac_is_unset(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    app_module = importlib.import_module("backend.app")
    monkeypatch.delenv("DF_WORKSPACE_RBAC_ENFORCED", raising=False)
    monkeypatch.setenv("DF_WEB_PROXY_SECRET", "server-only-secret")
    (tmp_path / "private-plan.pdf").write_bytes(b"private artifact")
    monkeypatch.setattr(app_module, "ARTIFACT_DIR", tmp_path)
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
    monkeypatch.setattr(workspace_authz, "_load_workspace_meta", lambda _workspace_id: {})

    response = TestClient(app).get("/api/artifacts/private-plan.pdf")

    assert response.status_code == 403
    assert b"private artifact" not in response.content
