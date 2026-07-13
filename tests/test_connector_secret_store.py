from __future__ import annotations

import pytest


def test_session_secret_store_reports_expiry_and_never_returns_credential_in_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.connector_secret_store import SessionSecretStore, SecretExpiredError

    now = [1000.0]
    store = SessionSecretStore(ttl_seconds=30, clock=lambda: now[0])
    reference = store.put("ws-secret", "sql-1", {"password": "not-for-records", "username": "reader"})

    assert reference.startswith("session:")
    assert "not-for-records" not in reference
    assert store.persistence == "session_only"
    assert store.get("ws-secret", "sql-1", reference)["password"] == "not-for-records"

    now[0] += 31
    with pytest.raises(SecretExpiredError):
        store.get("ws-secret", "sql-1", reference)


def test_key_vault_configuration_does_not_fall_back_when_client_initialization_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.connector_secret_store as secret_store

    monkeypatch.setenv("DF_KEY_VAULT_URL", "https://example.vault.azure.net")
    monkeypatch.setattr(secret_store, "KeyVaultSecretStore", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("identity unavailable")))

    with pytest.raises(RuntimeError, match="identity unavailable"):
        secret_store.secret_store_from_environment()


def test_key_vault_store_uses_opaque_reference_and_default_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.connector_secret_store as secret_store

    calls: dict[str, object] = {}

    class Credential:
        pass

    class Client:
        def __init__(self, vault_url, credential):
            calls["url"] = vault_url
            calls["credential"] = credential
            self.values: dict[str, str] = {}

        def set_secret(self, name, value):
            self.values[name] = value

        def get_secret(self, name):
            return type("Secret", (), {"value": self.values[name]})()

        def begin_delete_secret(self, name):
            self.values.pop(name)
            return type("Poller", (), {"wait": lambda self: None})()

    monkeypatch.setattr(secret_store, "DefaultAzureCredential", Credential)
    monkeypatch.setattr(secret_store, "SecretClient", Client)

    store = secret_store.KeyVaultSecretStore("https://example.vault.azure.net")
    reference = store.put("ws-secret", "sql-1", {"password": "not-for-records"})

    assert reference.startswith("kv:")
    assert "not-for-records" not in reference
    assert isinstance(calls["credential"], Credential)
    assert store.get("ws-secret", "sql-1", reference) == {"password": "not-for-records"}
    store.delete("ws-secret", "sql-1", reference)


def test_session_secret_reference_is_deterministically_bound_to_workspace_and_connector() -> None:
    from backend.connector_secret_store import SecretReferenceError, SessionSecretStore

    store = SessionSecretStore()
    reference = store.put("ws-a", "sql-a", {"password": "not-for-records"})

    assert reference == store.reference_for("ws-a", "sql-a")
    with pytest.raises(SecretReferenceError):
        store.get("ws-b", "sql-a", reference)
    with pytest.raises(SecretReferenceError):
        store.delete("ws-a", "sql-b", reference)


def test_key_vault_store_and_connector_store_share_the_exact_reference_contract(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.connector_secret_store as secret_store
    from backend.connector_store import ConnectorStore

    class Credential:
        pass

    class Client:
        values: dict[str, str] = {}
        def __init__(self, **_kwargs): pass
        def set_secret(self, name, value): self.values[name] = value
        def get_secret(self, name): return type("Secret", (), {"value": self.values[name]})()
        def begin_delete_secret(self, name): self.values.pop(name, None); return type("Poller", (), {"wait": lambda self: None})()

    monkeypatch.setattr(secret_store, "DefaultAzureCredential", Credential)
    monkeypatch.setattr(secret_store, "SecretClient", Client)
    vault = secret_store.KeyVaultSecretStore("https://example.vault.azure.net")
    connector = ConnectorStore(tmp_path / "connectors").create("ws-1", "sql", {"server": "sql.example"}, {"password": "secret"}, vault)

    assert connector["secret_ref"] == vault.reference_for("ws-1", connector["connector_id"])
    assert vault.get("ws-1", connector["connector_id"], connector["secret_ref"]) == {"password": "secret"}
