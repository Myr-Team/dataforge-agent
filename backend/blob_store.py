import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from azure.core import MatchConditions
from azure.core.exceptions import ResourceExistsError, ResourceModifiedError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings


DEFAULT_CONTAINER = "dataforge-workspaces"
REGISTRY_BLOB = "registry/workspaces.json"
REGISTRY_ENTRY_PREFIX = "registry/workspaces"
DELETED_WORKSPACE_PREFIX = "registry/deleted_workspaces"
ARTIFACT_PREFIX = "artifacts"


def blob_configured() -> bool:
    return bool(os.environ.get("AZURE_STORAGE_CONNECTION_STRING") or os.environ.get("DF_STORAGE_ACCOUNT"))


def probe_blob_container(timeout: float = 1.0) -> dict[str, Any]:
    if not blob_configured():
        return {"ok": False, "state": "unconfigured", "error": "blob storage is not configured"}
    try:
        container = _container_client()
        container.get_container_properties(timeout=timeout)
        return {"ok": True, "state": "ok", "container": _container_name()}
    except Exception as exc:
        return {
            "ok": False,
            "state": "down",
            "container": _container_name(),
            "error": f"{type(exc).__name__}: {exc}"[:500],
        }


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
    _delete_tombstone(container, workspace_id)
    entry = _registry_entry(workspace_meta, profile)
    _save_registry_entry(container, entry)
    return {
        "mode": "azure_blob",
        "container": _container_name(),
        "prefix": prefix,
        "registry_blob": REGISTRY_BLOB,
    }


def persist_workspace_bundle(
    *,
    workspace_id: str,
    raw_payloads: list[dict[str, Any]],
    reference_payloads: list[dict[str, Any]] | None = None,
    workspace_meta: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, Any]:
    container = _container_client()
    _ensure_container(container)
    prefix = f"workspaces/{workspace_id}"
    upload_tasks = [
        lambda: _upload_json(container, f"{prefix}/workspace.json", workspace_meta),
        lambda: _upload_json(container, f"{prefix}/profile.json", profile),
    ]
    for item in raw_payloads:
        raw_filename = PathSafe.name(str(item.get("raw_filename") or "upload"))
        raw_content = bytes(item.get("raw_content") or b"")
        upload_tasks.append(
            lambda raw_filename=raw_filename, raw_content=raw_content: container.upload_blob(
                f"{prefix}/raw_docs/{raw_filename}",
                raw_content,
                overwrite=True,
                content_settings=ContentSettings(content_type="application/octet-stream"),
            )
        )
        profile_filename = PathSafe.name(str(item.get("profile_filename") or f"{raw_filename}.json"))
        item_profile = item.get("profile")
        if isinstance(item_profile, dict):
            upload_tasks.append(
                lambda profile_filename=profile_filename, item_profile=item_profile: _upload_json(
                    container,
                    f"{prefix}/profiles/{profile_filename}",
                    item_profile,
                )
            )
    for item in reference_payloads or []:
        filename = PathSafe.name(str(item.get("filename") or "reference-image"))
        blob_name = f"{prefix}/reference_images/{filename}"
        content = bytes(item.get("content") or b"")
        content_type = str(item.get("content_type") or "application/octet-stream")
        upload_tasks.append(
            lambda blob_name=blob_name, content=content, content_type=content_type: container.upload_blob(
                blob_name,
                content,
                overwrite=True,
                content_settings=ContentSettings(content_type=content_type),
            )
        )
    _run_upload_tasks(upload_tasks)
    _delete_tombstone(container, workspace_id)
    entry = _registry_entry(workspace_meta, profile)
    _save_registry_entry(container, entry)
    return {
        "mode": "azure_blob",
        "container": _container_name(),
        "prefix": prefix,
        "registry_blob": REGISTRY_BLOB,
        "raw_count": len(raw_payloads),
        "reference_count": len(reference_payloads or []),
    }


