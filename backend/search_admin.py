from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:
    from ingest.build_index import _index_schema
    from ingest.embeddings import enrich_documents_with_embeddings
except ImportError:
    _index_schema = None
    enrich_documents_with_embeddings = None


ROOT = Path(__file__).resolve().parents[1]
API_VERSION = "2024-07-01"
DEFAULT_INDEX = "dataforge-workspaces"


def search_endpoint() -> str:
    value = os.environ.get("SEARCH_ENDPOINT")
    if value:
        return value.rstrip("/")
    service = os.environ.get("DF_SEARCH_SERVICE")
    if service:
        return f"https://{service}.search.windows.net"
    return _terraform_output("search_endpoint").rstrip("/")


def search_index_name() -> str:
    return os.environ.get("SEARCH_INDEX_NAME", DEFAULT_INDEX)


def ensure_index(index_name: str | None = None) -> bool:
    if _index_schema is None:
        raise RuntimeError("Search index schema helper is unavailable")
    index = index_name or search_index_name()
    endpoint = _require_endpoint()
    url = f"{endpoint}/indexes/{index}?api-version={API_VERSION}"
    try:
        existing = _request("GET", url)
        if _has_vector_field(existing):
            return True
        try:
            _request("PUT", url, _index_schema(index))
            return True
        except urllib.error.HTTPError:
            if os.environ.get("DF_SEARCH_RECREATE_ON_VECTOR_MISSING") == "1":
                _request("DELETE", url)
                _request("PUT", url, _index_schema(index))
                return True
            return False
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise RuntimeError(f"Search index lookup failed: HTTP {exc.code}: {_read_error(exc)}") from exc
    _request("PUT", url, _index_schema(index))
    return True


def index_documents(docs: list[dict[str, Any]], index_name: str | None = None) -> int:
    if not docs:
        return 0
    index = index_name or search_index_name()
    vector_supported = ensure_index(index)
    if vector_supported and enrich_documents_with_embeddings is not None:
        docs = enrich_documents_with_embeddings(docs)
    elif not vector_supported:
        docs = [_strip_vector_fields(doc) for doc in docs]
    url = f"{_require_endpoint()}/indexes/{index}/docs/index?api-version={API_VERSION}"
    _request("POST", url, {"value": docs})
    return len(docs)


def count_workspace_docs(workspace_id: str, index_name: str | None = None) -> int:
    index = index_name or search_index_name()
    endpoint = _require_endpoint()
    url = f"{endpoint}/indexes/{index}/docs/search?api-version={API_VERSION}"
    payload = {
        "search": "*",
        "top": 0,
        "count": True,
        "filter": _workspace_filter(workspace_id),
    }
    data = _request("POST", url, payload)
    return int(data.get("@odata.count") or 0)


def list_workspace_doc_ids(workspace_id: str, index_name: str | None = None) -> list[str]:
    index = index_name or search_index_name()
    endpoint = _require_endpoint()
    url = f"{endpoint}/indexes/{index}/docs/search?api-version={API_VERSION}"
    ids: list[str] = []
    top = 1000
    skip = 0
    while True:
        payload = {
            "search": "*",
            "top": top,
            "skip": skip,
            "select": "id",
            "filter": _workspace_filter(workspace_id),
        }
        try:
            data = _request("POST", url, payload)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return []
            raise
        batch = [str(item.get("id")) for item in data.get("value", []) if item.get("id")]
        ids.extend(batch)
        if len(batch) < top:
            break
        skip += len(batch)
    return ids


def delete_workspace_docs(workspace_id: str, index_name: str | None = None) -> int:
    ids = list_workspace_doc_ids(workspace_id, index_name)
    if not ids:
        return 0
    index = index_name or search_index_name()
    url = f"{_require_endpoint()}/indexes/{index}/docs/index?api-version={API_VERSION}"
    deleted = 0
    for start in range(0, len(ids), 1000):
        batch = ids[start : start + 1000]
        _request("POST", url, {"value": [{"@search.action": "delete", "id": item} for item in batch]})
        deleted += len(batch)
    return deleted


def _workspace_filter(workspace_id: str) -> str:
    return "workspace_id eq '" + workspace_id.replace("'", "''") + "'"


def _has_vector_field(index_schema: dict[str, Any]) -> bool:
    return any(field.get("name") == "content_vector" for field in index_schema.get("fields", []))


def _strip_vector_fields(doc: dict[str, Any]) -> dict[str, Any]:
    item = dict(doc)
    item.pop("content_vector", None)
    return item


def _request(method: str, url: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    key = os.environ.get("SEARCH_KEY") or _terraform_output("search_primary_key")
    if key:
        req.add_header("api-key", key)
    else:
        from azure.identity import DefaultAzureCredential

        token = DefaultAzureCredential().get_token("https://search.azure.com/.default")
        req.add_header("Authorization", f"Bearer {token.token}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=90) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def _require_endpoint() -> str:
    endpoint = search_endpoint()
    if not endpoint:
        raise RuntimeError("Missing SEARCH_ENDPOINT or DF_SEARCH_SERVICE for AI Search")
    return endpoint


def _terraform_output(name: str) -> str:
    tf_dir = ROOT / "infra" / "envs" / "dev"
    if not tf_dir.exists():
        return ""
    result = subprocess.run(
        ["terraform", "output", "-raw", name],
        cwd=tf_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _read_error(exc: urllib.error.HTTPError) -> str:
    return exc.read().decode("utf-8", errors="replace")
