import base64
import json
from urllib.parse import quote

import backend.conversation_store as conversation_store
import backend.control_plane as control_plane
import backend.run_store as run_store
from backend.identity import actor_from_headers, actor_from_ui_context


def _principal(claims):
    payload = {"auth_typ": "aad", "name_typ": "name", "role_typ": "roles", "claims": claims}
    raw = json.dumps(payload).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def test_actor_from_easy_auth_claims_extracts_entra_identity() -> None:
    actor = actor_from_headers(
        {
            "x-ms-client-principal": _principal(
                [
                    {"typ": "name", "val": "Fu Zihao"},
                    {"typ": "preferred_username", "val": "fuzihao@gdjiuyun.onmicrosoft.com"},
                    {"typ": "http://schemas.microsoft.com/identity/claims/objectidentifier", "val": "oid-123"},
                    {"typ": "http://schemas.microsoft.com/identity/claims/tenantid", "val": "tid-456"},
                ]
            ),
            "x-ms-client-principal-id": "fallback-id",
            "x-ms-client-principal-idp": "aad",
        }
    )

    assert actor["email"] == "fuzihao@gdjiuyun.onmicrosoft.com"
    assert actor["name"] == "Fu Zihao"
    assert actor["actor_id"] == "oid-123"
    assert actor["tenant_id"] == "tid-456"
    assert actor["source"] == "easy_auth"


def test_actor_from_client_actor_header_when_backend_not_behind_easy_auth() -> None:
    raw_actor = quote(json.dumps({"name": "Guest Reviewer", "email": "guest.reviewer@contoso.com"}))

    actor = actor_from_headers({"x-dataforge-actor": raw_actor})

    assert actor["email"] == "guest.reviewer@contoso.com"
    assert actor["name"] == "Guest Reviewer"
    assert actor["source"] == "client_actor"


def test_actor_from_ui_context_never_trusts_placeholder_demo_user() -> None:
    actor = actor_from_ui_context({"actor": {"name": "Demo User", "email": "local.demo@dataforge"}})

    assert actor["email"] == "fuzihao@gdjiuyun.onmicrosoft.com"
    assert actor["source"] == "workspace_default"