def load_workspace_registry() -> list[dict[str, Any]]:
    if not blob_configured():
        return []
    by_id: dict[str, dict[str, Any]] = {}
    try:
        container = _container_client()
        blob = container.get_blob_client(REGISTRY_BLOB)
        raw = blob.download_blob().readall().decode("utf-8")
    except ResourceNotFoundError:
        raw = ""
    except Exception:
        raw = ""
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {}
        if isinstance(data, dict):
            items = data.get("workspaces") or []
        else:
            items = data
        for item in items:
            if isinstance(item, dict) and item.get("workspace_id"):
                by_id[str(item["workspace_id"])] = item
    try:
        container = _container_client()
        for blob in container.list_blobs(name_starts_with=f"{REGISTRY_ENTRY_PREFIX}/"):
            try:
                raw_entry = container.get_blob_client(blob.name).download_blob().readall().decode("utf-8")
                entry = json.loads(raw_entry)
            except Exception:
                continue
            if isinstance(entry, dict) and entry.get("workspace_id"):
                by_id[str(entry["workspace_id"])] = entry
    except Exception:
        pass
    return sorted(by_id.values(), key=lambda item: str(item.get("created_at") or ""), reverse=True)


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
        try:
            container.delete_blob(blob.name)
            deleted_blobs += 1
        except ResourceNotFoundError:
            continue
    try:
        container.delete_blob(_registry_entry_blob(workspace_id))
    except ResourceNotFoundError:
        pass
    registry = [item for item in load_workspace_registry() if item.get("workspace_id") != workspace_id]
    _save_registry(registry)
    _write_tombstone(container, workspace_id)
    return {"deleted_blobs": deleted_blobs, "container": _container_name(), "prefix": prefix.rstrip("/")}


def workspace_deleted(workspace_id: str) -> bool:
    if not blob_configured():
        return False
    return download_blob_json(_tombstone_blob(workspace_id)) is not None


def upload_blob_json(blob_name: str, value: dict[str, Any]) -> dict[str, Any]:
    container = _container_client()
    _ensure_container(container)
    _upload_json(container, blob_name, value)
    return {"container": _container_name(), "blob_name": blob_name, "blob_url": _blob_url(blob_name)}


def upload_workspace_blob(workspace_id: str, relative_path: str, content: bytes, content_type: str = "application/octet-stream") -> dict[str, Any]:
    container = _container_client()
    _ensure_container(container)
    safe_workspace = PathSafe.name(workspace_id)
    safe_parts = [PathSafe.name(part) for part in str(relative_path or "").replace("\\", "/").split("/") if part]
    if not safe_workspace or not safe_parts:
        raise ValueError("workspace_id and relative_path are required")
    blob_name = f"workspaces/{safe_workspace}/{'/'.join(safe_parts)}"
    container.upload_blob(
        blob_name,
        bytes(content or b""),
        overwrite=True,
        content_settings=ContentSettings(content_type=content_type),
    )
    return {"container": _container_name(), "blob_name": blob_name, "blob_url": _blob_url(blob_name)}


