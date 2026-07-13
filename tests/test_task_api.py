from __future__ import annotations

import json
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

import backend.task_store as task_store
import backend.workspace_authz as workspace_authz
from backend.app import app


def _configure_store(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(task_store, "TASK_DIR", tmp_path / "tasks")
    monkeypatch.setattr(task_store, "blob_configured", lambda: False)
    monkeypatch.setattr(task_store, "download_blob_json", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(task_store, "list_blob_json", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(task_store, "upload_blob_json", lambda *_args, **_kwargs: {})


def _headers(email: str) -> dict[str, str]:
    return {"x-dataforge-actor": quote(json.dumps({"email": email, "actor_id": f"oid-{email}"}))}


def _task() -> dict[str, object]:
    return task_store.create_task(
        {"workspace_id": "ws-private", "task_type": "artifact.generate", "action": "artifact.generate"},
        {"email": "owner@contoso.com", "actor_id": "oid-owner"},
    )


def test_non_member_cannot_read_task_after_workspace_is_resolved(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    task = _task()
    monkeypatch.setenv("DF_WORKSPACE_RBAC_ENFORCED", "1")
    monkeypatch.setenv("DF_WORKSPACE_OWNER_EMAIL", "owner@contoso.com")
    monkeypatch.setattr(workspace_authz, "_load_workspace_meta", lambda _workspace_id: {})
    monkeypatch.setattr(workspace_authz, "workspace_role", lambda _workspace_id, actor: "owner" if actor.get("email") == "owner@contoso.com" else None)

    response = TestClient(app).get(f"/api/tasks/{task['task_id']}", headers=_headers("outsider@contoso.com"))

    assert response.status_code == 403


def test_task_list_and_retry_use_workspace_scoped_actions(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    task = _task()
    task_store.claim_task(task["task_id"], "worker")
    task_store.update_task(task["task_id"], status="failed", error={"message": "failed"})
    monkeypatch.setenv("DF_WORKSPACE_RBAC_ENFORCED", "1")
    monkeypatch.setenv("DF_WORKSPACE_OWNER_EMAIL", "owner@contoso.com")
    monkeypatch.setattr(workspace_authz, "_load_workspace_meta", lambda _workspace_id: {})
    monkeypatch.setattr(workspace_authz, "workspace_role", lambda _workspace_id, actor: "owner" if actor.get("email") == "owner@contoso.com" else None)
    client = TestClient(app)

    listed = client.get("/api/workspaces/ws-private/tasks", headers=_headers("owner@contoso.com"))
    retried = client.post(f"/api/tasks/{task['task_id']}/retry", headers=_headers("owner@contoso.com"))
    denied = client.post(f"/api/tasks/{task['task_id']}/cancel", headers=_headers("outsider@contoso.com"))

    assert listed.status_code == 200
    assert listed.json()["tasks"][0]["task_id"] == task["task_id"]
    assert retried.status_code == 202
    assert retried.json()["retry_of"] == task["task_id"]
    assert retried.json()["attempt"] == 2
    assert denied.status_code == 403


def test_workspace_task_list_rejects_member_of_a_different_workspace(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    _task()
    monkeypatch.setenv("DF_WORKSPACE_RBAC_ENFORCED", "1")
    monkeypatch.setattr(
        workspace_authz,
        "workspace_role",
        lambda workspace_id, actor: "owner" if workspace_id == "ws-other" and actor.get("email") == "owner@contoso.com" else None,
    )

    response = TestClient(app).get("/api/workspaces/ws-private/tasks", headers=_headers("owner@contoso.com"))

    assert response.status_code == 403
