from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import requests
from fastapi.testclient import TestClient
from openpyxl import Workbook


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "batch7_content_index_eval.json"
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

    def upload(self, path: Path, *, name: str, description: str | None = None) -> dict[str, Any]:
        data = {"name": name}
        if description:
            data["description"] = description
        with path.open("rb") as handle:
            files = [("file", (path.name, handle, _mime(path)))]
            if self.api_base:
                response = requests.post(f"{self.api_base}/api/upload", data=data, files=files, timeout=180)
            else:
                response = self.client.post("/api/upload", data=data, files=files)  # type: ignore[union-attr]
        response.raise_for_status()
        return response.json()

    def post_json(self, path: str, payload: dict[str, Any], timeout: int = 120) -> dict[str, Any]:
        if self.api_base:
            response = requests.post(f"{self.api_base}{path}", json=payload, timeout=timeout)
        else:
            response = self.client.post(path, json=payload)  # type: ignore[union-attr]
        response.raise_for_status()
        return response.json()

    def delete(self, workspace_id: str) -> dict[str, Any]:
        if self.api_base:
            response = requests.delete(f"{self.api_base}/api/workspaces/{workspace_id}", timeout=90)
        else:
            response = self.client.delete(f"/api/workspaces/{workspace_id}")  # type: ignore[union-attr]
        response.raise_for_status()
        return response.json()

    def chat(self, workspace_id: str, message: str) -> list[dict[str, Any]]:
        payload = {"workspace_id": workspace_id, "message": message}
        events: list[dict[str, Any]] = []
        if self.api_base:
            with requests.post(f"{self.api_base}/api/chat", json=payload, stream=True, timeout=240) as response:
                response.raise_for_status()
                buffer = ""
                for chunk in response.iter_content(chunk_size=None, decode_unicode=True):
                    if not chunk:
                        continue
                    buffer += chunk
                    while "\n\n" in buffer:
                        frame, buffer = buffer.split("\n\n", 1)
                        parsed = _parse_sse(frame)
                        if parsed:
                            events.append(parsed)
                        if parsed and parsed["event"] in {"final", "error"}:
                            return events
            return events
        with self.client.stream("POST", "/api/chat", json=payload) as response:  # type: ignore[union-attr]
            response.raise_for_status()
            for frame in response.iter_text().split("\n\n"):
                parsed = _parse_sse(frame)
                if parsed:
                    events.append(parsed)
        return events


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


def _parse_sse(frame: str) -> dict[str, Any] | None:
    event = None
    data = ""
    for line in frame.splitlines():
        if line.startswith("event: "):
            event = line.removeprefix("event: ").strip()
        elif line.startswith("data: "):
            data += line.removeprefix("data: ")
    if not event:
        return None
    try:
        parsed = json.loads(data) if data else {}
    except json.JSONDecodeError:
        parsed = {"raw": data}
    return {"event": event, "data": parsed}


def _make_csv(path: Path) -> Path:
    target = path / "batch7_content.csv"
    target.write_text(
        "store,activity,member_share,sponsor_signal\n"
        "后海旗舰店,联名护手霜体验日,0.38,运动品牌愿意赞助\n"
        "福田店,周边T恤试穿打卡,0.31,社媒曝光强\n",
        encoding="utf-8",
    )
    return target


def _make_excel(path: Path) -> Path:
    target = path / "batch7_content.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "ActivitySignals"
    sheet.append(["store", "pain_point", "product", "conversion_signal"])
    sheet.append(["南山后海汇旗舰店", "手部护理痛点明显", "护手霜", "复购会员愿意带朋友体验"])
    sheet.append(["福田领展中心店", "打卡传播强", "Logo T恤", "跨店会员占比高"])
    workbook.save(target)
    return target


