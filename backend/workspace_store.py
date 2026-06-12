from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ingest.adapters.upload_to_records import upload_to_records
from ingest.profiler import build_data_profile, compact_profile_for_workspace, profile_to_search_document, write_profile

try:
    from .blob_store import (
        download_blob_content,
        download_blob_json,
        get_registry_workspace,
        load_workspace_registry,
        persist_workspace,
        remove_workspace_from_blob,
        workspace_deleted,
    )
    from .search_admin import count_workspace_docs, delete_workspace_docs, index_documents, search_endpoint
except ImportError:
    from blob_store import (
        download_blob_content,
        download_blob_json,
        get_registry_workspace,
        load_workspace_registry,
        persist_workspace,
        remove_workspace_from_blob,
        workspace_deleted,
    )
    from search_admin import count_workspace_docs, delete_workspace_docs, index_documents, search_endpoint


ROOT = Path(__file__).resolve().parents[1]
WORKSPACES = ROOT / "workspaces"
_CONTEXT_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CONTEXT_CACHE_SECONDS = float(os.environ.get("DF_WORKSPACE_CONTEXT_CACHE_SECONDS", "60"))
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
IMAGE_CONTENT_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp"}
REFERENCE_ROLES = {"logo", "activity", "reference"}


def create_workspace_from_upload(
    *,
    filename: str,
    content: bytes,
    content_type: str | None = None,
    name: str | None = None,
    requested_workspace_id: str | None = None,
) -> dict[str, Any]:
    if not content:
        raise ValueError("Uploaded file is empty")
    safe_name = _safe_filename(filename)
    display_name = (name or Path(safe_name).stem).strip() or "上传数据"
    workspace_id = _unique_workspace_id(requested_workspace_id or display_name)
    workspace_dir = WORKSPACES / workspace_id
    raw_dir = workspace_dir / "raw_docs"
    raw_dir.mkdir(parents=True, exist_ok=False)
    raw_path = raw_dir / safe_name
    raw_path.write_bytes(content)

    source_file = f"raw_docs/{safe_name}"
    profile = build_data_profile(
        raw_path,
        workspace_id=workspace_id,
        name=display_name,
        source_file=source_file,
        content_type=content_type,
    )
    profile_path = workspace_dir / "profile.json"
    write_profile(profile_path, profile)
    workspace_meta = {
        "workspace_id": workspace_id,
        "name": display_name,
        "format": profile["format"],
        "language": "zh-Hans",
        "persona": f"业务负责人希望把上传的 {profile['format']} 数据转成可评估的数据产品机会",
        "ask_when": [
            "问题没有说明目标用户、业务场景或希望产出的交付物。",
            "请求依赖画像中没有出现的数据字段、授权范围或实时系统能力。",
        ],
        "raw_docs": [source_file],
        "profile_file": "profile.json",
        "created_at": profile["created_at"],
        "profile_summary": profile["profile_summary"],
    }

    indexed_count = _index_profile(profile)
    workspace_meta["indexed_count"] = indexed_count
    persistence = _persist_workspace(
        workspace_id=workspace_id,
        safe_name=safe_name,
        content=content,
        workspace_meta=workspace_meta,
        profile=profile,
    )
    workspace_meta["persistence"] = persistence
    (workspace_dir / "workspace.json").write_text(json.dumps(workspace_meta, indent=2, ensure_ascii=False), encoding="utf-8")
    _CONTEXT_CACHE.pop(workspace_id, None)
    return {
        "workspace_id": workspace_id,
        "name": display_name,
        "format": profile["format"],
        "indexed_count": indexed_count,
        "profile_summary": profile["profile_summary"],
    }


