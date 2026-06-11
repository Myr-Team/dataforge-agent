from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ingest.adapters.excel_to_records import excel_to_records


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKSPACE = ROOT / "workspaces" / "demo-corpus"
DEFAULT_INDEX = "dataforge-workspaces"
API_VERSION = "2024-07-01"


def _terraform_output(name: str) -> str:
    tf_dir = ROOT / "infra" / "envs" / "dev"
    result = subprocess.run(
        ["terraform", "output", "-raw", name],
        cwd=tf_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _setting(name: str, fallback_output: str | None = None) -> str:
    value = os.environ.get(name)
    if value:
        return value
    if fallback_output:
        value = _terraform_output(fallback_output)
        if value:
            return value
    raise RuntimeError(f"Missing setting {name}")


def _request(method: str, url: str, key: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("api-key", key)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} failed: HTTP {exc.code}: {detail}") from exc


def _index_schema(index_name: str) -> dict[str, Any]:
    schema = {
        "name": index_name,
        "fields": [
            {"name": "id", "type": "Edm.String", "key": True, "filterable": True},
            {"name": "workspace_id", "type": "Edm.String", "filterable": True, "facetable": True},
            {"name": "title", "type": "Edm.String", "searchable": True},
            {"name": "content", "type": "Edm.String", "searchable": True},
            {"name": "source_file", "type": "Edm.String", "filterable": True, "facetable": True},
            {"name": "chunk_id", "type": "Edm.String", "filterable": True},
            {"name": "document_type", "type": "Edm.String", "filterable": True, "facetable": True},
            {"name": "language", "type": "Edm.String", "filterable": True},
            {"name": "sheet", "type": "Edm.String", "filterable": True, "facetable": True},
            {"name": "row", "type": "Edm.String", "filterable": True},
        ],
    }
    for field in schema["fields"]:
        if field["name"] in {"title", "content"}:
            field["analyzer"] = "zh-Hans.microsoft"
    return schema


def _read_workspace(workspace_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    meta = json.loads((workspace_dir / "workspace.json").read_text(encoding="utf-8"))
    docs: list[dict[str, Any]] = []
    for rel in meta["raw_docs"]:
        path = workspace_dir / rel
        if path.suffix.lower() == ".xlsx":
            docs.append({"path": rel, "title": path.stem, "kind": "excel"})
        else:
            text = path.read_text(encoding="utf-8")
            title = next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), path.stem)
            docs.append({"path": rel, "title": title, "content": text, "kind": "markdown"})
    return meta, docs


def _chunks(text: str, size: int = 1100, overlap: int = 150) -> list[str]:
    normalized = re.sub(r"\n{3,}", "\n\n", text).strip()
    parts: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(start + size, len(normalized))
        if end < len(normalized):
            boundary = normalized.rfind("\n\n", start, end)
            if boundary > start + 300:
                end = boundary
        parts.append(normalized[start:end].strip())
        if end >= len(normalized):
            break
        start = max(0, end - overlap)
    return [p for p in parts if p]


def build_documents(workspace_dir: Path) -> tuple[str, list[dict[str, Any]]]:
    meta, docs = _read_workspace(workspace_dir)
    workspace_id = meta["workspace_id"]
    records: list[dict[str, Any]] = []
    for doc in docs:
        if doc["kind"] == "excel":
            records.extend(excel_to_records(workspace_dir / doc["path"], doc["path"], workspace_id))
            continue
        for idx, content in enumerate(_chunks(doc["content"])):
            safe_file = re.sub(r"[^A-Za-z0-9_-]+", "-", Path(doc["path"]).stem).strip("-")
            chunk_id = f"{safe_file}-{idx:03d}"
            records.append(
                {
                    "@search.action": "mergeOrUpload",
                    "id": f"{workspace_id}-{chunk_id}",
                    "workspace_id": workspace_id,
                    "title": doc["title"],
                    "content": content,
                    "source_file": doc["path"],
                    "chunk_id": chunk_id,
                    "document_type": "markdown",
                    "language": "en",
                }
            )
    return workspace_id, records


def upload_index(workspace_dir: Path, index_name: str) -> dict[str, Any]:
    endpoint = _setting("SEARCH_ENDPOINT", "search_endpoint").rstrip("/")
    key = _setting("SEARCH_KEY", "search_primary_key")
    workspace_id, docs = build_documents(workspace_dir)
    index_url = f"{endpoint}/indexes/{index_name}?api-version={API_VERSION}"
    docs_url = f"{endpoint}/indexes/{index_name}/docs/index?api-version={API_VERSION}"
    try:
        _request("PUT", index_url, key, _index_schema(index_name))
    except RuntimeError:
        try:
            _request("DELETE", index_url, key)
        except RuntimeError:
            pass
        _request("PUT", index_url, key, _index_schema(index_name))
    upload_result = _request("POST", docs_url, key, {"value": docs})
    return {"workspace_id": workspace_id, "index_name": index_name, "document_count": len(docs), "upload": upload_result}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-dir", type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument("--index-name", default=os.environ.get("SEARCH_INDEX_NAME", DEFAULT_INDEX))
    args = parser.parse_args()
    result = upload_index(args.workspace_dir, args.index_name)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
