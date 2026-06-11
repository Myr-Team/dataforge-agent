from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "wp_r3_agent_service_eval.json"
ENV_CANDIDATES = [
    Path(r"C:\Users\12140\.dataforge-codex.env"),
    ROOT.parent / "\u6570\u636e\u4ea7\u54c1\u5316Agent" / ".dataforge-codex.env",
]
USER_EXCEL_CANDIDATES = [
    Path(r"D:\Work\培训方案.xlsx"),
]

sys.path.insert(0, str(ROOT))

from backend.orchestrator import orchestrate_chat  # noqa: E402
from backend.schemas import ChatRequest  # noqa: E402
from ingest.build_index import upload_index  # noqa: E402
from ingest.search_smoke import search  # noqa: E402


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
    if not os.environ.get("DF_USER_EXCEL_PATH"):
        for path in USER_EXCEL_CANDIDATES:
            if path.exists():
                os.environ["DF_USER_EXCEL_PATH"] = str(path)
                break
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
    return {"event": event, "data": json.loads(data)}


async def _collect(message: str, workspace_id: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    async for frame in orchestrate_chat(ChatRequest(workspace_id=workspace_id, message=message)):
        parsed = _parse_sse_frame(frame)
        if parsed:
            events.append(parsed)
    return events


def _event_data(events: list[dict[str, Any]], event: str) -> list[dict[str, Any]]:
    return [item["data"] for item in events if item["event"] == event]


def _hit_summary(hit: dict[str, Any] | None) -> dict[str, Any] | None:
    if not hit:
        return None
    return {
        "id": hit.get("id"),
        "title": hit.get("title"),
        "source_file": hit.get("source_file"),
        "sheet": hit.get("sheet"),
        "row": hit.get("row"),
        "document_type": hit.get("document_type"),
    }


async def main() -> int:
    loaded_env = _load_env()
    if not os.environ.get("DF_USER_EXCEL_PATH"):
        raise RuntimeError("DF_USER_EXCEL_PATH is required for user Excel corpus evaluation")

    upload = upload_index(ROOT / "workspaces" / "user-excel-corpus", os.environ.get("SEARCH_INDEX_NAME", "dataforge-workspaces"))
    user_hits = search("\u5b9e\u4e60\u57f9\u8bad\u65b9\u6848 \u6c9f\u901a\u6280\u5de7", workspace_id="user-excel-corpus", top=5)

    user_message = "\u8bf7\u8bc4\u4f30\u57fa\u4e8e\u5b9e\u4e60\u57f9\u8bad\u65b9\u6848\u7684\u5458\u5de5\u57f9\u8bad\u5efa\u8bae\u548c\u590d\u76d8\u6570\u636e\u4ea7\u54c1"
    user_events = await _collect(user_message, "user-excel-corpus")
    user_final = _event_data(user_events, "final")[-1]
    model_events = _event_data(user_events, "model_response")
    autonomous_tool_events = [
        item["data"]
        for item in user_events
        if item["event"] == "tool_call" and item["data"].get("autonomous")
    ]
    feasibility = user_final["artifact"]["feasibility"]

    no_hit_message = "\u706b\u661f\u6c34\u7a3b\u4fdd\u9669\u6838\u4fdd\u4ea7\u54c1\u9700\u8981\u91cf\u5b50\u536b\u661f\u5b9e\u65f6\u6570\u636e"
    no_hit_events = await _collect(no_hit_message, "demo-corpus")
    no_hit_final = _event_data(no_hit_events, "final")[-1]

    checks = {
        "user_excel_uploaded": upload["workspace_id"] == "user-excel-corpus" and upload["document_count"] > 0,
        "user_excel_search_sheet_row": any(hit.get("sheet") and hit.get("row") for hit in user_hits),
        "feasibility_agent_service": feasibility.get("_llm", {}).get("mode") == "foundry_agent_service",
        "feasibility_autonomous_tool": bool(feasibility.get("_llm", {}).get("tool_calls")),
        "auditor_agent_service": any(
            item.get("agent") == "df-auditor" and item.get("mode") == "foundry_agent_service" for item in model_events
        ),
        "auditor_autonomous_tool": any(
            item.get("agent") == "df-auditor" and item.get("tool_calls") for item in model_events
        ),
        "sse_autonomous_tool_events": bool(autonomous_tool_events),
        "schema_valid_final": feasibility.get("verdict") in {"feasible", "conditional", "not_yet_feasible"} and bool(feasibility.get("dimensions")),
        "empty_evidence_no_double_period": "\u3002\u3002" not in no_hit_final["text"],
        "empty_evidence_no_error": not _event_data(no_hit_events, "error"),
    }
    result = {
        "ok": all(checks.values()),
        "loaded_env": loaded_env,
        "user_excel_path_configured": bool(os.environ.get("DF_USER_EXCEL_PATH")),
        "upload": {"workspace_id": upload["workspace_id"], "document_count": upload["document_count"]},
        "checks": checks,
        "user_first_hit": _hit_summary(user_hits[0] if user_hits else None),
        "user_events": [item["event"] for item in user_events],
        "autonomous_tool_events": autonomous_tool_events,
        "model_modes": [
            {"agent": item.get("agent"), "mode": item.get("mode"), "tool_call_count": len(item.get("tool_calls") or [])}
            for item in model_events
        ],
        "verdict": feasibility.get("verdict"),
        "no_hit_final_text": no_hit_final["text"],
    }
    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