def create_workspace_from_uploads(
    *,
    files: list[dict[str, Any]],
    name: str | None = None,
    description: str | None = None,
    requested_workspace_id: str | None = None,
    asset_role: str | None = None,
) -> dict[str, Any]:
    clean_files = [item for item in files if item.get("content")]
    if not clean_files:
        raise ValueError("Uploaded file is empty")
    role = _normalize_asset_role(asset_role)
    data_files = [item for item in clean_files if not _is_reference_image(item)]
    reference_files = [item for item in clean_files if _is_reference_image(item)]
    append_workspace_id = str(requested_workspace_id or "").strip()
    existing_meta: dict[str, Any] = {}
    existing_profile: dict[str, Any] = {}
    if append_workspace_id:
        if append_workspace_id.startswith("upload-") and workspace_deleted(append_workspace_id):
            raise FileNotFoundError(append_workspace_id)
        loaded = _load_workspace_bundle(append_workspace_id)
        if loaded is None:
            raise FileNotFoundError(append_workspace_id)
        existing_meta, existing_profile = loaded

    display_seed = (
        name
        or existing_meta.get("name")
        or existing_profile.get("name")
        or Path(_safe_filename(str(clean_files[0].get("filename") or "upload"))).stem
    )
    display_name = str(display_seed or "uploaded data").strip() or "uploaded data"
    description = str(description or "").strip() or existing_meta.get("description") or None
    workspace_id = append_workspace_id or _unique_workspace_id(display_name)
    workspace_dir = WORKSPACES / workspace_id
    raw_dir = workspace_dir / "raw_docs"
    profiles_dir = workspace_dir / "profiles"
    reference_dir = workspace_dir / "reference_images"
    raw_dir.mkdir(parents=True, exist_ok=bool(append_workspace_id))
    profiles_dir.mkdir(parents=True, exist_ok=True)
    if reference_files:
        reference_dir.mkdir(parents=True, exist_ok=True)

    existing_documents = _detail_documents(workspace_dir, existing_meta) if existing_meta else []
    used_names: set[str] = _existing_raw_names(existing_meta)
    used_reference_names: set[str] = _existing_reference_names(existing_meta)
    profiles: list[dict[str, Any]] = []
    content_records: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = list(existing_documents)
    raw_payloads: list[dict[str, Any]] = []
    reference_payloads: list[dict[str, Any]] = []
    reference_images = _reference_images(existing_meta)
    profile_index = _next_profile_index(existing_meta, existing_profile)
    for index, item in enumerate(data_files):
        safe_name = _unique_safe_filename(str(item.get("filename") or f"upload-{index + 1}"), used_names)
        used_names.add(safe_name)
        content = bytes(item.get("content") or b"")
        raw_path = raw_dir / safe_name
        raw_path.write_bytes(content)
        source_file = f"raw_docs/{safe_name}"
        profile = build_data_profile(
            raw_path,
            workspace_id=workspace_id,
            name=display_name,
            source_file=source_file,
            content_type=item.get("content_type"),
        )
        profile_id = f"profile-{profile_index + index:03d}"
        profile["profile_id"] = profile_id
        profile["profile_file"] = f"profiles/{profile_id}.json"
        write_profile(profiles_dir / f"{profile_id}.json", profile)
        profiles.append(profile)
        records = upload_to_records(
            raw_path,
            source_file,
            workspace_id,
            content_type=item.get("content_type"),
        )
        content_records.extend(records)
        document = {
            "source_file": source_file,
            "name": safe_name,
            "format": profile.get("format"),
            "bytes": len(content),
            "profile_file": profile["profile_file"],
            "record_count": len(records),
        }
        documents.append(document)
        raw_payloads.append(
            {
                "raw_filename": safe_name,
                "raw_content": content,
                "profile_filename": f"{profile_id}.json",
                "profile": profile,
            }
        )

    for index, item in enumerate(reference_files):
        safe_name = _unique_safe_filename(str(item.get("filename") or f"reference-{index + 1}"), used_reference_names)
        used_reference_names.add(safe_name)
        content = bytes(item.get("content") or b"")
        content_type = _reference_image_content_type(safe_name, item.get("content_type"))
        local_path = reference_dir / safe_name
        local_path.write_bytes(content)
        blob_name = f"workspaces/{workspace_id}/reference_images/{safe_name}"
        asset = {
            "url": _reference_asset_url(workspace_id, safe_name),
            "blob_url": _reference_asset_blob_url(blob_name),
            "blob_name": blob_name,
            "role": role,
            "filename": safe_name,
            "source_file": f"reference_images/{safe_name}",
            "content_type": content_type,
            "bytes": len(content),
        }
        reference_images.append(asset)
        reference_payloads.append({"filename": safe_name, "content": content, "content_type": content_type})

    if profiles:
        aggregate_profile = _aggregate_profiles(
            workspace_id,
            display_name,
            profiles,
            documents,
            existing_profile=existing_profile,
        )
    elif existing_profile:
        aggregate_profile = dict(existing_profile)
        aggregate_profile.setdefault("workspace_id", workspace_id)
        aggregate_profile.setdefault("name", display_name)
        aggregate_profile.setdefault("format", existing_meta.get("format") or "mixed")
        aggregate_profile.setdefault("documents", documents)
        aggregate_profile.setdefault("profile_summary", existing_meta.get("profile_summary") or "Workspace profile retained.")
    else:
        aggregate_profile = _reference_only_profile(workspace_id, display_name, reference_images)
    write_profile(workspace_dir / "profile.json", aggregate_profile)
    created_at = existing_meta.get("created_at") or aggregate_profile["created_at"]
    workspace_meta = {
        "workspace_id": workspace_id,
        "name": display_name,
        "description": description,
        "format": aggregate_profile["format"],
        "language": "zh-Hans",
        "persona": "Uploaded customer data workspace for product feasibility analysis.",
        "ask_when": [
            "The request lacks target customer, product scope, or expected output.",
            "The request depends on fields or facts not present in the uploaded profiles.",
        ],
        "raw_docs": [item["source_file"] for item in documents],
        "documents": documents,
        "profile_files": [item["profile_file"] for item in documents],
        "profile_file": "profile.json",
        "created_at": created_at,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "profile_summary": aggregate_profile["profile_summary"],
        "reference_images": reference_images,
    }
    indexed_delta = _index_documents([profile_to_search_document(profile) for profile in profiles] + content_records)
    previous_indexed = int(existing_meta.get("indexed_count") or 0)
    workspace_meta["indexed_count"] = previous_indexed + indexed_delta
    persistence = _persist_workspace_bundle(
        workspace_id=workspace_id,
        raw_payloads=raw_payloads,
        reference_payloads=reference_payloads,
        workspace_meta=workspace_meta,
        profile=aggregate_profile,
    )
    workspace_meta["persistence"] = persistence
    (workspace_dir / "workspace.json").write_text(json.dumps(workspace_meta, indent=2, ensure_ascii=False), encoding="utf-8")
    _CONTEXT_CACHE.pop(workspace_id, None)
    return {
        "workspace_id": workspace_id,
        "name": display_name,
        "description": description,
        "format": aggregate_profile["format"],
        "indexed_count": workspace_meta["indexed_count"],
        "indexed_delta": indexed_delta,
        "profile_summary": aggregate_profile["profile_summary"],
        "documents": documents,
        "reference_images": reference_images,
    }


