from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import shutil

import pytest

import backend.task_store as task_store


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "workspace_id": "ws-tasks",
        "task_type": "artifact.generate",
        "action": "artifact.generate",
        "result": {"artifact_job_id": "artifact_job_123"},
    }
    payload.update(overrides)
    return payload


def _actor() -> dict[str, str]:
    return {"email": "owner@contoso.com", "actor_id": "oid-owner", "access_token": "never-persist"}


def _configure_store(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(task_store, "TASK_DIR", tmp_path / "tasks")
    monkeypatch.setattr(task_store, "blob_configured", lambda: False)
    monkeypatch.setattr(task_store, "download_blob_json", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(task_store, "list_blob_json", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(task_store, "upload_blob_json", lambda *_args, **_kwargs: {})


def test_task_survives_local_store_loss_via_blob(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    remote: dict[str, dict] = {}
    monkeypatch.setattr(task_store, "blob_configured", lambda: True)
    monkeypatch.setattr(task_store, "upload_blob_json", lambda name, value: remote.__setitem__(name, dict(value)) or {})
    monkeypatch.setattr(task_store, "download_blob_json", lambda name: remote.get(name))

    task = task_store.create_task(_payload(), _actor())
    shutil.rmtree(task_store.TASK_DIR)

    recovered = task_store.get_task(task["task_id"])

    assert recovered["workspace_id"] == "ws-tasks"
    assert recovered["actor"] == {"email": "owner@contoso.com", "actor_id": "oid-owner", "name": "owner", "source": "ui_context"}
    assert "access_token" not in recovered["actor"]


def test_only_one_worker_claims_task(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    task = task_store.create_task(_payload(), _actor())

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: task_store.claim_task(task["task_id"], "worker"), range(2)))

    assert sum(item is not None for item in claims) == 1
    assert task_store.get_task(task["task_id"])["status"] == "running"


def test_progress_is_monotonic_and_retry_creates_new_attempt(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    task = task_store.create_task(_payload(), _actor())
    task_store.claim_task(task["task_id"], "worker")

    task_store.update_task(task["task_id"], status="running", progress=60)
    with pytest.raises(ValueError, match="progress"):
        task_store.update_task(task["task_id"], status="running", progress=59)
    failed = task_store.update_task(task["task_id"], status="failed", error={"message": "provider unavailable"})
    retried = task_store.retry_task(failed["task_id"], _actor())

    assert retried["task_id"] != failed["task_id"]
    assert retried["retry_of"] == failed["task_id"]
    assert retried["attempt"] == 2
    assert retried["status"] == "queued"


def test_cancel_does_not_rewrite_completed_task(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    task = task_store.create_task(_payload(), _actor())
    task_store.claim_task(task["task_id"], "worker")
    completed = task_store.update_task(task["task_id"], status="completed", result={"artifact_job_id": "artifact_job_123"})

    cancelled = task_store.request_cancel(completed["task_id"])

    assert cancelled["status"] == "completed"
    assert cancelled["result"] == {"artifact_job_id": "artifact_job_123"}
