from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from pathlib import Path
from typing import Any


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
    workspace = ROOT / "workspaces" / workspace_id / "raw_docs"
    terms = [term.lower() for term in query.split() if len(term) > 2]
    chinese_hints = {
        "\u4ea7\u54c1": ["product", "analytics", "subscription"],
        "\u8fd0\u8425": ["operations", "pilot", "support"],
        "\u8bed\u6599": ["workspace", "documents", "data"],
        "\u8bc1\u636e": ["evidence", "data", "support"],
        "\u53ef\u884c": ["feasible", "cost", "technical"],
        "\u56fe": ["image", "dashboard", "report"],
        "\u8bed\u97f3": ["audio", "summary", "report"],
    }
    for hint, mapped in chinese_hints.items():
        if hint in query:
            terms.extend(mapped)
    hits: list[dict[str, Any]] = []
    for path in workspace.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        score = sum(text.lower().count(term) for term in terms) if terms else 0
        if score:
            hits.append(
                {
                    "id": f"{workspace_id}-{path.stem}",
                    "workspace_id": workspace_id,
                    "title": path.stem.replace("_", " ").title(),
                    "content": text[:1600],
                    "source_file": f"raw_docs/{path.name}",
                    "chunk_id": path.stem,
                    "score": score,
                }
            )
    if hits:
        return sorted(hits, key=lambda hit: hit["score"], reverse=True)[:top_k]
    fallback: list[dict[str, Any]] = []
    for path in sorted(workspace.glob("*.md"))[:top_k]:
        text = path.read_text(encoding="utf-8")
        fallback.append(
            {
                "id": f"{workspace_id}-{path.stem}",
                "workspace_id": workspace_id,
                "title": path.stem.replace("_", " ").title(),
                "content": text[:1600],
                "source_file": f"raw_docs/{path.name}",
                "chunk_id": path.stem,
                "score": 0.1,
            }
        )
    return fallback


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
        "select": "id,workspace_id,title,content,source_file,chunk_id,document_type,language",
    }
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST")
    if key:
        req.add_header("api-key", key)
    else:
        from azure.identity import DefaultAzureCredential

        token = DefaultAzureCredential().get_token("https://search.azure.com/.default")
        req.add_header("Authorization", f"Bearer {token.token}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
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
            "score": hit.get("@search.score", 0.0),
        }
        for hit in data.get("value", [])
    ]
    return results or _local_search(workspace_id, query, top_k)
