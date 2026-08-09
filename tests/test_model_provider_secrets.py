from __future__ import annotations

import pytest
from azure.core.exceptions import ResourceNotFoundError

import backend.model_provider_secrets as provider_secrets
from backend.model_provider_secrets import (
    KeyVaultModelProviderSecretStore,
    ModelProviderSecretError,
    model_provider_secret_store_from_environment,
    provider_secret_name,
)
from backend.aws_bedrock_provider import AwsBedrockCredential


class _Secret:
    def __init__(self, value: str) -> None:
        self.value = value


class _SecretClient:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def set_secret(self, name: str, value: str) -> None:
        self.values[name] = value

    def get_secret(self, name: str) -> _Secret:
        return _Secret(self.values[name])


class _FailingSecretClient:
    def set_secret(self, name: str, value: str) -> None:
        raise RuntimeError(f"provider failed with {value}")


class _MissingSecretClient:
    def get_secret(self, name: str) -> _Secret:
        raise ResourceNotFoundError(message=f"missing {name}")


def test_provider_secret_name_is_opaque_stable_and_provider_specific() -> None:
    name = provider_secret_name("tenant-sensitive", "provider_01")

    assert name.startswith("df-model-provider-")
    assert name == provider_secret_name("tenant-sensitive", "provider_01")
    assert "tenant-sensitive" not in name
    assert "provider_01" not in name


def test_key_vault_provider_store_returns_only_a_reference() -> None:
    client = _SecretClient()
    store = KeyVaultModelProviderSecretStore(client=client)

    reference = store.put("tenant-a", "provider_01", "secret-marker")

    assert reference.startswith("kv:df-model-provider-")
    assert store.get("tenant-a", "provider_01", reference) == "secret-marker"
    assert "secret-marker" not in reference


def test_key_vault_provider_store_reports_stored_secret_status() -> None:
    client = _SecretClient()
    store = KeyVaultModelProviderSecretStore(client=client)
    reference = store.put("tenant-a", "provider_01", "secret-marker")

    assert store.status("tenant-a", "provider_01", reference) == "stored"


def test_key_vault_provider_store_reports_missing_secret_status() -> None:
    store = KeyVaultModelProviderSecretStore(client=_MissingSecretClient())
    reference = f"kv:{provider_secret_name('tenant-a', 'provider_01')}"

    assert store.status("tenant-a", "provider_01", reference) == "missing"
    with pytest.raises(ModelProviderSecretError) as captured:
        store.get("tenant-a", "provider_01", reference)

    assert captured.value.code == "provider_secret_missing"


def test_provider_store_round_trips_one_bedrock_credential_bundle() -> None:
    client = _SecretClient()
    store = KeyVaultModelProviderSecretStore(client=client)
    bundle = AwsBedrockCredential(
        access_key_id="AKIAEXAMPLE",
        secret_access_key="secret-marker-123",
    ).to_secret_value()

    reference = store.put("tenant-a", "provider_01", bundle)

    assert AwsBedrockCredential.from_secret_value(
        store.get("tenant-a", "provider_01", reference)
    ).secret_access_key == "secret-marker-123"


def test_provider_store_preserves_opaque_legacy_key_starting_with_brace() -> None:
    store = KeyVaultModelProviderSecretStore(client=_SecretClient())
    legacy_key = '{"not":"bedrock"}'

    reference = store.put("tenant-a", "provider_01", legacy_key)

    assert store.get("tenant-a", "provider_01", reference) == legacy_key


def test_provider_store_redacts_key_from_failure() -> None:
    store = KeyVaultModelProviderSecretStore(client=_FailingSecretClient())

    with pytest.raises(ModelProviderSecretError) as captured:
        store.put("tenant-a", "provider_01", "secret-marker")

    assert captured.value.code == "provider_secret_put_failed"
    assert "secret-marker" not in str(captured.value)
    assert "secret-marker" not in repr(captured.value)


def test_candidate_provider_store_requires_key_vault_configuration() -> None:
    with pytest.raises(ModelProviderSecretError) as captured:
        model_provider_secret_store_from_environment({})

    assert captured.value.code == "provider_key_vault_required"


def test_provider_store_uses_system_identity_when_azure_client_id_is_unrelated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _SystemManagedIdentity:
        def __init__(self, *, client_id: str | None = None) -> None:
            captured["managed_identity_client_id"] = client_id

    class _CapturingSecretClient:
        def __init__(self, *, vault_url: str, credential: object) -> None:
            captured["vault_url"] = vault_url
            captured["credential"] = credential

    monkeypatch.setenv("AZURE_CLIENT_ID", "unrelated-foundry-client")
    monkeypatch.setattr(
        provider_secrets,
        "DefaultAzureCredential",
        lambda: (_ for _ in ()).throw(AssertionError("default credential selected")),
        raising=False,
    )
    monkeypatch.setattr(
        provider_secrets,
        "ManagedIdentityCredential",
        _SystemManagedIdentity,
        raising=False,
    )
    monkeypatch.setattr(
        provider_secrets,
        "SecretClient",
        _CapturingSecretClient,
    )

    store = model_provider_secret_store_from_environment(
        {"DF_KEY_VAULT_URL": "https://example.vault.azure.net/"}
    )

    assert isinstance(store, KeyVaultModelProviderSecretStore)
    assert captured["managed_identity_client_id"] is None
    assert captured["vault_url"] == "https://example.vault.azure.net"
