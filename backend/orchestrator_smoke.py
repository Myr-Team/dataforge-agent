from __future__ import annotations

import asyncio
import json
from typing import Any

try:
    from .orchestrator import orchestrate_chat
    from .schemas import ChatRequest
except ImportError:
    from orchestrator import orchestrate_chat
    from schemas import ChatRequest


def _parse_sse(frame: str) -> tuple[str, Any] | None:
    event = None
    data = None
    for line in frame.splitlines():
        if line.startswith("event: "):
            event = line.removeprefix("event: ")
        elif line.startswith("data: "):
            raw = line.removeprefix("data: ")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = raw
    return (event, data) if event else None


async def _collect(message: str) -> list[tuple[str, Any]]:
    req = ChatRequest(workspace_id="demo-corpus", message=message)
    events: list[tuple[str, Any]] = []
    async for frame in orchestrate_chat(req):
        parsed = _parse_sse(frame)
        if parsed:
            events.append(parsed)
    return events


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def main() -> int:
    product = await _collect("基于这些资料能做什么数据产品或 SaaS？")
    corpus = await _collect("只问资料里有什么，列出主要内容")
    clarify = await _collect("随便搞一个东西")
    medical = await _collect("能不能做健康诊断产品？")

    product_plan = next(data for event, data in product if event == "plan")
    corpus_plan = next(data for event, data in corpus if event == "plan")

    _require(product_plan["intent"] == "product_feasibility", "Product query did not route to product_feasibility")
    _require("df-feasibility-analyst" in product_plan["experts"], "Product route missed feasibility analyst")
    _require(corpus_plan["intent"] == "corpus_qa", "Corpus query did not route to corpus_qa")
    _require(corpus_plan["experts"] == ["df-corpus-analyst"], "Corpus query should only call corpus analyst")
    _require(any(event == "clarify" for event, _ in clarify), "Vague query did not short-circuit with clarify")

    medical_audits = [data for event, data in medical if event == "audit"]
    _require(any(audit["verdict"] == "revise" for audit in medical_audits), "Medical query did not trigger auditor revise")
    _require(any(audit["verdict"] == "pass" for audit in medical_audits), "Medical query did not pass after revision")
    _require(any(event == "final" for event, _ in product), "Product query did not emit final")

    print(
        json.dumps(
            {
                "ok": True,
                "product_events": [event for event, _ in product],
                "corpus_events": [event for event, _ in corpus],
                "clarify_events": [event for event, _ in clarify],
                "medical_audits": medical_audits,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
