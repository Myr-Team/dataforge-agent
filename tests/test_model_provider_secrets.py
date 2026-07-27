from __future__ import annotations

import pytest

from backend.model_provider_secrets import (
    KeyVaultModelProviderSecretStore,
    ModelProviderSecretError,
    model_provider_secret_store_from_environment,
    provider_secret_name,
)


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
