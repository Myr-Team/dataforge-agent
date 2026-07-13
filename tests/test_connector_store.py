from __future__ import annotations

import json

import pytest


class FakeSecretStore:
    persistence = "key_vault"

    def __init__(self) -> None:
        self.values: dict[str, dict[str, str]] = {}
        self.deleted: list[str] = []

    def put(self, workspace_id: str, connector_id: str, secret: dict[str, str]) -> str:
        from backend.connector_secret_store import expected_secret_reference

        reference = expected_secret_reference(self.persistence, workspace_id, connector_id)
        self.values[reference] = dict(secret)
        return reference

    def reference_for(self, workspace_id: str, connector_id: str) -> str:
        from backend.connector_secret_store import expected_secret_reference

        return expected_secret_reference(self.persistence, workspace_id, connector_id)

    def get(self, workspace_id: str, connector_id: str, reference: str) -> dict[str, str]:
        assert reference == self.reference_for(workspace_id, connector_id)
        return dict(self.values[reference])

    def delete(self, workspace_id: str, connector_id: str, reference: str) -> None:
        assert reference == self.reference_for(workspace_id, connector_id)
        self.deleted.append(reference)
        self.values.pop(reference)


def test_connector_record_contains_only_opaque_secret_reference(tmp_path) -> None:
    from backend.connector_store import ConnectorStore

    secrets = FakeSecretStore()
    store = ConnectorStore(tmp_path / "connectors")
    connector = store.create(
        workspace_id="ws-1",
        kind="sql",
        metadata={"server": "sql.example", "database": "sales"},
        secret={"username": "reader", "password": "very-secret", "connection_string": "Server=sql.example;Pwd=very-secret"},
        secret_store=secrets,
    )

    serialized = json.dumps(connector)
    assert connector["secret_ref"].startswith("kv:")
    for prohibited in ("very-secret", "reader", "connection_string", "password", "username"):
        assert prohibited not in serialized
    assert store.get("ws-1", connector["connector_id"]) == connector


def test_durable_record_reconnects_after_new_store_instance(tmp_path) -> None:
    from backend.connector_store import ConnectorStore

    secrets = FakeSecretStore()
    created = ConnectorStore(tmp_path / "connectors").create(
        workspace_id="ws-1",
        kind="blob",
        metadata={"account": "storageacct"},
        secret={"sas": "secret-sas"},
        secret_store=secrets,
    )

    restarted = ConnectorStore(tmp_path / "connectors")
    record, payload = restarted.reconnect("ws-1", created["connector_id"], secrets)

    assert record["status"] == "connected"
    assert payload == {"sas": "secret-sas"}
    assert "secret-sas" not in json.dumps(record)


def test_configured_blob_store_recovers_only_redacted_connector_record(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.connector_store as connector_store
    from backend.connector_store import ConnectorStore

    remote: dict[str, dict] = {}
    monkeypatch.setattr(connector_store, "blob_configured", lambda: True, raising=False)
    monkeypatch.setattr(connector_store, "upload_blob_json", lambda name, value: remote.__setitem__(name, dict(value)) or {}, raising=False)
    monkeypatch.setattr(connector_store, "download_blob_json_strict", lambda name: remote.get(name), raising=False)
    monkeypatch.setattr(connector_store, "list_blob_json_strict", lambda prefix: [value for name, value in remote.items() if name.startswith(prefix)], raising=False)

    secrets = FakeSecretStore()
    created = ConnectorStore(tmp_path / "first").create("ws-1", "sql", {"server": "sql.example"}, {"password": "very-secret"}, secrets)
    recovered = ConnectorStore(tmp_path / "second").get("ws-1", created["connector_id"])

    assert remote
    assert recovered == created
    assert "very-secret" not in json.dumps(remote)


def test_delete_keeps_record_for_recovery_when_secret_delete_fails(tmp_path) -> None:
    from backend.connector_store import ConnectorStore, ConnectorDeleteError

    class FailingSecretStore(FakeSecretStore):
        def delete(self, workspace_id: str, connector_id: str, reference: str) -> None:
            raise RuntimeError("vault unavailable")

    secrets = FailingSecretStore()
    store = ConnectorStore(tmp_path / "connectors")
    connector = store.create("ws-1", "sql", {"server": "sql.example"}, {"password": "very-secret"}, secrets)

    with pytest.raises(ConnectorDeleteError, match="secret delete failed"):
        store.delete("ws-1", connector["connector_id"], secrets)

    record = store.get("ws-1", connector["connector_id"])
    assert record["status"] == "error"
    assert record["delete_pending"] is True
    assert "very-secret" not in json.dumps(record)


def test_store_fails_closed_when_record_identity_or_reference_is_forged(tmp_path) -> None:
    from backend.connector_store import ConnectorStore

    secrets = FakeSecretStore()
    store = ConnectorStore(tmp_path / "connectors")
    connector = store.create("ws-1", "sql", {"server": "sql.example"}, {"password": "very-secret"}, secrets)
    path = tmp_path / "connectors" / "ws-1" / f"{connector['connector_id']}.json"
    forged = json.loads(path.read_text(encoding="utf-8"))
    forged["workspace_id"] = "ws-other"
    path.write_text(json.dumps(forged), encoding="utf-8")

    with pytest.raises(ValueError, match="identity"):
        store.get("ws-1", connector["connector_id"])
