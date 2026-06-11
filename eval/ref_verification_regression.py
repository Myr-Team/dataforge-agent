from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "ref_verification_regression.json"
ENV_CANDIDATES = [
    Path(r"C:\Users\12140\.dataforge-codex.env"),
    ROOT.parent / "\u6570\u636e\u4ea7\u54c1\u5316Agent" / ".dataforge-codex.env",
]

sys.path.insert(0, str(ROOT))

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


async def _run_once(run_number: int) -> dict[str, Any]:
    message = (
        "Evaluate commercial feasibility for an outdoor training analytics "
        f"product from the demo corpus evidence. Run {run_number}."
    )
    events: list[dict[str, Any]] = []
    started = time.perf_counter()
    async for frame in orchestrate_chat(ChatRequest(workspace_id="demo-corpus", message=message)):
        parsed = _parse_sse_frame(frame)
        if parsed:
            events.append(parsed)

    finals = [item["data"] for item in events if item["event"] == "final"]
    final = finals[-1] if finals else {}
    feasibility = final.get("artifact", {}).get("feasibility", {})
    errors = [item["data"] for item in events if item["event"] == "error"]
    return {
        "run": run_number,
        "seconds": round(time.perf_counter() - started, 3),
        "events": [item["event"] for item in events],
        "error_count": len(errors),
        "errors": errors,
        "verdict": feasibility.get("verdict"),
        "dimension_count": len(feasibility.get("dimensions") or []),
        "llm_mode": feasibility.get("_llm", {}).get("mode"),
        "evidence_warnings": feasibility.get("_llm", {}).get("evidence_warnings", []),
    }


async def main() -> int:
    loaded_env = _load_env()
    runs = []
    for run_number in range(1, 6):
        result = await _run_once(run_number)
        print(json.dumps(result, ensure_ascii=False))
        runs.append(result)

    summary = {
        "ok": all(item["error_count"] == 0 for item in runs),
        "loaded_env": loaded_env,
        "total_errors": sum(item["error_count"] for item in runs),
        "runs": runs,
    }
    OUT.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