def download_blob_json(blob_name: str) -> dict[str, Any] | None:
    if not blob_configured():
        return None
    try:
        container = _container_client()
        raw = container.get_blob_client(blob_name).download_blob().readall().decode("utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except ResourceNotFoundError:
        return None
    except Exception:
        return None


def list_blob_json(prefix: str) -> list[dict[str, Any]]:
    if not blob_configured():
        return []
    items: list[dict[str, Any]] = []
    try:
        container = _container_client()
        for entry in container.list_blobs(name_starts_with=str(prefix or "")):
            name = str(getattr(entry, "name", "") or "")
            if not name.endswith(".json"):
                continue
            try:
                raw = container.get_blob_client(name).download_blob().readall().decode("utf-8")
                value = json.loads(raw)
            except Exception:
                continue
            if isinstance(value, dict):
                items.append(value)
    except Exception:
        return []
    return items


def claim_blob_json(
    blob_name: str,
    *,
    expected_status: str,
    changes: dict[str, Any],
) -> dict[str, Any] | None:
    if not blob_configured():
        return None
    try:
        container = _container_client()
        blob = container.get_blob_client(blob_name)
        properties = blob.get_blob_properties()
        raw = blob.download_blob().readall().decode("utf-8")
        current = json.loads(raw)
        if not isinstance(current, dict) or str(current.get("status") or "") != expected_status:
            return None
        updated = {**current, **changes}
        blob.upload_blob(
            json.dumps(updated, ensure_ascii=False).encode("utf-8"),
            overwrite=True,
            etag=properties.etag,
            match_condition=MatchConditions.IfNotModified,
            content_settings=ContentSettings(content_type="application/json; charset=utf-8"),
        )
        return updated
    except (ResourceModifiedError, ResourceNotFoundError):
        return None
    except Exception:
        return None


def delete_blob_name(blob_name: str) -> bool:
    if not blob_configured():
        return False
    try:
        _container_client().delete_blob(blob_name)
        return True
    except ResourceNotFoundError:
        return False
    except Exception:
        return False


def upload_artifact(name: str, content: bytes, content_type: str) -> dict[str, Any]:
    container = _container_client()
    _ensure_container(container)
    blob_name = f"{ARTIFACT_PREFIX}/{PathSafe.name(name)}"
    container.upload_blob(
        blob_name,
        content,
        overwrite=True,
        content_settings=ContentSettings(content_type=content_type),
    )
    return {"container": _container_name(), "blob_name": blob_name, "blob_url": _blob_url(blob_name)}


def download_blob_content(blob_name: str) -> tuple[bytes, str] | None:
    if not blob_configured():
        return None
    try:
        blob = _container_client().get_blob_client(blob_name)
        props = blob.get_blob_properties()
        content_type = getattr(props.content_settings, "content_type", None) or "application/octet-stream"
        return blob.download_blob().readall(), content_type
    except ResourceNotFoundError:
        return None
    except Exception:
        return None


def download_artifact(name: str) -> tuple[bytes, str] | None:
    if not blob_configured():
        return None
    blob_name = f"{ARTIFACT_PREFIX}/{PathSafe.name(name)}"
    try:
        blob = _container_client().get_blob_client(blob_name)
        props = blob.get_blob_properties()
        content_type = getattr(props.content_settings, "content_type", None) or "application/octet-stream"
        return blob.download_blob().readall(), content_type
    except ResourceNotFoundError:
        return None
    except Exception:
        return None


def _registry_entry(workspace_meta: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    workspace_id = str(workspace_meta.get("workspace_id") or profile.get("workspace_id"))
    documents = workspace_meta.get("documents") or []
    return {
        "workspace_id": workspace_id,
        "name": workspace_meta.get("name") or profile.get("name") or workspace_id,
        "format": workspace_meta.get("format") or profile.get("format") or "unknown",
        "description": workspace_meta.get("description"),
        "profile_summary": workspace_meta.get("profile_summary") or profile.get("profile_summary"),
        "created_at": workspace_meta.get("created_at") or profile.get("created_at"),
        "doc_count": len(documents) if documents else int(workspace_meta.get("indexed_count") or 1),
        "source_file": profile.get("source_file"),
        "documents": documents,
        "reference_images": workspace_meta.get("reference_images") or [],
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


def _save_registry_entry(container: Any, entry: dict[str, Any]) -> None:
    _upload_json(container, _registry_entry_blob(str(entry.get("workspace_id") or "")), entry)


def _registry_entry_blob(workspace_id: str) -> str:
    return f"{REGISTRY_ENTRY_PREFIX}/{PathSafe.name(workspace_id)}.json"


def _run_upload_tasks(tasks: list[Any]) -> None:
    if not tasks:
        return
    if len(tasks) == 1:
        tasks[0]()
        return
    with ThreadPoolExecutor(max_workers=min(8, len(tasks))) as executor:
        futures = [executor.submit(task) for task in tasks]
        for future in futures:
            future.result()


def _write_tombstone(container: Any, workspace_id: str) -> None:
    _upload_json(
        container,
        _tombstone_blob(workspace_id),
        {"workspace_id": workspace_id, "deleted_at": datetime.now(timezone.utc).isoformat()},
    )


def _delete_tombstone(container: Any, workspace_id: str) -> None:
    try:
        container.delete_blob(_tombstone_blob(workspace_id))
    except ResourceNotFoundError:
        pass


def _tombstone_blob(workspace_id: str) -> str:
    return f"{DELETED_WORKSPACE_PREFIX}/{PathSafe.name(workspace_id)}.json"


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


def _blob_url(blob_name: str) -> str:
    account = os.environ.get("DF_STORAGE_ACCOUNT")
    if account:
        return f"https://{account}.blob.core.windows.net/{_container_name()}/{blob_name}"
    return f"{_container_name()}/{blob_name}"


class PathSafe:
    @staticmethod
    def name(value: str) -> str:
        return str(value or "artifact.bin").replace("\\", "/").split("/")[-1]
