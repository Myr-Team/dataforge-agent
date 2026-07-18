import json
import copy
import re
from concurrent.futures import ThreadPoolExecutor

import backend.control_plane as control_plane
import backend.graph_client as graph_client
import backend.invitation_store as invitation_store
from backend.app import app
from fastapi.testclient import TestClient
import pytest
from auth_fixtures import active_member, install_workspace_memberships, trusted_headers


class RequestStub:
    def __init__(self, headers=None):
        self.headers = headers or trusted_headers(actor_id="owner-oid", tenant_id="tenant-1")


def _workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("DF_INVITATION_PSEUDONYM_SALT", "test-member-projection-salt")
    monkeypatch.setenv("DF_ENVIRONMENT", "test")
    install_workspace_memberships(
        monkeypatch,
        {"ws-graph": [active_member("owner-oid", "tenant-1", "owner")]},
    )
    workspace_root = tmp_path / "workspaces"
    workspace_dir = workspace_root / "ws-graph"
    workspace_dir.mkdir(parents=True)
    workspace_path = workspace_dir / "workspace.json"
    workspace_path.write_text(json.dumps({"workspace_id": "ws-graph", "name": "Graph Members"}), encoding="utf-8")
    monkeypatch.setattr(control_plane, "WORKSPACES", workspace_root, raising=False)
    monkeypatch.setattr(control_plane, "download_blob_json", lambda *args, **kwargs: {}, raising=False)
    monkeypatch.setattr(control_plane, "upload_blob_json", lambda *args, **kwargs: None, raising=False)
    monkeypatch.setattr(control_plane, "list_runs", lambda workspace_id=None: [], raising=False)
    return workspace_path


def test_entra_user_search_missing_token_returns_controlled_status(monkeypatch):
    monkeypatch.setattr(graph_client, "graph_token_from_request", lambda request=None: "")

    result = graph_client.search_entra_users("fuzihao", RequestStub(), limit=5)

    assert result["connected"] is False
    assert result["users"] == []
    assert result["error"]["code"] == "graph_token_missing"


def test_entra_user_search_normalizes_graph_users(monkeypatch):
    monkeypatch.setattr(graph_client, "graph_token_from_request", lambda request=None: "token")

    def fake_request(method, path, token, *, params=None, json_payload=None, headers=None):
        assert method == "GET"
        assert path == "/users"
        assert token == "token"
        return {
            "value": [
                {
                    "id": "oid-1",
                    "displayName": "Fu Zihao",
                    "mail": None,
                    "userPrincipalName": "fuzihao@gdjiuyun.onmicrosoft.com",
                    "userType": "Member",
                }
            ]
        }

    monkeypatch.setattr(graph_client, "graph_request", fake_request)

    result = graph_client.search_entra_users("fu", RequestStub(), limit=5)

    assert result["connected"] is True
    assert result["users"] == [
        {
            "id": "oid-1",
            "display_name": "Fu Zihao",
            "email": "fuzihao@gdjiuyun.onmicrosoft.com",
            "user_principal_name": "fuzihao@gdjiuyun.onmicrosoft.com",
            "user_type": "Member",
            "source": "microsoft_graph",
        }
    ]


def test_directory_endpoint_requires_member_manage_and_returns_only_safe_selection_refs(monkeypatch):
    monkeypatch.setenv("DF_MEMBER_PSEUDONYM_SALT", "directory-selection-salt")
    raw_user = {
        "id": "directory-private-oid",
        "display_name": "Directory Private Name",
        "email": "directory.private@example.com",
        "user_principal_name": "directory.private@example.com",
        "user_type": "Member",
        "source": "microsoft_graph",
    }
    reads = []
    actions = []
    monkeypatch.setattr(control_plane, "search_entra_users", lambda *_args, **_kwargs: reads.append(True) or {"connected": True, "source": "microsoft_graph", "users": [raw_user]})

    def deny(_workspace_id, _actor, action, **_kwargs):
        actions.append(action)
        raise PermissionError(f"workspace permission denied for {action}")

    monkeypatch.setattr(control_plane, "require_sensitive_workspace_permission", deny)
    client = TestClient(app)
    denied = client.get("/api/workspaces/ws-graph/members/entra-users?query=directory.private@example.com")
    assert denied.status_code == 403
    assert actions == ["member.manage"]
    assert reads == []

    monkeypatch.setattr(control_plane, "require_sensitive_workspace_permission", lambda _workspace_id, _actor, action, **_kwargs: actions.append(action) or "owner")
    allowed = client.get("/api/workspaces/ws-graph/members/entra-users?query=directory.private@example.com")
    assert allowed.status_code == 200, allowed.text
    assert actions[-1] == "member.manage"
    user = allowed.json()["users"][0]
    assert set(user) == {"selection_ref", "subject_label"}
    assert re.fullmatch(r"selection_[0-9a-f]{40}", user["selection_ref"])
    assert re.fullmatch(r"member_[0-9a-f]{40}", user["subject_label"])
    serialized = json.dumps(allowed.json())
    for raw in ("directory.private@example.com", "directory-private-oid", "Directory Private Name", "Member"):
        assert raw not in serialized


