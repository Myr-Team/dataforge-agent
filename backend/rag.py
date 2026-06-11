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
    from ingest.adapters.excel_to_records import excel_to_records
    from ingest.adapters.tabular_profile import profile_search_content
except ImportError:
    excel_to_records = None
    profile_search_content = None


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


def _local_search(workspace_id: str, query: str, top_k: int) -> list[dict[str, Any]]:
    workspace_root = ROOT / "workspaces" / workspace_id
    workspace = workspace_root / "raw_docs"
    terms = _query_terms(query)
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
    for path in workspace.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        local_docs.append(
            {
                "id": f"{workspace_id}-{path.stem}",
                "workspace_id": workspace_id,
                "title": path.stem.replace("_", " ").title(),
                "content": text[:1600],
                "source_file": f"raw_docs/{path.name}",
                "chunk_id": path.stem,
                "document_type": "markdown",
                "language": "en",
            }
        )
    if excel_to_records is not None:
        for path in workspace.glob("*.xlsx"):
            local_docs.extend(excel_to_records(path, f"raw_docs/{path.name}", workspace_id))
    hits: list[dict[str, Any]] = []
    for doc in local_docs:
        haystack = f"{doc.get('title', '')} {doc.get('content', '')}".lower()
        score = _score_text(haystack, terms)
        if score:
            item = dict(doc)
            item["score"] = score
            hits.append(item)
    if hits:
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


def search(workspace_id: str, query: str, top_k: int = 5) -> list[dict[str, Any]]:
    endpoint = os.environ.get("SEARCH_ENDPOINT") or _terraform_output("search_endpoint")
    key = os.environ.get("SEARCH_KEY") or _terraform_output("search_primary_key")
    index_name = os.environ.get("SEARCH_INDEX_NAME", DEFAULT_INDEX)
    if not endpoint:
        return _local_search(workspace_id, query, top_k)

    url = f"{endpoint.rstrip('/')}/indexes/{index_name}/docs/search?api-version={API_VERSION}"
    payload = {
        "search": query,
        "top": top_k,
        "filter": f"workspace_id eq '{workspace_id}'",
        "select": "id,workspace_id,title,content,source_file,chunk_id,document_type,language,sheet,row",
    }
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST")
    if key:
        req.add_header("api-key", key)
    else:
        from azure.identity import DefaultAzureCredential

        token = DefaultAzureCredential().get_token("https://search.azure.com/.default")
        req.add_header("Authorization", f"Bearer {token.token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError:
        return _local_search(workspace_id, query, top_k)
    results = [
        {
            "id": hit.get("id"),
            "workspace_id": hit.get("workspace_id"),
            "title": hit.get("title"),
            "content": hit.get("content", ""),
            "source_file": hit.get("source_file"),
            "chunk_id": hit.get("chunk_id"),
            "document_type": hit.get("document_type"),
            "language": hit.get("language"),
            "sheet": hit.get("sheet"),
            "row": hit.get("row"),
            "score": hit.get("@search.score", 0.0),
        }
        for hit in data.get("value", [])
    ]
    return results
