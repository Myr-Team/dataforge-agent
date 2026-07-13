from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ingest.adapters.upload_to_records import upload_to_records
from ingest.profiler import build_data_profile, compact_profile_for_workspace, detect_format, profile_to_search_document, write_profile

try:
    from .customer_text import customer_summary_from_profile, friendly_label, sanitize_customer_text
    from .identity import public_actor
    from .blob_store import (
        download_blob_content,
        download_blob_json,
        get_registry_workspace,
        load_workspace_registry,
        persist_workspace,
        remove_workspace_from_blob,
        upload_blob_json,
        workspace_deleted,
    )
    from .search_admin import count_workspace_docs, delete_workspace_docs, index_documents, search_endpoint
except ImportError:
    from customer_text import customer_summary_from_profile, friendly_label, sanitize_customer_text
    from identity import public_actor
    from blob_store import (
        download_blob_content,
        download_blob_json,
        get_registry_workspace,
        load_workspace_registry,
        persist_workspace,
        remove_workspace_from_blob,
        upload_blob_json,
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
STATUS_PROCESSING = "解析中"
STATUS_READY = "已就绪"
STATUS_PARTIAL = "部分字段"
STATUS_FAILED = "失败"
STATUS_REFERENCE = "仅参考"
INGEST_INDEX_BATCH_SIZE = max(1, int(os.environ.get("DF_INGEST_INDEX_BATCH_SIZE", "150")))
INGEST_FILE_TIMEOUT_SECONDS = max(0.0, float(os.environ.get("DF_INGEST_FILE_TIMEOUT_SECONDS", "120")))
INGEST_FILE_MAX_RETRIES = max(1, int(os.environ.get("DF_INGEST_FILE_MAX_RETRIES", "2")))
INGEST_STALE_SECONDS = max(15.0, float(os.environ.get("DF_INGEST_STALE_SECONDS", "60")))


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
    workspace_meta["documents"] = _normalize_documents(workspace_dir, workspace_meta, profile)
    manifest = _build_workspace_manifest(workspace_id, workspace_meta, profile)
    workspace_meta["manifest_file"] = "manifest.json"
    (workspace_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    persistence = _persist_workspace(
        workspace_id=workspace_id,
        safe_name=safe_name,
        content=content,
        workspace_meta=workspace_meta,
        profile=profile,
    )
    _persist_workspace_manifest(workspace_id, manifest)
    workspace_meta["persistence"] = persistence
    (workspace_dir / "workspace.json").write_text(json.dumps(workspace_meta, indent=2, ensure_ascii=False), encoding="utf-8")
    _CONTEXT_CACHE.pop(workspace_id, None)
    return {
        "workspace_id": workspace_id,
        "name": display_name,
        "format": profile["format"],
        "indexed_count": indexed_count,
        "profile_summary": profile["profile_summary"],
        "documents": workspace_meta["documents"],
    }


def create_workspace_upload_job(
    *,
    files: list[dict[str, Any]],
    name: str | None = None,
    description: str | None = None,
    requested_workspace_id: str | None = None,
    reserved_workspace_id: str | None = None,
    asset_role: str | None = None,
    actor: dict[str, Any] | None = None,
    force_new_version: bool = False,
) -> dict[str, Any]:
    clean_files = [item for item in files if item.get("content")]
    if not clean_files:
        raise ValueError("Uploaded file is empty")
    role = _normalize_asset_role(asset_role)
    data_files = [item for item in clean_files if not _is_reference_image(item)]
    reference_files = [item for item in clean_files if _is_reference_image(item)]
    append_workspace_id = str(requested_workspace_id or "").strip()
    reserved_id = str(reserved_workspace_id or "").strip()
    if append_workspace_id and reserved_id:
        raise ValueError("requested_workspace_id and reserved_workspace_id are mutually exclusive")
    if append_workspace_id and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,159}", append_workspace_id):
        raise ValueError("requested_workspace_id is invalid")
    if reserved_id and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,159}", reserved_id):
        raise ValueError("reserved_workspace_id is invalid")
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
    workspace_id = append_workspace_id or reserved_id or _unique_workspace_id(display_name)
    workspace_root = WORKSPACES.resolve()
    workspace_dir = (workspace_root / workspace_id).resolve()
    if workspace_root not in workspace_dir.parents:
        raise ValueError("workspace_id escapes workspace root")
    raw_dir = workspace_dir / "raw_docs"
    profiles_dir = workspace_dir / "profiles"
    reference_dir = workspace_dir / "reference_images"
    raw_dir.mkdir(parents=True, exist_ok=bool(append_workspace_id))
    profiles_dir.mkdir(parents=True, exist_ok=True)
    if reference_files:
        reference_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).isoformat()
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    documents = _normalize_documents(workspace_dir, existing_meta, existing_profile) if existing_meta or existing_profile else []
    reference_images = _reference_images(existing_meta)
    used_names = _existing_raw_names({"documents": documents, "raw_docs": existing_meta.get("raw_docs") or []})
    used_reference_names = _existing_reference_names(existing_meta)
    raw_payloads: list[dict[str, Any]] = []
    reference_payloads: list[dict[str, Any]] = []
    pending_sources: list[str] = []
    skipped_sources: list[str] = []
    profile_index = _next_profile_index(existing_meta, existing_profile)
    seen_hashes = {
        str(item.get("content_sha256") or "")
        for item in documents
        if isinstance(item, dict) and item.get("content_sha256")
    }

    for index, item in enumerate(data_files):
        content = bytes(item.get("content") or b"")
        content_hash = hashlib.sha256(content).hexdigest()
        existing_doc = _find_existing_document(documents, content_hash)
        if existing_doc and existing_doc.get("status") != STATUS_FAILED and not force_new_version:
            skipped_sources.append(str(existing_doc.get("source_file") or existing_doc.get("name") or ""))
            continue

        if existing_doc and not force_new_version:
            safe_name = Path(str(existing_doc.get("source_file") or existing_doc.get("name") or "")).name
            source_file = str(existing_doc.get("source_file") or f"raw_docs/{safe_name}")
            document = existing_doc
        else:
            safe_name = _unique_safe_filename(str(item.get("filename") or f"upload-{index + 1}"), used_names)
            used_names.add(safe_name)
            source_file = f"raw_docs/{safe_name}"
            document = {
                "source_file": source_file,
                "name": safe_name,
                "created_at": now,
            }
            documents.append(document)

        raw_path = raw_dir / safe_name
        raw_path.write_bytes(content)
        fmt = _cheap_upload_format(safe_name, item.get("content_type"))
        profile_id = _profile_id_for_document(document, profile_index + len(pending_sources))
        profile_file = str(document.get("profile_file") or f"profiles/{profile_id}.json")
        document.update(
            {
                "source_file": source_file,
                "name": safe_name,
                "format": fmt,
                "bytes": len(content),
                "profile_file": profile_file,
                "record_count": int(document.get("record_count") or 0),
                "indexed_count": int(document.get("indexed_count") or 0),
                "status": STATUS_PROCESSING,
                "error": None,
                "ingest_job_id": job_id,
                "content_sha256": content_hash,
                "content_type": item.get("content_type"),
                "updated_at": now,
            }
        )
        seen_hashes.add(content_hash)
        pending_sources.append(source_file)
        raw_payloads.append({"raw_filename": safe_name, "raw_content": content})

    for index, item in enumerate(reference_files):
        content = bytes(item.get("content") or b"")
        content_hash = hashlib.sha256(content).hexdigest()
        if content_hash in seen_hashes:
            continue
        safe_name = _unique_safe_filename(str(item.get("filename") or f"reference-{index + 1}"), used_reference_names)
        used_reference_names.add(safe_name)
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
            "content_sha256": content_hash,
        }
        reference_images.append(asset)
        reference_payloads.append({"filename": safe_name, "content": content, "content_type": content_type})
        seen_hashes.add(content_hash)

    if existing_profile:
        aggregate_profile = dict(existing_profile)
        aggregate_profile.setdefault("workspace_id", workspace_id)
        aggregate_profile.setdefault("name", display_name)
        aggregate_profile.setdefault("format", existing_meta.get("format") or "mixed")
        aggregate_profile.setdefault("source_file", "profile.json")
        aggregate_profile.setdefault("created_at", existing_meta.get("created_at") or now)
        aggregate_profile["documents"] = documents
        if pending_sources:
            aggregate_profile["profile_summary"] = _processing_summary(display_name, documents)
    elif reference_images and not documents:
        aggregate_profile = _reference_only_profile(workspace_id, display_name, reference_images)
    else:
        aggregate_profile = _pending_workspace_profile(workspace_id, display_name, documents, now)

    created_at = existing_meta.get("created_at") or aggregate_profile.get("created_at") or now
    indexed_count = int(existing_meta.get("indexed_count") or 0)
    workspace_meta = {
        "workspace_id": workspace_id,
        "name": display_name,
        "description": description,
        "format": aggregate_profile.get("format") or _format_from_documents(documents, reference_images),
        "language": "zh-Hans",
        "persona": existing_meta.get("persona") or "Uploaded customer data workspace for product feasibility analysis.",
        "ask_when": existing_meta.get("ask_when")
        or [
            "The request lacks target customer, product scope, or expected output.",
            "The request depends on fields or facts not present in the uploaded profiles.",
        ],
        "raw_docs": [item["source_file"] for item in documents if item.get("source_file")],
        "documents": documents,
        "profile_files": [item["profile_file"] for item in documents if item.get("profile_file")],
        "profile_file": "profile.json",
        "manifest_file": "manifest.json",
        "created_at": created_at,
        "updated_at": now,
        "profile_summary": aggregate_profile.get("profile_summary") or _processing_summary(display_name, documents),
        "reference_images": reference_images,
        "indexed_count": indexed_count,
        "workspace_owner": existing_meta.get("workspace_owner") or public_actor(actor) or None,
        "ingest_jobs": _upsert_ingest_job(
            existing_meta.get("ingest_jobs"),
            {
                "job_id": job_id,
                "state": "processing" if pending_sources else "ready",
                "created_at": now,
                "updated_at": now,
                "pending_sources": pending_sources,
                "skipped_sources": skipped_sources,
            },
        ),
    }
    workspace_meta["documents"] = _normalize_documents(workspace_dir, workspace_meta, aggregate_profile)
    manifest = _build_workspace_manifest(workspace_id, workspace_meta, aggregate_profile)
    _write_workspace_files(workspace_dir, workspace_meta, aggregate_profile, manifest)
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
        "format": workspace_meta["format"],
        "indexed_count": indexed_count,
        "profile_summary": workspace_meta["profile_summary"],
        "documents": workspace_meta["documents"],
        "reference_images": reference_images,
        "ingest_job_id": job_id if pending_sources else None,
        "ingest_status": _ingest_status_from_documents(workspace_meta),
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
            "status": "已就绪" if records or profile.get("tables") else "部分字段",
            "created_at": profile.get("created_at"),
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
    workspace_meta["documents"] = _normalize_documents(workspace_dir, workspace_meta, aggregate_profile)
    manifest = _build_workspace_manifest(workspace_id, workspace_meta, aggregate_profile)
    workspace_meta["manifest_file"] = "manifest.json"
    (workspace_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    persistence = _persist_workspace_bundle(
        workspace_id=workspace_id,
        raw_payloads=raw_payloads,
        reference_payloads=reference_payloads,
        workspace_meta=workspace_meta,
        profile=aggregate_profile,
    )
    _persist_workspace_manifest(workspace_id, manifest)
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
        "documents": workspace_meta["documents"],
        "reference_images": reference_images,
    }