def test_directory_selection_invite_and_direct_invite_responses_never_echo_identity(tmp_path, monkeypatch):
    _workspace(tmp_path, monkeypatch)
    monkeypatch.setenv("DF_MEMBER_PSEUDONYM_SALT", "directory-selection-salt")
    owner = {"actor_id": "owner-oid", "tenant_id": "tenant-1", "email": "owner@contoso.com", "source": "easy_auth"}
    monkeypatch.setattr(control_plane, "actor_from_request", lambda *_args, **_kwargs: owner)
    monkeypatch.setattr(control_plane, "search_entra_users", lambda *_args, **_kwargs: {
        "connected": True,
        "source": "microsoft_graph",
        "users": [{
            "id": "selected-private-oid",
            "display_name": "Selected Private Name",
            "email": "selected.private@example.com",
            "user_principal_name": "selected.private@example.com",
            "user_type": "Member",
            "source": "microsoft_graph",
        }],
    })
    captured = []
    monkeypatch.setattr(control_plane, "send_graph_invitation", lambda email, *_args, **kwargs: captured.append((email, kwargs)) or {
        "status": "sent",
        "source": "microsoft_graph",
        "invitation_id": "provider-private-invitation",
        "invited_user_id": "selected-private-oid",
        "resource_tenant_id": "tenant-1",
        "email": email,
    })

    directory = control_plane.workspace_entra_users("ws-graph", RequestStub(), "selected", 8)
    selection_ref = directory["users"][0]["selection_ref"]
    graph_result = control_plane.invite_entra_workspace_member(
        "ws-graph",
        {"selection_ref": selection_ref, "role": "editor", "send_email": True},
        RequestStub(),
    )
    direct_result = control_plane.invite_workspace_member(
        "ws-graph",
        {"email": "direct.private@example.com", "name": "Direct Private Name", "role": "viewer"},
        RequestStub(),
    )

    assert captured[0][0] == "selected.private@example.com"
    assert graph_result["invited_member"]["subject_label"] == graph_result["invitation"]["subject_label"]
    for result in (graph_result, direct_result):
        invitation = result["invitation"]
        assert set(invitation).issuperset({"invitation_ref", "subject_label", "role", "state", "updated_at"})
        assert re.fullmatch(r"invite_[0-9a-f]{40}", invitation["invitation_ref"])
        assert re.fullmatch(r"member_[0-9a-f]{40}", invitation["subject_label"])
        serialized = json.dumps(result)
        for raw in (
            "selected.private@example.com", "selected-private-oid", "Selected Private Name",
            "direct.private@example.com", "Direct Private Name", "tenant-1",
            "provider-private-invitation", "owner@contoso.com", "owner-oid",
        ):
            assert raw not in serialized


def test_directory_selection_workspace_mismatch_does_not_consume_reference(monkeypatch):
    monkeypatch.setenv("DF_MEMBER_PSEUDONYM_SALT", "directory-selection-salt")
    actor = {"actor_id": "owner-oid", "tenant_id": "tenant-1", "source": "easy_auth"}
    monkeypatch.setattr(control_plane, "actor_from_request", lambda *_args, **_kwargs: dict(actor))
    monkeypatch.setattr(control_plane, "search_entra_users", lambda *_args, **_kwargs: {
        "connected": True,
        "users": [{
            "id": "selected-private-oid",
            "display_name": "Selected Private Name",
            "email": "selected.private@example.com",
        }],
    })
    directory = control_plane.workspace_entra_users("workspace-a", RequestStub(), "selected", 8)
    selection_ref = directory["users"][0]["selection_ref"]

    with pytest.raises(ValueError, match="unavailable or expired"):
        control_plane._consume_directory_selection("workspace-b", RequestStub(), selection_ref)

    assert selection_ref in control_plane._DIRECTORY_SELECTIONS
    actor["actor_id"] = "different-owner-oid"
    with pytest.raises(ValueError, match="unavailable or expired"):
        control_plane._consume_directory_selection("workspace-a", RequestStub(), selection_ref)
    assert selection_ref in control_plane._DIRECTORY_SELECTIONS

    actor["actor_id"] = "owner-oid"
    monkeypatch.setattr(
        control_plane,
        "require_sensitive_workspace_permission",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("workspace permission denied for member.manage")),
    )
    with pytest.raises(control_plane.HTTPException) as denied:
        control_plane.invite_entra_workspace_member(
            "workspace-a",
            {"selection_ref": selection_ref, "role": "viewer", "send_email": False},
            RequestStub(),
        )
    assert denied.value.status_code == 403
    assert selection_ref in control_plane._DIRECTORY_SELECTIONS

    selected = control_plane._consume_directory_selection("workspace-a", RequestStub(), selection_ref)
    assert selected["workspace_id"] == "workspace-a"
    assert selection_ref not in control_plane._DIRECTORY_SELECTIONS


def test_directory_search_permission_denied_is_specific_but_exact_email_invite_still_works(monkeypatch):
    monkeypatch.setattr(graph_client, "graph_token_from_request", lambda request=None: "token")

    def search_denied(method, path, token, *, params=None, json_payload=None, headers=None):
        if method == "GET":
            raise graph_client.GraphClientError("graph_permission_denied", "missing permissions", status=403)
        return {"id": "provider-invite-1", "invitedUser": {"id": "provider-user-1"}}

    monkeypatch.setattr(graph_client, "graph_request", search_denied)

    result = graph_client.search_entra_users("reviewer", RequestStub())
    invite = graph_client.send_graph_invitation("reviewer@contoso.com", "https://example.test", RequestStub())

    assert result["connected"] is False
    assert result["error"]["code"] == "graph_directory_search_permission_denied"
    assert "User.ReadBasic.All" in result["error"]["message"]
    assert invite == {
        "status": "sent",
        "source": "microsoft_graph",
        "invitation_id": "provider-invite-1",
        "invited_user_id": "provider-user-1",
        "email": "reviewer@contoso.com",
    }


