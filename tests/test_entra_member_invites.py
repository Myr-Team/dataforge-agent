import json
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import backend.control_plane as control_plane
import backend.graph_client as graph_client
import backend.invitation_store as invitation_store
import pytest


class RequestStub:
    def __init__(self, headers=None):
        self.headers = headers or {
            "x-dataforge-actor": quote(json.dumps({"name": "Owner", "email": "owner@contoso.com"})),
        }


def _workspace(tmp_path, monkeypatch):
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

    reviewer = next(member for member in result["members"] if member["email"] == "reviewer@contoso.com")
    assert reviewer["role"] == "editor"
    assert reviewer["status"] == "pending"
    assert result["graph_invite"]["status"] == "unavailable"
    assert result["graph_invite"]["error"]["code"] == "graph_token_missing"
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

    result = control_plane.update_workspace_member_role(
        "ws-graph",
        "reviewer@contoso.com",
        {"role": "viewer"},
        RequestStub(),
    )

    reviewer = next(member for member in result["members"] if member["email"] == "reviewer@contoso.com")
    assert reviewer["role"] == "viewer"
    saved = json.loads(workspace_path.read_text(encoding="utf-8"))
    assert saved["workspace_members"][0]["role"] == "viewer"
    assert saved["workspace_members"][0]["updated_by"]["email"] == "owner@contoso.com"
    assert uploads and uploads[-1][0] == "workspaces/ws-graph/workspace.json"
