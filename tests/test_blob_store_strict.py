from __future__ import annotations

import pytest
from azure.core.exceptions import ResourceNotFoundError

import backend.blob_store as blob_store
from backend.blob_store import BlobJsonReadError


class _Download:
    def __init__(self, value: bytes) -> None:
        self._value = value

    def readall(self) -> bytes:
        return self._value


class _Blob:
    def __init__(self, outcome) -> None:
        self._outcome = outcome

    def download_blob(self):
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return _Download(self._outcome)


class _Container:
    def __init__(self, outcome, entries=None) -> None:
        self._outcome = outcome
        self._entries = entries or []

    def get_blob_client(self, _name: str) -> _Blob:
        return _Blob(self._outcome)

    def list_blobs(self, **_kwargs):
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._entries


def test_strict_blob_json_helpers_only_treat_not_found_as_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(blob_store, "blob_configured", lambda: True)
    missing = ResourceNotFoundError(message="missing", response=None)
    monkeypatch.setattr(blob_store, "_container_client", lambda: _Container(missing))

    assert blob_store.download_blob_json_strict("tasks/task_missing.json") is None
    assert blob_store.list_blob_json_strict("tasks/") == []

    monkeypatch.setattr(blob_store, "_container_client", lambda: _Container(RuntimeError("timeout")))
    with pytest.raises(BlobJsonReadError):
        blob_store.download_blob_json_strict("tasks/task_timeout.json")
    with pytest.raises(BlobJsonReadError):
        blob_store.list_blob_json_strict("tasks/")


def test_production_workspace_store_uses_system_managed_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    managed_identity = object()
    default_credential = object()

    class _Service:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        def get_container_client(self, name: str) -> str:
            return name

    monkeypatch.setenv("DF_ENVIRONMENT", "production")
    monkeypatch.setenv("STORAGE_ACCOUNT_NAME", "dataforgeprod")
    monkeypatch.delenv("AZURE_STORAGE_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("AZURE_STORAGE_KEY", raising=False)
    monkeypatch.delenv("DF_STORAGE_KEY", raising=False)
    monkeypatch.setattr(blob_store, "ManagedIdentityCredential", lambda: managed_identity, raising=False)
    monkeypatch.setattr(blob_store, "DefaultAzureCredential", lambda: default_credential)
    monkeypatch.setattr(blob_store, "BlobServiceClient", _Service)

    assert blob_store._container_client() == blob_store.DEFAULT_CONTAINER
    assert captured["credential"] is managed_identity