def test_invitation_events_are_append_only_idempotent_and_reject_illegal_transitions():
    meta = {}
    pending = invitation_store.create_pending_invitation(
        meta,
        "ws-graph",
        email="reviewer@contoso.com",
        role="editor",
        invited_by={"actor_id": "owner-oid", "tenant_id": "tenant-1", "source": "easy_auth"},
    )
    accepted = invitation_store.transition_invitation(
        meta,
        pending["invitation_id"],
        "accepted",
        identity={"actor_id": "reviewer-oid", "tenant_id": "tenant-1", "source": "easy_auth"},
    )
    retried = invitation_store.transition_invitation(
        meta,
        pending["invitation_id"],
        "accepted",
        identity={"actor_id": "reviewer-oid", "tenant_id": "tenant-1", "source": "easy_auth"},
    )

    assert accepted == retried
    assert [event["state"] for event in meta["workspace_invitation_events"]] == ["pending", "accepted"]
    with pytest.raises(invitation_store.InvitationTransitionError, match="cannot transition"):
        invitation_store.transition_invitation(meta, pending["invitation_id"], "expired")


def test_concurrent_pending_invitation_retries_append_one_event():
    meta = {}

    def create():
        return invitation_store.create_pending_invitation(
            meta,
            "ws-graph",
            email="reviewer@contoso.com",
            role="viewer",
            invited_by={"actor_id": "owner-oid", "tenant_id": "tenant-1", "source": "easy_auth"},
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(lambda _index: create(), range(8)))

    assert {record["invitation_id"] for record in records} == {records[0]["invitation_id"]}
    assert len(meta["workspace_invitation_events"]) == 1


@pytest.mark.parametrize("role", ["owner", "operator", ""])
def test_invitation_store_rejects_owner_and_invalid_roles(role):
    with pytest.raises(invitation_store.InvitationTransitionError, match="role"):
        invitation_store.create_pending_invitation(
            {},
            "ws-graph",
            email="reviewer@contoso.com",
            role=role,
            invited_by={"actor_id": "owner-oid", "tenant_id": "tenant-1", "source": "easy_auth"},
        )


def test_provider_identity_acceptance_requires_graph_oid_and_tenant():
    meta = {}
    pending = invitation_store.create_pending_invitation(
        meta,
        "ws-graph",
        email="reviewer@contoso.com",
        role="viewer",
        invited_by={"actor_id": "owner-oid", "tenant_id": "tenant-1", "source": "easy_auth"},
    )

    assert invitation_store.accept_provider_invitation(
        meta,
        "ws-graph",
        pending["invitation_id"],
        {"source": "microsoft_graph", "status": "sent", "invited_user_id": "oid-reviewer"},
        inviter={"actor_id": "owner", "source": "easy_auth"},
    ) is None
    accepted = invitation_store.accept_provider_invitation(
        meta,
        "ws-graph",
        pending["invitation_id"],
        {
            "source": "microsoft_graph",
            "status": "sent",
            "invitation_id": "graph-invite-1",
            "invited_user_id": "oid-reviewer",
            "tenant_id": "tenant-1",
        },
        inviter={"actor_id": "owner", "tenant_id": "tenant-1", "source": "easy_auth"},
    )

    assert accepted["state"] == "accepted"
    assert accepted["accepted_identity"] == {"actor_id": "oid-reviewer", "tenant_id": "tenant-1"}


def test_durable_event_append_retries_after_stale_cas_and_preserves_remote_events(monkeypatch):
    meta = {}
    remote = {
        "revision": 1,
        "events": [
            {
                "event_id": "existing-event",
                "invitation_id": "existing-invitation",
                "state": "pending",
                "event_type": "state",
                "email": "other@contoso.com",
                "role": "viewer",
            }
        ],
    }
    reads = [None, remote]
    cas_calls = []

    monkeypatch.setattr(invitation_store, "blob_configured", lambda: True)
    monkeypatch.setattr(invitation_store, "download_blob_json", lambda _name: copy.deepcopy(reads.pop(0)))

    def cas(_name, *, expected_revision, changes):
        cas_calls.append(expected_revision)
        if len(cas_calls) == 1:
            return None
        return copy.deepcopy(changes)

    monkeypatch.setattr(invitation_store, "compare_and_swap_blob_json", cas)

    created = invitation_store.create_pending_invitation(
        meta,
        "ws-graph",
        email="reviewer@contoso.com",
        role="viewer",
        invited_by={"actor_id": "owner-oid", "tenant_id": "tenant-1", "source": "easy_auth"},
    )

    assert cas_calls == [0, 1]
    assert {event["invitation_id"] for event in meta["workspace_invitation_events"]} == {
        "existing-invitation",
        created["invitation_id"],
    }


def test_durable_absent_journal_is_empty_for_workspace_without_invitations(monkeypatch):
    monkeypatch.setattr(invitation_store, "blob_configured", lambda: True)
    monkeypatch.setattr(invitation_store, "download_blob_json", lambda _name: None)

    actor = {"actor_id": "owner-oid", "tenant_id": "tenant-1", "source": "easy_auth"}

    assert invitation_store.current_invited_member_role({}, "ws-without-invites", actor) is None
    assert invitation_store.accepted_invitation_for_actor({}, actor, workspace_id="ws-without-invites") is None


def test_reinvite_revokes_every_effective_prior_invitation_before_creating_new_one():
    meta = {}
    first = invitation_store.create_pending_invitation(
        meta,
        "ws-graph",
        email="reviewer@contoso.com",
        role="editor",
        invited_by={"actor_id": "owner-oid", "tenant_id": "tenant-1", "source": "easy_auth"},
    )
    invitation_store.transition_invitation(
        meta,
        first["invitation_id"],
        "accepted",
        identity={"actor_id": "reviewer-oid", "tenant_id": "tenant-1", "source": "easy_auth"},
    )

    second = invitation_store.create_pending_invitation(
        meta,
        "ws-graph",
        email="reviewer@contoso.com",
        role="viewer",
        invited_by={"actor_id": "owner-oid", "tenant_id": "tenant-1", "source": "easy_auth"},
        reissue=True,
    )

    assert second["invitation_id"] != first["invitation_id"]
    assert invitation_store.effective_invitation_state(meta, first["invitation_id"]) == "revoked"
    assert invitation_store.effective_invitation_state(meta, second["invitation_id"]) == "pending"


