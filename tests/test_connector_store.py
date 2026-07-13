from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
import multiprocessing
from pathlib import Path

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


def _disconnect_from_separate_process(root: str, workspace_id: str, connector_id: str, start, results) -> None:
    from backend.connector_store import ConnectorStore

    start.wait(10)
    try:
        results.put(("ok", ConnectorStore(Path(root)).update(workspace_id, connector_id, status="disconnected")["revision"]))
    except Exception as exc:
        results.put(("error", type(exc).__name__))


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

    with pytest.raises(ConnectorDeleteError) as raised:
        store.delete("ws-1", connector["connector_id"], secrets)
    assert raised.value.code == "connector_secret_delete_failed"

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


def test_remote_update_uses_revision_cas_and_reports_a_stable_conflict(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.connector_store as connector_store
    from backend.connector_store import ConnectorConflictError, ConnectorStore

    remote: dict[str, dict] = {}
    monkeypatch.setattr(connector_store, "blob_configured", lambda: True)
    monkeypatch.setattr(connector_store, "upload_blob_json", lambda name, value: remote.__setitem__(name, dict(value)) or {})
    monkeypatch.setattr(connector_store, "download_blob_json_strict", lambda name: remote.get(name))
    monkeypatch.setattr(connector_store, "list_blob_json_named_strict", lambda prefix: [(name, value) for name, value in remote.items() if name.startswith(prefix)], raising=False)
    calls: list[tuple[str, int]] = []

    def cas(name, *, expected_revision, changes):
        calls.append((name, expected_revision))
        return None

    monkeypatch.setattr(connector_store, "compare_and_swap_blob_json", cas, raising=False)
    connector = ConnectorStore(tmp_path / "connectors").create("ws-1", "sql", {"server": "sql.example"}, {"password": "secret"}, FakeSecretStore())

    with pytest.raises(ConnectorConflictError) as raised:
        ConnectorStore(tmp_path / "other").update("ws-1", connector["connector_id"], expected_revision=connector["revision"], status="disconnected")

    assert raised.value.code == "connector_conflict"
    assert calls and calls[0][1] == connector["revision"]


def test_local_record_updates_are_serialized_without_lost_revision(tmp_path) -> None:
    from backend.connector_store import ConnectorStore

    secrets = FakeSecretStore()
    root = tmp_path / "connectors"
    connector = ConnectorStore(root).create("ws-1", "sql", {"server": "sql.example"}, {"password": "secret"}, secrets)

    def disconnect() -> int:
        return ConnectorStore(root).update("ws-1", connector["connector_id"], status="disconnected")["revision"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        revisions = list(pool.map(lambda _value: disconnect(), range(2)))

    final = ConnectorStore(root).get("ws-1", connector["connector_id"])
    assert sorted(revisions) == [2, 3]
    assert final["revision"] == 3
    assert not list((root / "ws-1").glob("*.tmp"))


def test_local_sidecar_serializes_updates_across_separate_processes(tmp_path) -> None:
    from backend.connector_store import ConnectorStore

    root = tmp_path / "connectors"
    connector = ConnectorStore(root).create("ws-1", "sql", {"server": "sql.example"}, {"password": "secret"}, FakeSecretStore())
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    workers = [
        context.Process(target=_disconnect_from_separate_process, args=(str(root), "ws-1", connector["connector_id"], start, results))
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    start.set()
    for worker in workers:
        worker.join(20)

    assert all(worker.exitcode == 0 for worker in workers)
    assert sorted(results.get(timeout=5) for _ in workers) == [("ok", 2), ("ok", 3)]
    assert ConnectorStore(root).get("ws-1", connector["connector_id"])["revision"] == 3


def test_blob_list_rejects_record_under_a_different_filename(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.connector_store as connector_store
    from backend.connector_store import ConnectorStore

    secrets = FakeSecretStore()
    store = ConnectorStore(tmp_path / "connectors")
    connector = store.create("ws-1", "sql", {"server": "sql.example"}, {"password": "secret"}, secrets)
    monkeypatch.setattr(connector_store, "blob_configured", lambda: True)
    monkeypatch.setattr(
        connector_store,
        "list_blob_json_named_strict",
        lambda _prefix: [("connectors/ws-1/other.json", connector)],
        raising=False,
    )

    with pytest.raises(ValueError, match="storage path"):
        store.list("ws-1")


def test_delete_resumes_from_secret_deleted_without_deleting_secret_again(tmp_path) -> None:
    from backend.connector_store import ConnectorStore

    class NoSecondDeleteStore(FakeSecretStore):
        def delete(self, *_args) -> None:
            raise AssertionError("secret delete must be skipped after secret_deleted")

    secrets = NoSecondDeleteStore()
    store = ConnectorStore(tmp_path / "connectors")
    connector = store.create("ws-1", "sql", {"server": "sql.example"}, {"password": "secret"}, secrets)
    store.update("ws-1", connector["connector_id"], status="deleting", delete_pending=True, delete_phase="secret_deleted")

    store.delete("ws-1", connector["connector_id"], secrets)

    with pytest.raises(FileNotFoundError):
        store.get("ws-1", connector["connector_id"])


def test_delete_is_idempotent_when_secret_or_record_is_already_missing(tmp_path) -> None:
    from backend.connector_store import ConnectorStore

    class MissingSecretStore(FakeSecretStore):
        def delete(self, *_args) -> None:
            raise KeyError("missing")

    store = ConnectorStore(tmp_path / "connectors")
    connector = store.create("ws-1", "sql", {"server": "sql.example"}, {"password": "secret"}, MissingSecretStore())
    store.delete("ws-1", connector["connector_id"], MissingSecretStore())
    store.delete("ws-1", connector["connector_id"], MissingSecretStore())


def test_delete_keeps_secret_deleted_phase_when_record_delete_fails_then_recovers(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.connector_store import ConnectorDeleteError, ConnectorStore

    secrets = FakeSecretStore()
    store = ConnectorStore(tmp_path / "connectors")
    connector = store.create("ws-1", "sql", {"server": "sql.example"}, {"password": "secret"}, secrets)
    original = store._delete_record
    monkeypatch.setattr(store, "_delete_record", lambda *_args: (_ for _ in ()).throw(OSError("disk unavailable")))

    with pytest.raises(ConnectorDeleteError) as raised:
        store.delete("ws-1", connector["connector_id"], secrets)
    assert raised.value.code == "connector_record_delete_failed"
    assert store.get("ws-1", connector["connector_id"])["delete_phase"] == "secret_deleted"

    monkeypatch.setattr(store, "_delete_record", original)
    store.delete("ws-1", connector["connector_id"], secrets)