def run_workspace_ingest_job(workspace_id: str, job_id: str) -> dict[str, Any]:
    workspace_id = str(workspace_id or "").strip()
    job_id = str(job_id or "").strip()
    if not workspace_id or not job_id:
        raise ValueError("workspace_id and job_id are required")
    loaded = _load_workspace_bundle(workspace_id)
    if loaded is None:
        raise FileNotFoundError(workspace_id)
    meta, profile = loaded
    workspace_dir = WORKSPACES / workspace_id
    (workspace_dir / "raw_docs").mkdir(parents=True, exist_ok=True)
    (workspace_dir / "profiles").mkdir(parents=True, exist_ok=True)
    meta["ingest_jobs"] = _mark_ingest_job(meta.get("ingest_jobs"), job_id, state="processing")
    try:
        _persist_workspace_state(workspace_id, workspace_dir, meta, profile, include_raw_payloads=True)

        for document in list(meta.get("documents") or []):
            if not isinstance(document, dict):
                continue
            if document.get("ingest_job_id") != job_id or document.get("status") != STATUS_PROCESSING:
                continue
            try:
                document["status"] = STATUS_PROCESSING
                document["error"] = None
                document["started_at"] = document.get("started_at") or _utc_now_iso()
                document["updated_at"] = _utc_now_iso()
                document["attempt_count"] = int(document.get("attempt_count") or 0)
                _replace_document(meta, document)
                meta["ingest_jobs"] = _mark_ingest_job(
                    meta.get("ingest_jobs"),
                    job_id,
                    state="processing",
                    pct=_ingest_status_from_documents(meta)["pct"],
                )
                _persist_workspace_state(workspace_id, workspace_dir, meta, profile)
                _ingest_document_with_retries(workspace_id, workspace_dir, meta, profile, document)
            except Exception as exc:
                document["status"] = STATUS_FAILED
                document["error"] = f"{type(exc).__name__}: {exc}"[:500]
                document["updated_at"] = _utc_now_iso()
            _replace_document(meta, document)
            profile = _rebuild_workspace_profile(workspace_id, workspace_dir, meta, profile)
            _persist_workspace_state(workspace_id, workspace_dir, meta, profile)

        status = _ingest_status_from_documents(meta)
        meta["ingest_jobs"] = _mark_ingest_job(meta.get("ingest_jobs"), job_id, state=status["state"], pct=status["pct"])
        _persist_workspace_state(workspace_id, workspace_dir, meta, profile)
        _CONTEXT_CACHE.pop(workspace_id, None)
        return status
    except BaseException as exc:
        for document in meta.get("documents") or []:
            if not isinstance(document, dict):
                continue
            if document.get("ingest_job_id") == job_id and document.get("status") == STATUS_PROCESSING:
                document["status"] = STATUS_FAILED
                document["error"] = f"Ingest job interrupted: {type(exc).__name__}: {exc}"[:500]
                document["updated_at"] = _utc_now_iso()
        status = _ingest_status_from_documents(meta)
        meta["ingest_jobs"] = _mark_ingest_job(meta.get("ingest_jobs"), job_id, state=status["state"], pct=status["pct"])
        try:
            profile = _rebuild_workspace_profile(workspace_id, workspace_dir, meta, profile)
            _persist_workspace_state(workspace_id, workspace_dir, meta, profile)
        except Exception:
            pass
        if isinstance(exc, Exception):
            raise
        raise


