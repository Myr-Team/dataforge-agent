import base64
import importlib
import json
import secrets
from urllib.parse import quote

import pytest

import backend.conversation_store as conversation_store
import backend.audit_store as audit_store
import backend.control_plane as control_plane
import backend.data_workbench as data_workbench
import backend.run_store as run_store
import backend.task_store as task_store
import backend.workspace_store as workspace_store
from backend.audit_store import AuditPersistenceError
from backend.identity import actor_from_headers, actor_from_ui_context, is_trusted_identity, is_trusted_tenant_identity
from fastapi.testclient import TestClient
from backend.app import app


@pytest.fixture(autouse=True)
def _explicit_test_audit_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_store, "AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setenv("DF_AUDIT_LOCAL_MODE", "1")
    monkeypatch.setenv("DF_AUDIT_HMAC_ACTIVE_KEY_ID", "test-v1")
    monkeypatch.setenv(
        "DF_AUDIT_HMAC_KEYS",
        json.dumps({"test-v1": base64.b64encode(secrets.token_bytes(32)).decode("ascii")}),
    )


def _principal(claims):
    payload = {"auth_typ": "aad", "name_typ": "name", "role_typ": "roles", "claims": claims}
    raw = json.dumps(payload).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def test_actor_from_easy_auth_claims_extracts_entra_identity(monkeypatch) -> None:
    monkeypatch.setenv("DF_WEB_PROXY_SECRET", "test-proxy-secret")
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
            "x-dataforge-proxy-secret": "test-proxy-secret",
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
    assert is_trusted_identity(actor) is False


def test_trusted_identity_requires_proxy_verified_easy_auth_and_actor_id() -> None:
    assert is_trusted_identity({"source": "easy_auth", "actor_id": "oid-1"}) is True
    assert is_trusted_identity({"source": "easy_auth"}) is False
    assert is_trusted_identity({"source": "workspace_default", "actor_id": "oid-1"}) is False
    assert is_trusted_tenant_identity({"source": "easy_auth", "actor_id": "oid-1", "tenant_id": "tenant-1"}) is True
    assert is_trusted_tenant_identity({"source": "easy_auth", "actor_id": "oid-1"}) is False


def test_roi_endpoint_fails_closed_before_reading_for_compatibility_or_nonmember(monkeypatch) -> None:
    monkeypatch.delenv("DF_WORKSPACE_RBAC_ENFORCED", raising=False)
    monkeypatch.setenv("DF_WEB_PROXY_SECRET", "test-proxy-secret")
    reads: list[str] = []
    monkeypatch.setattr(control_plane, "workspace_roi_snapshot", lambda workspace_id, *_args: reads.append(workspace_id) or {"workspace_id": workspace_id})
    monkeypatch.setattr(control_plane, "active_workspace_role", lambda _workspace_id, actor: "viewer" if actor.get("actor_id") == "viewer-oid" else None)
    client = TestClient(app)
    query = "?from=2026-07-10T00:00:00Z&to=2026-07-11T00:00:00Z"

    for headers in ({}, {"x-ms-client-principal": _principal([{"typ": "oid", "val": "viewer-oid"}]), "x-dataforge-proxy-secret": "test-proxy-secret"}):
        assert client.get(f"/api/workspaces/ws-locked/governance/roi{query}", headers=headers).status_code == 403
    allowed = client.get(
        f"/api/workspaces/ws-locked/governance/roi{query}",
        headers={"x-ms-client-principal": _principal([{"typ": "oid", "val": "viewer-oid"}, {"typ": "tid", "val": "tenant-1"}]), "x-dataforge-proxy-secret": "test-proxy-secret"},
    )

    assert allowed.status_code == 200
    assert reads == ["ws-locked"]


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
    assert detail["trusted_identity"] is False
    assert detail["actor"]["email"] == "reviewer@example.com"
    assert detail["tokens"] == {"total": 15, "prompt": 10, "completion": 5}

    summary = run_store.list_runs("ws-actor")[0]
    assert summary["actor"]["email"] == "reviewer@example.com"
    assert summary["tokens"]["total"] == 15


