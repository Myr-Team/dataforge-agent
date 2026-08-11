from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:
    from backend.customer_text import customer_hit_title
    from ingest.adapters.excel_to_records import excel_to_records
    from ingest.adapters.tabular_profile import profile_search_content
    from ingest.adapters.upload_to_records import upload_to_records
    from ingest.embeddings import try_embed_texts
except ImportError:
    try:
        from customer_text import customer_hit_title
    except ImportError:
        customer_hit_title = None
    excel_to_records = None
    profile_search_content = None
    upload_to_records = None
    try_embed_texts = None


ROOT = Path(__file__).resolve().parents[1]
API_VERSION = "2024-07-01"
DEFAULT_INDEX = "dataforge-workspaces"


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


def _local_documents(workspace_id: str) -> list[dict[str, Any]]:
    workspace_root = ROOT / "workspaces" / workspace_id
    workspace = workspace_root / "raw_docs"
    local_docs: list[dict[str, Any]] = []
    profile_path = workspace_root / "profile.json"
    if profile_path.exists() and profile_search_content is not None:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        local_docs.append(
            {
                "id": f"{workspace_id}-profile-000",
                "workspace_id": workspace_id,
                "title": f"{profile.get('name', workspace_id)} 数据画像",
                "content": profile_search_content(profile),
                "source_file": "profile.json",
                "chunk_id": "profile-000",
                "document_type": "profile",
                "language": "zh-Hans",
            }
        )
    if upload_to_records is not None:
        if workspace.exists():
            for path in workspace.iterdir():
                if path.is_file():
                    local_docs.extend(upload_to_records(path, f"raw_docs/{path.name}", workspace_id))
    elif excel_to_records is not None:
        for path in workspace.glob("*.xlsx"):
            local_docs.extend(excel_to_records(path, f"raw_docs/{path.name}", workspace_id))
    return local_docs


def _local_adapter_documents(workspace_id: str) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for document in _local_documents(workspace_id):
        item = dict(document)
        _apply_customer_hit_title(item)
        documents.append(item)
    return documents


def _local_search(workspace_id: str, query: str, top_k: int) -> list[dict[str, Any]]:
    terms = _query_terms(query)
    hits: list[dict[str, Any]] = []
    for doc in _local_documents(workspace_id):
        haystack = f"{doc.get('title', '')} {doc.get('content', '')}".lower()
        score = _score_text(haystack, terms)
        if score:
            item = dict(doc)
            item["score"] = score
            hits.append(item)
    if hits:
        for item in hits:
            item["retrieval_mode"] = "local_keyword"
            _apply_customer_hit_title(item)
        return sorted(hits, key=lambda hit: hit["score"], reverse=True)[:top_k]
    return []


def _query_terms(query: str) -> list[str]:
    lowered = query.lower()
    terms = [term for term in re.split(r"[^0-9a-zA-Z]+", lowered) if len(term) > 2]
    cjk_runs = re.findall(r"[\u4e00-\u9fff]+", query)
    for run in cjk_runs:
        terms.extend(run[idx : idx + 2] for idx in range(max(0, len(run) - 1)))
        terms.extend(run[idx : idx + 3] for idx in range(max(0, len(run) - 2)))
    seen: set[str] = set()
    return [term for term in terms if term and not (term in seen or seen.add(term))]


def _score_text(haystack: str, terms: list[str]) -> int:
    if not terms:
        return 0
    return sum(haystack.count(term.lower()) for term in terms)


def _trusted_allowed_corpus_refs(workspace_id: str) -> tuple[str, ...] | None:
    """Read the server-maintained corpus scope for a local adapter request.

    Local adapters have no request actor of their own, so their public entry point
    must consume an explicit, workspace-scoped authorization decision rather than
    treating an omitted scope as permission for every local document.
    """

    workspace_base = (ROOT / "workspaces").resolve()
    metadata_path = (workspace_base / workspace_id / "workspace.json").resolve()
    if metadata_path.parent.parent != workspace_base or not metadata_path.is_file():
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(metadata, dict):
        return None
    refs = metadata.get("authorized_corpus_refs")
    if not isinstance(refs, list):
        return None
    normalized = tuple(dict.fromkeys(str(ref).strip() for ref in refs if str(ref).strip()))
    return normalized


def search(
    workspace_id: str,
    query: str,
    top_k: int = 5,
    *,
    use_vector: bool = True,
    prefer_local: bool = False,
) -> list[dict[str, Any]]:
    try:
        from backend.retrieval_adapters import (
            RetrievalAdapterError,
            RetrievalRequest,
            create_search_adapter,
            legacy_fallback,
            offline_legacy_fallback,
        )
    except ImportError:
        from retrieval_adapters import (
            RetrievalAdapterError,
            RetrievalRequest,
            create_search_adapter,
            legacy_fallback,
            offline_legacy_fallback,
        )

    backend = os.environ.get("DF_RETRIEVAL_BACKEND", "legacy").strip().lower() or "legacy"
    local_backends = {"local_keyword", "local_graph", "local_hybrid_graph"}
    allowed_corpus_refs: tuple[str, ...] = ()
    authorization_applied = False
    if backend in local_backends:
        resolved_scope = _trusted_allowed_corpus_refs(workspace_id)
        if resolved_scope is None:
            return []
        allowed_corpus_refs = resolved_scope
        authorization_applied = True
    request = RetrievalRequest(
        workspace_id=workspace_id,
        query=query,
        top_k=top_k,
        allowed_corpus_refs=allowed_corpus_refs,
        authorization_applied=authorization_applied,
        mode=backend,
        graph_path=os.environ.get("DF_LOCAL_GRAPH_PATH") or None,
        use_vector=use_vector,
        prefer_local=prefer_local,
    )
    try:
        adapter = create_search_adapter(
            backend,
            legacy_search=_legacy_search,
            document_loader=_local_adapter_documents,
            workspace_base=ROOT / "workspaces",
        )
    except RetrievalAdapterError as exc:
        return legacy_fallback(_legacy_search, request, exc)
    if backend == "legacy":
        return adapter.search(request)
    try:
        return adapter.search(request)
    except Exception as exc:
        return offline_legacy_fallback(_offline_local_search, request, exc)


