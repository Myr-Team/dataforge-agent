from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import base64
import json
import multiprocessing
from pathlib import Path
from urllib.parse import quote

import pytest

import backend.artifact_jobs as artifact_jobs
import backend.artifact_registry as artifact_registry
import backend.app as app_module
import backend.task_store as task_store
import backend.tools.render_pdf as render_pdf
import backend.workspace_authz as workspace_authz
from backend.app import app
from backend.artifact_jobs import ArtifactJobPersistenceError
from backend.blob_store import BlobJsonReadError
from backend.schemas import ProduceRequest
from backend.task_store import TaskPersistenceError
from fastapi.testclient import TestClient


def _run_local_artifact_job_from_process(task_dir: str, job_dir: str, job_id: str, start, job_claim_barrier, results) -> None:
    import backend.artifact_jobs as child_jobs
    import backend.task_store as child_tasks

    child_tasks.TASK_DIR = Path(task_dir)
    child_tasks.blob_configured = lambda: False
    child_tasks.download_blob_json = lambda *_args, **_kwargs: None
    child_tasks.list_blob_json = lambda *_args, **_kwargs: []
    child_tasks.upload_blob_json = lambda *_args, **_kwargs: {}
    child_jobs.ARTIFACT_JOB_DIR = Path(job_dir)
    child_jobs.blob_configured = lambda: False
    child_jobs.download_blob_json = lambda *_args, **_kwargs: None
    child_jobs.list_blob_json = lambda *_args, **_kwargs: []
    child_jobs.upload_blob_json = lambda *_args, **_kwargs: {}
    child_jobs._producer_payload = lambda _job: {}
    original_get_job = child_jobs.get_artifact_job
    first_get = True

    def synchronized_get_job(current_job_id: str):
        nonlocal first_get
        value = original_get_job(current_job_id)
        if first_get:
            first_get = False
            job_claim_barrier.wait(10)
        return value

    child_jobs.get_artifact_job = synchronized_get_job
    child_jobs._produce = lambda _payload: results.put(("produce", job_id)) or {
        "artifact_urls": {"pdf": "/api/artifacts/process.pdf"},
        "pdf": {"artifact_url": "/api/artifacts/process.pdf"},
    }
    start.wait(10)
    result = child_jobs.run_artifact_job(job_id)
    results.put(("status", result["status"]))