def workspace_ingest_status(workspace_id: str) -> dict[str, Any]:
    workspace_id = str(workspace_id or "").strip()
    if not workspace_id:
        raise ValueError("workspace_id is required")
    loaded = _load_workspace_bundle(workspace_id)
    if loaded is None:
        raise FileNotFoundError(workspace_id)
    meta, _profile = loaded
    status = _ingest_status_from_documents(meta)
    status["workspace_id"] = workspace_id
    return status


def workspace_pending_ingest_jobs(workspace_id: str, *, stale_only: bool = True) -> list[dict[str, Any]]:
    workspace_id = str(workspace_id or "").strip()
    if not workspace_id:
        raise ValueError("workspace_id is required")
    loaded = _load_workspace_bundle(workspace_id)
    if loaded is None:
        raise FileNotFoundError(workspace_id)
    meta, _profile = loaded
    now = datetime.now(timezone.utc)
    processing_docs = [
        item
        for item in meta.get("documents") or []
        if isinstance(item, dict) and item.get("status") == STATUS_PROCESSING and item.get("ingest_job_id")
    ]
    by_job: dict[str, list[dict[str, Any]]] = {}
    for document in processing_docs:
        by_job.setdefault(str(document.get("ingest_job_id")), []).append(document)
    jobs: list[dict[str, Any]] = []
    for job_id, documents in by_job.items():
        timestamps = [
            _parse_utc_iso(item.get("updated_at") or item.get("started_at") or item.get("created_at"))
            for item in documents
        ]
        for job in meta.get("ingest_jobs") or []:
            if isinstance(job, dict) and str(job.get("job_id") or "") == job_id:
                timestamps.append(_parse_utc_iso(job.get("updated_at") or job.get("created_at")))
        last_update = max((item for item in timestamps if item is not None), default=None)
        age_seconds = (now - last_update).total_seconds() if last_update else INGEST_STALE_SECONDS + 1
        if stale_only and age_seconds < INGEST_STALE_SECONDS:
            continue
        jobs.append(
            {
                "workspace_id": workspace_id,
                "ingest_job_id": job_id,
                "age_seconds": int(age_seconds),
                "processing_count": len(documents),
            }
        )
    return jobs


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
    prefer_blob = _prefer_blob_workspace_state(workspace_id)
    if prefer_blob:
        meta = download_blob_json(f"workspaces/{workspace_id}/workspace.json") or {}
        profile = download_blob_json(f"workspaces/{workspace_id}/profile.json") or {}
    if not meta and workspace_dir.exists() and (workspace_dir / "workspace.json").exists():
        meta = _read_json(workspace_dir / "workspace.json")
        profile_path = workspace_dir / str(meta.get("profile_file") or "profile.json")
        if profile_path.exists():
            profile = _read_json(profile_path)
    if not meta and not prefer_blob:
        meta = download_blob_json(f"workspaces/{workspace_id}/workspace.json") or {}
    if not profile and not prefer_blob:
        profile = download_blob_json(f"workspaces/{workspace_id}/profile.json") or {}
    if not profile and meta:
        profile_path = workspace_dir / str(meta.get("profile_file") or "profile.json")
        if profile_path.exists():
            profile = _read_json(profile_path)
    if meta and not meta.get("last_analysis") and not prefer_blob:
        blob_meta = download_blob_json(f"workspaces/{workspace_id}/workspace.json") or {}
        if isinstance(blob_meta, dict) and blob_meta.get("last_analysis"):
            meta["last_analysis"] = blob_meta["last_analysis"]
    if not meta and not profile:
        raise FileNotFoundError(workspace_id)

    summary = workspace_context(workspace_id)
    tables = profile.get("tables") or []
    rows = sum(int(table.get("row_count") or 0) for table in tables)
    columns = _detail_columns(tables)
    documents = _normalize_documents(workspace_dir, meta, profile)
    metrics = _workspace_bi_metrics(profile, meta, columns)
    customer_summary = customer_summary_from_profile(profile, meta)
    manifest = _load_workspace_manifest(workspace_id, workspace_dir) or _build_workspace_manifest(
        workspace_id,
        {**meta, "documents": documents},
        profile,
        customer_summary=customer_summary,
        columns=columns,
        metrics=metrics,
    )
    return {
        "workspace_id": workspace_id,
        "name": meta.get("name") or profile.get("name") or summary.get("name") or workspace_id,
        "description": meta.get("description") or summary.get("description"),
        "format": meta.get("format") or profile.get("format") or summary.get("format") or "mixed",
        "rows": rows,
        "row_count": metrics["row_count"],
        "field_count": metrics["field_count"],
        "indexed_count": metrics["indexed_count"],
        "fill_rate": metrics["fill_rate"],
        "signal_score": metrics["signal_score"],
        "signal_distribution": metrics["signal_distribution"],
        "columns": columns,
        "customer_summary": customer_summary,
        "doc_count": summary.get("doc_count") or _workspace_doc_count(workspace_id, workspace_dir, meta),
        "documents": documents,
        "reference_images": _reference_images(meta),
        "profile_summary": meta.get("profile_summary") or profile.get("profile_summary") or summary.get("profile_summary"),
        "signals": _detail_signals(tables),
        "manifest": manifest,
        "last_analysis": meta.get("last_analysis") or profile.get("last_analysis") or {},
        "created_at": meta.get("created_at") or profile.get("created_at") or summary.get("created_at"),
    }


def workspace_context(workspace_id: str) -> dict[str, Any]:
    workspace_id = str(workspace_id or "").strip()
    if _prefer_blob_workspace_state(workspace_id):
        loaded = _load_workspace_bundle(workspace_id)
        if loaded:
            meta, profile = loaded
            return _workspace_summary_from_state(workspace_id, WORKSPACES / workspace_id, meta, profile)
        registry_item = get_registry_workspace(workspace_id)
        if registry_item:
            return {
                "workspace_id": workspace_id,
                "name": registry_item.get("name") or workspace_id,
                "doc_count": _registry_doc_count(registry_item),
                "format": registry_item.get("format") or "mixed",
                "description": registry_item.get("description"),
                "profile_summary": registry_item.get("profile_summary"),
                "created_at": registry_item.get("created_at"),
                "documents": registry_item.get("documents") or [],
                "reference_images": _reference_images(registry_item),
                "last_analysis": registry_item.get("last_analysis") or {},
            }
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
        "last_analysis": {},
    }
    _CONTEXT_CACHE[workspace_id] = (now + min(_CONTEXT_CACHE_SECONDS, 10), dict(fallback))
    return fallback