def _mime(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return "application/json"
    if suffix == ".csv":
        return "text/csv"
    if suffix == ".xlsx":
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return "application/octet-stream"


def _hits(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in payload.get("hits") or [] if isinstance(item, dict)]


def _sources(hits: list[dict[str, Any]]) -> set[str]:
    return {str(item.get("source_file") or "") for item in hits}


def _content_join(hits: list[dict[str, Any]]) -> str:
    return "\n".join(str(item.get("content") or "") for item in hits)


def _final_citations(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    finals = [event["data"] for event in events if event.get("event") == "final"]
    if not finals:
        return []
    artifact = finals[-1].get("artifact") or {}
    citations = artifact.get("citations") or (artifact.get("answer") or {}).get("citations") or []
    return [item for item in citations if isinstance(item, dict)]


def run(api_base: str | None = None, cleanup: bool = True, banana_path: Path | None = None) -> dict[str, Any]:
    loaded_env = _load_env()
    harness = Harness(api_base)
    uploaded_ids: list[str] = []
    banana = banana_path or Path(r"D:\Demo\banana_climbing_shenzhen.json")
    if not banana.exists():
        raise FileNotFoundError(f"Missing banana demo file: {banana}")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        csv_path = _make_csv(root)
        excel_path = _make_excel(root)
        banana_upload = harness.upload(banana, name="batch7 banana content index", description="Batch7 JSON content index")
        csv_upload = harness.upload(csv_path, name="batch7 csv content index", description="Batch7 CSV content index")
        excel_upload = harness.upload(excel_path, name="batch7 excel content index", description="Batch7 Excel content index")
        uploaded_ids.extend([banana_upload["workspace_id"], csv_upload["workspace_id"], excel_upload["workspace_id"]])

        banana_search = harness.post_json(
            "/api/search-pack-context",
            {
                "workspace_id": banana_upload["workspace_id"],
                "query": "推广 活动 周边 会员 赞助 护手",
                "top_k": 10,
            },
        )
        csv_search = harness.post_json(
            "/api/search-pack-context",
            {"workspace_id": csv_upload["workspace_id"], "query": "护手霜 赞助 会员 活动", "top_k": 5},
        )
        excel_search = harness.post_json(
            "/api/search-pack-context",
            {"workspace_id": excel_upload["workspace_id"], "query": "手部护理 护手霜 复购会员", "top_k": 5},
        )
        chat_events = harness.chat(
            banana_upload["workspace_id"],
            "我想做一个活动让攀岩馆获得推广该怎么做？请基于工作区证据给出建议并引用证据。",
        )

    banana_hits = _hits(banana_search)
    csv_hits = _hits(csv_search)
    excel_hits = _hits(excel_search)
    banana_content = _content_join(banana_hits)
    csv_content = _content_join(csv_hits)
    excel_content = _content_join(excel_hits)
    citations = _final_citations(chat_events)
    checks = {
        "banana_indexed_many_docs": banana_upload.get("indexed_count", 0) > 10,
        "banana_search_multiple_real_records": len(banana_hits) >= 3
        and any(item.get("document_type") == "json" for item in banana_hits),
        "banana_search_specific_content": any(
            phrase in banana_content
            for phrase in ["护手霜", "跨店会员", "带logo", "会员占比", "赞助"]
        ),
        "csv_content_searchable": any("batch7_content.csv" in item for item in _sources(csv_hits))
        and "护手霜" in csv_content,
        "excel_content_searchable": any("batch7_content.xlsx" in item for item in _sources(excel_hits))
        and any(item.get("row") for item in excel_hits),
        "chat_has_citations": len(citations) > 0,
        "chat_cites_uploaded_records": any(
            str(item.get("source_file") or "").endswith("banana_climbing_shenzhen.json") for item in citations
        ),
    }
    delete_results: dict[str, Any] = {}
    if cleanup:
        for workspace_id in uploaded_ids:
            try:
                delete_results[workspace_id] = harness.delete(workspace_id)
            except Exception as exc:
                delete_results[workspace_id] = {"error": f"{type(exc).__name__}: {exc}"}
    result = {
        "api_base": api_base or "local-asgi",
        "loaded_env": loaded_env,
        "uploads": {"banana": banana_upload, "csv": csv_upload, "excel": excel_upload},
        "banana_search_count": len(banana_hits),
        "banana_search_samples": banana_hits[:5],
        "csv_search_samples": csv_hits[:3],
        "excel_search_samples": excel_hits[:3],
        "chat_event_names": [event.get("event") for event in chat_events],
        "chat_citations_count": len(citations),
        "chat_citations": citations[:5],
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
    parser.add_argument("--banana-path", type=Path, default=None)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    result = run(api_base=args.api_base, cleanup=not args.no_cleanup, banana_path=args.banana_path)
    print(
        json.dumps(
            {
                "ok": result["ok"],
                "uploads": {key: value["workspace_id"] for key, value in result["uploads"].items()},
                "banana_search_count": result["banana_search_count"],
                "chat_citations_count": result["chat_citations_count"],
                "checks": result["checks"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
