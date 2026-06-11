from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from openpyxl import Workbook


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "onboarding_upload_eval.json"
ENV_CANDIDATES = [
    Path(r"C:\Users\12140\.dataforge-codex.env"),
    ROOT.parent / "数据产品化Agent" / ".dataforge-codex.env",
]

import sys

sys.path.insert(0, str(ROOT))

from backend.app import app  # noqa: E402
from backend.orchestrator import orchestrate_chat  # noqa: E402
from backend.schemas import ChatRequest  # noqa: E402


def _load_env() -> list[str]:
    loaded: list[str] = []
    for path in ENV_CANDIDATES:
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        loaded.append(str(path))
    os.environ.setdefault("MCP_MARKET_URL", "https://ca-dataforge-mcp.thankfultree-c0fc8321.eastus2.azurecontainerapps.io")
    return loaded


def _parse_sse_frame(frame: str) -> dict[str, Any] | None:
    event = None
    data = ""
    for line in frame.splitlines():
        if line.startswith("event: "):
            event = line.removeprefix("event: ")
        elif line.startswith("data: "):
            data += line.removeprefix("data: ")
    if not event:
        return None
    return {"event": event, "data": json.loads(data) if data else {}}


async def _collect_chat(workspace_id: str, message: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    async for frame in orchestrate_chat(ChatRequest(workspace_id=workspace_id, message=message)):
        parsed = _parse_sse_frame(frame)
        if parsed:
            events.append(parsed)
    return events


def _make_files(root: Path) -> dict[str, Path]:
    csv_path = root / "regional_revenue.csv"
    csv_path.write_text(
        "region,revenue,churn_rate,expansion_signal\n"
        "华东,128000,0.03,企业客户复购\n"
        "华南,76000,0.08,渠道培训不足\n"
        "西南,42000,0.12,交付响应慢\n",
        encoding="utf-8",
    )
    json_path = root / "support_tickets.json"
    json_path.write_text(
        json.dumps(
            {
                "tickets": [
                    {"category": "退款", "resolution_hours": 6, "sentiment": "negative", "automation_gap": "合同解释"},
                    {"category": "部署", "resolution_hours": 18, "sentiment": "neutral", "automation_gap": "环境检查"},
                    {"category": "培训", "resolution_hours": 4, "sentiment": "positive", "automation_gap": "知识库推荐"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    excel_path = root / "factory_quality.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "QualitySignals"
    sheet.append(["line", "defect_rate", "rework_cost", "supplier", "inspection_note"])
    sheet.append(["A", 0.021, 3200, "S1", "焊点偏移集中在夜班"])
    sheet.append(["B", 0.074, 18500, "S2", "来料批次波动明显"])
    sheet.append(["C", 0.018, 2100, "S1", "稳定"])
    workbook.save(excel_path)
    return {"csv": csv_path, "json": json_path, "excel": excel_path}


def _upload(client: TestClient, path: Path, name: str) -> dict[str, Any]:
    with path.open("rb") as handle:
        response = client.post("/api/upload", data={"name": name}, files={"file": (path.name, handle, _mime(path))})
    response.raise_for_status()
    return response.json()


def _mime(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return "text/csv"
    if suffix == ".json":
        return "application/json"
    if suffix == ".xlsx":
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return "application/octet-stream"


async def main() -> int:
    loaded_env = _load_env()
    client = TestClient(app)
    with tempfile.TemporaryDirectory() as tmp:
        files = _make_files(Path(tmp))
        uploads = {
            fmt: _upload(client, path, f"验收 {fmt} 数据 {path.stem}")
            for fmt, path in files.items()
        }

    search_checks = {}
    queries = {
        "csv": "revenue churn expansion 华东",
        "json": "resolution_hours automation_gap 退款",
        "excel": "defect_rate rework_cost 焊点",
    }
    for fmt, upload in uploads.items():
        response = client.post(
            "/api/search-pack-context",
            json={"workspace_id": upload["workspace_id"], "query": queries[fmt], "top_k": 3},
        )
        response.raise_for_status()
        data = response.json()
        search_checks[fmt] = {"count": data["count"], "first": data["hits"][0] if data["hits"] else None}

    csv_events = await _collect_chat(uploads["csv"]["workspace_id"], "请基于这个工作区判断最适合产品化的数据产品方向。")
    json_events = await _collect_chat(uploads["json"]["workspace_id"], "请基于这个工作区判断最适合产品化的数据产品方向。")
    csv_final = _final_payload(csv_events)
    json_final = _final_payload(json_events)
    csv_feasibility = csv_final.get("artifact", {}).get("feasibility", {})
    json_feasibility = json_final.get("artifact", {}).get("feasibility", {})

    clarify_questions = []
    clarify_modes = []
    for _ in range(3):
        events = await _collect_chat(uploads["csv"]["workspace_id"], "你好")
        clarify = next(item["data"] for item in events if item["event"] == "clarify")
        clarify_questions.append(clarify.get("question"))
        clarify_modes.append(clarify.get("mode"))

    workspaces = client.get("/api/workspaces")
    workspaces.raise_for_status()
    workspace_payload = workspaces.json()

    summary = {
        "ok": True,
        "loaded_env": loaded_env,
        "uploads": uploads,
        "search_checks": search_checks,
        "conclusions_differ": json.dumps(csv_feasibility, ensure_ascii=False, sort_keys=True)
        != json.dumps(json_feasibility, ensure_ascii=False, sort_keys=True),
        "csv_chat_errors": [item for item in csv_events if item["event"] == "error"],
        "json_chat_errors": [item for item in json_events if item["event"] == "error"],
        "clarify_questions": clarify_questions,
        "clarify_modes": clarify_modes,
        "clarify_varies": len(set(clarify_questions)) > 1,
        "workspace_count": len(workspace_payload.get("workspaces", [])),
    }
    checks = {
        "uploaded_all_formats": set(uploads) == {"csv", "json", "excel"},
        "indexed_profiles": all(item["indexed_count"] >= 1 for item in uploads.values()),
        "searchable_all_formats": all(item["count"] > 0 for item in search_checks.values()),
        "conclusions_differ": summary["conclusions_differ"],
        "no_chat_errors": not summary["csv_chat_errors"] and not summary["json_chat_errors"],
        "clarify_llm": all(mode == "coordinator_llm" for mode in clarify_modes),
        "clarify_varies": summary["clarify_varies"],
        "workspaces_listed": len(workspace_payload.get("workspaces", [])) >= 3,
    }
    summary["checks"] = checks
    summary["ok"] = all(checks.values())
    OUT.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if summary["ok"] else 1


def _final_payload(events: list[dict[str, Any]]) -> dict[str, Any]:
    finals = [item["data"] for item in events if item["event"] == "final"]
    return finals[-1] if finals else {}


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
