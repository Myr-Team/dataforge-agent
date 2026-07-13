from __future__ import annotations

import shutil

import pytest
from fastapi.testclient import TestClient

import backend.control_plane as control_plane
import backend.task_store as task_store
from backend.app import app
from backend.blob_store import BlobJsonReadError


def _configure_task_store(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(task_store, "TASK_DIR", tmp_path / "tasks")
    monkeypatch.setattr(task_store, "blob_configured", lambda: False)
    monkeypatch.setattr(task_store, "download_blob_json", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(task_store, "download_blob_json_strict", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(task_store, "list_blob_json", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(task_store, "list_blob_json_strict", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(task_store, "upload_blob_json", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(control_plane, "list_runs", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(control_plane, "list_artifact_jobs", lambda *_args, **_kwargs: [])


def test_workspace_artifacts_returns_503_for_strict_task_list_failure(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_task_store(tmp_path, monkeypatch)
    remote: dict[str, dict] = {}
    monkeypatch.setattr(task_store, "blob_configured", lambda: True)
    monkeypatch.setattr(task_store, "upload_blob_json", lambda name, value: remote.__setitem__(name, dict(value)) or {})
    task_store.create_task(
        {"workspace_id": "ws-artifacts", "task_type": "artifact.generate", "action": "artifact.generate"},
        {"email": "owner@contoso.com"},
    )
    shutil.rmtree(task_store.TASK_DIR)
    monkeypatch.setattr(
        task_store,
        "list_blob_json_strict",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(BlobJsonReadError("blob list unavailable")),
    )

    response = TestClient(app, raise_server_exceptions=False).get("/api/workspaces/ws-artifacts/artifacts")

    assert response.status_code == 503
    assert response.json()["detail"] == "Task persistence is unavailable"


def test_workspace_artifacts_keeps_tasks_in_success_response(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_task_store(tmp_path, monkeypatch)
    task = task_store.create_task(
        {"workspace_id": "ws-artifacts", "task_type": "artifact.generate", "action": "artifact.generate"},
        {"email": "owner@contoso.com"},
    )

    response = TestClient(app).get("/api/workspaces/ws-artifacts/artifacts")

    assert response.status_code == 200
    assert response.json()["tasks"] == [task]
