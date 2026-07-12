"""Run bounded MAF client checks inside the deployed backend container."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from agent_framework.orchestrations import SequentialBuilder

from .maf_agents import create_agent_registry
from .maf_team_runtime import _safe_error_diagnostic
from .tracing import configure_monitoring


async def _direct_check(agent: Any) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        await asyncio.wait_for(
            agent.run('Return only this JSON object: {"status":"ok"}'),
            timeout=90,
        )
    except Exception as error:
        return {
            "check": "direct",
            "status": "failed",
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "error": _safe_error_diagnostic(error),
        }
    return {
        "check": "direct",
        "status": "completed",
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    }


async def _workflow_check(agent: Any) -> dict[str, Any]:
    started = time.perf_counter()
    stream = SequentialBuilder(participants=[agent]).build().run(
        json.dumps({"task": "Return a brief evidence-oriented response."}),
        stream=True,
    )
    workflow_error: Any | None = None
    try:
        async for event in stream:
            if event.type == "executor_failed" and event.executor_id == agent.id:
                workflow_error = event.data
        await asyncio.wait_for(stream.get_final_response(), timeout=90)
    except Exception as error:
        workflow_error = workflow_error or error
    if workflow_error is not None:
        return {
            "check": "sequential_workflow",
            "status": "failed",
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "error": _safe_error_diagnostic(workflow_error),
        }
    return {
        "check": "sequential_workflow",
        "status": "completed",
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    }


async def _main() -> None:
    configure_monitoring()
    registry = create_agent_registry(workspace_id="demo-corpus")
    results = [
        await _direct_check(registry.agent("df-coordinator")),
        await _workflow_check(registry.agent("df-corpus-analyst")),
    ]
    print(json.dumps({"maf_runtime_smoke": results}, ensure_ascii=True, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(_main())
