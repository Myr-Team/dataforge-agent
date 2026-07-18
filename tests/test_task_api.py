from __future__ import annotations

import shutil

import pytest
from fastapi.testclient import TestClient

import backend.task_store as task_store
from backend.app import app
from backend.blob_store import BlobJsonReadError
from auth_fixtures import active_member, install_workspace_memberships, trusted_headers


def _configure_store(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(task_store, "TASK_DIR", tmp_path / "tasks")
    monkeypatch.setattr(task_store, "blob_configured", lambda: False)
    monkeypatch.setattr(task_store, "download_blob_json", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(task_store, "download_blob_json_strict", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(task_store, "list_blob_json", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(task_store, "list_blob_json_strict", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(task_store, "upload_blob_json", lambda *_args, **_kwargs: {})


def _headers(actor_id: str, tenant_id: str = "task-tenant") -> dict[str, str]:
    return trusted_headers(actor_id=actor_id, tenant_id=tenant_id, email=f"{actor_id}@contoso.com")


def _install_memberships(monkeypatch: pytest.MonkeyPatch, members: list[dict[str, str]]) -> None:
    install_workspace_memberships(monkeypatch, {"ws-private": members})


def _task() -> dict[str, object]:
    return task_store.create_task(
        {"workspace_id": "ws-private", "task_type": "artifact.generate", "action": "artifact.generate"},
        {"email": "owner@contoso.com", "actor_id": "oid-owner"},
    )


def test_non_member_cannot_read_task_after_workspace_is_resolved(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    task = _task()
    _install_memberships(monkeypatch, [active_member("owner-oid", "task-tenant", "owner")])

    response = TestClient(app).get(f"/api/tasks/{task['task_id']}", headers=_headers("outsider-oid"))

    assert response.status_code == 403


def test_task_list_and_unsupported_retry_use_workspace_scoped_actions(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    task = _task()
    task_store.claim_task(task["task_id"], "worker")
    task_store.update_task(task["task_id"], status="failed", error={"message": "failed"})
    _install_memberships(monkeypatch, [active_member("owner-oid", "task-tenant", "owner")])
    client = TestClient(app)

    listed = client.get("/api/workspaces/ws-private/tasks", headers=_headers("owner-oid"))
    retried = client.post(f"/api/tasks/{task['task_id']}/retry", headers=_headers("owner-oid"))
    denied = client.post(f"/api/tasks/{task['task_id']}/cancel", headers=_headers("outsider-oid"))

    assert listed.status_code == 200
    assert listed.json()["tasks"][0]["task_id"] == task["task_id"]
    assert retried.status_code == 409
    assert retried.json()["detail"] == "Task retry is not supported"
    assert denied.status_code == 403


def test_workspace_task_list_rejects_member_of_a_different_workspace(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    _task()
    install_workspace_memberships(
        monkeypatch,
        {"ws-other": [active_member("owner-oid", "task-tenant", "owner")]},
    )

    response = TestClient(app).get("/api/workspaces/ws-private/tasks", headers=_headers("owner-oid"))

    assert response.status_code == 403


def test_task_get_and_workspace_list_return_503_for_strict_blob_read_failures(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    remote: dict[str, dict] = {}
    monkeypatch.setattr(task_store, "blob_configured", lambda: True)
    monkeypatch.setattr(task_store, "upload_blob_json", lambda name, value: remote.__setitem__(name, dict(value)) or {})
    task = _task()
    _install_memberships(monkeypatch, [active_member("owner-oid", "task-tenant", "owner")])
    shutil.rmtree(task_store.TASK_DIR)
    monkeypatch.setattr(task_store, "download_blob_json", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        task_store,
        "download_blob_json_strict",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(BlobJsonReadError("transport unavailable")),
        raising=False,
    )
    monkeypatch.setattr(
        task_store,
        "list_blob_json_strict",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(BlobJsonReadError("transport unavailable")),
        raising=False,
    )
    client = TestClient(app, raise_server_exceptions=False)

    detail = client.get(f"/api/tasks/{task['task_id']}", headers=_headers("owner-oid"))
    listed = client.get("/api/workspaces/ws-private/tasks", headers=_headers("owner-oid"))

    assert detail.status_code == 503
    assert listed.status_code == 503
