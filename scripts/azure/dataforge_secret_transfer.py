"""Write-only helpers for moving Azure secret values without logging them."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from typing import Any


_SAFE_NAME = re.compile(r"^[a-z0-9-]{1,127}$")
_SAFE_SCOPE = re.compile(r"^[A-Za-z0-9-]{1,128}$")
_SAFE_RESOURCE_NAME = re.compile(r"^[A-Za-z0-9_.()-]{1,260}$")


class SecretTransferError(RuntimeError):
    """A provider-safe error that never includes a secret or Azure identifier."""


def transfer_secrets(
    records: Iterable[Mapping[str, Any]],
    writer: Callable[[str, str], Any],
) -> list[str]:
    """Validate and write secret records, returning names and never values."""

    written: list[str] = []
    for record in records:
        name = str(record.get("name") or "")
        value = record.get("value")
        if not _SAFE_NAME.fullmatch(name) or not isinstance(value, str) or not value:
            raise ValueError("invalid secret record")
        writer(name, value)
        written.append(name)
    return sorted(written)


def transfer_containerapp_secrets_to_vault(
    *,
    subscription_ref: str,
    resource_group: str,
    app_name: str,
    vault_url: str,
    credential: Any | None = None,
    api_version: str = "2025-01-01",
) -> list[str]:
    """Read source Container Apps secrets and write them directly to Key Vault.

    The provider response and values stay in local memory. The only return value is
    the sorted list of secret names that were written successfully.
    """

    _validate_source_reference(subscription_ref, resource_group, app_name, api_version)
    if not re.fullmatch(r"https://[A-Za-z0-9-]+\.vault\.azure\.net/?", vault_url):
        raise ValueError("invalid vault URL")

    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
    except ImportError as exc:  # pragma: no cover - deployment dependency guard
        raise SecretTransferError("Azure secret transfer dependencies unavailable") from exc

    active_credential = credential or DefaultAzureCredential()
    records = _read_containerapp_secrets(
        subscription_ref=subscription_ref,
        resource_group=resource_group,
        app_name=app_name,
        api_version=api_version,
        credential=active_credential,
    )
    client = SecretClient(vault_url=vault_url.rstrip("/"), credential=active_credential)

    def write(name: str, value: str) -> None:
        try:
            client.set_secret(name, value)
        except Exception as exc:  # provider errors can contain request identifiers
            raise SecretTransferError("target secret write failed") from None

    return transfer_secrets(records, write)


def _read_containerapp_secrets(
    *,
    subscription_ref: str,
    resource_group: str,
    app_name: str,
    api_version: str,
    credential: Any,
) -> list[dict[str, str]]:
    token = credential.get_token("https://management.azure.com/.default").token
    url = (
        "https://management.azure.com/subscriptions/"
        f"{subscription_ref}/resourceGroups/{resource_group}/providers/"
        f"Microsoft.App/containerApps/{app_name}/listSecrets?api-version={api_version}"
    )
    request = urllib.request.Request(
        url,
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Length": "0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.HTTPError):
        raise SecretTransferError("source secret read failed") from None

    rows = payload.get("value") if isinstance(payload, Mapping) else None
    if not isinstance(rows, list):
        raise SecretTransferError("source secret response invalid")
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def _validate_source_reference(
    subscription_ref: str,
    resource_group: str,
    app_name: str,
    api_version: str,
) -> None:
    if not _SAFE_SCOPE.fullmatch(subscription_ref):
        raise ValueError("invalid source scope")
    if not _SAFE_RESOURCE_NAME.fullmatch(resource_group):
        raise ValueError("invalid source resource group")
    if not _SAFE_RESOURCE_NAME.fullmatch(app_name):
        raise ValueError("invalid source app name")
    if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}(?:-preview)?", api_version):
        raise ValueError("invalid API version")
