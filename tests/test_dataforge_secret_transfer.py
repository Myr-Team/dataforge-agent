import sys
from types import ModuleType

from scripts.azure import dataforge_secret_transfer as secret_transfer
from scripts.azure.dataforge_secret_transfer import transfer_secrets


def _install_fake_azure_modules(monkeypatch, client_type):
    azure_module = ModuleType("azure")
    identity_module = ModuleType("azure.identity")
    keyvault_module = ModuleType("azure.keyvault")
    secrets_module = ModuleType("azure.keyvault.secrets")
    identity_module.DefaultAzureCredential = object
    secrets_module.SecretClient = client_type
    azure_module.identity = identity_module
    azure_module.keyvault = keyvault_module
    keyvault_module.secrets = secrets_module
    monkeypatch.setitem(sys.modules, "azure", azure_module)
    monkeypatch.setitem(sys.modules, "azure.identity", identity_module)
    monkeypatch.setitem(sys.modules, "azure.keyvault", keyvault_module)
    monkeypatch.setitem(sys.modules, "azure.keyvault.secrets", secrets_module)


def test_transfer_returns_names_only(capsys):
    written = {}

    result = transfer_secrets(
        [{"name": "provider-key", "value": "never-print-this"}],
        lambda name, value: written.setdefault(name, value),
    )

    assert result == ["provider-key"]
    assert written == {"provider-key": "never-print-this"}
    captured = capsys.readouterr()
    assert "never-print-this" not in captured.out
    assert "never-print-this" not in captured.err


def test_transfer_rejects_invalid_secret_names():
    try:
        transfer_secrets([{"name": "../bad", "value": "hidden"}], lambda *_: None)
    except ValueError as exc:
        assert "invalid secret record" in str(exc)
    else:
        raise AssertionError("invalid secret name must fail")


def test_transfer_rejects_empty_values_without_calling_writer():
    called = []

    try:
        transfer_secrets(
            [{"name": "provider-key", "value": ""}],
            lambda *_: called.append(True),
        )
    except ValueError as exc:
        assert "invalid secret record" in str(exc)
    else:
        raise AssertionError("empty secret value must fail")

    assert called == []


def test_writer_failure_does_not_echo_secret_value():
    def fail(_name, _value):
        raise RuntimeError("target write unavailable")

    try:
        transfer_secrets(
            [{"name": "provider-key", "value": "never-echo-this"}],
            fail,
        )
    except RuntimeError as exc:
        assert "never-echo-this" not in str(exc)
    else:
        raise AssertionError("writer failure must propagate")


def test_azure_bridge_returns_names_and_keeps_values_in_writer(monkeypatch, capsys):
    written = {}

    class Client:
        def __init__(self, **_kwargs):
            pass

        def set_secret(self, name, value):
            written[name] = value

    _install_fake_azure_modules(monkeypatch, Client)
    monkeypatch.setattr(
        secret_transfer,
        "_read_containerapp_secrets",
        lambda **_kwargs: [
            {"name": "provider-key", "value": "memory-only"},
            {"name": "source-only-key", "value": "must-not-migrate"},
        ],
    )

    names = secret_transfer.transfer_containerapp_secrets_to_vault(
        subscription_ref="source-scope",
        resource_group="rg-dataforge-dev",
        app_name="ca-dataforge-backend",
        vault_url="https://example-vault.vault.azure.net",
        credential=object(),
        allowed_names={"provider-key"},
    )

    assert names == ["provider-key"]
    assert written == {"provider-key": "memory-only"}
    captured = capsys.readouterr()
    assert "memory-only" not in captured.out
    assert "memory-only" not in captured.err
    assert "must-not-migrate" not in captured.out
    assert "must-not-migrate" not in captured.err


def test_azure_bridge_redacts_provider_failure(monkeypatch):
    class Client:
        def __init__(self, **_kwargs):
            pass

        def set_secret(self, _name, value):
            raise RuntimeError(f"provider echoed {value}")

    _install_fake_azure_modules(monkeypatch, Client)
    monkeypatch.setattr(
        secret_transfer,
        "_read_containerapp_secrets",
        lambda **_kwargs: [{"name": "provider-key", "value": "never-surface"}],
    )

    try:
        secret_transfer.transfer_containerapp_secrets_to_vault(
            subscription_ref="source-scope",
            resource_group="rg-dataforge-dev",
            app_name="ca-dataforge-backend",
            vault_url="https://example-vault.vault.azure.net",
            credential=object(),
        )
    except secret_transfer.SecretTransferError as exc:
        assert str(exc) == "target secret write failed"
        assert "never-surface" not in str(exc)
    else:
        raise AssertionError("provider failure must be normalized")


def test_azure_bridge_fails_when_allowlisted_secret_is_missing(monkeypatch):
    class Client:
        def __init__(self, **_kwargs):
            pass

    _install_fake_azure_modules(monkeypatch, Client)
    monkeypatch.setattr(
        secret_transfer,
        "_read_containerapp_secrets",
        lambda **_kwargs: [{"name": "available-key", "value": "hidden"}],
    )

    try:
        secret_transfer.transfer_containerapp_secrets_to_vault(
            subscription_ref="source-scope",
            resource_group="rg-dataforge-dev",
            app_name="ca-dataforge-backend",
            vault_url="https://example-vault.vault.azure.net",
            credential=object(),
            allowed_names={"required-key"},
        )
    except secret_transfer.SecretTransferError as exc:
        assert str(exc) == "required source secret missing"
    else:
        raise AssertionError("missing allowlisted secret must fail closed")