def list_workspaces() -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    WORKSPACES.mkdir(parents=True, exist_ok=True)
    for workspace_dir in WORKSPACES.iterdir():
        if not workspace_dir.is_dir():
            continue
        meta_path = workspace_dir / "workspace.json"
        if not meta_path.exists():
            continue
        meta = _read_json(meta_path)
        workspace_id = str(meta.get("workspace_id") or workspace_dir.name)
        if workspace_id.startswith("upload-") and workspace_deleted(workspace_id):
            continue
        profile = _read_profile(workspace_dir, meta)
        doc_count = _workspace_doc_count(workspace_id, workspace_dir, meta)
        by_id[workspace_id] = {
            "workspace_id": workspace_id,
            "name": meta.get("name") or workspace_id,
            "doc_count": doc_count,
            "format": meta.get("format") or profile.get("format") or "mixed",
            "description": meta.get("description"),
            "profile_summary": meta.get("profile_summary") or profile.get("profile_summary"),
            "created_at": meta.get("created_at") or profile.get("created_at"),
            "documents": meta.get("documents") or _detail_documents(workspace_dir, meta),
            "reference_images": _reference_images(meta),
        }
    for item in load_workspace_registry():
        workspace_id = str(item.get("workspace_id") or "")
        if not workspace_id:
            continue
        if workspace_deleted(workspace_id):
            continue
        by_id[workspace_id] = {
            "workspace_id": workspace_id,
            "name": item.get("name") or workspace_id,
            "doc_count": _registry_doc_count(item),
            "format": item.get("format") or "unknown",
            "description": item.get("description"),
            "profile_summary": item.get("profile_summary"),
            "created_at": item.get("created_at"),
            "documents": item.get("documents") or [],
            "reference_images": _reference_images(item),
        }
    return sorted(by_id.values(), key=lambda item: str(item.get("created_at") or ""), reverse=True)


