from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import multiprocessing
import os
import shutil
import threading
import time
from pathlib import Path

import pytest

import backend.task_store as task_store


def _claim_from_separate_process(task_dir: str, task_id: str, start, results) -> None:
    import backend.task_store as child_store

    child_store.TASK_DIR = Path(task_dir)
    child_store.blob_configured = lambda: False
    start.wait(10)
    results.put(child_store.claim_task(task_id, "process-worker") is not None)


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


def test_only_one_separate_process_claims_a_local_task(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    task = task_store.create_task(_payload(), _actor())
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    workers = [context.Process(target=_claim_from_separate_process, args=(str(task_store.TASK_DIR), task["task_id"], start, results)) for _ in range(2)]
    for worker in workers:
        worker.start()
    start.set()
    for worker in workers:
        worker.join(20)

    assert all(worker.exitcode == 0 for worker in workers)
    assert [results.get(timeout=5) for _ in workers].count(True) == 1
    assert task_store.get_task(task["task_id"])["status"] == "running"


def test_stale_local_claim_lock_is_recovered_but_terminal_task_is_not_claimed(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    task = task_store.create_task(_payload(), _actor())
    lock_path = task_store._claim_lock_path(task["task_id"])
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("crashed", encoding="utf-8")
    os.utime(lock_path, (time.time() - 3600, time.time() - 3600))

    assert task_store.claim_task(task["task_id"], "recovery-worker") is not None
    task_store.update_task(task["task_id"], status="completed")
    assert task_store.claim_task(task["task_id"], "late-worker") is None


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


def test_queued_task_can_be_marked_failed_for_persistence_compensation(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    task = task_store.create_task(_payload(), _actor())

    compensated = task_store.update_task(task["task_id"], status="failed", error={"category": "artifact_job", "code": "persistence_failed"})

    assert compensated["status"] == "failed"
    assert compensated["error"] == {"category": "artifact_job", "code": "persistence_failed"}


def test_blob_write_failure_does_not_report_or_leave_a_local_task(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    monkeypatch.setattr(task_store, "blob_configured", lambda: True)
    monkeypatch.setattr(task_store, "upload_blob_json", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("blob unavailable")))

    with pytest.raises(RuntimeError, match="durable"):
        task_store.create_task(_payload(), _actor())

    assert not task_store.TASK_DIR.exists()


def test_blob_update_failure_preserves_remote_task_after_local_loss(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    remote: dict[str, dict] = {}
    monkeypatch.setattr(task_store, "blob_configured", lambda: True)
    monkeypatch.setattr(task_store, "upload_blob_json", lambda name, value: remote.__setitem__(name, dict(value)) or {})
    monkeypatch.setattr(task_store, "download_blob_json", lambda name: remote.get(name))
    task = task_store.create_task(_payload(), _actor())
    monkeypatch.setattr(task_store, "claim_blob_json", lambda name, **kwargs: remote.__setitem__(name, {**remote[name], **kwargs["changes"]}) or remote[name])
    task_store.claim_task(task["task_id"], "worker")
    monkeypatch.setattr(task_store, "compare_and_swap_blob_json", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("blob unavailable")), raising=False)

    with pytest.raises(RuntimeError, match="durable"):
        task_store.update_task(task["task_id"], status="failed", error={"category": "worker", "code": "down"})
    shutil.rmtree(task_store.TASK_DIR)

    assert task_store.get_task(task["task_id"])["status"] == "running"


def test_task_payload_allowlist_never_persists_or_returns_sensitive_values(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    secret = "AccountKey=super-secret;Password=hunter2"
    task = task_store.create_task(
        _payload(
            result={
                "artifact_job_id": "artifact_job_123",
                "artifact_url": "/api/artifacts/a.pdf",
                "message": secret,
                "debug": {"token": "bearer-secret"},
                "url": "https://example.invalid/a?sig=secret-sas",
            },
            input={"connection_string": secret},
        ),
        {**_actor(), "debug": secret},
    )
    task_store.claim_task(task["task_id"], "worker")
    updated = task_store.update_task(task["task_id"], status="failed", error={"category": "connector", "code": "failed", "message": secret, "input": secret})
    serialized = (task_store.TASK_DIR / f"{task['task_id']}.json").read_text(encoding="utf-8")

    assert updated["result"] == {"artifact_job_id": "artifact_job_123", "artifact_url": "/api/artifacts/a.pdf"}
    assert updated["error"] == {"category": "connector", "code": "failed"}
    assert secret not in serialized
    assert "bearer-secret" not in str(updated)
    assert "secret-sas" not in str(updated)


def test_blob_cas_reloads_completed_instead_of_overwriting_it_with_cancel(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    remote: dict[str, dict] = {}
    monkeypatch.setattr(task_store, "blob_configured", lambda: True)
    monkeypatch.setattr(task_store, "upload_blob_json", lambda name, value: remote.__setitem__(name, dict(value)) or {})
    monkeypatch.setattr(task_store, "download_blob_json", lambda name: remote.get(name))
    task = task_store.create_task(_payload(), _actor())

    def compare_and_swap(name, *, expected_revision, changes):
        current = remote[name]
        remote[name] = {**current, "status": "completed", "revision": expected_revision + 1}
        return None

    monkeypatch.setattr(task_store, "compare_and_swap_blob_json", compare_and_swap, raising=False)
    cancelled = task_store.request_cancel(task["task_id"])

    assert cancelled["status"] == "completed"


def test_blob_conditional_claim_allows_only_one_worker(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    remote: dict[str, dict] = {}
    lock = threading.Lock()
    monkeypatch.setattr(task_store, "blob_configured", lambda: True)
    monkeypatch.setattr(task_store, "upload_blob_json", lambda name, value: remote.__setitem__(name, dict(value)) or {})
    monkeypatch.setattr(task_store, "download_blob_json", lambda name: remote.get(name))
    task = task_store.create_task(_payload(), _actor())

    def conditional_claim(name, *, expected_status, changes):
        with lock:
            current = remote[name]
            if current.get("status") != expected_status:
                return None
            remote[name] = {**current, **changes}
            return dict(remote[name])

    monkeypatch.setattr(task_store, "claim_blob_json", conditional_claim)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: task_store.claim_task(task["task_id"], "blob-worker"), range(2)))

    assert sum(item is not None for item in claims) == 1
    assert remote[next(iter(remote))]["status"] == "running"
