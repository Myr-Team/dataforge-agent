from __future__ import annotations

import asyncio

import pytest

import backend.app as app_module
import backend.artifact_jobs as artifact_jobs
import backend.data_workbench as data_workbench
import backend.task_store as task_store
from backend.connector_store import ConnectorStore
from backend.task_store import TaskPersistenceError
from fastapi.testclient import TestClient
from backend.app import app


def _configure_tasks(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(task_store, "TASK_DIR", tmp_path / "tasks")
    monkeypatch.setattr(task_store, "blob_configured", lambda: False)
    monkeypatch.setattr(task_store, "download_blob_json", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(task_store, "list_blob_json", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(task_store, "upload_blob_json", lambda *_args, **_kwargs: {})


def _connector_record(tmp_path, monkeypatch: pytest.MonkeyPatch, workspace_id: str) -> dict:
    store = ConnectorStore(tmp_path / "connectors")
    monkeypatch.setattr(data_workbench, "_CONNECTOR_STORE", store)
    return store.create(
        workspace_id,
        "sql",
        {"server": "sql.example", "database": "sales"},
        {"username": "reader", "password": "very-secret"},
        data_workbench._SECRET_STORE,
    )


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
    monkeypatch.setattr(data_workbench, "preview_sql_table", lambda *_args, **_kwargs: {"columns": [{"name": "id"}], "rows": [[1]]})
    monkeypatch.setattr(data_workbench, "create_workspace_upload_job", lambda **_kwargs: dict(upload))
    monkeypatch.setattr(data_workbench, "run_workspace_ingest_job", lambda *_args: (_ for _ in ()).throw(RuntimeError("connector failed")))
    monkeypatch.setattr(data_workbench, "_record_import_history", lambda *_args: None)
    connector = _connector_record(tmp_path, monkeypatch, "ws-bridge")

    with pytest.raises(RuntimeError, match="connector failed"):
        data_workbench.import_sql_table("ws-bridge", {"connection_id": connector["connector_id"], "table": "sales"}, actor={"email": "owner@contoso.com"})
    tasks = task_store.list_tasks("ws-bridge")

    assert upload["documents"] == [{"name": "import.csv"}]
    assert tasks[0]["result"] == {"ingest_job_id": "ingest-1", "connector_id": connector["connector_id"]}
    assert tasks[0]["status"] == "failed"
    assert "connection-secret" not in str(tasks[0])


def test_connector_sync_marks_claimed_task_failed_when_syncing_transition_fails(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_tasks(tmp_path, monkeypatch)
    connector = _connector_record(tmp_path, monkeypatch, "ws-bridge")
    original_update = data_workbench._CONNECTOR_STORE.update

    def fail_syncing(workspace_id, connector_id, **changes):
        if changes.get("status") == "syncing":
            raise RuntimeError("storage password=never-expose")
        return original_update(workspace_id, connector_id, **changes)

    monkeypatch.setattr(data_workbench._CONNECTOR_STORE, "update", fail_syncing)

    with pytest.raises(RuntimeError):
        data_workbench.import_sql_table("ws-bridge", {"connection_id": connector["connector_id"], "table": "sales"})

    task = task_store.list_tasks("ws-bridge")[0]
    assert task["status"] == "failed"
    assert task["error"] == {"category": "connector", "code": "sync_failed"}


def test_connector_sync_completes_task_only_after_lineage_and_connector_state(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_tasks(tmp_path, monkeypatch)
    connector = _connector_record(tmp_path, monkeypatch, "ws-bridge")
    monkeypatch.setattr(data_workbench, "preview_sql_table", lambda *_args, **_kwargs: {"columns": [{"name": "id"}], "rows": [[1]]})
    monkeypatch.setattr(data_workbench, "create_workspace_upload_job", lambda **_kwargs: {"workspace_id": "ws-bridge", "ingest_job_id": "ingest-1", "documents": [{"source_file": "raw_docs/new.csv", "ingest_job_id": "ingest-1"}]})
    monkeypatch.setattr(data_workbench, "run_workspace_ingest_job", lambda *_args: {"state": "ready"})
    monkeypatch.setattr(data_workbench, "_record_connector_lineage", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("history failure")))
    monkeypatch.setattr(data_workbench, "_record_import_history", lambda *_args: None)

    with pytest.raises(RuntimeError):
        data_workbench.import_sql_table("ws-bridge", {"connection_id": connector["connector_id"], "table": "sales"})

    task = task_store.list_tasks("ws-bridge")[0]
    record = data_workbench._CONNECTOR_STORE.get("ws-bridge", connector["connector_id"])
    assert task["status"] == "failed"
    assert record["status"] == "error"


def test_connector_sync_finalizes_task_before_returning_connector_to_connected(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_tasks(tmp_path, monkeypatch)
    connector = _connector_record(tmp_path, monkeypatch, "ws-bridge")
    events: list[tuple[str, str]] = []
    original_transition = data_workbench._CONNECTOR_STORE.transition
    original_update = data_workbench.update_task

    monkeypatch.setattr(data_workbench, "preview_sql_table", lambda *_args, **_kwargs: {"columns": [{"name": "id"}], "rows": [[1]]})
    monkeypatch.setattr(data_workbench, "create_workspace_upload_job", lambda **_kwargs: {"workspace_id": "ws-bridge", "ingest_job_id": "ingest-1", "documents": [{"source_file": "raw_docs/new.csv", "ingest_job_id": "ingest-1"}]})
    monkeypatch.setattr(data_workbench, "run_workspace_ingest_job", lambda *_args: {"state": "ready"})
    monkeypatch.setattr(data_workbench, "_record_connector_lineage", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(data_workbench, "_record_import_history", lambda *_args, **_kwargs: None)

    def observe_transition(*args, **changes):
        events.append(("connector", str(changes.get("status") or "")))
        return original_transition(*args, **changes)

    def observe_task(task_id, **changes):
        if changes.get("status") == "completed":
            events.append(("task", "completed"))
        return original_update(task_id, **changes)

    monkeypatch.setattr(data_workbench._CONNECTOR_STORE, "transition", observe_transition)
    monkeypatch.setattr(data_workbench, "update_task", observe_task)

    result = data_workbench.import_sql_table("ws-bridge", {"connection_id": connector["connector_id"], "table": "sales"})

    assert result["task"]["status"] == "completed"
    assert events.index(("connector", "finalizing")) < events.index(("task", "completed")) < events.index(("connector", "connected"))


def test_finalizing_connector_recovers_connected_only_after_completed_task(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_tasks(tmp_path, monkeypatch)
    connector = _connector_record(tmp_path, monkeypatch, "ws-bridge")
    task = task_store.create_task({"workspace_id": "ws-bridge", "task_type": "connector.sql.sync", "action": "connector.manage", "result": {"ingest_job_id": "ingest-1", "connector_id": connector["connector_id"]}}, actor={})
    task_store.claim_task(task["task_id"], "worker")
    task_store.update_task(task["task_id"], status="completed")
    data_workbench._CONNECTOR_STORE.update(
        "ws-bridge", connector["connector_id"], status="finalizing", pending_task_id=task["task_id"], sync_token="a" * 32,
    )
    monkeypatch.setattr(data_workbench, "_connector_ingest_job_belongs_to_workspace", lambda *_args: True)

    listed = data_workbench.list_connectors("ws-bridge")
    recovered = next(item for item in listed["connectors"] if item["connector_id"] == connector["connector_id"])

    assert recovered["status"] == "connected"
    assert "pending_task_id" not in recovered
    assert data_workbench._CONNECTOR_STORE.get("ws-bridge", connector["connector_id"])["status"] == "connected"


def test_finalizing_connector_does_not_claim_connected_when_task_is_not_completed(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_tasks(tmp_path, monkeypatch)
    connector = _connector_record(tmp_path, monkeypatch, "ws-bridge")
    task = task_store.create_task({"workspace_id": "ws-bridge", "task_type": "connector.sql.sync", "action": "connector.manage", "result": {"ingest_job_id": "ingest-1", "connector_id": connector["connector_id"]}}, actor={})
    data_workbench._CONNECTOR_STORE.update(
        "ws-bridge", connector["connector_id"], status="finalizing", pending_task_id=task["task_id"], sync_token="b" * 32,
    )
    monkeypatch.setattr(data_workbench, "_connector_ingest_job_belongs_to_workspace", lambda *_args: True)

    listed = data_workbench.list_connectors("ws-bridge")

    assert listed["connectors"][0]["status"] == "finalizing"


def test_finalizing_running_connector_recovers_task_then_connected(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_tasks(tmp_path, monkeypatch)
    connector = _connector_record(tmp_path, monkeypatch, "ws-bridge")
    task = task_store.create_task({"workspace_id": "ws-bridge", "task_type": "connector.sql.import", "action": "connector.manage", "result": {"ingest_job_id": "ingest-1", "connector_id": connector["connector_id"]}}, actor={})
    task_store.claim_task(task["task_id"], "worker")
    data_workbench._CONNECTOR_STORE.update(
        "ws-bridge", connector["connector_id"], status="finalizing", pending_task_id=task["task_id"], sync_token="c" * 32,
    )
    monkeypatch.setattr(data_workbench, "_connector_ingest_job_belongs_to_workspace", lambda *_args: True)

    recovered = data_workbench.list_connectors("ws-bridge")["connectors"][0]

    assert recovered["status"] == "connected"
    assert task_store.get_task(task["task_id"])["status"] == "completed"


@pytest.mark.parametrize(("task_patch", "code"), [
    ("missing", "sync_task_missing"),
    ("mismatch", "sync_task_mismatch"),
])
def test_bad_pending_finalizing_task_isolated_to_its_connector(tmp_path, monkeypatch: pytest.MonkeyPatch, task_patch: str, code: str) -> None:
    _configure_tasks(tmp_path, monkeypatch)
    bad = _connector_record(tmp_path, monkeypatch, "ws-bridge")
    good = _connector_record(tmp_path, monkeypatch, "ws-bridge")
    task = task_store.create_task({"workspace_id": "ws-bridge", "task_type": "connector.sql.import", "action": "connector.manage", "result": {"ingest_job_id": "ingest-1", "connector_id": bad["connector_id"]}}, actor={})
    task_store.claim_task(task["task_id"], "worker")
    data_workbench._CONNECTOR_STORE.update("ws-bridge", bad["connector_id"], status="finalizing", pending_task_id=("task_missing" if task_patch == "missing" else task["task_id"]), sync_token="d" * 32)
    monkeypatch.setattr(data_workbench, "_connector_ingest_job_belongs_to_workspace", lambda *_args: task_patch != "mismatch")

    listed = data_workbench.list_connectors("ws-bridge")["connectors"]
    by_id = {item["connector_id"]: item for item in listed}

    assert by_id[bad["connector_id"]]["status"] == "error"
    assert by_id[bad["connector_id"]]["error"] == code
    assert by_id[good["connector_id"]]["status"] == "connected"

    status = data_workbench.connector_status("ws-bridge", "sql", bad["connector_id"])
    reconnect = data_workbench.reconnect_connector("ws-bridge", bad["connector_id"])
    assert status["error"] == code
    assert reconnect["error"] == code


def test_finalizing_rejects_same_workspace_same_kind_task_for_another_connector(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_tasks(tmp_path, monkeypatch)
    first = _connector_record(tmp_path, monkeypatch, "ws-bridge")
    second = _connector_record(tmp_path, monkeypatch, "ws-bridge")
    task = task_store.create_task({
        "workspace_id": "ws-bridge",
        "task_type": "connector.sql.import",
        "action": "connector.manage",
        "result": {"ingest_job_id": "ingest-1", "connector_id": second["connector_id"]},
    }, actor={})
    task_store.claim_task(task["task_id"], "worker")
    data_workbench._CONNECTOR_STORE.update("ws-bridge", first["connector_id"], status="finalizing", pending_task_id=task["task_id"], sync_token="e" * 32)
    monkeypatch.setattr(data_workbench, "_connector_ingest_job_belongs_to_workspace", lambda *_args: True)

    recovered = data_workbench.list_connectors("ws-bridge")["connectors"]
    first_record = next(item for item in recovered if item["connector_id"] == first["connector_id"])

    assert first_record["status"] == "error"
    assert first_record["error"] == "sync_task_mismatch"


def test_finalizing_task_persistence_failure_has_stable_connector_api_error(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    _configure_tasks(tmp_path, monkeypatch)
    connector = _connector_record(tmp_path, monkeypatch, "ws-bridge")
    data_workbench._CONNECTOR_STORE.update("ws-bridge", connector["connector_id"], status="finalizing", pending_task_id="task_unavailable", sync_token="f" * 32)
    monkeypatch.setattr(data_workbench, "get_task", lambda _task_id: (_ for _ in ()).throw(TaskPersistenceError("secret-bearing failure")))

    async def call() -> None:
        with pytest.raises(HTTPException) as raised:
            await data_workbench._call(data_workbench.list_connectors, "ws-bridge")
        assert raised.value.status_code == 503
        assert raised.value.detail == {"category": "connector", "code": "connector_task_unavailable"}

    asyncio.run(call())


def test_sync_task_completion_conflict_is_stable_and_never_leaves_connected(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_tasks(tmp_path, monkeypatch)
    connector = _connector_record(tmp_path, monkeypatch, "ws-bridge")
    original_update = data_workbench.update_task
    monkeypatch.setattr(data_workbench, "preview_sql_table", lambda *_args, **_kwargs: {"columns": [{"name": "id"}], "rows": [[1]]})
    monkeypatch.setattr(data_workbench, "create_workspace_upload_job", lambda **_kwargs: {"workspace_id": "ws-bridge", "ingest_job_id": "ingest-1", "documents": [{"source_file": "raw_docs/new.csv", "ingest_job_id": "ingest-1"}]})
    monkeypatch.setattr(data_workbench, "run_workspace_ingest_job", lambda *_args: {"state": "ready"})
    monkeypatch.setattr(data_workbench, "_record_connector_lineage", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(data_workbench, "_record_import_history", lambda *_args, **_kwargs: None)

    def conflict_completion(task_id, **changes):
        if changes.get("status") == "completed":
            return task_store.get_task(task_id)
        return original_update(task_id, **changes)

    monkeypatch.setattr(data_workbench, "update_task", conflict_completion)

    with pytest.raises(data_workbench.ConnectorTaskUnavailableError):
        data_workbench.import_sql_table("ws-bridge", {"connection_id": connector["connector_id"], "table": "sales"})

    record = data_workbench._CONNECTOR_STORE.get("ws-bridge", connector["connector_id"])
    assert record["status"] == "error"
    assert record["error"] == "connector_task_unavailable"


def test_missing_key_vault_secret_while_syncing_marks_durable_record_error(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.connector_secret_store import SecretExpiredError
    from backend.connector_secret_store import expected_secret_reference

    class MissingVault:
        persistence = "key_vault"

        def put(self, workspace_id, connector_id, _secret):
            return expected_secret_reference(self.persistence, workspace_id, connector_id)

        def get(self, *_args):
            raise SecretExpiredError("connector_secret_missing")

        def delete(self, *_args):
            return None

    store = ConnectorStore(tmp_path / "connectors")
    connector = store.create("ws-bridge", "sql", {"server": "sql.example"}, {"password": "secret"}, MissingVault())
    store.update("ws-bridge", connector["connector_id"], status="syncing")
    monkeypatch.setattr(data_workbench, "_CONNECTOR_STORE", store)
    monkeypatch.setattr(data_workbench, "_SECRET_STORE", MissingVault())

    with pytest.raises(SecretExpiredError):
        data_workbench._connector_payload("ws-bridge", connector["connector_id"], "sql", allow_syncing=True)

    record = store.get("ws-bridge", connector["connector_id"])
    assert record["status"] == "error"
    assert record["error"] == "connector_secret_expired"


def test_connector_sync_task_persistence_error_has_a_stable_api_shape() -> None:
    from fastapi import HTTPException

    def invoke() -> None:
        raise data_workbench.ConnectorTaskUnavailableError()

    async def call() -> None:
        with pytest.raises(HTTPException) as raised:
            await data_workbench._call(invoke)
        assert raised.value.status_code == 503
        assert raised.value.detail == {"category": "connector", "code": "connector_task_unavailable"}

    asyncio.run(call())


def test_session_reconnect_missing_on_this_instance_is_only_an_ephemeral_expired_projection(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.connector_secret_store import SessionSecretStore

    store = ConnectorStore(tmp_path / "connectors")
    first = SessionSecretStore()
    connector = store.create("ws-bridge", "sql", {"server": "sql.example"}, {"password": "secret"}, first)
    monkeypatch.setattr(data_workbench, "_CONNECTOR_STORE", store)
    monkeypatch.setattr(data_workbench, "_SECRET_STORE", SessionSecretStore())

    result = data_workbench.reconnect_connector("ws-bridge", connector["connector_id"])

    assert result["status"] == "expired"
    assert result["requires_credentials"] is True
    assert store.get("ws-bridge", connector["connector_id"])["status"] == "connected"


def test_upload_background_ingest_completes_linked_generic_task(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_tasks(tmp_path, monkeypatch)
    task = task_store.create_task(
        {"workspace_id": "ws-bridge", "task_type": "workspace.ingest", "action": "file.create", "result": {"ingest_job_id": "ingest-2"}},
        {"email": "owner@contoso.com"},
    )
    monkeypatch.setattr(app_module, "run_workspace_ingest_job", lambda *_args: {"state": "completed"})

    asyncio.run(app_module._run_upload_ingest_background("ws-bridge", "ingest-2", task["task_id"]))

    assert task_store.get_task(task["task_id"])["status"] == "completed"


@pytest.mark.parametrize(("ui_context", "task_type"), [({}, "analysis.run"), ({"iteration_inputs": [{"metric": "conversion"}]}, "analysis.iterate")])
def test_chat_stream_creates_a_durable_analysis_task_and_exposes_its_id(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    ui_context: dict,
    task_type: str,
) -> None:
    _configure_tasks(tmp_path, monkeypatch)

    async def stream(_request):
        yield 'event: ready\ndata: {"conversation_id":"run-chat-1"}\n\n'
        yield 'event: final\ndata: {"conversation_id":"run-chat-1","artifact":{"version_id":"version-2"}}\n\n'

    monkeypatch.setattr(app_module, "orchestrate_chat", stream)
    response = TestClient(app).post(
        "/api/chat",
        json={"workspace_id": "ws-bridge", "message": "Run analysis", "ui_context": ui_context},
    )

    assert response.status_code == 200
    assert response.headers["x-dataforge-task-id"]
    assert "event: final" in response.text
    task = task_store.get_task(response.headers["x-dataforge-task-id"])
    assert task["task_type"] == task_type
    assert task["action"] == "analysis.run"
    assert task["status"] == "completed"
    assert task["result"] == {"run_id": "run-chat-1", "version_id": "version-2"}


def test_workspace_auto_analysis_stream_uses_run_id_without_message_audit(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_tasks(tmp_path, monkeypatch)
    audit_actions: list[str] = []

    async def stream(request):
        assert request.origin == "workspace_auto_analysis"
        assert request.conversation_id is None
        yield 'event: ready\ndata: {"run_id":"run-auto-bridge","conversation_id":null,"origin":"workspace_auto_analysis"}\n\n'
        yield 'event: final\ndata: {"run_id":"run-auto-bridge","conversation_id":null,"artifact":{"run_id":"run-auto-bridge","version_id":"version-3"}}\n\n'

    monkeypatch.setattr(app_module, "_require_workspace_action", lambda *_args: None)
    monkeypatch.setattr(app_module, "_audit_required", lambda _request, _workspace, action, *_args: audit_actions.append(action))
    monkeypatch.setattr(app_module, "orchestrate_chat", stream)

    response = TestClient(app).post(
        "/api/chat",
        json={
            "workspace_id": "ws-bridge",
            "message": "Analyze the workspace",
            "origin": "workspace_auto_analysis",
            "persist_messages": False,
            "conversation_id": None,
        },
    )

    assert response.status_code == 200
    assert audit_actions == ["analysis.run"]
    task = task_store.get_task(response.headers["x-dataforge-task-id"])
    assert task["result"] == {"run_id": "run-auto-bridge", "version_id": "version-3"}


def test_cancelled_chat_stream_leaves_the_durable_task_cancelled(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_tasks(tmp_path, monkeypatch)
    task = task_store.create_task({"workspace_id": "ws-bridge", "task_type": "analysis.run", "action": "analysis.run"}, actor={})
    task_store.claim_task(task["task_id"], "chat-stream")

    async def cancelled_stream(_request):
        raise asyncio.CancelledError()
        yield ""  # pragma: no cover

    monkeypatch.setattr(app_module, "orchestrate_chat", cancelled_stream)

    async def consume() -> None:
        async for _ in app_module._task_backed_chat_stream(
            app_module.ChatRequest(workspace_id="ws-bridge", message="Run analysis"),
            task["task_id"],
        ):
            pass

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(consume())
    assert task_store.get_task(task["task_id"])["status"] == "cancelled"


def test_running_chat_cancel_discards_later_final_frame_and_result(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_tasks(tmp_path, monkeypatch)
    task = task_store.create_task({"workspace_id": "ws-bridge", "task_type": "analysis.run", "action": "analysis.run"}, actor={})
    task_store.claim_task(task["task_id"], "chat-stream")

    allow_cancel = asyncio.Event()

    async def stream(_request):
        yield 'event: ready\ndata: {"conversation_id":"run-cancel"}\n\n'
        await allow_cancel.wait()
        task_store.request_cancel(task["task_id"])
        yield 'event: final\ndata: {"conversation_id":"run-cancel","artifact":{"version_id":"version-cancel"}}\n\n'

    monkeypatch.setattr(app_module, "orchestrate_chat", stream)

    async def consume() -> list[str]:
        stream_iter = app_module._task_backed_chat_stream(
            app_module.ChatRequest(workspace_id="ws-bridge", message="Run analysis"),
            task["task_id"],
        )
        first = await anext(stream_iter)
        allow_cancel.set()
        return [first, *[frame async for frame in stream_iter]]

    frames = asyncio.run(consume())
    assert len(frames) == 1
    assert "event: final" not in "".join(frames)
    final = task_store.get_task(task["task_id"])
    assert final["status"] == "cancelled"
    assert final["result"] == {"run_id": "run-cancel"}


@pytest.mark.parametrize(
    ("event", "payload", "terminal_status"),
    [
        ("final", '{"conversation_id":"run-late","artifact":{"version_id":"version-late"}}', "completed"),
        ("error", '{"message":"late failure"}', "failed"),
    ],
)
def test_chat_cancel_between_initial_check_and_terminal_yield_hides_late_frame(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    event: str,
    payload: str,
    terminal_status: str,
) -> None:
    _configure_tasks(tmp_path, monkeypatch)
    task = task_store.create_task({"workspace_id": "ws-bridge", "task_type": "analysis.run", "action": "analysis.run"}, actor={})
    task_store.claim_task(task["task_id"], "chat-stream")

    async def stream(_request):
        yield f"event: {event}\ndata: {payload}\n\n"

    monkeypatch.setattr(app_module, "orchestrate_chat", stream)
    original_update_task = app_module.update_task

    def cancel_before_terminal_task_update(task_id: str, **changes):
        if task_id == task["task_id"] and changes.get("status") == terminal_status:
            task_store.request_cancel(task_id)
        return original_update_task(task_id, **changes)

    monkeypatch.setattr(app_module, "update_task", cancel_before_terminal_task_update)

    async def consume() -> list[str]:
        return [frame async for frame in app_module._task_backed_chat_stream(
            app_module.ChatRequest(workspace_id="ws-bridge", message="Run analysis"),
            task["task_id"],
        )]

    assert asyncio.run(consume()) == []
    assert task_store.get_task(task["task_id"])["status"] == "cancelled"


def test_connector_import_task_requires_connector_manage_action(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_tasks(tmp_path, monkeypatch)
    upload = {"workspace_id": "ws-bridge", "ingest_job_id": "ingest-connector", "documents": []}
    monkeypatch.setattr(data_workbench, "preview_sql_table", lambda *_args, **_kwargs: {"columns": [{"name": "id"}], "rows": [[1]]})
    monkeypatch.setattr(data_workbench, "create_workspace_upload_job", lambda **_kwargs: dict(upload))
    monkeypatch.setattr(data_workbench, "run_workspace_ingest_job", lambda *_args: {"state": "ready"})
    monkeypatch.setattr(data_workbench, "_record_import_history", lambda *_args: None)
    connector = _connector_record(tmp_path, monkeypatch, "ws-bridge")

    data_workbench.import_sql_table("ws-bridge", {"connection_id": connector["connector_id"], "table": "sales"}, actor={"email": "owner@contoso.com"})

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


def test_workbench_task_persistence_failure_returns_503_for_file_create(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(data_workbench, "create_workspace_file", lambda *_args, **_kwargs: (_ for _ in ()).throw(TaskPersistenceError("durable unavailable")))

    response = TestClient(app, raise_server_exceptions=False).post("/api/workspaces/ws-bridge/files", json={"name": "new.csv"})

    assert response.status_code == 503
    assert response.json()["detail"] == "Durable task storage is unavailable"


def test_workbench_task_persistence_failure_returns_503_for_connector_ingest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(data_workbench, "import_sql_table", lambda *_args, **_kwargs: (_ for _ in ()).throw(TaskPersistenceError("durable unavailable")))

    response = TestClient(app, raise_server_exceptions=False).post("/api/workspaces/ws-bridge/connectors/sql/import", json={"connection_id": "connector", "table": "sales"})

    assert response.status_code == 503
    assert response.json()["detail"] == "Durable task storage is unavailable"


def test_workbench_task_persistence_failure_returns_503_for_selected_file_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    document = {"id": "file-1", "name": "pending.csv", "source_file": "pending.csv", "status": "processing", "ingest_job_id": "ingest-analysis", "bytes": 3}
    monkeypatch.setattr(data_workbench, "get_workspace_detail", lambda _workspace_id: {"documents": [dict(document)]})
    monkeypatch.setattr(data_workbench, "_find_document", lambda *_args, **_kwargs: dict(document))
    monkeypatch.setattr(data_workbench, "list_tasks", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(data_workbench, "create_task", lambda *_args, **_kwargs: (_ for _ in ()).throw(TaskPersistenceError("durable unavailable")))

    response = TestClient(app, raise_server_exceptions=False).post("/api/workspaces/ws-bridge/files/analyze", json={"file_ids": ["file-1"]})

    assert response.status_code == 503
    assert response.json()["detail"] == "Durable task storage is unavailable"


def test_artifact_blob_and_compensation_failure_leaves_prepared_task_for_recovery(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_tasks(tmp_path, monkeypatch)
    monkeypatch.setattr(artifact_jobs, "ARTIFACT_JOB_DIR", tmp_path / "artifact-jobs")
    monkeypatch.setattr(artifact_jobs, "blob_configured", lambda: True)
    monkeypatch.setattr(artifact_jobs, "upload_blob_json", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("blob unavailable")))
    monkeypatch.setattr(artifact_jobs, "get_run", lambda run_id: {"workspace_id": "ws-bridge", "run_id": run_id, "artifact": {"feasibility": {}}})
    original_update = artifact_jobs.update_task
    monkeypatch.setattr(artifact_jobs, "update_task", lambda *_args, **_kwargs: (_ for _ in ()).throw(TaskPersistenceError("compensation unavailable")))

    with pytest.raises(RuntimeError, match="durable artifact job"):
        artifact_jobs.create_artifact_job({"workspace_id": "ws-bridge", "conversation_id": "run-1", "kinds": ["pdf"]}, actor={})

    task = task_store.list_tasks("ws-bridge")[0]
    assert task["status"] == "preparing"
    assert artifact_jobs._claim_linked_task({"task_id": task["task_id"]}) is None

    monkeypatch.setattr(artifact_jobs, "blob_configured", lambda: False)
    monkeypatch.setattr(artifact_jobs, "update_task", original_update)
    recovered = artifact_jobs.recover_prepared_artifact_tasks("ws-bridge")

    assert recovered == []
    assert task_store.get_task(task["task_id"])["status"] == "failed"
