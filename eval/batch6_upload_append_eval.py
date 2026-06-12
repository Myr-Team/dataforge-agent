from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import requests
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "batch6_upload_append_eval.json"
ENV_CANDIDATES = [
    Path(r"C:\Users\12140\.dataforge-codex.env"),
    ROOT.parent / "数据产品化Agent" / ".dataforge-codex.env",
]

import sys

sys.path.insert(0, str(ROOT))

from backend.app import app  # noqa: E402


class Harness:
    def __init__(self, api_base: str | None = None) -> None:
        self.api_base = api_base.rstrip("/") if api_base else None
        self.client = None if self.api_base else TestClient(app)

    def upload(
        self,
        path: Path,
        *,
        name: str,
        description: str | None = None,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        data = {"name": name}
        if description is not None:
            data["description"] = description
        if workspace_id:
            data["workspace_id"] = workspace_id
        with path.open("rb") as handle:
            files = [("file", (path.name, handle, _mime(path)))]
            if self.api_base:
                response = requests.post(f"{self.api_base}/api/upload", data=data, files=files, timeout=120)
            else:
                response = self.client.post("/api/upload", data=data, files=files)  # type: ignore[union-attr]
        response.raise_for_status()
        return response.json()

    def get(self, path: str, **params: Any) -> dict[str, Any]:
        if self.api_base:
            response = requests.get(f"{self.api_base}{path}", params=params or None, timeout=60)
        else:
            response = self.client.get(path, params=params or None)  # type: ignore[union-attr]
        response.raise_for_status()
        return response.json()

    def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self.api_base:
            response = requests.post(f"{self.api_base}{path}", json=payload, timeout=90)
        else:
            response = self.client.post(path, json=payload)  # type: ignore[union-attr]
        response.raise_for_status()
        return response.json()

    def delete(self, path: str) -> dict[str, Any]:
        if self.api_base:
            response = requests.delete(f"{self.api_base}{path}", timeout=90)
        else:
            response = self.client.delete(path)  # type: ignore[union-attr]
        response.raise_for_status()
        return response.json()


def _load_env() -> list[str]:
    loaded: list[str] = []
    for path in ENV_CANDIDATES:
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line.removeprefix("export ").strip()
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        loaded.append(str(path))
    return loaded


def _make_files(root: Path) -> tuple[Path, Path, Path]:
    first = root / "batch6_customers.csv"
    first.write_text(
        "segment,revenue,churn_risk,signal\n"
        "enterprise,240000,0.04,renewal-expansion\n"
        "midmarket,96000,0.12,onboarding-delay\n"
        "smb,42000,0.21,refund-churn\n",
        encoding="utf-8",
    )
    second = root / "batch6_usage.csv"
    second.write_text(
        "feature,usage_score,activation_gap,signal\n"
        "forecasting,91,low,high-adoption\n"
        "alerting,43,medium,workflow-training\n"
        "automation,18,high,integration-blocker\n",
        encoding="utf-8",
    )
    third = root / "batch6_single.csv"
    third.write_text(
        "region,orders,return_rate\n"
        "north,120,0.03\n"
        "south,75,0.11\n",
        encoding="utf-8",
    )
    return first, second, third


def _mime(path: Path) -> str:
    if path.suffix.lower() == ".csv":
        return "text/csv"
    return "application/octet-stream"


def _document_names(detail: dict[str, Any]) -> set[str]:
    return {str(item.get("name") or "") for item in detail.get("documents") or []}


def run(api_base: str | None = None, cleanup: bool = True) -> dict[str, Any]:
    loaded_env = _load_env()
    harness = Harness(api_base)
    uploaded_ids: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        first, second, third = _make_files(Path(tmp))
        name = "batch6 two-file append workspace"
        description = "Batch6 append acceptance description"
        upload1 = harness.upload(first, name=name, description=description)
        wid = str(upload1["workspace_id"])
        uploaded_ids.append(wid)
        detail_after_first = harness.get(f"/api/workspaces/{wid}")
        search_second_before = harness.post_json(
            "/api/search-pack-context",
            {"workspace_id": wid, "query": "integration blocker automation usage score", "top_k": 5},
        )
        upload2 = harness.upload(second, name=name, description=description, workspace_id=wid)
        detail = harness.get(f"/api/workspaces/{wid}")
        search_first = harness.post_json(
            "/api/search-pack-context",
            {"workspace_id": wid, "query": "refund churn customer revenue", "top_k": 5},
        )
        search_second = harness.post_json(
            "/api/search-pack-context",
            {"workspace_id": wid, "query": "integration blocker automation usage score", "top_k": 5},
        )
        single = harness.upload(third, name="batch6 single upload compatibility", description="single file compatibility")
        uploaded_ids.append(str(single["workspace_id"]))
        single_detail = harness.get(f"/api/workspaces/{single['workspace_id']}")

    document_names = _document_names(detail)
    detail_columns = {str(item.get("name") or "") for item in detail.get("columns") or []}
    first_detail_columns = {str(item.get("name") or "") for item in detail_after_first.get("columns") or []}
    search_second_before_sources = {str(item.get("source_file") or "") for item in search_second_before.get("hits") or []}
    search_first_sources = {str(item.get("source_file") or "") for item in search_first.get("hits") or []}
    search_second_sources = {str(item.get("source_file") or "") for item in search_second.get("hits") or []}
    checks = {
        "same_workspace_id": upload2.get("workspace_id") == wid,
        "detail_doc_count_at_least_2": int(detail.get("doc_count") or 0) >= 2,
        "documents_include_both_files": {"batch6_customers.csv", "batch6_usage.csv"}.issubset(document_names),
        "description_persisted": detail.get("description") == description,
        "search_reflects_first_file": any("batch6_customers.csv" in item for item in search_first_sources),
        "search_reflects_second_file": any("batch6_usage.csv" in item for item in search_second_sources),
        "search_results_change_after_append": not any("batch6_usage.csv" in item for item in search_second_before_sources)
        and any("batch6_usage.csv" in item for item in search_second_sources),
        "profile_changed_after_append": "usage_score" not in first_detail_columns
        and {"revenue", "usage_score"}.issubset(detail_columns),
        "single_upload_compatible": single.get("workspace_id") != wid and int(single_detail.get("doc_count") or 0) >= 1,
    }
    delete_results: dict[str, Any] = {}
    if cleanup:
        for workspace_id in uploaded_ids:
            try:
                delete_results[workspace_id] = harness.delete(f"/api/workspaces/{workspace_id}")
            except Exception as exc:
                delete_results[workspace_id] = {"error": f"{type(exc).__name__}: {exc}"}
    result = {
        "api_base": api_base or "local-asgi",
        "loaded_env": loaded_env,
        "workspace_id": wid,
        "upload1": upload1,
        "upload2": upload2,
        "detail_after_first": detail_after_first,
        "detail": detail,
        "single": single,
        "single_detail": single_detail,
        "search_second_before_sources": sorted(search_second_before_sources),
        "search_first_sources": sorted(search_first_sources),
        "search_second_sources": sorted(search_second_sources),
        "checks": checks,
        "delete_results": delete_results,
        "ok": all(checks.values()),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-base", default=None)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    result = run(api_base=args.api_base, cleanup=not args.no_cleanup)
    print(json.dumps({"ok": result["ok"], "workspace_id": result["workspace_id"], "checks": result["checks"]}, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
