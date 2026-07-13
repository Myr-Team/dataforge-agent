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
        from backend.connector_secret_store import expected_secret_reference

        ref = expected_secret_reference(self.persistence, workspace_id, connector_id)
        self.values[ref] = dict(secret)
        return ref

    def reference_for(self, workspace_id: str, connector_id: str) -> str:
        from backend.connector_secret_store import expected_secret_reference

        return expected_secret_reference(self.persistence, workspace_id, connector_id)

    def get(self, workspace_id: str, connector_id: str, reference: str) -> dict[str, str]:
        assert reference == self.reference_for(workspace_id, connector_id)
        return dict(self.values[reference])

    def delete(self, workspace_id: str, connector_id: str, reference: str) -> None:
        assert reference == self.reference_for(workspace_id, connector_id)
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
        json={"table": "dbo.sales"},
    )

    assert response.status_code == 202
    serialized = json.dumps(response.json())
    assert "very-secret" not in serialized
    assert response.json()["task"]["task_type"] == "connector.sql.sync"
    assert lineage["lineage"]["connector_id"] == created["connector_id"]
    assert lineage["lineage"]["table"] == "dbo.sales"
    assert lineage["lineage"]["row_count"] == 1
    assert "cursor" not in lineage["lineage"]


def test_connector_lifecycle_errors_expose_only_stable_codes(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_durable_connectors(tmp_path, monkeypatch)
    monkeypatch.setattr(data_workbench, "_blob_containers", lambda _payload: (_ for _ in ()).throw(RuntimeError("sig=never-expose;password=never-expose")))
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post("/api/workspaces/ws-safe/connectors/blob/connect", json={"account": "storageacct", "sas": "sig=never-expose"})

    assert response.status_code == 400
    assert response.json()["detail"] == {"category": "connector", "code": "blob_connect_failed"}
    assert "never-expose" not in response.text


@pytest.mark.parametrize("field", ["cursor", "watermark"])
@pytest.mark.parametrize("value", ["Bearer eyJhbGci", "sig=abc", "Password=very-secret", "https://reader:very-secret@example"])
def test_sync_rejects_client_controlled_cursor_and_watermark(tmp_path, monkeypatch: pytest.MonkeyPatch, field: str, value: str) -> None:
    _configure_durable_connectors(tmp_path, monkeypatch)
    monkeypatch.setattr(data_workbench, "_sql_tables", lambda _payload: [{"schema": "dbo", "name": "sales", "id": "dbo.sales"}])
    client = TestClient(app)
    connector = client.post(
        "/api/workspaces/ws-safe/connectors/sql/connect",
        json={"server": "sql.example", "database": "sales", "username": "reader", "password": "very-secret"},
    ).json()

    response = client.post(f"/api/workspaces/ws-safe/connectors/{connector['connector_id']}/sync", json={"table": "dbo.sales", field: value})

    assert response.status_code == 422
    assert value not in response.text
