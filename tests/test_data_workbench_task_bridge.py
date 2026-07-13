from __future__ import annotations

import asyncio

import pytest

import backend.app as app_module
import backend.artifact_jobs as artifact_jobs
import backend.data_workbench as data_workbench
import backend.task_store as task_store


def _configure_tasks(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(task_store, "TASK_DIR", tmp_path / "tasks")
    monkeypatch.setattr(task_store, "blob_configured", lambda: False)
    monkeypatch.setattr(task_store, "download_blob_json", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(task_store, "list_blob_json", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(task_store, "upload_blob_json", lambda *_args, **_kwargs: {})


def test_artifact_job_keeps_legacy_id_and_links_completed_generic_task(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_tasks(tmp_path, monkeypatch)
    monkeypatch.setattr(artifact_jobs, "ARTIFACT_JOB_DIR", tmp_path / "artifact-jobs")
    monkeypatch.setattr(artifact_jobs, "blob_configured", lambda: False)
    monkeypatch.setattr(artifact_jobs, "download_blob_json", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(artifact_jobs, "list_blob_json", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(artifact_jobs, "upload_blob_json", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(artifact_jobs, "get_run", lambda run_id: {"workspace_id": "ws-bridge", "run_id": run_id, "artifact": {"feasibility": {}}})
    monkeypatch.setattr(artifact_jobs, "_producer_payload", lambda _job: {})
    monkeypatch.setattr(artifact_jobs, "_produce", lambda _payload: {"artifact_urls": {"pdf": "/api/artifacts/a.pdf"}, "pdf": {"artifact_url": "/api/artifacts/a.pdf"}})

    job = artifact_jobs.create_artifact_job({"workspace_id": "ws-bridge", "conversation_id": "run-1", "kinds": ["pdf"]}, actor={"email": "owner@contoso.com"})
    result = artifact_jobs.run_artifact_job(job["job_id"])
    tasks = task_store.list_tasks("ws-bridge")

    assert result["job_id"] == job["job_id"]
    assert job["job_id"].startswith("artifact_job_")
    assert tasks[0]["result"] == {"artifact_job_id": job["job_id"]}
    assert tasks[0]["status"] == "completed"


def test_ingest_failure_preserves_upload_result_and_records_safe_task(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_tasks(tmp_path, monkeypatch)
    upload = {"workspace_id": "ws-bridge", "ingest_job_id": "ingest-1", "documents": [{"name": "import.csv"}], "indexed_count": 0}
    monkeypatch.setattr(data_workbench, "preview_sql_table", lambda *_args: {"columns": [{"name": "id"}], "rows": [[1]]})
    monkeypatch.setattr(data_workbench, "create_workspace_upload_job", lambda **_kwargs: dict(upload))
    monkeypatch.setattr(data_workbench, "run_workspace_ingest_job", lambda *_args: (_ for _ in ()).throw(RuntimeError("connector failed")))
    monkeypatch.setattr(data_workbench, "_record_import_history", lambda *_args: None)

    with pytest.raises(RuntimeError, match="connector failed"):
        data_workbench.import_sql_table("ws-bridge", {"connection_id": "connection-secret", "table": "sales"}, actor={"email": "owner@contoso.com"})
    tasks = task_store.list_tasks("ws-bridge")

    assert upload["documents"] == [{"name": "import.csv"}]
    assert tasks[0]["result"] == {"ingest_job_id": "ingest-1"}
    assert tasks[0]["status"] == "failed"
    assert "connection-secret" not in str(tasks[0])


def test_upload_background_ingest_completes_linked_generic_task(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_tasks(tmp_path, monkeypatch)
    task = task_store.create_task(
        {"workspace_id": "ws-bridge", "task_type": "workspace.ingest", "action": "file.create", "result": {"ingest_job_id": "ingest-2"}},
        {"email": "owner@contoso.com"},
    )
    monkeypatch.setattr(app_module, "run_workspace_ingest_job", lambda *_args: {"state": "completed"})

    asyncio.run(app_module._run_upload_ingest_background("ws-bridge", "ingest-2", task["task_id"]))

    assert task_store.get_task(task["task_id"])["status"] == "completed"


def test_connector_import_task_requires_connector_manage_action(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_tasks(tmp_path, monkeypatch)
    upload = {"workspace_id": "ws-bridge", "ingest_job_id": "ingest-connector", "documents": []}
    monkeypatch.setattr(data_workbench, "preview_sql_table", lambda *_args: {"columns": [{"name": "id"}], "rows": [[1]]})
    monkeypatch.setattr(data_workbench, "create_workspace_upload_job", lambda **_kwargs: dict(upload))
    monkeypatch.setattr(data_workbench, "run_workspace_ingest_job", lambda *_args: {"state": "ready"})
    monkeypatch.setattr(data_workbench, "_record_import_history", lambda *_args: None)

    data_workbench.import_sql_table("ws-bridge", {"connection_id": "connection-1", "table": "sales"}, actor={"email": "owner@contoso.com"})

    assert task_store.list_tasks("ws-bridge")[0]["action"] == "connector.manage"


@pytest.mark.parametrize(("ingest_state", "task_status"), [("ready", "completed"), ("partial", "partial"), ("failed", "failed")])
def test_selected_file_analysis_syncs_ingest_task_state(tmp_path, monkeypatch: pytest.MonkeyPatch, ingest_state: str, task_status: str) -> None:
    _configure_tasks(tmp_path, monkeypatch)
    document = {"id": "file-1", "name": "pending.csv", "source_file": "pending.csv", "status": "processing", "ingest_job_id": "ingest-analysis", "bytes": 3}
    monkeypatch.setattr(data_workbench, "get_workspace_detail", lambda _workspace_id: {"documents": [dict(document)]})
    monkeypatch.setattr(data_workbench, "_find_document", lambda *_args, **_kwargs: dict(document))
    monkeypatch.setattr(data_workbench, "_read_document_bytes", lambda *_args: (b"a,b", "text/csv"))
    monkeypatch.setattr(data_workbench, "run_workspace_ingest_job", lambda *_args: {"state": ingest_state, "pct": 100})

    async def consume(_req, *, collect=False):
        return {"conversation_id": "conversation-1", "events": [], "final": None}

    monkeypatch.setattr(data_workbench, "_consume_workbench_analysis", consume)
    result = asyncio.run(data_workbench.analyze_selected_files("ws-bridge", {"file_ids": ["file-1"], "ui_context": {"actor": {"email": "owner@contoso.com"}}}))
    task = task_store.list_tasks("ws-bridge")[0]

    assert result["status"] == "started"
    assert task["result"] == {"ingest_job_id": "ingest-analysis"}
    assert task["status"] == task_status
    assert task["action"] == "analysis.run"


def test_artifact_job_is_not_exposed_when_generic_task_is_not_durable(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_tasks(tmp_path, monkeypatch)
    monkeypatch.setattr(artifact_jobs, "ARTIFACT_JOB_DIR", tmp_path / "artifact-jobs")
    monkeypatch.setattr(artifact_jobs, "get_run", lambda run_id: {"workspace_id": "ws-bridge", "run_id": run_id, "artifact": {"feasibility": {}}})
    monkeypatch.setattr(artifact_jobs, "create_task", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("durable task unavailable")))

    with pytest.raises(RuntimeError, match="durable task unavailable"):
        artifact_jobs.create_artifact_job({"workspace_id": "ws-bridge", "conversation_id": "run-1", "kinds": ["pdf"]}, actor={"email": "owner@contoso.com"})

    assert not artifact_jobs.ARTIFACT_JOB_DIR.exists()
