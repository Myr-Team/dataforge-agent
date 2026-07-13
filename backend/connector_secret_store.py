from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Mapping, Protocol

try:
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient
except ImportError:  # Raised explicitly when Key Vault is configured below.
    DefaultAzureCredential = None  # type: ignore[assignment]
    SecretClient = None  # type: ignore[assignment]


class SecretStore(Protocol):
    persistence: str

    def put(self, workspace_id: str, connector_id: str, secret: Mapping[str, str]) -> str: ...
    def reference_for(self, workspace_id: str, connector_id: str) -> str: ...
    def get(self, workspace_id: str, connector_id: str, reference: str) -> dict[str, str]: ...
    def delete(self, workspace_id: str, connector_id: str, reference: str) -> None: ...


class SecretExpiredError(ValueError):
    pass


class SecretStoreConfigurationError(RuntimeError):
    pass


class SecretReferenceError(ValueError):
    pass


@dataclass
class _SessionSecret:
    expires_at: float
    ciphertext: bytes


class SessionSecretStore:
    """Encrypted process-local fallback when a Key Vault URL is intentionally absent."""

    persistence = "session_only"

    def __init__(self, *, ttl_seconds: int | None = None, clock=time.time) -> None:
        self._ttl_seconds = max(1, int(ttl_seconds or os.environ.get("DF_CONNECTOR_SESSION_TTL_SECONDS", "3600")))
        self._clock = clock
        self._values: dict[str, _SessionSecret] = {}
        self._fernet = self._build_fernet()

    def put(self, workspace_id: str, connector_id: str, secret: Mapping[str, str]) -> str:
        self._purge()
        reference = self.reference_for(workspace_id, connector_id)
        self._values[reference] = _SessionSecret(
            expires_at=self._clock() + self._ttl_seconds,
            ciphertext=self._fernet.encrypt(_secret_json(secret)),
        )
        return reference

    def reference_for(self, workspace_id: str, connector_id: str) -> str:
        return _reference_for("session", workspace_id, connector_id)

    def get(self, workspace_id: str, connector_id: str, reference: str) -> dict[str, str]:
        self._validate_reference(workspace_id, connector_id, reference)
        self._purge()
        item = self._values.get(str(reference or ""))
        if item is None:
            raise SecretExpiredError("Connector session not found or expired")
        return _decode_secret(self._fernet.decrypt(item.ciphertext))

    def delete(self, workspace_id: str, connector_id: str, reference: str) -> None:
        self._validate_reference(workspace_id, connector_id, reference)
        self._values.pop(str(reference or ""), None)

    def _validate_reference(self, workspace_id: str, connector_id: str, reference: str) -> None:
        if str(reference or "") != self.reference_for(workspace_id, connector_id):
            raise SecretReferenceError("Connector secret reference does not match its trusted context")

    def clear(self) -> None:
        self._values.clear()

    def expires_at(self, reference: str) -> str | None:
        item = self._values.get(str(reference or ""))
        if item is None or item.expires_at <= self._clock():
            return None
        from datetime import datetime, timezone

        return datetime.fromtimestamp(item.expires_at, timezone.utc).isoformat()

    def _purge(self) -> None:
        now = self._clock()
        for reference in [key for key, value in self._values.items() if value.expires_at <= now]:
            self._values.pop(reference, None)

    @staticmethod
    def _build_fernet():
        try:
            from cryptography.fernet import Fernet
        except ImportError as exc:
            raise SecretStoreConfigurationError("cryptography is required for connector session encryption") from exc
        key = os.environ.get("DF_CONNECTOR_CREDENTIAL_KEY")
        raw = key.encode("utf-8") if key else Fernet.generate_key()
        if len(raw) != 44:
            raw = base64.urlsafe_b64encode(hashlib.sha256(raw).digest())
        return Fernet(raw)


class KeyVaultSecretStore:
    persistence = "key_vault"

    def __init__(self, vault_url: str) -> None:
        if not DefaultAzureCredential or not SecretClient:
            raise SecretStoreConfigurationError("DF_KEY_VAULT_URL is configured but azure-keyvault-secrets is unavailable")
        self._client = SecretClient(vault_url=str(vault_url).rstrip("/"), credential=DefaultAzureCredential())

    def put(self, workspace_id: str, connector_id: str, secret: Mapping[str, str]) -> str:
        name = _vault_secret_name(workspace_id, connector_id)
        self._client.set_secret(name, _secret_json(secret).decode("utf-8"))
        return self.reference_for(workspace_id, connector_id)

    def reference_for(self, workspace_id: str, connector_id: str) -> str:
        return f"kv:{_vault_secret_name(workspace_id, connector_id)}"

    def get(self, workspace_id: str, connector_id: str, reference: str) -> dict[str, str]:
        self._validate_reference(workspace_id, connector_id, reference)
        secret = self._client.get_secret(_reference_name(reference))
        return _decode_secret(str(secret.value).encode("utf-8"))

    def delete(self, workspace_id: str, connector_id: str, reference: str) -> None:
        self._validate_reference(workspace_id, connector_id, reference)
        poller = self._client.begin_delete_secret(_reference_name(reference))
        wait = getattr(poller, "wait", None)
        if callable(wait):
            wait()

    def _validate_reference(self, workspace_id: str, connector_id: str, reference: str) -> None:
        if str(reference or "") != self.reference_for(workspace_id, connector_id):
            raise SecretReferenceError("Connector secret reference does not match its trusted context")


def secret_store_from_environment() -> SecretStore:
    vault_url = str(os.environ.get("DF_KEY_VAULT_URL") or "").strip()
    if vault_url:
        return KeyVaultSecretStore(vault_url)
    return SessionSecretStore()


def _vault_secret_name(workspace_id: str, connector_id: str) -> str:
    material = f"{workspace_id}:{connector_id}".encode("utf-8")
    return f"df-connector-{hashlib.sha256(material).hexdigest()[:40]}"


def _reference_for(prefix: str, workspace_id: str, connector_id: str) -> str:
    material = f"{workspace_id}:{connector_id}".encode("utf-8")
    return f"{prefix}:{hashlib.sha256(material).hexdigest()[:48]}"


def expected_secret_reference(persistence: str, workspace_id: str, connector_id: str) -> str:
    return _reference_for("kv" if persistence == "key_vault" else "session", workspace_id, connector_id)


def _reference_name(reference: str) -> str:
    value = str(reference or "")
    if not value.startswith("kv:") or len(value) < 5:
        raise ValueError("Invalid connector secret reference")
    return value.removeprefix("kv:")


def _secret_json(secret: Mapping[str, str]) -> bytes:
    clean = {str(key): str(value) for key, value in secret.items() if value is not None}
    return json.dumps(clean, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _decode_secret(raw: bytes) -> dict[str, str]:
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Invalid connector secret")
    return {str(key): str(item) for key, item in value.items()}


__all__ = [
    "KeyVaultSecretStore",
    "SecretExpiredError",
    "SecretReferenceError",
    "SecretStore",
    "SecretStoreConfigurationError",
    "SessionSecretStore",
    "expected_secret_reference",
    "secret_store_from_environment",
]