def test_run_store_persists_actor_and_token_summary(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "upload_blob_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_store, "download_blob_json", lambda *args, **kwargs: {})
    run_store._ACTIVE.clear()

    actor = {"name": "Reviewer", "email": "reviewer@example.com", "actor_id": "oid-reviewer"}
    run_store.start_run("run-actor", "ws-actor", "analyze", actor=actor)
    run_store.record_event(
        "run-actor",
        "model_response",
        {"agent": "df-analyst", "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}},
    )
    run_store.complete_run("run-actor", final={"text": "done"}, artifact={})

    detail = run_store.get_run("run-actor")
    assert detail["actor"]["email"] == "reviewer@example.com"
    assert detail["tokens"] == {"total": 15, "prompt": 10, "completion": 5}

    summary = run_store.list_runs("ws-actor")[0]
    assert summary["actor"]["email"] == "reviewer@example.com"
    assert summary["tokens"]["total"] == 15


def test_run_summary_and_trace_expose_dynamic_evidence(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "upload_blob_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_store, "download_blob_json", lambda *args, **kwargs: {})
    run_store._ACTIVE.clear()

    run_store.start_run("run-dynamic", "ws-dynamic", "analyze", actor={"email": "reviewer@example.com"})
    run_store.record_event("run-dynamic", "ready", {"run_id": "run-dynamic", "conversation_id": "run-dynamic"})
    run_store.record_event(
        "run-dynamic",
        "model_response",
        {"agent": "df-answer-writer", "usage": {"input_tokens": 12, "output_tokens": 7, "total_tokens": 19}},
    )
    run_store.record_event("run-dynamic", "final", {"mode": "analysis"})
    run_store.complete_run("run-dynamic", final={"text": "done"}, artifact={})

    summary = control_plane.run_summary("run-dynamic")
    trace = control_plane.run_trace("run-dynamic")

    assert summary["duration_ms"] >= 0
    assert summary["tokens"]["total"] == 19
    assert summary["evidence"]["dynamic"] is True
    assert summary["evidence"]["duration"] == "run.started_at -> run.completed_at"
    assert summary["evidence"]["tokens"] == "run.models[].usage or steps[].data.usage"
    assert summary["evidence"]["trace"] == "run.steps"
    assert trace
    assert trace[0]["source"] == "run_store.steps"
    assert trace[0]["evidence"]["event"] == "ready"
    assert trace[1]["tokens"]["total"] == 19


def test_conversation_store_persists_actor_on_user_message(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(conversation_store, "CONVERSATION_DIR", tmp_path / "conversations")
    monkeypatch.setattr(conversation_store, "upload_blob_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(conversation_store, "download_blob_json", lambda *args, **kwargs: {})

    actor = {"name": "Analyst", "email": "analyst@example.com", "actor_id": "oid-analyst"}
    conversation_store.append_message(
        "conv-actor",
        workspace_id="ws-actor",
        role="user",
        text="review this",
        actor=actor,
        remote_load=False,
    )

    detail = conversation_store.get_conversation("conv-actor")
    assert detail["messages"][0]["actor"]["email"] == "analyst@example.com"
    assert detail["actors"][0]["email"] == "analyst@example.com"


def test_workspace_members_include_actor_usage(monkeypatch) -> None:
    owner_actor = {"name": "Owner", "email": "owner@contoso.com", "actor_id": "owner-oid"}
    reviewer_actor = {"name": "Reviewer", "email": "reviewer@contoso.com", "actor_id": "reviewer-oid"}
    runs = [
        {"run_id": "run-owner", "workspace_id": "ws-usage", "actor": owner_actor, "tokens": {"total": 11, "prompt": 7, "completion": 4}, "time": "2026-07-01T00:00:00Z"},
        {"run_id": "run-reviewer", "workspace_id": "ws-usage", "actor": reviewer_actor, "tokens": {"total": 19, "prompt": 10, "completion": 9}, "time": "2026-07-01T00:01:00Z"},
    ]
    monkeypatch.setattr(control_plane, "list_runs", lambda workspace_id=None: runs)
    monkeypatch.setattr(control_plane, "get_run", lambda run_id: next(item for item in runs if item["run_id"] == run_id))

    class RequestStub:
        headers = {
            "x-ms-client-principal": _principal(
                [
                    {"typ": "name", "val": "Owner"},
                    {"typ": "preferred_username", "val": "owner@contoso.com"},
                    {"typ": "oid", "val": "owner-oid"},
                ]
            )
        }

    result = control_plane.workspace_member_roles("ws-usage", RequestStub())
    members = result["members"]

    assert members[0]["email"] == "owner@contoso.com"
    assert members[0]["usage"]["total_tokens"] == 11
    assert any(member["email"] == "reviewer@contoso.com" and member["usage"]["total_tokens"] == 19 for member in members)
    assert result["usage"]["totals"]["total_tokens"] == 30


def test_invited_workspace_member_persists_and_merges_usage(tmp_path, monkeypatch) -> None:
    workspace_root = tmp_path / "workspaces"
    workspace_dir = workspace_root / "ws-members"
    workspace_dir.mkdir(parents=True)
    workspace_path = workspace_dir / "workspace.json"
    workspace_path.write_text(
        json.dumps({"workspace_id": "ws-members", "name": "Member Test", "format": "mixed"}),
        encoding="utf-8",
    )
    uploads = []
    monkeypatch.setattr(control_plane, "WORKSPACES", workspace_root, raising=False)
    monkeypatch.setattr(control_plane, "download_blob_json", lambda *args, **kwargs: {}, raising=False)
    monkeypatch.setattr(control_plane, "upload_blob_json", lambda *args, **kwargs: uploads.append(args) or {}, raising=False)
    monkeypatch.setattr(
        control_plane,
        "list_runs",
        lambda workspace_id=None: [
            {
                "run_id": "run-reviewer",
                "workspace_id": "ws-members",
                "actor": {"name": "Reviewer", "email": "reviewer@contoso.com"},
                "tokens": {"total": 21, "prompt": 13, "completion": 8},
                "time": "2026-07-09T00:00:00Z",
            }
        ],
    )
    monkeypatch.setattr(control_plane, "get_run", lambda run_id: control_plane.list_runs("ws-members")[0])

    class RequestStub:
        headers = {
            "x-dataforge-actor": quote(json.dumps({"name": "Owner", "email": "owner@contoso.com"})),
        }

    result = control_plane.invite_workspace_member(
        "ws-members",
        {"email": "reviewer@contoso.com", "name": "Reviewer", "role": "editor"},
        RequestStub(),
    )

    reviewer = next(member for member in result["members"] if member["email"] == "reviewer@contoso.com")
    assert reviewer["status"] == "active"
    assert reviewer["role"] == "editor"
    assert reviewer["usage"]["total_tokens"] == 21
    assert uploads and uploads[-1][0] == "workspaces/ws-members/workspace.json"
    saved = json.loads(workspace_path.read_text(encoding="utf-8"))
    assert saved["workspace_members"][0]["email"] == "reviewer@contoso.com"
    assert saved["workspace_members"][0]["invited_by"]["email"] == "owner@contoso.com"
