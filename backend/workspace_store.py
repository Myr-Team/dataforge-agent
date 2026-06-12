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

from ingest.profiler import build_data_profile, compact_profile_for_workspace, profile_to_search_document, write_profile

try:
    from .blob_store import download_blob_json, get_registry_workspace, load_workspace_registry, persist_workspace, remove_workspace_from_blob, workspace_deleted
    from .search_admin import count_workspace_docs, delete_workspace_docs, index_documents, search_endpoint
except ImportError:
    from blob_store import download_blob_json, get_registry_workspace, load_workspace_registry, persist_workspace, remove_workspace_from_blob, workspace_deleted
    from search_admin import count_workspace_docs, delete_workspace_docs, index_documents, search_endpoint


ROOT = Path(__file__).resolve().parents[1]
WORKSPACES = ROOT / "workspaces"
_CONTEXT_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CONTEXT_CACHE_SECONDS = float(os.environ.get("DF_WORKSPACE_CONTEXT_CACHE_SECONDS", "60"))


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
            "profile_summary": meta.get("profile_summary") or profile.get("profile_summary"),
            "created_at": meta.get("created_at") or profile.get("created_at"),
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
            "profile_summary": item.get("profile_summary"),
            "created_at": item.get("created_at"),
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
        "format": meta.get("format") or profile.get("format") or summary.get("format") or "mixed",
        "rows": rows,
        "columns": columns,
        "doc_count": summary.get("doc_count") or _workspace_doc_count(workspace_id, workspace_dir, meta),
        "documents": documents,
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
        "profile_summary": None,
    }
    _CONTEXT_CACHE[workspace_id] = (now + min(_CONTEXT_CACHE_SECONDS, 10), dict(fallback))
    return fallback


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
        "doc_count": int(meta.get("indexed_count") or len(meta.get("raw_docs") or []) or 1),
        "format": meta.get("format") or profile.get("format") or "mixed",
        "profile_summary": meta.get("profile_summary") or profile.get("profile_summary"),
        "created_at": meta.get("created_at") or profile.get("created_at"),
    }


def _index_profile(profile: dict[str, Any]) -> int:
    doc = profile_to_search_document(profile)
    try:
        return index_documents([doc])
    except Exception:
        if search_endpoint():
            raise
        return 1


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
        if search_endpoint():
            raise
        return {"mode": "local_fallback", "error": str(exc)[:300]}


def _registry_doc_count(item: dict[str, Any]) -> int:
    workspace_id = str(item.get("workspace_id") or "")
    if workspace_id:
        try:
            return count_workspace_docs(workspace_id)
        except Exception:
            pass
    return int(item.get("doc_count") or 1)


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
    try:
        return count_workspace_docs(workspace_id)
    except Exception:
        pass
    if meta.get("profile_file"):
        return 1
    raw_docs = meta.get("raw_docs") or []
    return len(raw_docs)


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


def _safe_slug(value: str) -> str:
    text = value.strip().lower()
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff_-]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-_")
    if not text:
        return ""
    ascii_text = re.sub(r"[^a-z0-9_-]+", "", text)
    ascii_text = re.sub(r"-{2,}", "-", ascii_text).strip("-_")
    return ascii_text or f"cn-{uuid.uuid5(uuid.NAMESPACE_URL, text).hex[:10]}"
