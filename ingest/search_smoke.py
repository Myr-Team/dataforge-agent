from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
API_VERSION = "2024-07-01"
INDEX_NAME = os.environ.get("SEARCH_INDEX_NAME", "dataforge-workspaces")


def _terraform_output(name: str) -> str:
    result = subprocess.run(
        ["terraform", "output", "-raw", name],
        cwd=ROOT / "infra" / "envs" / "dev",
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _setting(name: str, fallback_output: str) -> str:
    return os.environ.get(name) or _terraform_output(fallback_output)


def search(query: str, workspace_id: str = "demo-corpus", top: int = 5) -> list[dict[str, Any]]:
    endpoint = _setting("SEARCH_ENDPOINT", "search_endpoint").rstrip("/")
    key = _setting("SEARCH_KEY", "search_primary_key")
    if not endpoint or not key:
        raise RuntimeError("Missing SEARCH_ENDPOINT or SEARCH_KEY")
    url = f"{endpoint}/indexes/{INDEX_NAME}/docs/search?api-version={API_VERSION}"
    body = {
        "search": query,
        "top": top,
        "filter": f"workspace_id eq '{workspace_id}'",
        "select": "id,workspace_id,title,content,source_file,chunk_id",
    }
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST")
    req.add_header("api-key", key)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("value", [])
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Search failed: HTTP {exc.code}: {detail}") from exc


def assert_contains(query: str, required_terms: list[str]) -> None:
    hits = search(query)
    haystack = "\n".join(hit.get("content", "") for hit in hits).lower()
    missing = [term for term in required_terms if term.lower() not in haystack]
    if missing:
        print(json.dumps({"query": query, "missing": missing, "hits": hits[:2]}, indent=2), file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps({"query": query, "hit_count": len(hits), "first_source": hits[0].get("source_file") if hits else None}))


def main() -> int:
    assert_contains(
        "subscription analytics SaaS outdoor coaching teams repeated activity patterns",
        ["subscription analytics SaaS", "outdoor coaching teams", "repeated activity patterns"],
    )
    assert_contains(
        "health diagnosis product feasibility medical consent clinical outcomes",
        ["not", "medical consent", "clinical", "diagnosis"],
    )
    assert_contains(
        "why is medical diagnosis not yet feasible",
        ["medical consent", "validated outcome labels", "clinical governance"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

