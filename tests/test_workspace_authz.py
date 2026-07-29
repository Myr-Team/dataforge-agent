from __future__ import annotations

import backend.workspace_authz as workspace_authz


def _actor() -> dict[str, object]:
    return {
        "actor_id": "actor-a",
        "tenant_id": "tenant-a",
        "source": "easy_auth",
        "group_refs": ["group-safe"],
        "group_resolution_state": "observed",
    }


def test_explicit_membership_precedes_group_mapping(monkeypatch) -> None:
    monkeypatch.setattr(
        workspace_authz,
        "_load_workspace_meta",
        lambda _workspace_id: {
            "workspace_owner": {
                "actor_id": "owner-a",
                "tenant_id": "tenant-a",
            },
            "workspace_members": [
                {
                    "actor_id": "actor-a",
                    "tenant_id": "tenant-a",
                    "role": "viewer",
                    "status": "active",
                }
            ],
        },
    )
    monkeypatch.setattr(
        workspace_authz,
        "resolve_actor_group_role",
        lambda *_args: ("admin", "group_match"),
    )

    decision = workspace_authz.workspace_access_decision("ws-a", _actor())

    assert decision.role == "viewer"
    assert decision.reason_code == "member_match"


def test_group_mapping_is_used_only_after_explicit_membership(monkeypatch) -> None:
    monkeypatch.setattr(
        workspace_authz,
        "_load_workspace_meta",
        lambda _workspace_id: {
            "workspace_owner": {
                "actor_id": "owner-a",
                "tenant_id": "tenant-a",
            },
            "workspace_members": [],
        },
    )
    monkeypatch.setattr(
        workspace_authz,
        "resolve_actor_group_role",
        lambda *_args: ("editor", "group_match"),
    )

    decision = workspace_authz.workspace_access_decision("ws-a", _actor())

    assert decision.allowed is True
    assert decision.role == "editor"
    assert decision.reason_code == "group_match"


def test_group_resolution_failure_is_fail_closed(monkeypatch) -> None:
    monkeypatch.setattr(
        workspace_authz,
        "_load_workspace_meta",
        lambda _workspace_id: {
            "workspace_owner": {
                "actor_id": "owner-a",
                "tenant_id": "tenant-a",
            },
            "workspace_members": [],
        },
    )
    monkeypatch.setattr(
        workspace_authz,
        "resolve_actor_group_role",
        lambda *_args: (None, "group_resolution_unavailable"),
    )

    decision = workspace_authz.workspace_access_decision("ws-a", _actor())

    assert decision.allowed is False
    assert decision.role is None
    assert decision.reason_code == "group_resolution_unavailable"
