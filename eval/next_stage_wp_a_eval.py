from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "wp_a_next_stage_eval.json"
ENV_CANDIDATES = [
    Path(r"C:\Users\12140\.dataforge-codex.env"),
    ROOT.parent / "\u6570\u636e\u4ea7\u54c1\u5316Agent" / ".dataforge-codex.env",
]

sys.path.insert(0, str(ROOT))

from backend.foundry_client import run_agent  # noqa: E402
from backend.orchestrator import orchestrate_chat  # noqa: E402
from backend.schemas import AuditVerdict, ChatRequest  # noqa: E402


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


def _parse_sse(frame: str) -> dict[str, Any] | None:
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


async def _collect(message: str, workspace_id: str = "demo-corpus") -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    async for frame in orchestrate_chat(ChatRequest(workspace_id=workspace_id, message=message)):
        parsed = _parse_sse(frame)
        if parsed:
            events.append(parsed)
    return events


def _event_data(events: list[dict[str, Any]], event: str) -> list[dict[str, Any]]:
    return [item["data"] for item in events if item["event"] == event]


def _model_event_ok(events: list[dict[str, Any]], agent: str) -> bool:
    for data in _event_data(events, "model_response"):
        if data.get("agent") != agent:
            continue
        usage = data.get("usage") or {}
        return bool(data.get("response_id")) and any(value for value in usage.values())
    return False


def _grep_guard() -> dict[str, Any]:
    result = subprocess.run(
        ["rg", "-ni", r"not_yet_feasible|conditional|outdoor-analytics-copilot", "backend/orchestrator.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return {"passed": result.returncode == 1, "stdout": result.stdout, "stderr": result.stderr}


def _audit_bad_draft() -> dict[str, Any]:
    payload = {
        "workspace_id": "demo-corpus",
        "user_request": "Evaluate whether the workspace supports a safety-critical autonomous insurance underwriting product.",
        "feasibility": {
            "opportunity_id": "unsupported-underwriting-engine",
            "verdict": "feasible",
            "overall_confidence": "data_confirmed",
            "gap_list": [],
            "dimensions": [
                {
                    "name": "asset_data",
                    "score": 5,
                    "rationale": "The corpus fully supports underwriting decisions.",
                    "evidence": [{"source_type": "corpus", "ref": "missing-file#missing-row", "quote": "not in catalog"}],
                    "confidence": "data_confirmed",
                }
            ],
        },
        "evidence_catalog": [
            {
                "source_type": "corpus",
                "ref": "raw_docs/customer_feedback_summary.md#customer_feedback_summary-000",
                "quote": "Customers discuss outdoor activity summaries, coaching feedback, and exportable reports.",
            }
        ],
    }
    result = run_agent("df-auditor", json.dumps(payload, ensure_ascii=False, indent=2), response_schema=AuditVerdict.model_json_schema())
    audit = AuditVerdict.model_validate(result["structured"])
    return {"audit": audit.model_dump(), "response_id": result.get("response_id"), "usage": result.get("usage") or {}}


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-cloud", action="store_true")
    args = parser.parse_args()
    loaded_env = _load_env()

    if args.skip_cloud:
        result = {"ok": True, "skip_cloud": True, "grep_guard": _grep_guard(), "loaded_env": loaded_env}
        OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    required = ["FOUNDRY_PROJECT_ENDPOINT", "DF_CHAT_DEPLOYMENT"]
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"Missing required settings: {', '.join(missing)}")

    supported = await _collect("Evaluate a product opportunity from this workspace for operations teams.")
    unsupported = await _collect("Can we build a real-time national wildfire insurance underwriting product from this workspace?")
    bad_audit = _audit_bad_draft()

    supported_final = _event_data(supported, "final")[-1]
    unsupported_final = _event_data(unsupported, "final")[-1]
    supported_feasibility = supported_final["artifact"]["feasibility"]
    unsupported_feasibility = unsupported_final["artifact"]["feasibility"]
    grep_guard = _grep_guard()
    checks = {
        "feasibility_model_response": _model_event_ok(supported, "df-feasibility-analyst"),
        "auditor_model_response": _model_event_ok(supported, "df-auditor"),
        "unsupported_not_stronger_than_supported": unsupported_feasibility["verdict"] != "feasible"
        or unsupported_feasibility["verdict"] != supported_feasibility["verdict"],
        "different_rationale": json.dumps(supported_feasibility, sort_keys=True, ensure_ascii=False)
        != json.dumps(unsupported_feasibility, sort_keys=True, ensure_ascii=False),
        "auditor_real_revise": bad_audit["audit"]["verdict"] == "revise"
        and bool(bad_audit["response_id"])
        and any(value for value in bad_audit["usage"].values()),
        "grep_guard": grep_guard["passed"],
    }
    result = {
        "ok": all(checks.values()),
        "loaded_env": loaded_env,
        "checks": checks,
        "supported_verdict": supported_feasibility["verdict"],
        "unsupported_verdict": unsupported_feasibility["verdict"],
        "supported_model_responses": _event_data(supported, "model_response"),
        "unsupported_model_responses": _event_data(unsupported, "model_response"),
        "bad_audit": bad_audit,
        "grep_guard": grep_guard,
    }
    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