def delete_workspace(workspace_id: str) -> dict[str, Any]:
    workspace_id = str(workspace_id or "").strip()
    if not workspace_id:
        raise ValueError("workspace_id is required")
    if not workspace_id.startswith("upload-"):
        raise PermissionError("Built-in workspaces cannot be deleted")

    registry_entry = get_registry_workspace(workspace_id)
    workspace_dir = WORKSPACES / workspace_id
    if registry_entry is None and not workspace_dir.exists():
        if workspace_deleted(workspace_id):
            return {"workspace_id": workspace_id, "deleted": True, "deleted_docs": 0, "deleted_blobs": 0}
        raise FileNotFoundError(workspace_id)

    deleted_docs = _delete_search_docs(workspace_id)
    blob_result: dict[str, Any] = {"deleted_blobs": 0}
    if registry_entry is not None:
        blob_result = remove_workspace_from_blob(workspace_id)
    if workspace_dir.exists():
        _delete_local_workspace_dir(workspace_dir)
    _CONTEXT_CACHE.pop(workspace_id, None)
    return {
        "workspace_id": workspace_id,
        "deleted": True,
        "deleted_docs": deleted_docs,
        **blob_result,
    }


def get_workspace_detail(workspace_id: str) -> dict[str, Any]:
    workspace_id = str(workspace_id or "").strip()
    if not workspace_id:
        raise ValueError("workspace_id is required")
    if workspace_id.startswith("upload-") and workspace_deleted(workspace_id):
        raise FileNotFoundError(workspace_id)
    workspace_dir = WORKSPACES / workspace_id
    meta: dict[str, Any] = {}
    profile: dict[str, Any] = {}
    if workspace_dir.exists() and (workspace_dir / "workspace.json").exists():
        meta = _read_json(workspace_dir / "workspace.json")
        profile_path = workspace_dir / str(meta.get("profile_file") or "profile.json")
        if profile_path.exists():
            profile = _read_json(profile_path)
    if not meta:
        meta = download_blob_json(f"workspaces/{workspace_id}/workspace.json") or {}
    if not profile:
        profile = download_blob_json(f"workspaces/{workspace_id}/profile.json") or {}
    if not meta and not profile:
        raise FileNotFoundError(workspace_id)

    summary = workspace_context(workspace_id)
    tables = profile.get("tables") or []
    rows = sum(int(table.get("row_count") or 0) for table in tables)
    columns = _detail_columns(tables)
    documents = _detail_documents(workspace_dir, meta)
    return {
        "workspace_id": workspace_id,
        "name": meta.get("name") or profile.get("name") or summary.get("name") or workspace_id,
        "description": meta.get("description") or summary.get("description"),
        "format": meta.get("format") or profile.get("format") or summary.get("format") or "mixed",
        "rows": rows,
        "columns": columns,
        "doc_count": summary.get("doc_count") or _workspace_doc_count(workspace_id, workspace_dir, meta),
        "documents": documents,
        "reference_images": _reference_images(meta),
        "profile_summary": meta.get("profile_summary") or profile.get("profile_summary") or summary.get("profile_summary"),
        "signals": _detail_signals(tables),
        "created_at": meta.get("created_at") or profile.get("created_at") or summary.get("created_at"),
    }


def workspace_context(workspace_id: str) -> dict[str, Any]:
    now = time.monotonic()
    cached = _CONTEXT_CACHE.get(workspace_id)
    if cached and now < cached[0]:
        return dict(cached[1])
    local = _local_workspace_summary(workspace_id)
    if local:
        _CONTEXT_CACHE[workspace_id] = (now + _CONTEXT_CACHE_SECONDS, dict(local))
        return local
    for item in list_workspaces():
        if item["workspace_id"] == workspace_id:
            _CONTEXT_CACHE[workspace_id] = (now + _CONTEXT_CACHE_SECONDS, dict(item))
            return item
    fallback = {
        "workspace_id": workspace_id,
        "name": workspace_id,
        "doc_count": 0,
        "format": "unknown",
        "description": None,
        "profile_summary": None,
        "documents": [],
        "reference_images": [],
    }
    _CONTEXT_CACHE[workspace_id] = (now + min(_CONTEXT_CACHE_SECONDS, 10), dict(fallback))
    return fallback


