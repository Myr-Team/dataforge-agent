import json
from urllib.parse import quote

import backend.control_plane as control_plane
import backend.graph_client as graph_client


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