def test_token_summary_keeps_route_usage_when_model_response_usage_is_empty(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "upload_blob_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_store, "download_blob_json", lambda *args, **kwargs: {})
    run_store._ACTIVE.clear()

    run_store.start_run("run-route-usage", "ws-actor", "analyze")
    run_store.record_event(
        "run-route-usage",
        "route",
        {"usage": {"input_tokens": 12, "output_tokens": 7, "total_tokens": 19}},
    )
    run_store.record_event(
        "run-route-usage",
        "model_response",
        {"agent": "df-answer-writer", "usage": {}},
    )
    run_store.complete_run("run-route-usage", final={"text": "done"}, artifact={})

    detail = run_store.get_run("run-route-usage")

    assert detail["tokens"] == {"total": 19, "prompt": 12, "completion": 7}
    assert control_plane.run_summary("run-route-usage")["tokens"]["total"] == 19


def test_run_store_persists_observed_model_identifier_for_versioned_pricing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "upload_blob_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_store, "download_blob_json", lambda *args, **kwargs: {})
    run_store._ACTIVE.clear()

    run_store.start_run("run-priced", "ws-priced", "analyze", actor={"actor_id": "owner-oid"})
    run_store.record_event(
        "run-priced",
        "model_response",
        {"agent": "df-analyst", "model": "gpt-5", "usage": {"input_tokens": 10, "output_tokens": 5}},
    )
    run_store.complete_run("run-priced", final={"text": "done"}, artifact={})

    assert run_store.get_run("run-priced")["models"][0]["model"] == "gpt-5"


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


def test_run_summary_and_trace_endpoints_keep_unknown_usage_null(monkeypatch) -> None:
    run = {
        "run_id": "run-no-usage",
        "workspace_id": "ws-no-usage",
        "status": "completed",
        "steps": [
            {
                "time": "2026-07-12T00:00:00Z",
                "event": "model_response",
                "data": {"agent": "df-answer-writer", "status": "completed"},
            }
        ],
        "models": [],
    }
    monkeypatch.setattr(control_plane, "get_run", lambda _run_id: run)

    client = TestClient(app)
    summary = client.get("/api/runs/run-no-usage/summary")
    trace = client.get("/api/runs/run-no-usage/trace")

    assert summary.status_code == 200
    assert summary.json()["tokens"] is None
    assert trace.status_code == 200
    assert trace.json()[0]["tokens"] is None
    assert "0 tokens" not in trace.json()[0]["summary"]


def test_conversation_store_persists_actor_on_user_message(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(conversation_store, "CONVERSATION_DIR", tmp_path / "conversations")
    monkeypatch.setattr(conversation_store, "upload_blob_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(conversation_store, "download_blob_json", lambda *args, **kwargs: {})

    actor = {"name": "Analyst", "email": "analyst@example.com", "actor_id": "oid-analyst", "tenant_id": "tenant-1", "source": "easy_auth"}
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
    assert detail["messages"][0]["message_id"].startswith("message_")
    assert detail["messages"][0]["trusted_identity"] is True
    assert detail["messages"][0]["actor"]["tenant_id"] == "tenant-1"
    assert detail["actors"][0]["email"] == "analyst@example.com"


def test_historical_conversation_message_id_is_workspace_scoped_and_untrusted_actor_is_not_attributed(monkeypatch) -> None:
    historical = {
        "conversation_id": "conv-history",
        "workspace_id": "ws-history",
        "messages": [
            {"role": "user", "text": "stable", "time": "2026-07-10T12:00:00Z", "actor": {"actor_id": "oid-user", "tenant_id": "tenant-1", "source": "easy_auth"}, "trusted_identity": True},
            {"role": "user", "text": "untrusted", "time": "2026-07-10T12:01:00Z", "actor": {"actor_id": "oid-spoof", "tenant_id": "tenant-1", "source": "client_actor"}, "trusted_identity": True},
        ],
    }
    monkeypatch.setattr(control_plane, "list_conversations", lambda _workspace_id: [{"conversation_id": "conv-history", "workspace_id": "ws-history"}])
    monkeypatch.setattr(control_plane, "get_conversation", lambda _conversation_id: historical)
    window = {"from": "2026-07-10T00:00:00Z", "to": "2026-07-11T00:00:00Z"}
    first, _ = control_plane._workspace_messages_for_chargeback("ws-history", window)
    second, _ = control_plane._workspace_messages_for_chargeback("ws-history", window)

    assert first[0]["message_id"] == second[0]["message_id"]
    assert first[0]["message_id"].startswith("legacy_message_")
    from backend.roi_service import member_chargeback
    result = member_chargeback("ws-history", window, runs=[], messages=first, tasks=[], memberships=[{"actor_id": "oid-user", "tenant_id": "tenant-1", "status": "active"}], prices=[], pseudonym_salt="salt")
    assert sum(group["activity_count"] for group in result["groups"]) == 1


def test_workspace_members_include_actor_usage(monkeypatch) -> None:
    monkeypatch.setenv("DF_WEB_PROXY_SECRET", "test-proxy-secret")
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
            ),
            "x-dataforge-proxy-secret": "test-proxy-secret",
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


def test_roi_and_chargeback_api_enforce_window_scope_and_member_comparison_role(monkeypatch) -> None:
    run = {
        "run_id": "run-roi-api",
        "workspace_id": "ws-roi-api",
        "actor": {"actor_id": "owner-oid", "tenant_id": "tenant-1", "email": "spoofed@example.com", "source": "easy_auth"},
        "trusted_identity": True,
        "completed_at": "2026-07-10T12:00:00Z",
        "models": [{"model": "gpt-5", "usage": {"input_tokens": 100, "output_tokens": 50}}],
    }
    monkeypatch.setattr(control_plane, "list_runs", lambda workspace_id=None: [run])
    monkeypatch.setattr(control_plane, "get_run", lambda run_id: run)
    monkeypatch.setattr(control_plane, "list_conversations", lambda workspace_id=None: [])
    monkeypatch.setattr(control_plane, "list_tasks", lambda workspace_id=None: [])
    monkeypatch.setattr(control_plane, "list_outcome_events", lambda workspace_id: [])
    monkeypatch.setattr(
        control_plane,
        "_current_workspace_members_for_chargeback",
        lambda workspace_id: [{"actor_id": "owner-oid", "tenant_id": "tenant-1", "email": "owner@example.com", "name": "Owner", "role": "owner", "status": "active"}],
        raising=False,
    )
    monkeypatch.setenv(
        "DF_ROI_PRICE_CONFIG_JSON",
        '[{"version":"test","model":"gpt-5","currency":"USD","unit":"per_1m_tokens","input_per_1m":2,"output_per_1m":8,"effective_from":"2026-07-01T00:00:00Z","effective_to":null,"source":"test"}]',
    )
    monkeypatch.setenv("DF_WEB_PROXY_SECRET", "test-proxy-secret")
    monkeypatch.setenv("DF_ROI_PSEUDONYM_SALT", "test-salt")
    monkeypatch.setattr(control_plane, "active_workspace_role", lambda _workspace_id, actor: "owner" if actor.get("actor_id") == "owner-oid" else "editor")
    client = TestClient(app)
    query = "?from=2026-07-10T00:00:00Z&to=2026-07-11T00:00:00Z"
    owner_headers = {"x-ms-client-principal": _principal([{"typ": "oid", "val": "owner-oid"}, {"typ": "tid", "val": "tenant-1"}, {"typ": "preferred_username", "val": "owner@example.com"}]), "x-dataforge-proxy-secret": "test-proxy-secret"}
    editor_headers = {"x-ms-client-principal": _principal([{"typ": "oid", "val": "editor-oid"}, {"typ": "tid", "val": "tenant-1"}]), "x-dataforge-proxy-secret": "test-proxy-secret"}

    roi = client.get(f"/api/workspaces/ws-roi-api/governance/roi{query}", headers=owner_headers)
    assert roi.status_code == 200, roi.text
    assert roi.json()["business_value"] is None
    assert roi.json()["cost"]["total"] == 0.0006
    assert "spoofed@example.com" not in roi.text

    invalid_window = client.get("/api/workspaces/ws-roi-api/governance/roi?from=2026-07-10&to=2026-07-11T00:00:00Z", headers=owner_headers)
    assert invalid_window.status_code == 400

    denied = client.get(f"/api/workspaces/ws-roi-api/governance/chargeback{query}", headers=editor_headers)
    assert denied.status_code == 403

    allowed = client.get(
        f"/api/workspaces/ws-roi-api/governance/chargeback{query}",
        headers=owner_headers,
    )
    assert allowed.status_code == 200
    assert allowed.json()["members"][0]["member"]["email"] == "owner@example.com"
    assert "spoofed@example.com" not in allowed.text


def test_workbench_audit_failure_blocks_write_but_never_allows_denial(monkeypatch) -> None:
    writes: list[str] = []
    monkeypatch.setattr(data_workbench, "require_workspace_permission", lambda *_args: "editor")
    monkeypatch.setattr(
        data_workbench,
        "record_audit_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AuditPersistenceError("offline")),
        raising=False,
    )
    monkeypatch.setattr(
        data_workbench,
        "create_workspace_file",
        lambda workspace_id, *_args: writes.append(workspace_id) or {"workspace_id": workspace_id},
    )
    client = TestClient(app, raise_server_exceptions=False)

    blocked = client.post("/api/workspaces/ws-audit/files", json={"name": "blocked.csv"})

    assert blocked.status_code == 503
    assert writes == []

    def deny(*_args):
        raise PermissionError("denied")

    monkeypatch.setattr(data_workbench, "require_workspace_permission", deny)
    denied = client.post("/api/workspaces/ws-audit/files", json={"name": "denied.csv"})

    assert denied.status_code == 403
    assert writes == []


def test_existing_workspace_upload_stops_before_mutation_when_audit_is_unavailable(monkeypatch) -> None:
    app_module = importlib.import_module("backend.app")
    writes: list[str] = []
    monkeypatch.setattr(app_module, "require_workspace_permission", lambda *_args: "editor")
    monkeypatch.setattr(
        app_module,
        "record_audit_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AuditPersistenceError("offline")),
    )
    monkeypatch.setattr(
        app_module,
        "create_workspace_upload_job",
        lambda **kwargs: writes.append(str(kwargs.get("requested_workspace_id"))) or {},
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/api/upload",
        data={"workspace_id": "ws-audit"},
        files={"file": ("data.csv", b"a,b\n1,2\n", "text/csv")},
    )

    assert response.status_code == 503
    assert writes == []


def test_new_workspace_upload_reserves_and_audits_before_any_store_mutation(tmp_path, monkeypatch) -> None:
    app_module = importlib.import_module("backend.app")
    workspace_root = tmp_path / "workspaces"
    monkeypatch.setattr(workspace_store, "WORKSPACES", workspace_root)
    monkeypatch.setattr(app_module, "reserve_workspace_id", lambda _name=None: "upload-reserved-123")
    monkeypatch.setattr(
        app_module,
        "record_audit_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AuditPersistenceError("offline")),
    )
    monkeypatch.setattr(app_module, "create_workspace_upload_job", workspace_store.create_workspace_upload_job)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/api/upload",
        files={"file": ("data.csv", b"a,b\n1,2\n", "text/csv")},
    )

    assert response.status_code == 503
    assert not workspace_root.exists()


def test_reserved_workspace_id_is_passed_to_store_without_append_lookup(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(workspace_store, "WORKSPACES", tmp_path / "workspaces")
    monkeypatch.setattr(workspace_store, "_persist_workspace_bundle", lambda **_kwargs: {"mode": "test"})
    reserved = workspace_store.reserve_workspace_id("New workspace")

    result = workspace_store.create_workspace_upload_job(
        files=[{"filename": "data.csv", "content": b"a,b\n1,2\n", "content_type": "text/csv"}],
        name="New workspace",
        reserved_workspace_id=reserved,
    )

    assert result["workspace_id"] == reserved
    assert (workspace_store.WORKSPACES / reserved / "raw_docs" / "data.csv").exists()


def test_workspace_store_rejects_traversal_before_creating_paths(tmp_path, monkeypatch) -> None:
    workspace_root = tmp_path / "workspaces"
    monkeypatch.setattr(workspace_store, "WORKSPACES", workspace_root)

    with pytest.raises(ValueError, match="requested_workspace_id"):
        workspace_store.create_workspace_upload_job(
            files=[{"filename": "data.csv", "content": b"a,b\n1,2\n"}],
            requested_workspace_id="../escape",
        )

    assert not workspace_root.exists()


def test_governance_audit_api_is_owner_admin_only_and_truthfully_read_only(monkeypatch) -> None:
    actor = {"actor_id": "owner-oid", "tenant_id": "tenant-1", "source": "easy_auth"}
    reads: list[tuple[str, int, str | None]] = []
    denied_events: list[tuple] = []
    monkeypatch.setattr(control_plane, "actor_from_request", lambda *_args, **_kwargs: actor)
    monkeypatch.setattr(control_plane, "is_trusted_tenant_identity", lambda _actor: True)
    monkeypatch.setattr(control_plane, "active_workspace_role", lambda *_args: "owner")
    monkeypatch.setattr(
        control_plane,
        "list_audit_events",
        lambda workspace_id, *, limit, cursor=None: reads.append((workspace_id, limit, cursor))
        or {
            "workspace_id": workspace_id,
            "events": [],
            "count": 0,
            "revision": 0,
            "has_more": False,
            "next_cursor": None,
            "permissions": {"can_read": True, "can_update": False, "can_delete": False},
        },
        raising=False,
    )
    monkeypatch.setattr(control_plane, "record_audit_event", lambda *args, **kwargs: denied_events.append((args, kwargs)), raising=False)
    client = TestClient(app)

    allowed = client.get("/api/workspaces/ws-audit/governance/audit-events?limit=25&cursor=cursor-1")

    assert allowed.status_code == 200, allowed.text
    assert reads == [("ws-audit", 25, "cursor-1")]
    assert allowed.json()["permissions"] == {
        "role": "owner",
        "can_read": True,
        "can_update": False,
        "can_delete": False,
    }

    monkeypatch.setattr(control_plane, "active_workspace_role", lambda *_args: "viewer")
    denied = client.get("/api/workspaces/ws-audit/governance/audit-events")

    assert denied.status_code == 403
    assert reads == [("ws-audit", 25, "cursor-1")]
    assert denied_events[-1][0][1] == "member.read"
    assert denied_events[-1][1]["result"] == "denied"


def test_durable_task_create_start_complete_and_cancel_are_audited(tmp_path, monkeypatch) -> None:
    events: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(task_store, "TASK_DIR", tmp_path / "tasks")
    monkeypatch.setattr(task_store, "blob_configured", lambda: False)
    monkeypatch.setattr(task_store, "download_blob_json", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(task_store, "download_blob_json_strict", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(task_store, "list_blob_json", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(task_store, "list_blob_json_strict", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(task_store, "record_audit_event", lambda *args, **kwargs: events.append((args, kwargs)) or {}, raising=False)
    actor = {"actor_id": "owner-oid", "tenant_id": "tenant-1", "source": "easy_auth"}

    completed = task_store.create_task(
        {"workspace_id": "ws-audit", "task_type": "analysis.run", "action": "analysis.run"},
        actor,
    )
    task_store.claim_task(completed["task_id"], "worker")
    task_store.update_task(completed["task_id"], status="completed", result={"run_id": "run-1"})
    cancelled = task_store.create_task(
        {"workspace_id": "ws-audit", "task_type": "artifact.generate", "action": "artifact.generate"},
        actor,
    )
    task_store.request_cancel(cancelled["task_id"])

    actions = [args[1] for args, _kwargs in events]
    assert actions == [
        "task.create", "task.start", "task.transition", "task.complete",
        "task.create", "task.transition", "task.cancel",
    ]
    assert all(args[2]["resource_type"] == "task" for args, _kwargs in events)
    assert events[2][1]["reason_code"] == "transition_attempt"
    assert events[3][1]["correlation"]["task_id"] == completed["task_id"]
    assert events[3][1]["reason_code"] == "task_completed"
    assert events[-1][1]["reason_code"] == "task_cancelled"


def test_task_create_fails_before_persistence_when_required_audit_is_unavailable(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(task_store, "TASK_DIR", tmp_path / "tasks")
    monkeypatch.setattr(task_store, "blob_configured", lambda: False)
    monkeypatch.setattr(
        task_store,
        "record_audit_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AuditPersistenceError("offline")),
        raising=False,
    )

    with pytest.raises(task_store.TaskPersistenceError, match="audit"):
        task_store.create_task(
            {"workspace_id": "ws-audit", "task_type": "analysis.run", "action": "analysis.run"},
            {"actor_id": "owner-oid", "tenant_id": "tenant-1", "source": "easy_auth"},
        )

    assert not task_store.TASK_DIR.exists()


def test_experiment_promotion_hook_has_truthful_attempt_success_and_failure_phases(monkeypatch) -> None:
    captured: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(control_plane, "record_audit_event", lambda *args, **kwargs: captured.append((args, kwargs)) or {"event_id": "event-1"}, raising=False)

    attempt = control_plane.audit_experiment_promotion(
        "ws-audit",
        {"actor_id": "owner-oid", "tenant_id": "tenant-1", "source": "easy_auth"},
        "experiment-v2",
        phase="attempt",
        request_id="req-promote",
    )
    control_plane.audit_experiment_promotion("ws-audit", {}, "experiment-v2", phase="succeeded")
    control_plane.audit_experiment_promotion("ws-audit", {}, "experiment-v2", phase="failed")

    assert attempt == {"event_id": "event-1"}
    assert captured[0][0][1] == "experiment.promote"
    assert captured[0][0][2] == {
        "workspace_id": "ws-audit",
        "resource_type": "experiment",
        "resource_id": "experiment-v2",
    }
    assert captured[0][1]["correlation"] == {
        "request_id": "req-promote",
        "experiment_version_id": "experiment-v2",
    }
    assert captured[0][1]["result"] == "allowed"
    assert captured[0][1]["reason_code"] == "promotion_attempt"
    assert captured[1][1]["result"] == "allowed"
    assert captured[1][1]["reason_code"] == "experiment_promoted"
    assert captured[2][1]["result"] == "failed"
    assert captured[2][1]["reason_code"] == "promotion_failed"

    with pytest.raises(ValueError, match="phase"):
        control_plane.audit_experiment_promotion("ws-audit", {}, "experiment-v2", phase="unknown")


@pytest.mark.parametrize(
    ("path", "payload", "tool_name"),
    [
        ("/api/render-pdf-report", {"proposal": {}}, "render_pdf_report"),
        ("/api/generate-image", {"prompt": "concept"}, "generate_image"),
        ("/api/narrate-summary", {"text": "summary"}, "narrate_summary"),
    ],
)
def test_artifact_mutation_endpoints_require_workspace_permission_and_audit(monkeypatch, path, payload, tool_name) -> None:
    app_module = importlib.import_module("backend.app")
    calls: list[str] = []
    monkeypatch.setattr(app_module, tool_name, lambda *_args, **_kwargs: calls.append(tool_name) or {})
    client = TestClient(app, raise_server_exceptions=False)

    missing = client.post(path, json=payload)
    assert missing.status_code == 422

    unauthenticated = client.post(path, json={**payload, "workspace_id": "ws-artifacts"})
    assert unauthenticated.status_code == 401
    assert calls == []

    monkeypatch.setattr(app_module, "is_trusted_tenant_identity", lambda _actor: True)
    monkeypatch.setattr(
        app_module,
        "require_workspace_permission",
        lambda *_args: (_ for _ in ()).throw(PermissionError("denied")),
    )
    denied = client.post(path, json={**payload, "workspace_id": "ws-artifacts"})
    assert denied.status_code == 403
    assert calls == []

    monkeypatch.setattr(app_module, "require_workspace_permission", lambda *_args: "editor")
    monkeypatch.setattr(
        app_module,
        "record_audit_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AuditPersistenceError("offline")),
    )
    unavailable = client.post(path, json={**payload, "workspace_id": "ws-artifacts"})
    assert unavailable.status_code == 503
    assert calls == []


@pytest.mark.parametrize(
    ("path", "payload", "tool_name", "resource_id"),
    [
        ("/api/render-pdf-report", {"proposal": {}}, "render_pdf_report", "pdf"),
        ("/api/generate-image", {"prompt": "concept"}, "generate_image", "image"),
        ("/api/narrate-summary", {"text": "summary"}, "narrate_summary", "narration"),
    ],
)
def test_artifact_mutation_endpoints_audit_authorized_workspace_before_running(
    monkeypatch, path, payload, tool_name, resource_id
) -> None:
    app_module = importlib.import_module("backend.app")
    events: list[tuple[tuple, dict]] = []
    calls: list[str] = []
    monkeypatch.setattr(app_module, "is_trusted_tenant_identity", lambda _actor: True)
    monkeypatch.setattr(app_module, "require_workspace_permission", lambda *_args: "editor")
    monkeypatch.setattr(app_module, "record_audit_event", lambda *args, **kwargs: events.append((args, kwargs)) or {})
    monkeypatch.setattr(app_module, tool_name, lambda *_args, **_kwargs: calls.append(tool_name) or {"mode": "test"})
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(path, json={**payload, "workspace_id": "ws-artifacts"})

    assert response.status_code == 200, response.text
    assert calls == [tool_name]
    assert len(events) == 1
    assert events[0][0][1] == "artifact.generate"
    assert events[0][0][2] == {
        "workspace_id": "ws-artifacts",
        "resource_type": "artifact",
        "resource_id": resource_id,
    }
