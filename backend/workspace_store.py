from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ingest.profiler import build_data_profile, compact_profile_for_workspace, profile_to_search_document, write_profile

try:
    from .search_admin import count_workspace_docs, index_documents, search_endpoint
except ImportError:
    from search_admin import count_workspace_docs, index_documents, search_endpoint


ROOT = Path(__file__).resolve().parents[1]
WORKSPACES = ROOT / "workspaces"


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
    (workspace_dir / "workspace.json").write_text(json.dumps(workspace_meta, indent=2, ensure_ascii=False), encoding="utf-8")

    indexed_count = _index_profile(profile)
    return {
        "workspace_id": workspace_id,
        "name": display_name,
        "format": profile["format"],
        "indexed_count": indexed_count,
        "profile_summary": profile["profile_summary"],
    }


def list_workspaces() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    WORKSPACES.mkdir(parents=True, exist_ok=True)
    for workspace_dir in WORKSPACES.iterdir():
        if not workspace_dir.is_dir():
            continue
        meta_path = workspace_dir / "workspace.json"
        if not meta_path.exists():
            continue
        meta = _read_json(meta_path)
        workspace_id = str(meta.get("workspace_id") or workspace_dir.name)
        profile = _read_profile(workspace_dir, meta)
        doc_count = _workspace_doc_count(workspace_id, workspace_dir, meta)
        items.append(
            {
                "workspace_id": workspace_id,
                "name": meta.get("name") or workspace_id,
                "doc_count": doc_count,
                "format": meta.get("format") or profile.get("format") or "mixed",
                "profile_summary": meta.get("profile_summary") or profile.get("profile_summary"),
                "created_at": meta.get("created_at") or profile.get("created_at"),
            }
        )
    return sorted(items, key=lambda item: str(item.get("created_at") or ""), reverse=True)


def workspace_context(workspace_id: str) -> dict[str, Any]:
    for item in list_workspaces():
        if item["workspace_id"] == workspace_id:
            return item
    return {
        "workspace_id": workspace_id,
        "name": workspace_id,
        "doc_count": 0,
        "format": "unknown",
        "profile_summary": None,
    }


def _index_profile(profile: dict[str, Any]) -> int:
    doc = profile_to_search_document(profile)
    try:
        return index_documents([doc])
    except Exception:
        if search_endpoint():
            raise
        return 1


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


def _unique_workspace_id(value: str) -> str:
    base = _safe_slug(value) or "upload"
    if not base.startswith("upload-"):
        base = f"upload-{base}"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    candidate = f"{base}-{stamp}"
    if not (WORKSPACES / candidate).exists():
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
