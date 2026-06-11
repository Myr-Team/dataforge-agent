import json
import os
from typing import Any

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings


DEFAULT_CONTAINER = "dataforge-workspaces"
REGISTRY_BLOB = "registry/workspaces.json"


def blob_configured() -> bool:
    return bool(os.environ.get("AZURE_STORAGE_CONNECTION_STRING") or os.environ.get("DF_STORAGE_ACCOUNT"))


def persist_workspace(
    *,
    workspace_id: str,
    raw_filename: str,
    raw_content: bytes,
    workspace_meta: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, Any]:
    container = _container_client()
    _ensure_container(container)
    prefix = f"workspaces/{workspace_id}"
    _upload_json(container, f"{prefix}/workspace.json", workspace_meta)
    _upload_json(container, f"{prefix}/profile.json", profile)
    container.upload_blob(
        f"{prefix}/raw_docs/{raw_filename}",
        raw_content,
        overwrite=True,
        content_settings=ContentSettings(content_type="application/octet-stream"),
    )
    entry = _registry_entry(workspace_meta, profile)
    registry = load_workspace_registry()
    registry = [item for item in registry if item.get("workspace_id") != workspace_id]
    registry.append(entry)
    _save_registry(registry)
    return {
        "mode": "azure_blob",
        "container": _container_name(),
        "prefix": prefix,
        "registry_blob": REGISTRY_BLOB,
    }


def load_workspace_registry() -> list[dict[str, Any]]:
    if not blob_configured():
        return []
    try:
        container = _container_client()
        blob = container.get_blob_client(REGISTRY_BLOB)
        raw = blob.download_blob().readall().decode("utf-8")
    except ResourceNotFoundError:
        return []
    except Exception:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        items = data.get("workspaces") or []
    else:
        items = data
    return [item for item in items if isinstance(item, dict)]


def get_registry_workspace(workspace_id: str) -> dict[str, Any] | None:
    for item in load_workspace_registry():
        if item.get("workspace_id") == workspace_id:
            return item
    return None


def remove_workspace_from_blob(workspace_id: str) -> dict[str, Any]:
    container = _container_client()
    deleted_blobs = 0
    prefix = f"workspaces/{workspace_id}/"
    for blob in container.list_blobs(name_starts_with=prefix):
        container.delete_blob(blob.name)
        deleted_blobs += 1
    registry = [item for item in load_workspace_registry() if item.get("workspace_id") != workspace_id]
    _save_registry(registry)
    return {"deleted_blobs": deleted_blobs, "container": _container_name(), "prefix": prefix.rstrip("/")}


def _registry_entry(workspace_meta: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    workspace_id = str(workspace_meta.get("workspace_id") or profile.get("workspace_id"))
    return {
        "workspace_id": workspace_id,
        "name": workspace_meta.get("name") or profile.get("name") or workspace_id,
        "format": workspace_meta.get("format") or profile.get("format") or "unknown",
        "profile_summary": workspace_meta.get("profile_summary") or profile.get("profile_summary"),
        "created_at": workspace_meta.get("created_at") or profile.get("created_at"),
        "doc_count": int(workspace_meta.get("indexed_count") or 1),
        "source_file": profile.get("source_file"),
        "blob_prefix": f"workspaces/{workspace_id}",
        "persistence_mode": "azure_blob",
    }


def _save_registry(items: list[dict[str, Any]]) -> None:
    container = _container_client()
    _ensure_container(container)
    data = {
        "version": 1,
        "workspaces": sorted(items, key=lambda item: str(item.get("created_at") or ""), reverse=True),
    }
    _upload_json(container, REGISTRY_BLOB, data)


def _upload_json(container: Any, blob_name: str, value: dict[str, Any]) -> None:
    container.upload_blob(
        blob_name,
        json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"),
        overwrite=True,
        content_settings=ContentSettings(content_type="application/json; charset=utf-8"),
    )


def _container_client() -> Any:
    connection_string = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if connection_string:
        service = BlobServiceClient.from_connection_string(connection_string)
    else:
        account = os.environ.get("DF_STORAGE_ACCOUNT")
        if not account:
            raise RuntimeError("Missing DF_STORAGE_ACCOUNT or AZURE_STORAGE_CONNECTION_STRING for Blob persistence")
        storage_key = os.environ.get("AZURE_STORAGE_KEY") or os.environ.get("DF_STORAGE_KEY")
        service = BlobServiceClient(
            account_url=f"https://{account}.blob.core.windows.net",
            credential=storage_key or DefaultAzureCredential(),
        )
    return service.get_container_client(_container_name())


def _container_name() -> str:
    return os.environ.get("DF_WORKSPACE_CONTAINER", DEFAULT_CONTAINER)


def _ensure_container(container: Any) -> None:
    try:
        container.create_container()
    except ResourceExistsError:
        pass
