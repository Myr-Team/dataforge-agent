from __future__ import annotations

import pytest


def test_key_vault_health_is_configured_unverified_after_token_and_client_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.dependency_health as health

    class Credential:
        def get_token(self, _scope):
            return object()

    monkeypatch.setenv("DF_KEY_VAULT_URL", "https://example.vault.azure.net")
    monkeypatch.setattr(health, "DefaultAzureCredential", Credential)
    monkeypatch.setattr(health, "_redact_endpoint", lambda value: value)
    monkeypatch.setattr(health, "KeyVaultSecretClient", lambda **_kwargs: object())

    result = health._probe_key_vault()

    assert result["ok"] is False
    assert result["state"] == "configured_unverified"
