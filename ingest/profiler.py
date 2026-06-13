from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ingest.adapters.csv_to_profile import csv_to_profile
from ingest.adapters.excel_to_profile import excel_to_profile
from ingest.adapters.json_to_profile import json_to_profile
from ingest.adapters.markdown_to_profile import markdown_to_profile
from ingest.adapters.pdf_to_profile import pdf_to_profile
from ingest.adapters.tabular_profile import profile_search_content, summarize_profile


SUPPORTED_FORMATS = {
    ".csv": "csv",
    ".json": "json",
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "markdown",
    ".text": "markdown",
    ".pdf": "pdf",
    ".xlsx": "excel",
    ".xlsm": "excel",
}


def detect_format(path: Path, content_type: str | None = None) -> str:
    suffix = path.suffix.lower()
    if suffix in SUPPORTED_FORMATS:
        return SUPPORTED_FORMATS[suffix]
    content = (content_type or "").lower()
    if "json" in content:
        return "json"
    if "csv" in content or "comma-separated" in content:
        return "csv"
    if "spreadsheet" in content or "excel" in content:
        return "excel"
    if "markdown" in content or "text/plain" in content:
        return "markdown"
    if "pdf" in content:
        return "pdf"
    raise ValueError(f"Unsupported upload format for {path.name}")


def build_data_profile(
    path: Path,
    *,
    workspace_id: str,
    name: str,
    source_file: str,
    content_type: str | None = None,
) -> dict[str, Any]:
    fmt = detect_format(path, content_type)
    if fmt == "csv":
        tables = csv_to_profile(path)
    elif fmt == "json":
        tables = json_to_profile(path)
    elif fmt == "excel":
        tables = excel_to_profile(path)
    elif fmt == "markdown":
        tables = markdown_to_profile(path)
    elif fmt == "pdf":
        tables = pdf_to_profile(path)
    else:
        raise ValueError(f"Unsupported upload format: {fmt}")
    profile: dict[str, Any] = {
        "workspace_id": workspace_id,
        "name": name,
        "format": fmt,
        "source_file": source_file,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tables": tables,
    }
    profile["profile_summary"] = summarize_profile(profile)
    return profile


def profile_to_search_document(profile: dict[str, Any]) -> dict[str, Any]:
    workspace_id = str(profile["workspace_id"])
    profile_id = str(profile.get("profile_id") or "profile-000")
    return {
        "@search.action": "mergeOrUpload",
        "id": f"{workspace_id}-{profile_id}",
        "workspace_id": workspace_id,
        "title": f"{profile.get('name', workspace_id)} 数据画像",
        "content": profile_search_content(profile),
        "source_file": profile.get("source_file") or "profile.json",
        "chunk_id": profile_id,
        "document_type": "profile",
        "language": "zh-Hans",
        "sheet": None,
        "row": None,
    }


def compact_profile_for_workspace(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "workspace_id": profile.get("workspace_id"),
        "name": profile.get("name"),
        "format": profile.get("format"),
        "profile_summary": profile.get("profile_summary"),
        "created_at": profile.get("created_at"),
        "source_file": profile.get("source_file"),
    }


def write_profile(path: Path, profile: dict[str, Any]) -> None:
    path.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