def test_removal_fails_closed_when_effective_invitation_revocation_cannot_persist(tmp_path, monkeypatch):
    workspace_path = _workspace(tmp_path, monkeypatch)
    workspace_path.write_text(
        json.dumps(
            {
                "workspace_id": "ws-graph",
                "workspace_members": [{"email": "reviewer@contoso.com", "role": "editor", "status": "pending", "invitation_id": "invite-1"}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(control_plane, "revoke_effective_invitations", lambda *args, **kwargs: (_ for _ in ()).throw(invitation_store.InvitationPersistenceError("conflict")))
    subject_label = invitation_store.member_subject_label("ws-graph", "reviewer@contoso.com")

    with pytest.raises(invitation_store.InvitationPersistenceError, match="conflict"):
        control_plane.remove_workspace_member("ws-graph", subject_label, RequestStub())

    assert json.loads(workspace_path.read_text(encoding="utf-8"))["workspace_members"][0]["email"] == "reviewer@contoso.com"


def test_remove_after_reinvite_revokes_the_new_and_prior_effective_invitations(tmp_path, monkeypatch):
    workspace_path = _workspace(tmp_path, monkeypatch)
    meta = {}
    first = invitation_store.create_pending_invitation(
        meta,
        "ws-graph",
        email="reviewer@contoso.com",
        role="editor",
        invited_by={"actor_id": "owner-oid", "tenant_id": "tenant-1", "source": "easy_auth"},
    )
    second = invitation_store.create_pending_invitation(
        meta,
        "ws-graph",
        email="reviewer@contoso.com",
        role="viewer",
        invited_by={"actor_id": "owner-oid", "tenant_id": "tenant-1", "source": "easy_auth"},
        reissue=True,
    )
    workspace_path.write_text(
        json.dumps(
            {
                "workspace_id": "ws-graph",
                "workspace_members": [{"email": "reviewer@contoso.com", "role": "viewer", "status": "pending", "invitation_id": second["invitation_id"]}],
                "workspace_invitation_events": meta["workspace_invitation_events"],
            }
        ),
        encoding="utf-8",
    )

    subject_label = invitation_store.member_subject_label("ws-graph", "reviewer@contoso.com")
    control_plane.remove_workspace_member("ws-graph", subject_label, RequestStub())

    saved = json.loads(workspace_path.read_text(encoding="utf-8"))
    assert saved["workspace_members"] == []
    assert invitation_store.effective_invitation_state(saved, first["invitation_id"]) == "revoked"
    assert invitation_store.effective_invitation_state(saved, second["invitation_id"]) == "revoked"


def test_graph_invite_with_provider_oid_and_tenant_records_accepted_bootstrap(tmp_path, monkeypatch):
    workspace_path = _workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(
        control_plane,
        "send_graph_invitation",
        lambda *args, **kwargs: {
            "status": "sent",
            "source": "microsoft_graph",
            "invitation_id": "graph-1",
            "invited_user_id": "oid-reviewer",
            "tenant_id": "tenant-1",
        },
    )
    monkeypatch.setattr(control_plane, "actor_from_request", lambda _request, **_kwargs: {"actor_id": "owner-oid", "tenant_id": "tenant-1", "source": "easy_auth"})

    result = control_plane.invite_entra_workspace_member(
        "ws-graph",
        {"email": "reviewer@contoso.com", "role": "editor", "send_email": True},
        RequestStub(),
    )

    assert result["invitation"]["state"] == "accepted"
    assert [event["state"] for event in json.loads(workspace_path.read_text(encoding="utf-8"))["workspace_invitation_events"]] == ["pending", "accepted"]


def test_graph_invite_without_trusted_provider_tenant_remains_pending(tmp_path, monkeypatch):
    _workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(
        control_plane,
        "send_graph_invitation",
        lambda *args, **kwargs: {
            "status": "sent",
            "source": "microsoft_graph",
            "invitation_id": "graph-1",
            "invited_user_id": "oid-reviewer",
            "token_source": "app_only",
        },
    )

    result = control_plane.invite_entra_workspace_member(
        "ws-graph",
        {"email": "reviewer@contoso.com", "role": "editor", "send_email": True},
        RequestStub(),
    )

    assert result["invitation"]["state"] == "pending"


def test_graph_id_only_binds_to_trusted_inviter_tenant_and_rejects_missing_tenant(tmp_path, monkeypatch):
    workspace_path = _workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(control_plane, "send_graph_invitation", lambda *args, **kwargs: {"status": "sent", "source": "microsoft_graph", "invitation_id": "graph-1", "invited_user_id": "oid-reviewer"})
    trusted = RequestStub()
    monkeypatch.setattr(control_plane, "actor_from_request", lambda _request, **_kwargs: {"actor_id": "owner-oid", "tenant_id": "tenant-1", "source": "easy_auth"})

    result = control_plane.invite_entra_workspace_member("ws-graph", {"email": "reviewer@contoso.com", "role": "editor", "send_email": True}, trusted)

    assert result["invitation"]["state"] == "accepted"
    assert re.fullmatch(r"member_[0-9a-f]{40}", result["invitation"]["subject_label"])
    assert result["invited_member"]["subject_label"] == result["invitation"]["subject_label"]
    assert "accepted_identity" not in result["invitation"]
    assert "provider" not in result["invitation"] or "tenant_id" not in result["invitation"]["provider"]
    saved = json.loads(workspace_path.read_text(encoding="utf-8"))
    assert saved["workspace_invitation_events"][-1]["accepted_identity"] == {"actor_id": "oid-reviewer", "tenant_id": "tenant-1"}
    meta = {}
    pending = invitation_store.create_pending_invitation(meta, "ws-graph", email="other@contoso.com", role="viewer", invited_by={"actor_id": "owner", "tenant_id": "tenant-1", "source": "easy_auth"})
    assert invitation_store.accept_provider_invitation(meta, "ws-graph", pending["invitation_id"], {"source": "microsoft_graph", "invited_user_id": "oid-other"}, inviter={"actor_id": "owner", "source": "easy_auth"}) is None
    assert workspace_path.exists()


def test_noop_and_malformed_durable_journal_fail_closed_without_cas_write(monkeypatch):
    meta = {}
    writes = []
    monkeypatch.setattr(invitation_store, "blob_configured", lambda: True)
    monkeypatch.setattr(invitation_store, "download_blob_json", lambda _name: {"revision": 3, "events": []})
    monkeypatch.setattr(invitation_store, "compare_and_swap_blob_json", lambda *_args, **kwargs: writes.append(kwargs) or kwargs["changes"])

    assert invitation_store.accept_provider_invitation(meta, "ws-graph", "missing", {"source": "microsoft_graph", "invited_user_id": "oid"}, inviter={"actor_id": "owner", "tenant_id": "tenant", "source": "easy_auth"}) is None
    assert writes == []
    monkeypatch.setattr(invitation_store, "download_blob_json", lambda _name: {"revision": 3, "events": "broken"})
    with pytest.raises(invitation_store.InvitationPersistenceError, match="schema"):
        invitation_store.create_pending_invitation(meta, "ws-graph", email="bad@contoso.com", role="viewer", invited_by={"actor_id": "owner", "tenant_id": "tenant", "source": "easy_auth"})


@pytest.mark.parametrize("first_state", ["accepted", "failed", "revoked"])
def test_journal_replay_rejects_a_non_pending_first_state(first_state):
    events = [{"event_type": "state", "invitation_id": "invite-1", "state": first_state, "email": "user@contoso.com", "role": "viewer", "accepted_identity": {"actor_id": "oid", "tenant_id": "tenant"}}]

    with pytest.raises(invitation_store.InvitationPersistenceError, match="sequence"):
        invitation_store._latest_events({"workspace_invitation_events": events})


@pytest.mark.parametrize(
    "event",
    [
        {"event_type": "state", "invitation_id": "invite-1", "state": "accepted", "email": "user@contoso.com", "role": "viewer", "accepted_identity": {"actor_id": "", "tenant_id": "tenant"}},
        {"event_type": "activation", "invitation_id": "invite-1", "email": "user@contoso.com", "role": "viewer", "accepted_identity": {"actor_id": "other", "tenant_id": "tenant"}},
        {"event_type": "role_change", "invitation_id": "invite-1", "email": "user@contoso.com", "role": "owner"},
        {"event_type": "role_change", "email": "user@contoso.com", "role": "viewer"},
    ],
)
def test_journal_replay_rejects_missing_or_mismatched_identity_and_malformed_role_change(event):
    events = [
        {"event_type": "state", "invitation_id": "invite-1", "state": "pending", "email": "user@contoso.com", "role": "viewer"},
    ]
    if event["event_type"] == "activation":
        events.append({"event_type": "state", "invitation_id": "invite-1", "state": "accepted", "email": "user@contoso.com", "role": "viewer", "accepted_identity": {"actor_id": "oid", "tenant_id": "tenant"}})
    events.append(event)

    with pytest.raises(invitation_store.InvitationPersistenceError):
        invitation_store._latest_events({"workspace_invitation_events": events})


def test_pending_retry_uses_effective_role_and_rejects_an_obsolete_role_request():
    meta = {}
    pending = invitation_store.create_pending_invitation(meta, "ws", email="user@contoso.com", role="editor", invited_by={"actor_id": "owner", "tenant_id": "tenant", "source": "easy_auth"})
    assert invitation_store.update_invited_member_role(meta, "ws", email="user@contoso.com", role="viewer") is True

    retry = invitation_store.create_pending_invitation(meta, "ws", email="user@contoso.com", role="viewer", invited_by={"actor_id": "owner", "tenant_id": "tenant", "source": "easy_auth"})

    assert retry["invitation_id"] == pending["invitation_id"]
    assert retry["role"] == "viewer"
    with pytest.raises(invitation_store.InvitationTransitionError, match="effective role"):
        invitation_store.create_pending_invitation(meta, "ws", email="user@contoso.com", role="editor", invited_by={"actor_id": "owner", "tenant_id": "tenant", "source": "easy_auth"})
    assert len({event["invitation_id"] for event in meta["workspace_invitation_events"] if event.get("event_type") == "state"}) == 1


def test_alias_revocation_canonicalizes_oid_and_tenant_independent_of_mapping_order():
    meta = {}
    first = invitation_store.create_pending_invitation(meta, "ws", email="first@contoso.com", role="viewer", invited_by={"actor_id": "owner", "tenant_id": "tenant", "source": "easy_auth"})
    second = invitation_store.create_pending_invitation(meta, "ws", email="alias@contoso.com", role="viewer", invited_by={"actor_id": "owner", "tenant_id": "tenant", "source": "easy_auth"})
    invitation_store.transition_invitation(meta, first["invitation_id"], "accepted", identity={"tenant_id": "tenant", "actor_id": "oid", "source": "easy_auth"})
    invitation_store.transition_invitation(meta, second["invitation_id"], "accepted", identity={"actor_id": "oid", "tenant_id": "tenant", "source": "easy_auth"})

    invitation_store.revoke_effective_invitations(meta, "ws", email="first@contoso.com")

    assert invitation_store.effective_invitation_state(meta, first["invitation_id"]) == "revoked"
    assert invitation_store.effective_invitation_state(meta, second["invitation_id"]) == "revoked"


@pytest.mark.parametrize(
    "accepted",
    [
        {"event_type": "state", "invitation_id": "invite-1", "state": "accepted", "email": "user@contoso.com", "role": "admin", "accepted_identity": {"actor_id": "oid", "tenant_id": "tenant"}},
        {"event_type": "state", "invitation_id": "invite-1", "state": "accepted", "email": "other@contoso.com", "role": "viewer", "accepted_identity": {"actor_id": "oid", "tenant_id": "tenant"}},
    ],
)
def test_journal_replay_rejects_accepted_state_that_changes_pending_email_or_effective_role(accepted):
    events = [
        {"event_type": "state", "invitation_id": "invite-1", "state": "pending", "email": "user@contoso.com", "role": "viewer"},
        accepted,
    ]

    with pytest.raises(invitation_store.InvitationPersistenceError, match="sequence"):
        invitation_store._latest_events({"workspace_invitation_events": events})


def test_journal_replay_allows_accepted_state_with_the_role_from_a_valid_role_change():
    events = [
        {"event_type": "state", "invitation_id": "invite-1", "state": "pending", "email": "user@contoso.com", "role": "admin"},
        {"event_type": "role_change", "invitation_id": "invite-1", "email": "user@contoso.com", "role": "viewer"},
        {"event_type": "state", "invitation_id": "invite-1", "state": "accepted", "email": "user@contoso.com", "role": "viewer", "accepted_identity": {"actor_id": "oid", "tenant_id": "tenant"}},
    ]

    assert invitation_store._latest_events({"workspace_invitation_events": events})["invite-1"]["role"] == "viewer"


@pytest.mark.parametrize("malformed", [{}, "", 0, False, None])
def test_local_present_falsy_malformed_journal_fails_closed_without_overwrite(malformed):
    meta = {"workspace_invitation_events": malformed}

    with pytest.raises(invitation_store.InvitationPersistenceError, match="schema"):
        invitation_store.create_pending_invitation(meta, "ws", email="user@contoso.com", role="viewer", invited_by={"actor_id": "owner", "tenant_id": "tenant", "source": "easy_auth"})
    assert meta["workspace_invitation_events"] == malformed


def test_local_absent_journal_is_a_valid_empty_journal():
    meta = {}

    pending = invitation_store.create_pending_invitation(meta, "ws", email="user@contoso.com", role="viewer", invited_by={"actor_id": "owner", "tenant_id": "tenant", "source": "easy_auth"})

    assert pending["state"] == "pending"


def test_app_only_resource_tenant_must_match_trusted_inviter_and_never_persists_token(monkeypatch):
    monkeypatch.setenv("GRAPH_TENANT_ID", "tenant-a")
    monkeypatch.setattr(graph_client, "_app_only_token", lambda: "secret-token")
    source = graph_client.graph_token_context(None)
    assert source == {"source": "app_only", "resource_tenant_id": "tenant-a"}
    meta = {}
    pending = invitation_store.create_pending_invitation(meta, "ws", email="user@contoso.com", role="viewer", invited_by={"actor_id": "owner", "tenant_id": "tenant-a", "source": "easy_auth"})
    accepted = invitation_store.accept_provider_invitation(meta, "ws", pending["invitation_id"], {"source": "microsoft_graph", "invited_user_id": "oid-user", "resource_tenant_id": "tenant-a"}, inviter={"actor_id": "owner", "tenant_id": "tenant-a", "source": "easy_auth"})
    assert accepted and "token" not in str(accepted)
    assert invitation_store.accept_provider_invitation(meta, "ws", pending["invitation_id"], {"source": "microsoft_graph", "invited_user_id": "oid-user", "resource_tenant_id": "tenant-b"}, inviter={"actor_id": "owner", "tenant_id": "tenant-a", "source": "easy_auth"}) is None


def test_entra_invite_falls_back_to_workspace_member_when_graph_unavailable(tmp_path, monkeypatch):
    workspace_path = _workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(graph_client, "graph_token_from_request", lambda request=None: "")
    monkeypatch.setattr(control_plane, "graph_token_from_request", lambda request=None: "", raising=False)

    result = control_plane.invite_entra_workspace_member(
        "ws-graph",
        {
            "email": "reviewer@contoso.com",
            "name": "Reviewer",
            "role": "editor",
            "send_email": True,
            "fallback_to_workspace_member": True,
        },
        RequestStub(),
    )

    reviewer = next(member for member in result["members"] if member["role"] == "editor")
    assert re.fullmatch(r"member_[0-9a-f]{40}", reviewer["subject_label"])
    assert "email" not in reviewer
    assert reviewer["role"] == "editor"
    assert reviewer["status"] == "pending"
    assert result["graph_invite"]["status"] == "unavailable"
    assert result["graph_invite"]["code"] == "graph_token_missing"
    saved = json.loads(workspace_path.read_text(encoding="utf-8"))
    assert saved["workspace_members"][0]["email"] == "reviewer@contoso.com"
    assert [event["state"] for event in saved["workspace_invitation_events"]] == ["pending", "failed"]
    assert "error" not in saved["workspace_invitation_events"][-1]


def test_entra_invite_without_member_fallback_still_records_sanitized_failure(tmp_path, monkeypatch):
    workspace_path = _workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(graph_client, "graph_token_from_request", lambda request=None: "")

    result = control_plane.invite_entra_workspace_member(
        "ws-graph",
        {
            "email": "reviewer@contoso.com",
            "role": "editor",
            "send_email": True,
            "fallback_to_workspace_member": False,
        },
        RequestStub(),
    )

    saved = json.loads(workspace_path.read_text(encoding="utf-8"))
    assert result["graph_invite"]["status"] == "unavailable"
    assert [event["state"] for event in saved["workspace_invitation_events"]] == ["pending", "failed"]
    assert saved.get("workspace_members", []) == []
    assert saved["workspace_invitation_events"][-1]["provider"] == {
        "source": "microsoft_graph",
        "status": "unavailable",
        "error_code": "graph_token_missing",
    }


@pytest.mark.parametrize("fallback", [False, True])
def test_graph_failure_audits_truthful_terminal_event_after_durable_transition(tmp_path, monkeypatch, fallback):
    _workspace(tmp_path, monkeypatch)
    secret = "Bearer graph-secret-body AccountKey=never-persist"
    order: list[str] = []
    captured: list[tuple[tuple, dict]] = []
    original_save = control_plane._save_workspace_meta

    def graph_failure(*_args, **_kwargs):
        raise control_plane.GraphClientError("graph_provider_failed", secret, status=503)

    def save_then_mark(*args, **kwargs):
        result = original_save(*args, **kwargs)
        order.append("durable-save")
        return result

    def audit(*args, **kwargs):
        captured.append((args, kwargs))
        order.append(f"audit:{args[1]}")
        return {"event_id": "event-audit"}

    monkeypatch.setattr(control_plane, "send_graph_invitation", graph_failure)
    monkeypatch.setattr(control_plane, "_save_workspace_meta", save_then_mark)
    monkeypatch.setattr(control_plane, "record_audit_event", audit)

    result = control_plane.invite_entra_workspace_member(
        "ws-graph",
        {
            "email": "reviewer@contoso.com",
            "role": "editor",
            "send_email": True,
            "fallback_to_workspace_member": fallback,
        },
        RequestStub(),
    )

    fail_args, fail_kwargs = next((args, kwargs) for args, kwargs in captured if args[1] == "invitation.fail")
    invitation_id = fail_args[2]["resource_id"]
    assert order.index("durable-save") < order.index("audit:invitation.fail")
    assert fail_args[2] == {
        "workspace_id": "ws-graph",
        "resource_type": "invitation",
        "resource_id": invitation_id,
    }
    assert fail_kwargs["result"] == "failed"
    assert fail_kwargs["reason_code"] == "invitation_failed"
    assert fail_kwargs["correlation"] == {"invitation_id": invitation_id}
    assert "invitation_id" not in result["invitation"]
    assert re.fullmatch(r"invite_[0-9a-f]{40}", result["invitation"]["invitation_ref"])
    assert secret not in repr((fail_args, fail_kwargs))


def test_invitation_history_replays_every_effective_state_and_redacts_identity(monkeypatch):
    monkeypatch.setattr(invitation_store, "blob_configured", lambda: False)
    meta = {}
    inviter = {"actor_id": "owner-raw-oid", "tenant_id": "tenant-secret", "source": "easy_auth"}

    invitation_store.create_pending_invitation(meta, "ws-history", email="pending@contoso.com", role="viewer", invited_by=inviter)

    accepted = invitation_store.create_pending_invitation(meta, "ws-history", email="accepted@contoso.com", role="editor", invited_by=inviter)
    invitation_store.transition_invitation(meta, accepted["invitation_id"], "accepted", identity={"actor_id": "accepted-raw-oid", "tenant_id": "tenant-secret", "source": "easy_auth"})

    failed = invitation_store.create_pending_invitation(meta, "ws-history", email="failed@contoso.com", role="viewer", invited_by=inviter, provider={"source": "microsoft_graph", "invited_user_id": "provider-raw-oid"})
    invitation_store.transition_invitation(meta, failed["invitation_id"], "failed", provider={"source": "microsoft_graph", "error_code": "private-provider-code"})

    expired = invitation_store.create_pending_invitation(meta, "ws-history", email="expired@contoso.com", role="viewer", invited_by=inviter)
    invitation_store.transition_invitation(meta, expired["invitation_id"], "expired")

    revoked = invitation_store.create_pending_invitation(meta, "ws-history", email="revoked@contoso.com", role="admin", invited_by=inviter)
    invitation_store.transition_invitation(meta, revoked["invitation_id"], "revoked")

    removed = invitation_store.create_pending_invitation(meta, "ws-history", email="removed@contoso.com", role="editor", invited_by=inviter)
    removed_identity = {"actor_id": "removed-raw-oid", "tenant_id": "tenant-secret", "source": "easy_auth"}
    invitation_store.transition_invitation(meta, removed["invitation_id"], "accepted", identity=removed_identity)
    assert invitation_store.consume_accepted_invitation(meta, "ws-history", removed_identity)
    invitation_store.transition_invitation(meta, removed["invitation_id"], "revoked")

    history = invitation_store.list_invitation_history(meta, "ws-history", pseudonym_salt="test-history-salt")
    reloaded = invitation_store.list_invitation_history(
        {"workspace_invitation_events": json.loads(json.dumps(meta["workspace_invitation_events"]))},
        "ws-history",
        pseudonym_salt="test-history-salt",
    )

    assert history == reloaded
    assert {row["state"] for row in history} == {"pending", "accepted", "failed", "expired", "revoked", "removed"}
    assert all(re.fullmatch(r"member_[0-9a-f]{40}", row["subject_label"]) for row in history)
    assert all(re.fullmatch(r"invite_[0-9a-f]{40}", row["invitation_ref"]) for row in history)
    assert all(set(row) == {"invitation_ref", "subject_label", "role", "state", "invitation_state", "updated_at"} for row in history)
    serialized = json.dumps(history)
    for secret in ("@contoso.com", "raw-oid", "tenant-secret", "provider-raw-oid", "private-provider-code"):
        assert secret not in serialized


def test_invitation_history_keeps_same_subject_terminal_attempts_separate_after_reload(tmp_path, monkeypatch):
    workspace_path = _workspace(tmp_path, monkeypatch)
    monkeypatch.setenv("DF_INVITATION_PSEUDONYM_SALT", "history-reload-salt")
    meta = {"workspace_id": "ws-graph"}
    inviter = {"actor_id": "owner-oid", "tenant_id": "tenant-1", "source": "easy_auth"}
    failed = invitation_store.create_pending_invitation(meta, "ws-graph", email="same@contoso.com", role="viewer", invited_by=inviter)
    invitation_store.transition_invitation(meta, failed["invitation_id"], "failed")
    accepted = invitation_store.create_pending_invitation(meta, "ws-graph", email="same@contoso.com", role="editor", invited_by=inviter)
    invitation_store.transition_invitation(meta, accepted["invitation_id"], "accepted", identity={"actor_id": "member-oid", "tenant_id": "tenant-1", "source": "easy_auth"})
    meta["workspace_members"] = [{"email": "same@contoso.com", "actor_id": "member-oid", "tenant_id": "tenant-1", "role": "editor", "status": "active"}]
    workspace_path.write_text(json.dumps(meta), encoding="utf-8")

    first = control_plane.workspace_invitation_history("ws-graph")
    second = control_plane.workspace_invitation_history("ws-graph")

    assert first == second
    assert [row["state"] for row in first] == ["accepted", "failed"]
    assert first[0]["subject_label"] == first[1]["subject_label"]
    assert first[0]["invitation_ref"] != first[1]["invitation_ref"]
    assert "same@contoso.com" not in json.dumps(first)

    member_contract = control_plane.workspace_member_roles("ws-graph", RequestStub())
    from backend.roi_service import member_chargeback
    chargeback = member_chargeback(
        "ws-graph",
        {"from": "2026-07-01T00:00:00Z", "to": "2026-07-31T00:00:00Z"},
        runs=[{
            "workspace_id": "ws-graph",
            "completed_at": "2026-07-14T00:00:00Z",
            "trusted_identity": True,
            "actor": {"email": "same@contoso.com", "actor_id": "member-oid", "tenant_id": "tenant-1", "source": "easy_auth"},
            "models": [{"model": "gpt-5", "usage": {"input_tokens": 1, "output_tokens": 1}}],
        }],
        messages=[],
        tasks=[],
        memberships=[{"email": "same@contoso.com", "actor_id": "member-oid", "tenant_id": "tenant-1", "status": "active"}],
        prices=[],
        pseudonym_salt="history-reload-salt",
    )
    member_label = next(row["subject_label"] for row in member_contract["members"] if row["role"] == "editor")
    assert first[0]["subject_label"] == member_label == chargeback["members"][0]["member"]["subject_label"]


def test_update_workspace_member_role_persists_role_change(tmp_path, monkeypatch):
    workspace_path = _workspace(tmp_path, monkeypatch)
    workspace_path.write_text(
        json.dumps(
            {
                "workspace_id": "ws-graph",
                "name": "Graph Members",
                "workspace_members": [
                    {
                        "name": "Reviewer",
                        "email": "reviewer@contoso.com",
                        "role": "editor",
                        "status": "active",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    uploads = []
    monkeypatch.setattr(control_plane, "upload_blob_json", lambda *args, **kwargs: uploads.append(args) or {}, raising=False)
    subject_label = invitation_store.member_subject_label("ws-graph", "reviewer@contoso.com")

    result = control_plane.update_workspace_member_role(
        "ws-graph",
        subject_label,
        {"role": "viewer"},
        RequestStub(),
    )

    reviewer = next(member for member in result["members"] if member["role"] == "viewer")
    assert re.fullmatch(r"member_[0-9a-f]{40}", reviewer["subject_label"])
    assert "email" not in reviewer
    assert reviewer["role"] == "viewer"
    saved = json.loads(workspace_path.read_text(encoding="utf-8"))
    assert saved["workspace_members"][0]["role"] == "viewer"
    assert saved["workspace_members"][0]["updated_by"]["email"] == "owner@contoso.com"
    assert uploads and uploads[-1][0] == "workspaces/ws-graph/workspace.json"


def test_member_management_accepts_safe_subject_reference_without_public_email(tmp_path, monkeypatch):
    workspace_path = _workspace(tmp_path, monkeypatch)
    workspace_path.write_text(
        json.dumps({
            "workspace_id": "ws-graph",
            "workspace_members": [{"name": "Reviewer", "email": "reviewer@contoso.com", "role": "editor", "status": "active"}],
        }),
        encoding="utf-8",
    )
    subject_label = invitation_store.member_subject_label("ws-graph", "reviewer@contoso.com")

    with pytest.raises(ValueError, match="member reference"):
        control_plane.update_workspace_member_role("ws-graph", "reviewer@contoso.com", {"role": "viewer"}, RequestStub())
    with pytest.raises(ValueError, match="member reference"):
        control_plane.remove_workspace_member("ws-graph", "reviewer@contoso.com", RequestStub())

    updated = control_plane.update_workspace_member_role("ws-graph", subject_label, {"role": "viewer"}, RequestStub())
    removed = control_plane.remove_workspace_member("ws-graph", subject_label, RequestStub())

    assert updated["updated_member"]["subject_label"] == subject_label
    assert updated["updated_member"]["role"] == "viewer"
    assert removed["removed_member"] == {"subject_label": subject_label}
    assert "reviewer@contoso.com" not in json.dumps(updated)
    assert "reviewer@contoso.com" not in json.dumps(removed)