def save_workspace_last_analysis(workspace_id: str, final_payload: dict[str, Any]) -> dict[str, Any]:
    workspace_id = str(workspace_id or "").strip()
    if not workspace_id:
        raise ValueError("workspace_id is required")
    analysis = _last_analysis_from_final(final_payload)
    if not analysis:
        return {}
    workspace_dir = WORKSPACES / workspace_id
    prefer_blob = _prefer_blob_workspace_state(workspace_id)
    meta: dict[str, Any] = {}
    profile: dict[str, Any] = {}
    if prefer_blob:
        meta = download_blob_json(f"workspaces/{workspace_id}/workspace.json") or {}
        profile = download_blob_json(f"workspaces/{workspace_id}/profile.json") or {}
    if not meta and (workspace_dir / "workspace.json").exists():
        meta = _read_json(workspace_dir / "workspace.json")
    if not profile and (workspace_dir / "profile.json").exists():
        profile = _read_json(workspace_dir / "profile.json")
    if not meta and not prefer_blob:
        meta = download_blob_json(f"workspaces/{workspace_id}/workspace.json") or {}
    if not meta:
        raise FileNotFoundError(workspace_id)

    meta["last_analysis"] = analysis
    meta["updated_at"] = _utc_now_iso()
    workspace_dir.mkdir(parents=True, exist_ok=True)
    (workspace_dir / "workspace.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        upload_blob_json(f"workspaces/{workspace_id}/workspace.json", meta)
    except Exception as exc:
        if prefer_blob and search_endpoint():
            raise
        analysis["persistence_warning"] = str(exc)[:300]
    _CONTEXT_CACHE.pop(workspace_id, None)
    return analysis


def _last_analysis_from_final(final_payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(final_payload, dict):
        return {}
    artifact = final_payload.get("artifact") if isinstance(final_payload.get("artifact"), dict) else {}
    feasibility = artifact.get("feasibility") if isinstance(artifact.get("feasibility"), dict) else {}
    dimensions = [item for item in (feasibility.get("dimensions") or []) if isinstance(item, dict)]
    if not dimensions and not feasibility.get("verdict") and not feasibility.get("action_plan"):
        return {}
    answer = artifact.get("answer") if isinstance(artifact.get("answer"), dict) else {}
    citations = artifact.get("citations") or answer.get("citations") or []
    if not isinstance(citations, list):
        citations = []
    action_plan = feasibility.get("action_plan") or artifact.get("action_plan") or []
    if not isinstance(action_plan, list):
        action_plan = [action_plan]
    analysis = {
        "updated_at": _utc_now_iso(),
        "conversation_id": artifact.get("conversation_id") or final_payload.get("conversation_id"),
        "text": str(final_payload.get("text") or answer.get("text") or answer.get("markdown") or "")[:2400],
        "verdict": feasibility.get("verdict"),
        "opportunity_id": feasibility.get("opportunity_id"),
        "overall_confidence": feasibility.get("overall_confidence"),
        "recommendation": feasibility.get("recommendation") or artifact.get("recommendation"),
        "dimensions": _json_plain(dimensions[:5]),
        "action_plan": _json_plain([item for item in action_plan[:5] if str(item).strip()]),
        "gap_list": _json_plain([item for item in (feasibility.get("gap_list") or [])[:5] if str(item).strip()]),
        "citations": _json_plain([item for item in citations[:8] if isinstance(item, dict)]),
        "audit": _json_plain(artifact.get("audit") or {}),
        "routing": _json_plain(final_payload.get("routing") or artifact.get("routing") or {}),
        "output_contract": _json_plain(final_payload.get("output_contract") or artifact.get("output_contract") or {}),
    }
    return {key: value for key, value in analysis.items() if value not in (None, "", [], {})}


def _json_plain(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def workspace_reference_images(workspace_id: str) -> list[dict[str, Any]]:
    workspace_id = str(workspace_id or "").strip()
    if not workspace_id:
        return []
    registry_item = get_registry_workspace(workspace_id)
    if registry_item:
        images = _reference_images(registry_item)
        if images:
            return images
    try:
        bundle = _load_workspace_bundle(workspace_id)
    except Exception:
        bundle = None
    if not bundle:
        return []
    meta, _profile = bundle
    return _reference_images(meta)


def get_reference_image_content(workspace_id: str, filename: str) -> tuple[bytes, str] | None:
    workspace_id = str(workspace_id or "").strip()
    safe_name = Path(filename or "").name
    if not workspace_id or not safe_name:
        return None
    workspace_dir = WORKSPACES / workspace_id
    local_path = workspace_dir / "reference_images" / safe_name
    if local_path.exists() and local_path.is_file():
        return local_path.read_bytes(), _reference_image_content_type(safe_name, None)
    direct_blob_name = f"workspaces/{workspace_id}/reference_images/{safe_name}"
    downloaded = download_blob_content(direct_blob_name)
    if downloaded:
        return downloaded
    workspace_json = download_blob_json(f"workspaces/{workspace_id}/workspace.json") or get_registry_workspace(workspace_id) or {}
    for item in _reference_images(workspace_json):
        if Path(str(item.get("filename") or "")).name != safe_name:
            continue
        blob_name = str(item.get("blob_name") or direct_blob_name)
        downloaded = download_blob_content(blob_name)
        if downloaded:
            return downloaded
    bundle = _load_workspace_bundle(workspace_id)
    meta = bundle[0] if bundle else {}
    for item in _reference_images(meta):
        if Path(str(item.get("filename") or "")).name != safe_name:
            continue
        blob_name = str(item.get("blob_name") or direct_blob_name)
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
    prefer_blob = _prefer_blob_workspace_state(workspace_id)
    if prefer_blob:
        meta = download_blob_json(f"workspaces/{workspace_id}/workspace.json") or {}
        profile = download_blob_json(f"workspaces/{workspace_id}/profile.json") or {}
    meta_path = workspace_dir / "workspace.json"
    if not meta and meta_path.exists():
        meta = _read_json(meta_path)
        profile_path = workspace_dir / str(meta.get("profile_file") or "profile.json")
        if profile_path.exists():
            profile = _read_json(profile_path)
    if not meta and not prefer_blob:
        meta = download_blob_json(f"workspaces/{workspace_id}/workspace.json") or {}
    if not profile and not prefer_blob:
        profile = download_blob_json(f"workspaces/{workspace_id}/profile.json") or {}
    if not profile and meta:
        profile_path = workspace_dir / str(meta.get("profile_file") or "profile.json")
        if profile_path.exists():
            profile = _read_json(profile_path)
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
    incoming_sources = {str(profile.get("source_file") or "") for profile in profiles if profile.get("source_file")}
    table_keys: set[tuple[str, str]] = set()
    if existing_profile:
        existing_format = str(existing_profile.get("format") or "").strip()
        if existing_format and existing_format != "mixed":
            formats.add(existing_format)
        for table in existing_profile.get("tables") or []:
            if isinstance(table, dict):
                source = str(table.get("source_file") or existing_profile.get("source_file") or "")
                if source and source in incoming_sources:
                    continue
                key = (source, str(table.get("name") or ""))
                if key in table_keys:
                    continue
                item = dict(table)
                if source:
                    item["source_file"] = source
                tables.append(item)
                table_keys.add(key)
    for profile in profiles:
        for table in profile.get("tables") or []:
            item = dict(table)
            item["source_file"] = profile.get("source_file")
            key = (str(item.get("source_file") or ""), str(item.get("name") or ""))
            if key in table_keys:
                continue
            tables.append(item)
            table_keys.add(key)
    sorted_formats = sorted(formats or {"unknown"})
    document_profiles: list[dict[str, Any]] = []
    profile_keys: set[tuple[str, str]] = set()
    for item in (existing_profile or {}).get("document_profiles") or []:
        if not isinstance(item, dict):
            continue
        key = (str(item.get("source_file") or ""), str(item.get("profile_id") or item.get("profile_file") or ""))
        if key[0] in incoming_sources or key in profile_keys:
            continue
        document_profiles.append(dict(item))
        profile_keys.add(key)
    for profile in profiles:
        item = {
            "profile_id": profile.get("profile_id"),
            "profile_file": profile.get("profile_file"),
            "source_file": profile.get("source_file"),
            "format": profile.get("format"),
            "profile_summary": profile.get("profile_summary"),
        }
        key = (str(item.get("source_file") or ""), str(item.get("profile_id") or item.get("profile_file") or ""))
        if key in profile_keys:
            continue
        document_profiles.append(item)
        profile_keys.add(key)
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
    summaries: list[str] = []
    seen_summaries: set[str] = set()
    summary_candidates = []
    if existing_profile and existing_profile.get("profile_summary"):
        summary_candidates.append(str(existing_profile.get("profile_summary") or ""))
    summary_candidates.extend(str(profile.get("profile_summary") or "") for profile in profiles if profile.get("profile_summary"))
    for summary in summary_candidates:
        clean_summary = summary.strip()
        if not clean_summary or clean_summary in seen_summaries or _is_processing_summary(clean_summary):
            continue
        summaries.append(clean_summary)
        seen_summaries.add(clean_summary)
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
    return bool(
        os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
        or os.environ.get("STORAGE_ACCOUNT_NAME")
        or os.environ.get("DF_STORAGE_ACCOUNT")
    )


def _prefer_blob_workspace_state(workspace_id: str) -> bool:
    return str(workspace_id or "").startswith("upload-") and _blob_configured_for_workspace()


def _workspace_summary_from_state(
    workspace_id: str,
    workspace_dir: Path,
    meta: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, Any]:
    documents = meta.get("documents") or profile.get("documents") or []
    return {
        "workspace_id": str(meta.get("workspace_id") or profile.get("workspace_id") or workspace_id),
        "name": meta.get("name") or profile.get("name") or workspace_id,
        "doc_count": len(documents) if documents else _workspace_doc_count(workspace_id, workspace_dir, meta),
        "format": meta.get("format") or profile.get("format") or "mixed",
        "description": meta.get("description"),
        "profile_summary": meta.get("profile_summary") or profile.get("profile_summary"),
        "created_at": meta.get("created_at") or profile.get("created_at"),
        "documents": documents,
        "reference_images": _reference_images(meta),
        "last_analysis": meta.get("last_analysis") or profile.get("last_analysis") or {},
    }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_utc_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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


def _json_clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _ingest_document_with_retries(
    workspace_id: str,
    workspace_dir: Path,
    meta: dict[str, Any],
    existing_profile: dict[str, Any],
    document: dict[str, Any],
) -> None:
    last_error: Exception | None = None
    for attempt in range(1, INGEST_FILE_MAX_RETRIES + 1):
        document["attempt_count"] = attempt
        document["updated_at"] = _utc_now_iso()
        meta_work = _json_clone(meta)
        profile_work = _json_clone(existing_profile)
        document_work = _json_clone(document)
        try:
            if INGEST_FILE_TIMEOUT_SECONDS <= 0:
                _ingest_one_document(workspace_id, workspace_dir, meta_work, profile_work, document_work)
            else:
                executor = ThreadPoolExecutor(max_workers=1)
                future = executor.submit(
                    _ingest_one_document,
                    workspace_id,
                    workspace_dir,
                    meta_work,
                    profile_work,
                    document_work,
                )
                try:
                    future.result(timeout=INGEST_FILE_TIMEOUT_SECONDS)
                except FuturesTimeoutError as exc:
                    future.cancel()
                    executor.shutdown(wait=False, cancel_futures=True)
                    raise TimeoutError(f"file ingest timed out after {INGEST_FILE_TIMEOUT_SECONDS:g}s") from exc
                except Exception:
                    executor.shutdown(wait=True, cancel_futures=True)
                    raise
                else:
                    executor.shutdown(wait=True, cancel_futures=True)
            document.clear()
            document.update(document_work)
            document["attempt_count"] = attempt
            if meta_work.get("indexed_count") is not None:
                meta["indexed_count"] = int(meta_work.get("indexed_count") or 0)
            return
        except Exception as exc:
            last_error = exc
            document["last_error"] = f"{type(exc).__name__}: {exc}"[:500]
            document["updated_at"] = _utc_now_iso()
            if attempt < INGEST_FILE_MAX_RETRIES:
                continue
    assert last_error is not None
    raise last_error


def _ingest_one_document(
    workspace_id: str,
    workspace_dir: Path,
    meta: dict[str, Any],
    existing_profile: dict[str, Any],
    document: dict[str, Any],
) -> None:
    source_file = str(document.get("source_file") or "")
    if not source_file:
        raise ValueError("document.source_file is required")
    raw_path = workspace_dir / source_file
    if not raw_path.exists():
        downloaded = download_blob_content(f"workspaces/{workspace_id}/{source_file}")
        if not downloaded:
            raise FileNotFoundError(source_file)
        content, _content_type = downloaded
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(content)

    profile_file = str(document.get("profile_file") or "")
    if not profile_file:
        profile_file = f"profiles/{_profile_id_for_document(document, _next_profile_index(meta, existing_profile))}.json"
        document["profile_file"] = profile_file
    profile_id = Path(profile_file).stem
    profile = build_data_profile(
        raw_path,
        workspace_id=workspace_id,
        name=str(meta.get("name") or existing_profile.get("name") or workspace_id),
        source_file=source_file,
        content_type=document.get("content_type"),
    )
    profile["profile_id"] = profile_id
    profile["profile_file"] = profile_file
    profile_path = workspace_dir / profile_file
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    write_profile(profile_path, profile)
    try:
        upload_blob_json(f"workspaces/{workspace_id}/{profile_file}", profile)
    except Exception as exc:
        if _blob_configured_for_workspace() and search_endpoint():
            raise
        document["profile_persistence_warning"] = str(exc)[:300]

    records = upload_to_records(raw_path, source_file, workspace_id, content_type=document.get("content_type"))
    indexed_count = _index_documents_batched([profile_to_search_document(profile)] + records)
    previous_doc_indexed = int(document.get("indexed_count") or 0)
    current_workspace_indexed = int(meta.get("indexed_count") or 0)
    meta["indexed_count"] = max(0, current_workspace_indexed - previous_doc_indexed) + indexed_count
    now = datetime.now(timezone.utc).isoformat()
    document.update(
        {
            "format": profile.get("format"),
            "record_count": len(records),
            "indexed_count": indexed_count,
            "status": STATUS_READY if records else STATUS_PARTIAL,
            "error": None,
            "updated_at": now,
            "created_at": document.get("created_at") or profile.get("created_at") or now,
            "profile_file": profile_file,
        }
    )


def _rebuild_workspace_profile(
    workspace_id: str,
    workspace_dir: Path,
    meta: dict[str, Any],
    existing_profile: dict[str, Any],
) -> dict[str, Any]:
    profiles: list[dict[str, Any]] = []
    for document in meta.get("documents") or []:
        if not isinstance(document, dict):
            continue
        if document.get("status") not in {STATUS_READY, STATUS_PARTIAL}:
            continue
        profile_file = str(document.get("profile_file") or "")
        if not profile_file:
            continue
        profile = _load_document_profile(workspace_id, workspace_dir, profile_file)
        if profile:
            profiles.append(profile)
    if profiles:
        return _aggregate_profiles(
            workspace_id,
            str(meta.get("name") or existing_profile.get("name") or workspace_id),
            profiles,
            [item for item in meta.get("documents") or [] if isinstance(item, dict)],
            existing_profile=existing_profile,
        )
    if existing_profile:
        profile = dict(existing_profile)
        profile["documents"] = [item for item in meta.get("documents") or [] if isinstance(item, dict)]
        profile["profile_summary"] = profile.get("profile_summary") or _processing_summary(str(meta.get("name") or workspace_id), profile["documents"])
        return profile
    return _pending_workspace_profile(
        workspace_id,
        str(meta.get("name") or workspace_id),
        [item for item in meta.get("documents") or [] if isinstance(item, dict)],
        str(meta.get("created_at") or datetime.now(timezone.utc).isoformat()),
    )


def _load_document_profile(workspace_id: str, workspace_dir: Path, profile_file: str) -> dict[str, Any]:
    if _prefer_blob_workspace_state(workspace_id):
        blob_profile = download_blob_json(f"workspaces/{workspace_id}/{profile_file}")
        if blob_profile:
            return blob_profile
    local_path = workspace_dir / profile_file
    if local_path.exists():
        try:
            return _read_json(local_path)
        except Exception:
            pass
    if not _prefer_blob_workspace_state(workspace_id):
        return download_blob_json(f"workspaces/{workspace_id}/{profile_file}") or {}
    return {}


def _persist_workspace_state(
    workspace_id: str,
    workspace_dir: Path,
    workspace_meta: dict[str, Any],
    profile: dict[str, Any],
    *,
    include_raw_payloads: bool = False,
) -> None:
    workspace_meta["updated_at"] = datetime.now(timezone.utc).isoformat()
    documents = [item for item in workspace_meta.get("documents") or [] if isinstance(item, dict)]
    document_format = _format_from_documents(documents, _reference_images(workspace_meta))
    workspace_meta["format"] = document_format if document_format != "pending" else profile.get("format") or workspace_meta.get("format") or "mixed"
    workspace_meta["profile_summary"] = profile.get("profile_summary") or workspace_meta.get("profile_summary") or _processing_summary(
        str(workspace_meta.get("name") or workspace_id),
        documents,
    )
    workspace_meta["raw_docs"] = [item["source_file"] for item in workspace_meta.get("documents") or [] if isinstance(item, dict) and item.get("source_file")]
    workspace_meta["profile_files"] = [item["profile_file"] for item in workspace_meta.get("documents") or [] if isinstance(item, dict) and item.get("profile_file")]
    workspace_meta["documents"] = _normalize_documents(workspace_dir, workspace_meta, profile)
    manifest = _build_workspace_manifest(workspace_id, workspace_meta, profile)
    workspace_meta["manifest_file"] = "manifest.json"
    _write_workspace_files(workspace_dir, workspace_meta, profile, manifest)
    persistence = _persist_workspace_bundle(
        workspace_id=workspace_id,
        raw_payloads=_raw_payloads_from_documents(workspace_dir, workspace_meta) if include_raw_payloads else [],
        reference_payloads=[],
        workspace_meta=workspace_meta,
        profile=profile,
    )
    workspace_meta["persistence"] = persistence
    _persist_workspace_manifest(workspace_id, manifest)
    (workspace_dir / "workspace.json").write_text(json.dumps(workspace_meta, indent=2, ensure_ascii=False), encoding="utf-8")
    _CONTEXT_CACHE.pop(workspace_id, None)


def _raw_payloads_from_documents(workspace_dir: Path, workspace_meta: dict[str, Any]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    seen: set[str] = set()
    for document in workspace_meta.get("documents") or []:
        if not isinstance(document, dict):
            continue
        source_file = str(document.get("source_file") or "")
        if not source_file.startswith("raw_docs/") or source_file in seen:
            continue
        raw_path = workspace_dir / source_file
        if not raw_path.exists() or not raw_path.is_file():
            continue
        payloads.append({"raw_filename": raw_path.name, "raw_content": raw_path.read_bytes()})
        seen.add(source_file)
    return payloads


def _write_workspace_files(workspace_dir: Path, workspace_meta: dict[str, Any], profile: dict[str, Any], manifest: dict[str, Any]) -> None:
    workspace_dir.mkdir(parents=True, exist_ok=True)
    write_profile(workspace_dir / "profile.json", profile)
    (workspace_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    (workspace_dir / "workspace.json").write_text(json.dumps(workspace_meta, indent=2, ensure_ascii=False), encoding="utf-8")


def _index_documents_batched(docs: list[dict[str, Any]]) -> int:
    total = 0
    for start in range(0, len(docs), INGEST_INDEX_BATCH_SIZE):
        batch = docs[start : start + INGEST_INDEX_BATCH_SIZE]
        total += _index_documents(batch)
    return total


def _ingest_status_from_documents(meta: dict[str, Any]) -> dict[str, Any]:
    files = [
        {
            "name": item.get("name"),
            "source_file": item.get("source_file"),
            "status": item.get("status") or STATUS_PARTIAL,
            "error": item.get("error"),
            "record_count": item.get("record_count"),
            "indexed_count": item.get("indexed_count"),
            "ingest_job_id": item.get("ingest_job_id"),
        }
        for item in meta.get("documents") or []
        if isinstance(item, dict)
    ]
    total = len(files)
    done = sum(1 for item in files if item["status"] != STATUS_PROCESSING)
    statuses = {str(item["status"]) for item in files}
    if total == 0:
        state = "ready"
    elif STATUS_PROCESSING in statuses:
        state = "processing"
    elif statuses == {STATUS_FAILED}:
        state = "failed"
    elif STATUS_FAILED in statuses or STATUS_PARTIAL in statuses:
        state = "partial"
    else:
        state = "ready"
    return {
        "state": state,
        "pct": int(round((done / total) * 100)) if total else 100,
        "files": files,
    }


def _upsert_ingest_job(existing: Any, job: dict[str, Any]) -> list[dict[str, Any]]:
    jobs = [item for item in (existing or []) if isinstance(item, dict)]
    jobs = [item for item in jobs if item.get("job_id") != job.get("job_id")]
    jobs.append(job)
    return jobs[-20:]


def _mark_ingest_job(existing: Any, job_id: str, *, state: str, pct: int | None = None) -> list[dict[str, Any]]:
    jobs = [item for item in (existing or []) if isinstance(item, dict)]
    now = datetime.now(timezone.utc).isoformat()
    found = False
    for item in jobs:
        if item.get("job_id") == job_id:
            item["state"] = state
            item["updated_at"] = now
            if pct is not None:
                item["pct"] = pct
            found = True
            break
    if not found:
        jobs.append({"job_id": job_id, "state": state, "created_at": now, "updated_at": now, "pct": pct})
    return jobs[-20:]


def _find_existing_document(documents: list[dict[str, Any]], content_hash: str) -> dict[str, Any] | None:
    for document in documents:
        if str(document.get("content_sha256") or "") == content_hash:
            return document
    return None


def _replace_document(meta: dict[str, Any], document: dict[str, Any]) -> None:
    source_file = str(document.get("source_file") or "")
    documents = [item for item in meta.get("documents") or [] if isinstance(item, dict)]
    for index, item in enumerate(documents):
        if source_file and str(item.get("source_file") or "") == source_file:
            documents[index] = dict(document)
            meta["documents"] = documents
            return
    documents.append(dict(document))
    meta["documents"] = documents


def _cheap_upload_format(filename: str, content_type: Any) -> str:
    try:
        return detect_format(Path(filename), str(content_type or "") or None)
    except Exception:
        suffix = Path(filename).suffix.lower().lstrip(".")
        return suffix or "unknown"


def _profile_id_for_document(document: dict[str, Any], index: int) -> str:
    profile_file = str(document.get("profile_file") or "")
    if profile_file:
        return Path(profile_file).stem
    return f"profile-{index:03d}"


def _processing_summary(name: str, documents: list[dict[str, Any]]) -> str:
    processing = sum(1 for item in documents if item.get("status") == STATUS_PROCESSING)
    ready = sum(1 for item in documents if item.get("status") == STATUS_READY)
    failed = sum(1 for item in documents if item.get("status") == STATUS_FAILED)
    return f"{name} 已接入，后台正在生成数据画像。当前已就绪 {ready} 个，解析中 {processing} 个，失败 {failed} 个。"


def _is_processing_summary(value: str) -> bool:
    return "后台正在生成数据画像" in str(value or "")


def _pending_workspace_profile(workspace_id: str, name: str, documents: list[dict[str, Any]], created_at: str) -> dict[str, Any]:
    return {
        "workspace_id": workspace_id,
        "name": name,
        "format": _format_from_documents(documents, []),
        "source_file": "profile.json",
        "created_at": created_at,
        "tables": [],
        "documents": documents,
        "document_profiles": [],
        "profile_summary": _processing_summary(name, documents),
    }


def _format_from_documents(documents: list[dict[str, Any]], reference_images: list[dict[str, Any]]) -> str:
    formats = {str(item.get("format") or "").strip() for item in documents if isinstance(item, dict) and item.get("format")}
    if reference_images and not formats:
        return "reference_images"
    formats.discard("")
    if not formats:
        return "pending"
    return next(iter(formats)) if len(formats) == 1 else "mixed"


def _workspace_bi_metrics(profile: dict[str, Any], meta: dict[str, Any], columns: list[dict[str, Any]]) -> dict[str, Any]:
    tables = profile.get("tables") or []
    row_count = sum(int(table.get("row_count") or 0) for table in tables)
    field_count = sum(int(table.get("column_count") or len(table.get("columns") or [])) for table in tables)
    missing_rates = [float(column.get("missing_rate") or 0) for column in columns]
    fill_rate = 1 - (sum(missing_rates) / len(missing_rates)) if missing_rates else 0
    scores = [float(column.get("signal_score") or 0) for column in columns]
    signal_score = sum(scores) / len(scores) if scores else 0
    counts = {
        "strong": sum(1 for column in columns if column.get("signal") == "strong"),
        "mid": sum(1 for column in columns if column.get("signal") == "mid"),
        "noise": sum(1 for column in columns if column.get("signal") == "noise"),
    }
    total = max(1, sum(counts.values()))
    return {
        "row_count": row_count,
        "field_count": field_count,
        "indexed_count": int(meta.get("indexed_count") or 0),
        "fill_rate": round(max(0.0, min(1.0, fill_rate)), 4),
        "signal_score": round(max(0.0, min(1.0, signal_score)), 4),
        "signal_distribution": {key: round(value / total, 4) for key, value in counts.items()},
    }


def _build_workspace_manifest(
    workspace_id: str,
    meta: dict[str, Any],
    profile: dict[str, Any],
    *,
    customer_summary: str | None = None,
    columns: list[dict[str, Any]] | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    columns = columns if columns is not None else _detail_columns(profile.get("tables") or [])
    metrics = metrics if metrics is not None else _workspace_bi_metrics(profile, meta, columns)
    documents = _normalize_documents(WORKSPACES / workspace_id, meta, profile)
    summary = customer_summary or customer_summary_from_profile(profile, meta)
    return {
        "version": "dataforge.canonical_manifest.v1",
        "workspace_id": workspace_id,
        "name": meta.get("name") or profile.get("name") or workspace_id,
        "created_at": meta.get("created_at") or profile.get("created_at"),
        "updated_at": meta.get("updated_at") or datetime.now(timezone.utc).isoformat(),
        "format": meta.get("format") or profile.get("format") or "mixed",
        "customer_summary": summary,
        "profile_summary": meta.get("profile_summary") or profile.get("profile_summary"),
        "metrics": metrics,
        "documents": documents,
        "columns": columns[:120],
        "signals": _detail_signals(profile.get("tables") or []),
        "reference_images": _reference_images(meta),
    }


def _load_workspace_manifest(workspace_id: str, workspace_dir: Path) -> dict[str, Any] | None:
    if _prefer_blob_workspace_state(workspace_id):
        manifest = download_blob_json(f"workspaces/{workspace_id}/manifest.json")
        if manifest:
            return manifest
    local_path = workspace_dir / "manifest.json"
    if local_path.exists():
        try:
            return _read_json(local_path)
        except Exception:
            return None
    if not _prefer_blob_workspace_state(workspace_id):
        return download_blob_json(f"workspaces/{workspace_id}/manifest.json")
    return None


def _persist_workspace_manifest(workspace_id: str, manifest: dict[str, Any]) -> None:
    try:
        upload_blob_json(f"workspaces/{workspace_id}/manifest.json", manifest)
    except Exception as exc:
        if _blob_configured_for_workspace() and search_endpoint():
            raise
        manifest["persistence_warning"] = str(exc)[:300]


def _detail_columns(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    all_columns: list[dict[str, Any]] = []
    for table in tables:
        all_columns.extend([column for column in table.get("columns") or [] if isinstance(column, dict)])
    label_map = {
        str(column.get("name") or ""): friendly_label(
            column.get("name"),
            role=column.get("type"),
            value=(column.get("top_values") or [""])[0] if isinstance(column.get("top_values"), list) else "",
            index=index,
        )
        for index, column in enumerate(all_columns, start=1)
        if str(column.get("name") or "").strip()
    }
    table_signals = {
        str(table.get("name") or ""): (table.get("signals") or []) + (table.get("cross_signals") or [])
        for table in tables
    }
    table_noise = {
        str(table.get("name") or ""): table.get("noise") or []
        for table in tables
    }
    for table in tables:
        table_name = str(table.get("name") or "")
        signals = table_signals.get(table_name, [])
        noise_items = table_noise.get(table_name, [])
        row_count = int(table.get("row_count") or 0)
        for column in table.get("columns") or []:
            name = str(column.get("name") or "")
            key = f"{table_name}:{name}"
            if key in seen:
                continue
            seen.add(key)
            signal_reason = next((item for item in signals if name and name in str(item)), "")
            noise_reason = next((item for item in noise_items if name and name in str(item)), "")
            signal_score, signal_level = _column_signal_score(column, row_count, bool(signal_reason), bool(noise_reason))
            friendly = label_map.get(name) or friendly_label(name, role=column.get("type"))
            items.append(
                {
                    "table": table_name,
                    "name": name,
                    "friendly_label": friendly,
                    "role": column.get("type") or "unknown",
                    "signal": signal_level,
                    "signal_score": signal_score,
                    "signal_reason": sanitize_customer_text(signal_reason or noise_reason or _default_signal_reason(column, signal_level), label_map),
                    "missing_rate": column.get("missing_rate", 0),
                    "unique_count": column.get("unique_count", 0),
                    "non_empty": column.get("non_empty", 0),
                    "top_values": column.get("top_values", [])[:5],
                }
            )
    return items[:120]


def _column_signal_score(column: dict[str, Any], row_count: int, has_signal: bool, has_noise: bool) -> tuple[float, str]:
    missing_rate = float(column.get("missing_rate") or 0)
    unique_count = int(column.get("unique_count") or 0)
    non_empty = int(column.get("non_empty") or 0)
    type_name = str(column.get("type") or "unknown")
    completeness = max(0.0, min(1.0, 1.0 - missing_rate))
    variety = 0.0
    if row_count > 0:
        variety = max(0.0, min(1.0, unique_count / max(1, min(row_count, 20))))
    type_bonus = 0.12 if type_name in {"number", "date", "boolean"} else 0.08 if type_name == "text" else 0.0
    score = completeness * 0.56 + variety * 0.24 + type_bonus
    if has_signal:
        score += 0.18
    if has_noise:
        score = min(score, 0.34)
    if non_empty == 0:
        score = 0
    score = round(max(0.0, min(1.0, score)), 4)
    if score >= 0.68:
        return score, "strong"
    if score >= 0.38:
        return score, "mid"
    return score, "noise"


def _default_signal_reason(column: dict[str, Any], signal_level: str) -> str:
    name = str(column.get("name") or "字段")
    missing = float(column.get("missing_rate") or 0)
    unique = int(column.get("unique_count") or 0)
    if signal_level == "strong":
        return f"{name} 完整度较高且有 {unique} 个可区分取值，可作为重点观察信号"
    if signal_level == "mid":
        return f"{name} 有一定可用度，但缺失率约 {missing:.0%}，适合作为辅助信号"
    return f"{name} 当前缺失率或区分度不足，不宜单独作为核心判断依据"


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


def _normalize_documents(workspace_dir: Path, meta: dict[str, Any], profile: dict[str, Any]) -> list[dict[str, Any]]:
    profile_docs = {
        str(item.get("source_file") or ""): item
        for item in profile.get("document_profiles") or []
        if isinstance(item, dict) and item.get("source_file")
    }
    documents = _detail_documents(workspace_dir, meta)
    created_at = meta.get("created_at") or profile.get("created_at")
    if not documents and isinstance(profile.get("documents"), list):
        documents = [item for item in profile.get("documents") or [] if isinstance(item, dict)]

    normalized: list[dict[str, Any]] = []
    for raw in documents:
        source_file = str(raw.get("source_file") or raw.get("path") or "")
        profile_doc = profile_docs.get(source_file) or {}
        name = str(raw.get("name") or Path(source_file).name or "document")
        fmt = str(raw.get("format") or profile_doc.get("format") or Path(source_file).suffix.lstrip(".").lower() or "unknown")
        item = {
            "source_file": source_file or None,
            "name": name,
            "format": fmt,
            "bytes": raw.get("bytes"),
            "record_count": raw.get("record_count"),
            "indexed_count": raw.get("indexed_count"),
            "status": raw.get("status"),
            "error": raw.get("error"),
            "ingest_job_id": raw.get("ingest_job_id"),
            "content_sha256": raw.get("content_sha256"),
            "content_type": raw.get("content_type"),
            "created_at": raw.get("created_at") or profile_doc.get("created_at") or created_at,
            "updated_at": raw.get("updated_at"),
            "profile_file": raw.get("profile_file") or profile_doc.get("profile_file"),
            "external": bool(raw.get("external")),
        }
        if item["bytes"] is None and source_file:
            local_path = workspace_dir / source_file
            if local_path.exists() and local_path.is_file():
                item["bytes"] = local_path.stat().st_size
        if item["record_count"] is None:
            item["record_count"] = _document_record_count(source_file, profile_doc, profile)
        if not item["status"]:
            item["status"] = _document_status(item, profile_doc)
        normalized.append(item)
    return normalized


def _document_record_count(source_file: str, profile_doc: dict[str, Any], profile: dict[str, Any]) -> int | None:
    if profile_doc.get("record_count") is not None:
        try:
            return int(profile_doc.get("record_count") or 0)
        except (TypeError, ValueError):
            return None
    total = 0
    matched = False
    for table in profile.get("tables") or []:
        if not isinstance(table, dict):
            continue
        table_source = str(table.get("source_file") or profile.get("source_file") or "")
        if source_file and table_source and table_source != source_file:
            continue
        total += int(table.get("row_count") or 0)
        matched = True
    if matched:
        return total
    return None


def _document_status(item: dict[str, Any], profile_doc: dict[str, Any]) -> str:
    fmt = str(item.get("format") or profile_doc.get("format") or "").lower()
    record_count = item.get("record_count")
    if fmt in {"reference", "reference_images", "image", "png", "jpg", "jpeg", "webp"}:
        return "仅参考"
    if isinstance(record_count, int) and record_count > 0:
        return "已就绪"
    if fmt in {"markdown", "pdf"}:
        return "已就绪" if record_count else "部分字段"
    if fmt == "profile":
        return "画像文件"
    return "部分字段"


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
    account = os.environ.get("STORAGE_ACCOUNT_NAME") or os.environ.get("DF_STORAGE_ACCOUNT")
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


def reserve_workspace_id(value: str | None = None) -> str:
    """Return a collision-resistant ID without creating files or registry state."""
    base = _safe_slug(str(value or "upload")) or "upload"
    if not base.startswith("upload-"):
        base = f"upload-{base}"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{base[:120]}-{stamp}-{uuid.uuid4().hex[:12]}"


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
