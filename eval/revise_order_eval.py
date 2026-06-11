from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "revise_order_eval.json"
ENV_CANDIDATES = [
    Path(r"C:\Users\12140\.dataforge-codex.env"),
    ROOT.parent / "\u6570\u636e\u4ea7\u54c1\u5316Agent" / ".dataforge-codex.env",
]

sys.path.insert(0, str(ROOT))

from backend.orchestrator import orchestrate_chat  # noqa: E402
from backend.schemas import ChatRequest  # noqa: E402
from ingest.search_smoke import search as cloud_search  # noqa: E402


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
    try:
        payload: Any = json.loads(data)
    except json.JSONDecodeError:
        payload = {"text": data}
    return {"event": event, "data": payload}


async def _collect_local(message: str, workspace_id: str = "demo-corpus") -> tuple[list[dict[str, Any]], float]:
    events: list[dict[str, Any]] = []
    start = time.perf_counter()
    first_frame_at: float | None = None
    async for frame in orchestrate_chat(ChatRequest(workspace_id=workspace_id, message=message)):
        if first_frame_at is None:
            first_frame_at = time.perf_counter() - start
        parsed = _parse_sse_frame(frame)
        if parsed:
            events.append(parsed)
    return events, first_frame_at if first_frame_at is not None else -1


def _collect_http(api_base: str, message: str, workspace_id: str = "demo-corpus") -> tuple[list[dict[str, Any]], float, float]:
    response = requests.post(
        f"{api_base.rstrip('/')}/api/chat",
        json={"workspace_id": workspace_id, "message": message},
        stream=True,
        timeout=300,
    )
    response.raise_for_status()
    events: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    start = time.perf_counter()
    first_frame_at: float | None = None
    health_latency: float | None = None
    for raw in response.iter_lines(decode_unicode=True):
        if not raw:
            continue
        if first_frame_at is None:
            first_frame_at = time.perf_counter() - start
            health_latency = _health_latency(api_base)
        if raw.startswith("event: "):
            current = {"event": raw.removeprefix("event: "), "data": ""}
            events.append(current)
        elif raw.startswith("data: ") and current is not None:
            current["data"] += raw.removeprefix("data: ")
    for item in events:
        try:
            item["data"] = json.loads(item["data"])
        except json.JSONDecodeError:
            item["data"] = {"text": item["data"]}
    return events, first_frame_at if first_frame_at is not None else -1, health_latency if health_latency is not None else -1


def _event_data(events: list[dict[str, Any]], event: str) -> list[dict[str, Any]]:
    return [item["data"] for item in events if item["event"] == event]


def _health_latency(api_base: str) -> float:
    start = time.perf_counter()
    response = requests.get(f"{api_base.rstrip('/')}/api/health", timeout=30)
    response.raise_for_status()
    return time.perf_counter() - start


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-base", default="")
    args = parser.parse_args()
    loaded_env = _load_env()

    no_hit_message = "\u706b\u661f\u6c34\u7a3b\u4fdd\u9669\u6838\u4fdd\u4ea7\u54c1\u9700\u8981\u91cf\u5b50\u536b\u661f\u5b9e\u65f6\u6570\u636e"
    zh_message = "\u8bf7\u57fa\u4e8e\u8868\u683c\u8bed\u6599\u8bc4\u4f30\u4ea7\u7ebf\u7f3a\u9677\u68c0\u6d4b\u548c\u8fd4\u5de5\u6210\u672c\u6570\u636e\u4ea7\u54c1"

    if args.api_base:
        no_hit_events, first_frame, health_latency = _collect_http(args.api_base, no_hit_message, "demo-corpus")
        zh_events, _, _ = _collect_http(args.api_base, zh_message, "excel-corpus")
    else:
        no_hit_events, first_frame = await _collect_local(no_hit_message, "demo-corpus")
        zh_events, _ = await _collect_local(zh_message, "excel-corpus")
        health_latency = None

    no_hit_final = _event_data(no_hit_events, "final")[-1]
    zh_final = _event_data(zh_events, "final")[-1]
    zh_hits = cloud_search("\u4ea7\u7ebf\u7f3a\u9677\u68c0\u6d4b \u8fd4\u5de5\u6210\u672c", workspace_id="excel-corpus", top=5)
    rag_source = (ROOT / "backend" / "rag.py").read_text(encoding="utf-8")

    checks = {
        "no_chinese_hints": "chinese_hints" not in rag_source,
        "no_hit_no_error": not _event_data(no_hit_events, "error"),
        "no_hit_final": bool(no_hit_final),
        "no_hit_graceful_verdict": no_hit_final["artifact"]["feasibility"]["verdict"] == "not_yet_feasible",
        "zh_query_hits_sheet_row": any(hit.get("sheet") and hit.get("row") for hit in zh_hits),
        "zh_chat_no_error": not _event_data(zh_events, "error"),
        "zh_chat_model_or_final": bool(_event_data(zh_events, "model_response")) or bool(zh_final),
        "first_frame_under_1s": 0 <= first_frame < 1.0,
        "health_fast": health_latency is None or health_latency < 1.0,
    }
    result = {
        "ok": all(checks.values()),
        "loaded_env": loaded_env,
        "api_base": args.api_base or "in-process",
        "checks": checks,
        "first_frame_seconds": first_frame,
        "health_latency_seconds": health_latency,
        "no_hit_events": [item["event"] for item in no_hit_events],
        "no_hit_final": no_hit_final,
        "zh_events": [item["event"] for item in zh_events],
        "zh_first_search_hit": zh_hits[0] if zh_hits else None,
    }
    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
