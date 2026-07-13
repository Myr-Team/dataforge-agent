from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import backend.data_workbench as data_workbench
from backend.app import app


class FakeSecretStore:
    persistence = "key_vault"

    def __init__(self) -> None:
        self.values: dict[str, dict[str, str]] = {}

    def put(self, workspace_id: str, connector_id: str, secret: dict[str, str]) -> str:
        ref = f"kv:{connector_id}"
        self.values[ref] = dict(secret)
        return ref

    def get(self, reference: str) -> dict[str, str]:
        return dict(self.values[reference])

    def delete(self, reference: str) -> None:
        self.values.pop(reference)


def _configure_durable_connectors(tmp_path, monkeypatch: pytest.MonkeyPatch) -> FakeSecretStore:
    from backend.connector_store import ConnectorStore

    secrets = FakeSecretStore()
    monkeypatch.setattr(data_workbench, "_CONNECTOR_STORE", ConnectorStore(tmp_path / "connectors"))
    monkeypatch.setattr(data_workbench, "_SECRET_STORE", secrets)
    return secrets


def test_connector_api_redacts_credentials_and_reconnects_after_session_state_clear(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_durable_connectors(tmp_path, monkeypatch)
    monkeypatch.setattr(data_workbench, "_sql_tables", lambda _payload: [{"schema": "dbo", "name": "sales", "id": "dbo.sales"}])
    client = TestClient(app)

    created = client.post(
        "/api/workspaces/ws-safe/connectors/sql/connect",
        json={"server": "sql.example", "database": "sales", "username": "reader", "password": "very-secret"},
    )
    assert created.status_code == 200
    payload = created.json()
    connector_id = payload["connector_id"]
    for unsafe in ("very-secret", "reader", "password", "username", "connection_string", "credential"):
        assert unsafe not in json.dumps(payload)

    data_workbench.clear_connector_sessions()
    reconnected = client.post(f"/api/workspaces/ws-safe/connectors/{connector_id}/reconnect")

    assert reconnected.status_code == 200
    assert reconnected.json()["status"] == "connected"
    assert "very-secret" not in json.dumps(reconnected.json())


def test_sync_creates_safe_durable_task_and_connector_lineage(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_durable_connectors(tmp_path, monkeypatch)
    monkeypatch.setattr(data_workbench, "_sql_tables", lambda _payload: [{"schema": "dbo", "name": "sales", "id": "dbo.sales"}])
    monkeypatch.setattr(data_workbench, "_sql_preview", lambda *_args: {"columns": [{"name": "id"}], "rows": [[1]], "source": {"kind": "sql", "table": "dbo.sales"}})
    monkeypatch.setattr(
        data_workbench,
        "create_workspace_upload_job",
        lambda **_kwargs: {"workspace_id": "ws-safe", "ingest_job_id": "job-sync", "documents": [{"id": "file-sync", "source_file": "raw_docs/sales.csv"}]},
    )
    monkeypatch.setattr(data_workbench, "run_workspace_ingest_job", lambda *_args: {"state": "ready"})
    lineage: dict[str, object] = {}
    monkeypatch.setattr(data_workbench, "_record_connector_lineage", lambda *_args, **kwargs: lineage.update(kwargs))
    client = TestClient(app)
    created = client.post(
        "/api/workspaces/ws-safe/connectors/sql/connect",
        json={"server": "sql.example", "database": "sales", "username": "reader", "password": "very-secret"},
    ).json()

    response = client.post(
        f"/api/workspaces/ws-safe/connectors/{created['connector_id']}/sync",
        json={"table": "dbo.sales", "cursor": "2026-07-13T00:00:00Z", "watermark": "id:1"},
    )

    assert response.status_code == 202
    serialized = json.dumps(response.json())
    assert "very-secret" not in serialized
    assert response.json()["task"]["task_type"] == "connector.sql.sync"
    assert lineage["lineage"]["connector_id"] == created["connector_id"]
    assert lineage["lineage"]["table"] == "dbo.sales"
    assert lineage["lineage"]["cursor"] == "2026-07-13T00:00:00Z"
