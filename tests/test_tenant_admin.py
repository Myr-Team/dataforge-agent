from __future__ import annotations

from backend.tenant_admin import tenant_admin_capability


def _actor(*, actor_id: str = "owner-a", roles: tuple[str, ...] = ()) -> dict[str, object]:
    return {
        "actor_id": actor_id,
        "tenant_id": "tenant-a",
        "source": "easy_auth",
        "roles": list(roles),
    }


def test_configured_tenant_owner_is_admin_even_with_mixed_workspace_roles(monkeypatch) -> None:
    monkeypatch.setenv("DF_FINOPS_TENANT_OWNER_OIDS", "other-owner, OWNER-A")

    capability = tenant_admin_capability(
        _actor(),
        workspace_roles={"ws-owned": "owner", "ws-viewed": "viewer"},
    )

    assert capability.allowed is True
    assert capability.source == "configured_tenant_owner"


def test_configurable_entra_app_role_grants_tenant_admin(monkeypatch) -> None:
    monkeypatch.setenv("DF_FINOPS_TENANT_ADMIN_ROLE", "Contoso.FinOpsOwner")

    capability = tenant_admin_capability(
        _actor(roles=("Contoso.FinOpsOwner",)),
        workspace_roles={"ws-viewed": "viewer"},
    )

    assert capability.allowed is True
    assert capability.source == "entra_app_role"


def test_all_workspace_owner_compatibility_is_explicit(monkeypatch) -> None:
    monkeypatch.delenv("DF_FINOPS_TENANT_OWNER_OIDS", raising=False)
    monkeypatch.delenv("DF_FINOPS_TENANT_ADMIN_ROLE", raising=False)

    denied = tenant_admin_capability(
        _actor(),
        workspace_roles={"ws-a": "owner"},
    )
    allowed = tenant_admin_capability(
        _actor(),
        workspace_roles={"ws-a": "owner"},
        allow_all_workspace_owner=True,
    )

    assert denied.allowed is False
    assert allowed.allowed is True
    assert allowed.source == "all_workspaces_owner"


def test_untrusted_identity_never_receives_tenant_admin(monkeypatch) -> None:
    monkeypatch.setenv("DF_FINOPS_TENANT_OWNER_OIDS", "owner-a")
    actor = _actor()
    actor["source"] = "ui_context"

    assert tenant_admin_capability(actor).allowed is False