def _configure_store(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(artifact_jobs, "ARTIFACT_JOB_DIR", tmp_path / "artifact-jobs")
    monkeypatch.setattr(artifact_jobs, "download_blob_json", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(artifact_jobs, "download_blob_json_strict", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(artifact_jobs, "upload_blob_json", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(artifact_jobs, "blob_configured", lambda: False, raising=False)
    monkeypatch.setattr(artifact_jobs, "list_blob_json", lambda *_args, **_kwargs: [], raising=False)
    monkeypatch.setattr(
        artifact_jobs,
        "get_run",
        lambda run_id: {
            "run_id": run_id,
            "workspace_id": "ws-artifacts",
            "artifact": {"feasibility": {"verdict": "conditional"}},
        },
    )


def _configure_task_store(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(task_store, "TASK_DIR", tmp_path / "tasks")
    monkeypatch.setattr(task_store, "blob_configured", lambda: False)
    monkeypatch.setattr(task_store, "download_blob_json", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(task_store, "download_blob_json_strict", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(task_store, "list_blob_json", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(task_store, "list_blob_json_strict", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(task_store, "upload_blob_json", lambda *_args, **_kwargs: {})


def _request(**overrides) -> dict:
    payload = {
        "workspace_id": "ws-artifacts",
        "conversation_id": "run-v1",
        "kinds": ["pdf", "concept_image"],
        "feasibility": {"verdict": "conditional", "dimensions": [{"name": "asset_data", "score": 3}]},
        "answer": {"text": "Sensitive analysis text should not be copied into the job record."},
    }
    payload.update(overrides)
    return payload


def _authorized_headers(monkeypatch) -> dict[str, str]:
    monkeypatch.setenv("DF_WEB_PROXY_SECRET", "server-only-secret")
    monkeypatch.setattr(
        workspace_authz,
        "_load_workspace_meta",
        lambda _workspace_id: {
            "workspace_members": [
                {
                    "actor_id": "artifact-owner-oid",
                    "tenant_id": "artifact-tenant",
                    "role": "owner",
                    "status": "active",
                }
            ]
        },
    )
    principal = {
        "userDetails": "owner@contoso.com",
        "claims": [
            {"typ": "preferred_username", "val": "owner@contoso.com"},
            {"typ": "oid", "val": "artifact-owner-oid"},
            {"typ": "tid", "val": "artifact-tenant"},
        ],
    }
    encoded = base64.urlsafe_b64encode(json.dumps(principal).encode("utf-8")).decode("ascii")
    return {
        "x-ms-client-principal": encoded,
        "x-dataforge-proxy-secret": "server-only-secret",
    }


def test_job_state_survives_store_reload_without_copying_analysis_payload(tmp_path: Path, monkeypatch) -> None:
    _configure_store(tmp_path, monkeypatch)

    job = artifact_jobs.create_artifact_job(
        _request(),
        actor={"email": "owner@contoso.com", "actor_id": "oid-owner"},
        idempotency_key="request-1",
    )
    reloaded = artifact_jobs.get_artifact_job(job["job_id"])

    assert reloaded["status"] == "queued"
    assert reloaded["source_run_id"] == "run-v1"
    assert reloaded["requested_kinds"] == ["pdf", "concept_image"]
    assert reloaded["plan_version"].startswith("V")
    assert "feasibility" not in reloaded
    assert "answer" not in reloaded


def test_explicit_source_run_id_wins_over_legacy_conversation_id(tmp_path: Path, monkeypatch) -> None:
    _configure_store(tmp_path, monkeypatch)

    job = artifact_jobs.create_artifact_job(
        _request(conversation_id="legacy-conversation", source_run_id="run-canonical"),
        actor={},
    )

    assert job["source_run_id"] == "run-canonical"


def test_produce_request_preserves_source_run_id() -> None:
    request = ProduceRequest(
        workspace_id="ws-artifacts",
        source_run_id="run-canonical",
        kinds=["pdf"],
        feasibility={"verdict": "conditional"},
    )

    assert request.model_dump()["source_run_id"] == "run-canonical"


def test_idempotency_key_reuses_non_terminal_job(tmp_path: Path, monkeypatch) -> None:
    _configure_store(tmp_path, monkeypatch)

    first = artifact_jobs.create_artifact_job(_request(), actor={}, idempotency_key="same-request")
    second = artifact_jobs.create_artifact_job(_request(), actor={}, idempotency_key="same-request")

    assert first["job_id"] == second["job_id"]


def test_partial_generation_keeps_completed_artifacts(tmp_path: Path, monkeypatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    job = artifact_jobs.create_artifact_job(_request(), actor={}, idempotency_key="partial-request")
    monkeypatch.setattr(
        artifact_jobs,
        "_producer_payload",
        lambda _job: _request(),
    )
    monkeypatch.setattr(
        artifact_jobs,
        "_produce",
        lambda _payload: {
            "artifact_urls": {"pdf": "/api/artifacts/project-v1.pdf"},
            "pdf": {"artifact_url": "/api/artifacts/project-v1.pdf", "bytes": 1200},
            "concept_image": {"mode": "concept_image_error", "error": "provider timeout"},
            "warnings": [
                {
                    "kind": "concept_image",
                    "message": "概念图生成失败，建议书已生成。",
                    "error": "provider timeout",
                }
            ],
        },
    )

    result = artifact_jobs.run_artifact_job(job["job_id"])

    assert result["status"] == "partial"
    assert result["artifacts"]["pdf"]["artifact_url"].endswith("project-v1.pdf")
    assert result["errors"]["concept_image"]["message"] == "概念图生成失败，建议书已生成。"


def test_running_artifact_cancel_discards_producer_output_at_the_next_boundary(tmp_path: Path, monkeypatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    _configure_task_store(tmp_path, monkeypatch)
    job = artifact_jobs.create_artifact_job(_request(kinds=["pdf", "concept_image"]), actor={})
    monkeypatch.setattr(artifact_jobs, "_producer_payload", lambda _job: _request(kinds=["pdf", "concept_image"]))

    def produce(_payload):
        task_store.request_cancel(job["task_id"])
        return {
            "artifact_urls": {"pdf": "/api/artifacts/cancelled.pdf", "concept_image": "/api/artifacts/cancelled.png"},
            "pdf": {"artifact_url": "/api/artifacts/cancelled.pdf"},
            "concept_image": {"artifact_url": "/api/artifacts/cancelled.png"},
        }

    monkeypatch.setattr(artifact_jobs, "_produce", produce)
    result = artifact_jobs.run_artifact_job(job["job_id"])

    assert result["status"] == "cancelled"
    assert result["artifacts"] == {}
    assert task_store.get_task(job["task_id"])["status"] == "cancelled"


def test_producer_exception_after_cancel_commits_cancelled_without_error(tmp_path: Path, monkeypatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    _configure_task_store(tmp_path, monkeypatch)
    job = artifact_jobs.create_artifact_job(_request(kinds=["pdf"]), actor={})
    monkeypatch.setattr(artifact_jobs, "_producer_payload", lambda _job: _request(kinds=["pdf"]))

    def produce(_payload):
        task_store.request_cancel(job["task_id"])
        raise RuntimeError("producer failed after cancellation")

    monkeypatch.setattr(artifact_jobs, "_produce", produce)

    result = artifact_jobs.run_artifact_job(job["job_id"])

    assert result["status"] == "cancelled"
    assert result["artifacts"] == {}
    assert result["errors"] == {}
    assert task_store.get_task(job["task_id"])["status"] == "cancelled"


def test_cancel_winning_final_task_commit_clears_artifact_job_output(tmp_path: Path, monkeypatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    _configure_task_store(tmp_path, monkeypatch)
    job = artifact_jobs.create_artifact_job(_request(kinds=["pdf"]), actor={})
    monkeypatch.setattr(artifact_jobs, "_producer_payload", lambda _job: _request(kinds=["pdf"]))
    monkeypatch.setattr(
        artifact_jobs,
        "_produce",
        lambda _payload: {"artifact_urls": {"pdf": "/api/artifacts/late.pdf"}, "pdf": {"artifact_url": "/api/artifacts/late.pdf"}},
    )
    original_update_task = artifact_jobs.update_task

    def cancel_before_terminal_task_update(task_id: str, **changes):
        if changes.get("status") == "completed":
            task_store.request_cancel(task_id)
        return original_update_task(task_id, **changes)

    monkeypatch.setattr(artifact_jobs, "update_task", cancel_before_terminal_task_update)

    result = artifact_jobs.run_artifact_job(job["job_id"])

    assert result["status"] == "cancelled"
    assert result["artifacts"] == {}
    assert result["errors"] == {}
    assert task_store.get_task(job["task_id"])["status"] == "cancelled"


def test_terminal_job_is_not_reused_by_idempotency_key(tmp_path: Path, monkeypatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    first = artifact_jobs.create_artifact_job(_request(), actor={}, idempotency_key="repeat")
    artifact_jobs._update_job(first["job_id"], status="failed", errors={"pdf": {"message": "failed"}})

    second = artifact_jobs.create_artifact_job(_request(), actor={}, idempotency_key="repeat")

    assert first["job_id"] != second["job_id"]


def test_source_run_must_belong_to_requested_workspace(tmp_path: Path, monkeypatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    monkeypatch.setattr(
        artifact_jobs,
        "get_run",
        lambda run_id: {"run_id": run_id, "workspace_id": "ws-other", "artifact": {}},
    )

    try:
        artifact_jobs.create_artifact_job(_request(), actor={}, idempotency_key="cross-workspace")
    except ValueError as exc:
        assert "source run does not belong" in str(exc)
    else:
        raise AssertionError("cross-workspace source run was accepted")


def test_concurrent_workers_claim_a_queued_job_only_once(tmp_path: Path, monkeypatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    job = artifact_jobs.create_artifact_job(_request(kinds=["pdf"]), actor={}, idempotency_key="claim-once")
    calls = {"count": 0}

    def produce(_payload):
        calls["count"] += 1
        return {
            "artifact_urls": {"pdf": "/api/artifacts/project-v1.pdf"},
            "pdf": {"artifact_url": "/api/artifacts/project-v1.pdf"},
        }

    monkeypatch.setattr(artifact_jobs, "_produce", produce)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(artifact_jobs.run_artifact_job, [job["job_id"], job["job_id"]]))

    assert calls["count"] == 1
    assert all(item["job_id"] == job["job_id"] for item in results)
    assert artifact_jobs.get_artifact_job(job["job_id"])["status"] == "completed"


def test_separate_process_workers_use_linked_task_claim_as_the_authoritative_guard(tmp_path: Path, monkeypatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    _configure_task_store(tmp_path, monkeypatch)
    job = artifact_jobs.create_artifact_job(_request(kinds=["pdf"]), actor={}, idempotency_key="process-claim-once")
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    job_claim_barrier = context.Barrier(2)
    results = context.Queue()
    workers = [
        context.Process(
            target=_run_local_artifact_job_from_process,
            args=(str(task_store.TASK_DIR), str(artifact_jobs.ARTIFACT_JOB_DIR), job["job_id"], start, job_claim_barrier, results),
        )
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    start.set()
    for worker in workers:
        worker.join(20)

    events = []
    while len([event for event in events if event[0] == "status"]) < 2:
        events.append(results.get(timeout=5))
    assert all(worker.exitcode == 0 for worker in workers)
    assert [event for event in events if event[0] == "produce"] == [("produce", job["job_id"])]
    assert artifact_jobs.get_artifact_job(job["job_id"])["status"] == "completed"
    assert task_store.get_task(job["task_id"])["status"] == "completed"


def test_remote_job_blobs_are_authoritative_for_listing(tmp_path: Path, monkeypatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    remote = {
        "job_id": "artifact_job_remote",
        "workspace_id": "ws-artifacts",
        "source_run_id": "run-v1",
        "status": "completed",
        "created_at": "2026-07-12T01:00:00+00:00",
        "updated_at": "2026-07-12T01:01:00+00:00",
    }
    monkeypatch.setattr(artifact_jobs, "list_blob_json", lambda _prefix: [remote], raising=False)

    jobs = artifact_jobs.list_artifact_jobs("ws-artifacts")

    assert [item["job_id"] for item in jobs] == ["artifact_job_remote"]


def test_artifact_job_api_rejects_cross_workspace_source_run(tmp_path: Path, monkeypatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    headers = _authorized_headers(monkeypatch)
    monkeypatch.setattr(
        artifact_jobs,
        "get_run",
        lambda run_id: {"run_id": run_id, "workspace_id": "ws-other", "artifact": {}},
    )

    response = TestClient(app).post("/api/artifact-jobs", json=_request(), headers=headers)

    assert response.status_code == 400
    assert "source run does not belong" in response.json()["detail"]


def test_non_member_cannot_read_artifact_job_or_workspace_list(tmp_path: Path, monkeypatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    job = artifact_jobs.create_artifact_job(_request(), actor={}, idempotency_key="private-job")
    monkeypatch.setenv("DF_WORKSPACE_RBAC_ENFORCED", "1")
    monkeypatch.setenv("DF_WORKSPACE_OWNER_EMAIL", "owner@contoso.com")
    monkeypatch.setattr(workspace_authz, "_load_workspace_meta", lambda _workspace_id: {})
    headers = {"x-dataforge-actor": quote(json.dumps({"email": "outsider@contoso.com"}))}
    client = TestClient(app)

    detail = client.get(f"/api/artifact-jobs/{job['job_id']}", headers=headers)
    listed = client.get("/api/workspaces/ws-artifacts/artifact-jobs", headers=headers)

    assert detail.status_code == 403
    assert listed.status_code == 403


def test_blob_persist_failure_is_visible_and_compensates_generic_task(tmp_path: Path, monkeypatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    monkeypatch.setattr(artifact_jobs, "blob_configured", lambda: True, raising=False)
    monkeypatch.setattr(
        artifact_jobs,
        "upload_blob_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("blob unavailable")),
    )
    compensated: list[tuple[str, dict]] = []
    monkeypatch.setattr(artifact_jobs, "create_task", lambda *_args, **_kwargs: {"task_id": "task_artifact"})
    monkeypatch.setattr(artifact_jobs, "update_task", lambda task_id, **changes: compensated.append((task_id, changes)) or {})

    try:
        artifact_jobs.create_artifact_job(_request(kinds=["pdf"]), actor={}, idempotency_key="blob-down")
    except RuntimeError as exc:
        assert "durable artifact job" in str(exc)
    else:
        raise AssertionError("Blob persistence failure was reported as a local job")

    assert compensated == [("task_artifact", {"status": "failed", "error": {"category": "artifact_job", "code": "persistence_failed"}})]
    assert not artifact_jobs.ARTIFACT_JOB_DIR.exists()


def test_artifact_job_api_creates_and_exposes_persisted_status(tmp_path: Path, monkeypatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    headers = _authorized_headers(monkeypatch)
    monkeypatch.setattr(artifact_jobs, "_producer_payload", lambda _job: _request())
    monkeypatch.setattr(
        artifact_jobs,
        "_produce",
        lambda _payload: {
            "artifact_urls": {
                "pdf": "/api/artifacts/project-v1.pdf",
                "concept_image": "/api/artifacts/project-v1.png",
            },
            "pdf": {"artifact_url": "/api/artifacts/project-v1.pdf"},
            "concept_image": {"artifact_url": "/api/artifacts/project-v1.png"},
        },
    )
    client = TestClient(app)

    created = client.post(
        "/api/artifact-jobs",
        json=_request(),
        headers={**headers, "Idempotency-Key": "api-request-1"},
    )

    assert created.status_code == 202
    job_id = created.json()["job_id"]
    detail = client.get(f"/api/artifact-jobs/{job_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["status"] == "completed"

    listed = client.get("/api/workspaces/ws-artifacts/artifact-jobs", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["jobs"][0]["job_id"] == job_id


def test_artifact_job_api_returns_503_for_durable_persistence_failure(monkeypatch) -> None:
    headers = _authorized_headers(monkeypatch)
    monkeypatch.setattr(
        app_module,
        "create_artifact_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ArtifactJobPersistenceError("blob unavailable")),
    )

    response = TestClient(app, raise_server_exceptions=False).post("/api/artifact-jobs", json=_request(), headers=headers)

    assert response.status_code == 503
    assert response.json()["detail"] == "Durable artifact job storage is unavailable"


def test_artifact_job_api_returns_503_when_generic_task_create_is_unavailable(tmp_path: Path, monkeypatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    _configure_task_store(tmp_path, monkeypatch)
    headers = _authorized_headers(monkeypatch)
    monkeypatch.setattr(
        artifact_jobs,
        "create_task",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TaskPersistenceError("task store unavailable")),
    )

    response = TestClient(app, raise_server_exceptions=False).post("/api/artifact-jobs", json=_request(), headers=headers)

    assert response.status_code == 503
    assert response.json()["detail"] == "Durable artifact job storage is unavailable"


def test_retryable_artifact_task_creates_and_schedules_a_new_linked_job(tmp_path: Path, monkeypatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    _configure_task_store(tmp_path, monkeypatch)
    headers = _authorized_headers(monkeypatch)
    monkeypatch.setattr(artifact_jobs, "_producer_payload", lambda _job: _request(kinds=["pdf"]))
    calls: list[str] = []

    def fail_once(_payload):
        if not calls:
            calls.append("failed")
            raise RuntimeError("renderer unavailable")
        calls.append("completed")
        return {"artifact_urls": {"pdf": "/api/artifacts/retry.pdf"}, "pdf": {"artifact_url": "/api/artifacts/retry.pdf"}}

    monkeypatch.setattr(artifact_jobs, "_produce", fail_once)
    client = TestClient(app, raise_server_exceptions=False)
    created = client.post("/api/artifact-jobs", json=_request(kinds=["pdf"]), headers=headers)

    assert created.status_code == 202
    failed_task = task_store.get_task(created.json()["task_id"])
    assert failed_task["status"] == "failed"
    assert failed_task["retryable"] is True

    retried = client.post(f"/api/tasks/{failed_task['task_id']}/retry", headers=headers)

    assert retried.status_code == 202
    retry_task = retried.json()
    assert retry_task["retry_of"] == failed_task["task_id"]
    retry_job = artifact_jobs.get_artifact_job(retry_task["result"]["artifact_job_id"])
    assert retry_job["source_run_id"] == created.json()["source_run_id"]
    assert retry_job["requested_kinds"] == ["pdf"]
    assert calls == ["failed", "completed"]


def test_retry_rejects_non_dispatchable_task(tmp_path: Path, monkeypatch) -> None:
    _configure_task_store(tmp_path, monkeypatch)
    headers = _authorized_headers(monkeypatch)
    task = task_store.create_task({"workspace_id": "ws-artifacts", "task_type": "connector.sql.import", "action": "connector.manage"}, actor={})
    task_store.update_task(task["task_id"], status="failed", error={"code": "credential_required"})

    response = TestClient(app, raise_server_exceptions=False).post(f"/api/tasks/{task['task_id']}/retry", headers=headers)

    assert response.status_code == 409
    assert response.json()["detail"] == "Task retry is not supported"


def test_artifact_api_recovers_durable_job_after_activation_failure_and_schedules_once(tmp_path: Path, monkeypatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    _configure_task_store(tmp_path, monkeypatch)
    headers = _authorized_headers(monkeypatch)
    monkeypatch.setattr(artifact_jobs, "_producer_payload", lambda _job: _request(kinds=["pdf"]))
    monkeypatch.setattr(
        artifact_jobs,
        "_produce",
        lambda _payload: {"artifact_urls": {"pdf": "/api/artifacts/recovered.pdf"}, "pdf": {"artifact_url": "/api/artifacts/recovered.pdf"}},
    )
    original_activate = getattr(artifact_jobs, "activate_prepared_task", None)
    monkeypatch.setattr(artifact_jobs, "activate_prepared_task", lambda _task_id: None, raising=False)

    client = TestClient(app, raise_server_exceptions=False)
    first = client.post("/api/artifact-jobs", json=_request(kinds=["pdf"]), headers={**headers, "Idempotency-Key": "recover-activation"})

    assert first.status_code == 503
    prepared = task_store.list_tasks("ws-artifacts")[0]
    job_id = str(prepared["result"]["artifact_job_id"])
    assert artifact_jobs.get_artifact_job(job_id)["status"] == "queued"
    assert prepared["status"] == "preparing"

    assert original_activate is not None
    monkeypatch.setattr(artifact_jobs, "activate_prepared_task", original_activate)
    calls: list[str] = []
    original_run = artifact_jobs.run_artifact_job

    def run_recovered(job_id: str):
        calls.append(job_id)
        return original_run(job_id)

    monkeypatch.setattr(app_module, "run_artifact_job", run_recovered)
    second = client.post("/api/artifact-jobs", json=_request(kinds=["pdf"]), headers={**headers, "Idempotency-Key": "recover-activation"})

    assert second.status_code == 202
    assert second.json()["job_id"] == job_id
    assert calls == [job_id]
    assert artifact_jobs.get_artifact_job(job_id)["status"] == "completed"
    assert task_store.get_task(prepared["task_id"])["status"] == "completed"
    assert artifact_jobs.recover_prepared_artifact_tasks("ws-artifacts") == []


def test_artifact_api_returns_503_when_prepared_recovery_storage_fails(tmp_path: Path, monkeypatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    _configure_task_store(tmp_path, monkeypatch)
    headers = _authorized_headers(monkeypatch)
    original_activate = artifact_jobs.activate_prepared_task
    monkeypatch.setattr(artifact_jobs, "activate_prepared_task", lambda _task_id: None)
    client = TestClient(app, raise_server_exceptions=False)

    first = client.post("/api/artifact-jobs", json=_request(kinds=["pdf"]), headers={**headers, "Idempotency-Key": "recover-storage-error"})

    assert first.status_code == 503
    prepared = task_store.list_tasks("ws-artifacts")[0]
    job_id = str(prepared["result"]["artifact_job_id"])
    monkeypatch.setattr(
        artifact_jobs,
        "activate_prepared_task",
        lambda _task_id: (_ for _ in ()).throw(TaskPersistenceError("blob CAS unavailable")),
    )
    second = client.post("/api/artifact-jobs", json=_request(kinds=["pdf"]), headers={**headers, "Idempotency-Key": "recover-storage-error"})

    assert second.status_code == 503
    assert artifact_jobs.get_artifact_job(job_id)["status"] == "queued"
    assert task_store.get_task(prepared["task_id"])["status"] == "preparing"
    monkeypatch.setattr(artifact_jobs, "activate_prepared_task", original_activate)


def test_artifact_api_returns_503_when_prepared_recovery_task_list_read_fails(tmp_path: Path, monkeypatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    _configure_task_store(tmp_path, monkeypatch)
    headers = _authorized_headers(monkeypatch)
    monkeypatch.setattr(task_store, "blob_configured", lambda: True)
    monkeypatch.setattr(
        task_store,
        "list_blob_json_strict",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(BlobJsonReadError("blob list unavailable")),
        raising=False,
    )

    response = TestClient(app, raise_server_exceptions=False).post("/api/artifact-jobs", json=_request(kinds=["pdf"]), headers=headers)

    assert response.status_code == 503


def test_prepared_recovery_raises_when_linked_artifact_blob_read_fails(tmp_path: Path, monkeypatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    _configure_task_store(tmp_path, monkeypatch)
    task_store.create_task(
        {
            "workspace_id": "ws-artifacts",
            "task_type": "artifact.generate",
            "action": "artifact.generate",
            "initial_status": "preparing",
            "result": {"artifact_job_id": "artifact_job_unavailable"},
        },
        {"email": "owner@contoso.com"},
    )
    monkeypatch.setattr(artifact_jobs, "blob_configured", lambda: True)
    monkeypatch.setattr(
        artifact_jobs,
        "download_blob_json_strict",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(BlobJsonReadError("blob read unavailable")),
    )

    with pytest.raises(TaskPersistenceError, match="artifact job read"):
        artifact_jobs.recover_prepared_artifact_tasks("ws-artifacts")


def test_concurrent_prepared_recovery_returns_a_job_only_once(tmp_path: Path, monkeypatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    _configure_task_store(tmp_path, monkeypatch)
    original_activate = artifact_jobs.activate_prepared_task
    monkeypatch.setattr(artifact_jobs, "activate_prepared_task", lambda _task_id: None)

    with pytest.raises(ArtifactJobPersistenceError):
        artifact_jobs.create_artifact_job(_request(kinds=["pdf"]), actor={}, idempotency_key="recover-once")

    prepared = task_store.list_tasks("ws-artifacts")[0]
    job_id = str(prepared["result"]["artifact_job_id"])
    monkeypatch.setattr(artifact_jobs, "activate_prepared_task", original_activate)
    with ThreadPoolExecutor(max_workers=2) as pool:
        recovered = list(pool.map(lambda _: artifact_jobs.recover_prepared_artifact_tasks("ws-artifacts"), range(2)))

    recovered_ids = [item["job_id"] for batch in recovered for item in batch]
    assert recovered_ids == [job_id]
    assert task_store.get_task(prepared["task_id"])["status"] == "queued"


def test_pdf_artifact_uses_an_opaque_name_and_workspace_binding(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(render_pdf, "OUT_DIR", tmp_path)
    monkeypatch.setattr(artifact_registry, "ARTIFACT_RECORD_DIR", tmp_path / "artifact-registry")
    monkeypatch.setattr(artifact_registry, "blob_configured", lambda: False)
    monkeypatch.setattr(render_pdf, "_html_pdf", lambda _proposal, _template: b"%PDF-1.4\n%%EOF")

    result = render_pdf.render_pdf_report(
        {"opportunity_id": "Pilot plan", "doc_meta": {"version": "V2"}},
        "project_proposal",
        workspace_id="ws-artifacts",
    )

    assert result["artifact_name"].startswith("artifact-")
    assert result["artifact_name"].endswith(".pdf")
    assert "V2" not in result["artifact_name"]
    assert artifact_registry.get_artifact(result["artifact_name"])["workspace_id"] == "ws-artifacts"