def workspace_reference_images(workspace_id: str) -> list[dict[str, Any]]:
    try:
        bundle = _load_workspace_bundle(workspace_id)
    except Exception:
        bundle = None
    if not bundle:
        return []
    meta, _profile = bundle
    return _reference_images(meta)


def get_reference_image_content(workspace_id: str, filename: str) -> tuple[bytes, str] | None:
    safe_name = Path(filename or "").name
    if not workspace_id or not safe_name:
        return None
    workspace_dir = WORKSPACES / workspace_id
    local_path = workspace_dir / "reference_images" / safe_name
    if local_path.exists() and local_path.is_file():
        return local_path.read_bytes(), _reference_image_content_type(safe_name, None)
    bundle = _load_workspace_bundle(workspace_id)
    meta = bundle[0] if bundle else {}
    for item in _reference_images(meta):
        if Path(str(item.get("filename") or "")).name != safe_name:
            continue
        blob_name = str(item.get("blob_name") or f"workspaces/{workspace_id}/reference_images/{safe_name}")
        downloaded = download_blob_content(blob_name)
        if downloaded:
            return downloaded
    return None


def _local_workspace_summary(workspace_id: str) -> dict[str, Any] | None:
    workspace_dir = WORKSPACES / workspace_id
    meta_path = workspace_dir / "workspace.json"
    if not workspace_dir.exists() or not meta_path.exists():
        return None
    if workspace_id.startswith("upload-") and workspace_deleted(workspace_id):
        return None
    meta = _read_json(meta_path)
    profile = _read_profile(workspace_dir, meta)
    return {
        "workspace_id": str(meta.get("workspace_id") or workspace_id),
        "name": meta.get("name") or workspace_id,
        "doc_count": _workspace_doc_count(workspace_id, workspace_dir, meta),
        "format": meta.get("format") or profile.get("format") or "mixed",
        "description": meta.get("description"),
        "profile_summary": meta.get("profile_summary") or profile.get("profile_summary"),
        "created_at": meta.get("created_at") or profile.get("created_at"),
        "documents": meta.get("documents") or [],
        "reference_images": _reference_images(meta),
    }