def _offline_local_search(
    workspace_id: str,
    query: str,
    top_k: int = 5,
    *,
    use_vector: bool = True,
    prefer_local: bool = False,
) -> list[dict[str, Any]]:
    del use_vector, prefer_local
    try:
        from backend.retrieval_adapters import validate_workspace_root
    except ImportError:
        from retrieval_adapters import validate_workspace_root
    validate_workspace_root(ROOT / "workspaces", workspace_id)
    return _local_search(workspace_id, query, top_k)


def _legacy_search(
    workspace_id: str,
    query: str,
    top_k: int = 5,
    *,
    use_vector: bool = True,
    prefer_local: bool = False,
) -> list[dict[str, Any]]:
    if os.environ.get("DF_FORCE_LOCAL_SEARCH") == "1":
        return _local_search(workspace_id, query, top_k)
    if prefer_local:
        local_hits = _local_search(workspace_id, query, top_k)
        if local_hits or os.environ.get("DF_FAST_QA_REMOTE_FALLBACK") != "1":
            return local_hits
    endpoint = os.environ.get("SEARCH_ENDPOINT") or _terraform_output("search_endpoint")
    key = os.environ.get("SEARCH_KEY") or _terraform_output("search_primary_key")
    index_name = os.environ.get("SEARCH_INDEX_NAME", DEFAULT_INDEX)
    if not endpoint:
        return _local_search(workspace_id, query, top_k)

    url = f"{endpoint.rstrip('/')}/indexes/{index_name}/docs/search?api-version={API_VERSION}"
    payload = _search_payload(workspace_id, query, top_k)
    vector = _query_vector(query) if use_vector else None
    retrieval_mode = "keyword"
    if vector:
        payload["vectorQueries"] = [{"kind": "vector", "vector": vector, "fields": "content_vector", "k": top_k}]
        retrieval_mode = "hybrid"
    try:
        data = _remote_search(url, payload, key)
    except (urllib.error.HTTPError, urllib.error.URLError):
        if vector:
            try:
                payload = _search_payload(workspace_id, query, top_k)
                data = _remote_search(url, payload, key)
                retrieval_mode = "keyword_fallback"
            except (urllib.error.HTTPError, urllib.error.URLError):
                return _local_search(workspace_id, query, top_k)
        else:
            return _local_search(workspace_id, query, top_k)
    results = [_result_from_hit(hit, retrieval_mode) for hit in data.get("value", [])]
    return results


def _search_payload(workspace_id: str, query: str, top_k: int) -> dict[str, Any]:
    return {
        "search": query,
        "top": top_k,
        "filter": _workspace_filter(workspace_id),
        "select": "id,workspace_id,title,content,source_file,chunk_id,document_type,language,sheet,row",
    }


def _remote_search(url: str, payload: dict[str, Any], key: str) -> dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST")
    if key:
        req.add_header("api-key", key)
    else:
        from azure.identity import DefaultAzureCredential

        token = DefaultAzureCredential().get_token("https://search.azure.com/.default")
        req.add_header("Authorization", f"Bearer {token.token}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _result_from_hit(hit: dict[str, Any], retrieval_mode: str) -> dict[str, Any]:
    item = {
        "id": hit.get("id"),
        "workspace_id": hit.get("workspace_id"),
        "title": hit.get("title"),
        "raw_title": hit.get("title"),
        "content": hit.get("content", ""),
        "source_file": hit.get("source_file"),
        "chunk_id": hit.get("chunk_id"),
        "document_type": hit.get("document_type"),
        "language": hit.get("language"),
        "sheet": hit.get("sheet"),
        "row": hit.get("row"),
        "score": hit.get("@search.score", 0.0),
        "retrieval_mode": retrieval_mode,
    }
    _apply_customer_hit_title(item)
    return item


def _apply_customer_hit_title(item: dict[str, Any]) -> None:
    if customer_hit_title is None:
        return
    if "raw_title" not in item:
        item["raw_title"] = item.get("title")
    item["title"] = customer_hit_title(item)


def _query_vector(query: str) -> list[float] | None:
    if try_embed_texts is None or os.environ.get("DF_DISABLE_VECTOR_SEARCH") == "1":
        return None
    vector = try_embed_texts([query])[0]
    return vector if vector else None


def _workspace_filter(workspace_id: str) -> str:
    return "workspace_id eq '" + workspace_id.replace("'", "''") + "'"
