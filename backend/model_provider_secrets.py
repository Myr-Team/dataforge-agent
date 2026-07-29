from __future__ import annotations

import hashlib
import os
from typing import Any, Mapping, Protocol

from .aws_bedrock_provider import AwsBedrockCredential
from .connector_secret_store import validate_key_vault_url

try:
    from azure.identity import ManagedIdentityCredential
    from azure.keyvault.secrets import SecretClient
except ImportError:
    ManagedIdentityCredential = None  # type: ignore[assignment]
    SecretClient = None  # type: ignore[assignment]


class ModelProviderSecretError(RuntimeError):
    code = "provider_secret_store_unavailable"

    def __init__(self, code: str | None = None) -> None:
        if code:
            self.code = code
        super().__init__(self.code)


class ModelProviderSecretStore(Protocol):
    def put(self, tenant_ref: str, provider_id: str, api_key: str) -> str: ...

    def get(
        self,
        tenant_ref: str,
        provider_id: str,
        secret_ref: str,
    ) -> str: ...

    def rotate(self, tenant_ref: str, provider_id: str, api_key: str) -> str: ...


class KeyVaultModelProviderSecretStore:
    def __init__(
        self,
        vault_url: str | None = None,
        *,
        client: Any | None = None,
    ) -> None:
        if client is not None:
            self._client = client
            return
        if not vault_url:
            raise ModelProviderSecretError("provider_key_vault_required")
        if ManagedIdentityCredential is None or SecretClient is None:
            raise ModelProviderSecretError("provider_key_vault_unavailable")
        try:
            self._client = SecretClient(
                vault_url=validate_key_vault_url(vault_url),
                credential=ManagedIdentityCredential(),
            )
        except Exception:
            raise ModelProviderSecretError("provider_key_vault_unavailable") from None

    def put(self, tenant_ref: str, provider_id: str, api_key: str) -> str:
        key = _validated_secret_value(api_key)
        name = provider_secret_name(tenant_ref, provider_id)
        try:
            self._client.set_secret(name, key)
        except Exception:
            raise ModelProviderSecretError("provider_secret_put_failed") from None
        return f"kv:{name}"

    def get(
        self,
        tenant_ref: str,
        provider_id: str,
        secret_ref: str,
    ) -> str:
        name = self._reference_name(tenant_ref, provider_id, secret_ref)
        try:
            value = str(self._client.get_secret(name).value or "")
        except Exception:
            raise ModelProviderSecretError("provider_secret_get_failed") from None
        return _validated_secret_value(value)

    def rotate(self, tenant_ref: str, provider_id: str, api_key: str) -> str:
        return self.put(tenant_ref, provider_id, api_key)

    @staticmethod
    def _reference_name(
        tenant_ref: str,
        provider_id: str,
        secret_ref: str,
    ) -> str:
        expected = f"kv:{provider_secret_name(tenant_ref, provider_id)}"
        if str(secret_ref or "") != expected:
            raise ModelProviderSecretError("provider_secret_reference_invalid")
        return expected.removeprefix("kv:")


def provider_secret_name(tenant_ref: str, provider_id: str) -> str:
    tenant = str(tenant_ref or "").strip()
    provider = str(provider_id or "").strip()
    if not tenant or not provider:
        raise ModelProviderSecretError("provider_secret_context_invalid")
    digest = hashlib.sha256(f"{tenant}:{provider}".encode("utf-8")).hexdigest()[:40]
    return f"df-model-provider-{digest}"


def model_provider_secret_store_from_environment(
    environment: Mapping[str, str] | None = None,
) -> ModelProviderSecretStore:
    values = os.environ if environment is None else environment
    vault_url = str(values.get("DF_KEY_VAULT_URL") or "").strip()
    if not vault_url:
        raise ModelProviderSecretError("provider_key_vault_required")
    return KeyVaultModelProviderSecretStore(vault_url)


def _validated_api_key(value: str) -> str:
    key = str(value or "").strip()
    if len(key) < 8 or len(key) > 512 or any(char.isspace() for char in key):
        raise ModelProviderSecretError("provider_api_key_invalid")
    return key


def _validated_secret_value(value: str) -> str:
    secret_value = str(value or "").strip()
    if secret_value.startswith("{"):
        try:
            return AwsBedrockCredential.from_secret_value(secret_value).to_secret_value()
        except Exception:
            return _validated_api_key(secret_value)
    return _validated_api_key(secret_value)


__all__ = [
    "KeyVaultModelProviderSecretStore",
    "ModelProviderSecretError",
    "ModelProviderSecretStore",
    "model_provider_secret_store_from_environment",
    "provider_secret_name",
]
