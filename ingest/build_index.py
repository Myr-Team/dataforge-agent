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
from ingest.adapters.upload_to_records import upload_to_records
from ingest.embeddings import embedding_dimensions, enrich_documents_with_embeddings
from ingest.profiler import profile_to_search_document


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
            {
                "name": "content_vector",
                "type": "Collection(Edm.Single)",
                "searchable": True,
                "retrievable": False,
                "dimensions": embedding_dimensions(),
                "vectorSearchProfile": "content-vector-profile",
            },
        ],
        "vectorSearch": {
            "algorithms": [
                {
                    "name": "content-hnsw",
                    "kind": "hnsw",
                    "hnswParameters": {"metric": "cosine"},
                }
            ],
            "profiles": [{"name": "content-vector-profile", "algorithm": "content-hnsw"}],
        },
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
            docs.append({"path": rel, "absolute_path": path, "source_file": rel, "title": path.stem, "kind": "excel"})
        else:
            text = path.read_text(encoding="utf-8")
            title = next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), path.stem)
            docs.append({"path": rel, "title": title, "content": text, "kind": "markdown"})
    for item in meta.get("external_docs", []):
        configured_path = os.environ.get(item.get("path_env", "")) or item.get("path")
        if not configured_path:
            raise RuntimeError(f"Missing external doc path for {item}")
        path = Path(configured_path)
        if not path.exists():
            raise RuntimeError(f"External doc does not exist: {path}")
        source_file = item.get("source_file") or f"external/{path.name}"
        if path.suffix.lower() != ".xlsx":
            raise RuntimeError(f"Unsupported external doc type: {path}")
        docs.append(
            {
                "path": source_file,
                "absolute_path": path,
                "source_file": source_file,
                "title": item.get("title") or path.stem,
                "kind": "excel",
            }
        )
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


def _detect_language(text: str, workspace_language: str | None = None) -> str:
    if workspace_language:
        return workspace_language
    return "zh-Hans" if any("\u4e00" <= char <= "\u9fff" for char in text) else "en"


def build_documents(workspace_dir: Path) -> tuple[str, list[dict[str, Any]]]:
    meta, docs = _read_workspace(workspace_dir)
    workspace_id = meta["workspace_id"]
    records: list[dict[str, Any]] = []
    if meta.get("profile_file"):
        profile_path = workspace_dir / str(meta["profile_file"])
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        records.append(profile_to_search_document(profile))
    workspace_language = meta.get("language")
    for doc in docs:
        if doc["kind"] == "excel":
            records.extend(excel_to_records(Path(doc["absolute_path"]), doc["source_file"], workspace_id))
            continue
        absolute_path = workspace_dir / str(doc["path"])
        if absolute_path.exists():
            upload_records = upload_to_records(absolute_path, doc["path"], workspace_id)
            if upload_records:
                records.extend(upload_records)
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
                    "language": _detect_language(content, workspace_language),
                }
            )
    return workspace_id, records


def upload_index(workspace_dir: Path, index_name: str) -> dict[str, Any]:
    endpoint = _setting("SEARCH_ENDPOINT", "search_endpoint").rstrip("/")
    key = _setting("SEARCH_KEY", "search_primary_key")
    workspace_id, docs = build_documents(workspace_dir)
    docs = enrich_documents_with_embeddings(docs)
    index_url = f"{endpoint}/indexes/{index_name}?api-version={API_VERSION}"
    docs_url = f"{endpoint}/indexes/{index_name}/docs/index?api-version={API_VERSION}"
    try:
        _request("PUT", index_url, key, _index_schema(index_name))
        vector_supported = True
    except RuntimeError:
        if os.environ.get("DF_SEARCH_RECREATE_ON_SCHEMA_CONFLICT") == "1":
            try:
                _request("DELETE", index_url, key)
            except RuntimeError:
                pass
            _request("PUT", index_url, key, _index_schema(index_name))
            vector_supported = True
        else:
            vector_supported = False
    upload_docs = docs if vector_supported else [_without_vector(doc) for doc in docs]
    try:
        upload_result = _request("POST", docs_url, key, {"value": upload_docs})
    except RuntimeError:
        upload_docs = [_without_vector(doc) for doc in docs]
        upload_result = _request("POST", docs_url, key, {"value": upload_docs})
    return {"workspace_id": workspace_id, "index_name": index_name, "document_count": len(docs), "upload": upload_result}


def _without_vector(doc: dict[str, Any]) -> dict[str, Any]:
    item = dict(doc)
    item.pop("content_vector", None)
    return item


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