def _load_workspace_bundle(workspace_id: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    workspace_dir = WORKSPACES / workspace_id
    meta: dict[str, Any] = {}
    profile: dict[str, Any] = {}
    meta_path = workspace_dir / "workspace.json"
    if meta_path.exists():
        meta = _read_json(meta_path)
        profile_path = workspace_dir / str(meta.get("profile_file") or "profile.json")
        if profile_path.exists():
            profile = _read_json(profile_path)
    if not meta:
        meta = download_blob_json(f"workspaces/{workspace_id}/workspace.json") or {}
    if not profile:
        profile = download_blob_json(f"workspaces/{workspace_id}/profile.json") or {}
    if not meta and not profile:
        return None
    if not meta:
        meta = {
            "workspace_id": workspace_id,
            "name": profile.get("name") or workspace_id,
            "format": profile.get("format") or "mixed",
            "profile_file": "profile.json",
            "profile_summary": profile.get("profile_summary"),
            "created_at": profile.get("created_at"),
            "documents": profile.get("documents") or [],
        }
    return meta, profile


def _index_profile(profile: dict[str, Any]) -> int:
    return _index_documents([profile_to_search_document(profile)])


def _index_profiles(profiles: list[dict[str, Any]]) -> int:
    return _index_documents([profile_to_search_document(profile) for profile in profiles])


def _index_documents(docs: list[dict[str, Any]]) -> int:
    if not docs:
        return 0
    try:
        return index_documents(docs)
    except Exception:
        if search_endpoint():
            raise
        return len(docs)


def _aggregate_profiles(
    workspace_id: str,
    name: str,
    profiles: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    *,
    existing_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tables: list[dict[str, Any]] = []
    formats = {str(profile.get("format") or "unknown") for profile in profiles}
    if existing_profile:
        existing_format = str(existing_profile.get("format") or "").strip()
        if existing_format and existing_format != "mixed":
            formats.add(existing_format)
        for table in existing_profile.get("tables") or []:
            if isinstance(table, dict):
                tables.append(dict(table))
    for profile in profiles:
        for table in profile.get("tables") or []:
            item = dict(table)
            item["source_file"] = profile.get("source_file")
            tables.append(item)
    sorted_formats = sorted(formats or {"unknown"})
    document_profiles = [
        dict(item)
        for item in (existing_profile or {}).get("document_profiles") or []
        if isinstance(item, dict)
    ]
    document_profiles.extend(
        {
            "profile_id": profile.get("profile_id"),
            "profile_file": profile.get("profile_file"),
            "source_file": profile.get("source_file"),
            "format": profile.get("format"),
            "profile_summary": profile.get("profile_summary"),
        }
        for profile in profiles
    )
    aggregate = {
        "workspace_id": workspace_id,
        "name": name,
        "format": sorted_formats[0] if len(sorted_formats) == 1 else "mixed",
        "source_file": "profile.json",
        "created_at": (existing_profile or {}).get("created_at")
        or (profiles[0].get("created_at") if profiles else datetime.now(timezone.utc).isoformat()),
        "tables": tables,
        "documents": documents,
        "document_profiles": document_profiles,
    }
    summaries = []
    if existing_profile and existing_profile.get("profile_summary"):
        summaries.append(str(existing_profile.get("profile_summary") or ""))
    summaries.extend(str(profile.get("profile_summary") or "") for profile in profiles if profile.get("profile_summary"))
    aggregate["profile_summary"] = " | ".join(summaries[:5])[:1800] or f"{len(profiles)} uploaded documents profiled."
    return aggregate


def _persist_workspace(
    *,
    workspace_id: str,
    safe_name: str,
    content: bytes,
    workspace_meta: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, Any]:
    try:
        return persist_workspace(
            workspace_id=workspace_id,
            raw_filename=safe_name,
            raw_content=content,
            workspace_meta=workspace_meta,
            profile=profile,
        )
    except Exception as exc:
        if _blob_configured_for_workspace() and search_endpoint():
            raise
        return {"mode": "local_fallback", "error": str(exc)[:300]}


def _persist_workspace_bundle(
    *,
    workspace_id: str,
    raw_payloads: list[dict[str, Any]],
    reference_payloads: list[dict[str, Any]],
    workspace_meta: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, Any]:
    try:
        from .blob_store import persist_workspace_bundle
    except ImportError:
        from blob_store import persist_workspace_bundle

    try:
        return persist_workspace_bundle(
            workspace_id=workspace_id,
            raw_payloads=raw_payloads,
            reference_payloads=reference_payloads,
            workspace_meta=workspace_meta,
            profile=profile,
        )
    except Exception as exc:
        if _blob_configured_for_workspace() and search_endpoint():
            raise
        return {"mode": "local_fallback", "error": str(exc)[:300]}


def _registry_doc_count(item: dict[str, Any]) -> int:
    documents = item.get("documents") or []
    if documents:
        return len(documents)
    return int(item.get("doc_count") or (1 if item.get("source_file") else 0) or 1)


def _blob_configured_for_workspace() -> bool:
    return bool(os.environ.get("AZURE_STORAGE_CONNECTION_STRING") or os.environ.get("DF_STORAGE_ACCOUNT"))


def _delete_search_docs(workspace_id: str) -> int:
    try:
        return delete_workspace_docs(workspace_id)
    except Exception:
        if search_endpoint():
            raise
        return 0


def _delete_local_workspace_dir(workspace_dir: Path) -> None:
    root = WORKSPACES.resolve()
    target = workspace_dir.resolve()
    if root not in target.parents:
        raise RuntimeError(f"Refusing to delete outside workspace root: {target}")
    shutil.rmtree(target)


def _workspace_doc_count(workspace_id: str, workspace_dir: Path, meta: dict[str, Any]) -> int:
    if meta.get("documents"):
        return len(meta.get("documents") or [])
    raw_docs = meta.get("raw_docs") or []
    if raw_docs:
        return len(raw_docs)
    if meta.get("profile_file"):
        return 1
    return 0


def _read_profile(workspace_dir: Path, meta: dict[str, Any]) -> dict[str, Any]:
    profile_file = meta.get("profile_file")
    if not profile_file:
        return {}
    path = workspace_dir / str(profile_file)
    if not path.exists():
        return {}
    return compact_profile_for_workspace(_read_json(path))


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _detail_columns(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    table_signals = {
        str(table.get("name") or ""): (table.get("signals") or []) + (table.get("cross_signals") or [])
        for table in tables
    }
    for table in tables:
        table_name = str(table.get("name") or "")
        signals = table_signals.get(table_name, [])
        for column in table.get("columns") or []:
            name = str(column.get("name") or "")
            key = f"{table_name}:{name}"
            if key in seen:
                continue
            seen.add(key)
            signal = next((item for item in signals if name and name in str(item)), "")
            items.append(
                {
                    "table": table_name,
                    "name": name,
                    "role": column.get("type") or "unknown",
                    "signal": signal,
                    "missing_rate": column.get("missing_rate", 0),
                    "unique_count": column.get("unique_count", 0),
                    "top_values": column.get("top_values", [])[:5],
                }
            )
    return items[:120]


def _detail_signals(tables: list[dict[str, Any]]) -> list[str]:
    signals: list[str] = []
    for table in tables:
        signals.extend(str(item) for item in table.get("cross_signals", [])[:8])
        signals.extend(str(item) for item in table.get("signals", [])[:8])
    return signals[:20]


def _detail_documents(workspace_dir: Path, meta: dict[str, Any]) -> list[dict[str, Any]]:
    if meta.get("documents"):
        return [item for item in meta.get("documents") or [] if isinstance(item, dict)]
    documents: list[dict[str, Any]] = []
    for rel in meta.get("raw_docs") or []:
        path = workspace_dir / str(rel)
        documents.append(
            {
                "source_file": str(rel),
                "name": Path(str(rel)).name,
                "format": Path(str(rel)).suffix.lstrip(".").lower() or "unknown",
                "bytes": path.stat().st_size if path.exists() else None,
            }
        )
    for item in meta.get("external_docs") or []:
        documents.append(
            {
                "source_file": item.get("source_file") or item.get("path"),
                "name": item.get("title") or Path(str(item.get("source_file") or item.get("path") or "external")).name,
                "format": Path(str(item.get("source_file") or item.get("path") or "")).suffix.lstrip(".").lower() or "external",
                "external": True,
            }
        )
    if not documents and meta.get("profile_file"):
        documents.append({"source_file": meta.get("profile_file"), "name": "profile.json", "format": "profile"})
    return documents


def _is_reference_image(item: dict[str, Any]) -> bool:
    filename = str(item.get("filename") or "")
    suffix = Path(filename).suffix.lower()
    content_type = str(item.get("content_type") or "").split(";")[0].strip().lower()
    return suffix in IMAGE_EXTENSIONS or content_type in IMAGE_CONTENT_TYPES


def _normalize_asset_role(value: str | None) -> str:
    role = str(value or "reference").strip().lower()
    return role if role in REFERENCE_ROLES else "reference"


def _reference_image_content_type(filename: str, content_type: Any) -> str:
    provided = str(content_type or "").split(";")[0].strip().lower()
    if provided in IMAGE_CONTENT_TYPES:
        return "image/jpeg" if provided == "image/jpg" else provided
    suffix = Path(filename).suffix.lower()
    if suffix == ".png":
        return "image/png"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    return "application/octet-stream"


def _reference_images(meta: dict[str, Any]) -> list[dict[str, Any]]:
    images: list[dict[str, Any]] = []
    for item in meta.get("reference_images") or []:
        if not isinstance(item, dict):
            continue
        filename = Path(str(item.get("filename") or item.get("source_file") or "reference-image")).name
        if not filename:
            continue
        workspace_id = str(meta.get("workspace_id") or "")
        normalized: dict[str, Any] = {
            "url": item.get("url") or (_reference_asset_url(workspace_id, filename) if workspace_id else ""),
            "role": _normalize_asset_role(str(item.get("role") or "reference")),
            "filename": filename,
        }
        for key in ("blob_url", "blob_name", "source_file", "content_type", "bytes"):
            if item.get(key) is not None:
                normalized[key] = item.get(key)
        images.append(normalized)
    return images


def _existing_reference_names(meta: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for item in _reference_images(meta):
        filename = item.get("filename")
        if filename:
            names.add(Path(str(filename)).name)
    return names


def _reference_asset_url(workspace_id: str, filename: str) -> str:
    safe = Path(filename).name
    return f"/api/workspaces/{workspace_id}/reference-images/{safe}"


def _reference_asset_blob_url(blob_name: str) -> str:
    account = os.environ.get("DF_STORAGE_ACCOUNT")
    container = os.environ.get("DF_WORKSPACE_CONTAINER", "dataforge-workspaces")
    if account:
        return f"https://{account}.blob.core.windows.net/{container}/{blob_name}"
    return f"{container}/{blob_name}"


def _reference_only_profile(workspace_id: str, name: str, reference_images: list[dict[str, Any]]) -> dict[str, Any]:
    roles = sorted({str(item.get("role") or "reference") for item in reference_images})
    return {
        "workspace_id": workspace_id,
        "name": name,
        "format": "reference_images",
        "source_file": "reference_images",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tables": [],
        "documents": [],
        "document_profiles": [],
        "profile_summary": f"工作区包含 {len(reference_images)} 张参考图片素材；角色包括：{', '.join(roles) or 'reference'}。",
    }


def _existing_raw_names(meta: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for item in meta.get("documents") or []:
        if not isinstance(item, dict):
            continue
        value = item.get("source_file") or item.get("name")
        if value:
            names.add(Path(str(value)).name)
    for rel in meta.get("raw_docs") or []:
        if rel:
            names.add(Path(str(rel)).name)
    return names


def _next_profile_index(meta: dict[str, Any], profile: dict[str, Any]) -> int:
    indexes: list[int] = []
    values: list[str] = []
    values.extend(str(item) for item in meta.get("profile_files") or [])
    for item in meta.get("documents") or []:
        if isinstance(item, dict) and item.get("profile_file"):
            values.append(str(item.get("profile_file")))
    for item in profile.get("document_profiles") or []:
        if isinstance(item, dict):
            values.append(str(item.get("profile_id") or ""))
            values.append(str(item.get("profile_file") or ""))
    for value in values:
        match = re.search(r"profile-(\d+)", value)
        if match:
            indexes.append(int(match.group(1)))
    return (max(indexes) + 1) if indexes else 0


def _unique_workspace_id(value: str) -> str:
    base = _safe_slug(value) or "upload"
    if not base.startswith("upload-"):
        base = f"upload-{base}"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    candidate = f"{base}-{stamp}"
    existing = {str(item.get("workspace_id")) for item in load_workspace_registry()}
    if not (WORKSPACES / candidate).exists() and candidate not in existing:
        return candidate
    return f"{candidate}-{uuid.uuid4().hex[:6]}"


def _safe_filename(filename: str) -> str:
    name = Path(filename or "upload.dat").name
    stem = _safe_slug(Path(name).stem) or "upload"
    suffix = Path(name).suffix.lower()
    return f"{stem}{suffix}"


def _unique_safe_filename(filename: str, used: set[str]) -> str:
    safe = _safe_filename(filename)
    if safe not in used:
        return safe
    stem = Path(safe).stem
    suffix = Path(safe).suffix
    index = 2
    while f"{stem}-{index}{suffix}" in used:
        index += 1
    return f"{stem}-{index}{suffix}"


def _safe_slug(value: str) -> str:
    text = value.strip().lower()
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff_-]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-_")
    if not text:
        return ""
    ascii_text = re.sub(r"[^a-z0-9_-]+", "", text)
    ascii_text = re.sub(r"-{2,}", "-", ascii_text).strip("-_")
    return ascii_text or f"cn-{uuid.uuid5(uuid.NAMESPACE_URL, text).hex[:10]}"
