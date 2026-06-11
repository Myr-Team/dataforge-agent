from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "wp_c_excel_eval.json"
ENV_CANDIDATES = [
    Path(r"C:\Users\12140\.dataforge-codex.env"),
    ROOT.parent / "\u6570\u636e\u4ea7\u54c1\u5316Agent" / ".dataforge-codex.env",
]

sys.path.insert(0, str(ROOT))

from backend.orchestrator import orchestrate_chat  # noqa: E402
from backend.schemas import ChatRequest  # noqa: E402
from ingest.build_index import _index_schema, build_documents, upload_index  # noqa: E402
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


async def _collect(message: str, workspace_id: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    async for frame in orchestrate_chat(ChatRequest(workspace_id=workspace_id, message=message)):
        parsed = _parse_sse(frame)
        if parsed:
            events.append(parsed)
    return events


def _event_data(events: list[dict[str, Any]], event: str) -> list[dict[str, Any]]:
    return [item["data"] for item in events if item["event"] == event]


async def main() -> int:
    loaded_env = _load_env()
    schema = _index_schema(os.environ.get("SEARCH_INDEX_NAME", "dataforge-workspaces"))
    analyzer_fields = {
        field["name"]: field.get("analyzer")
        for field in schema["fields"]
        if field["name"] in {"title", "content"}
    }
    sheet_fields = {field["name"] for field in schema["fields"] if field["name"] in {"sheet", "row"}}

    build_summary: dict[str, Any] = {}
    for workspace in ["demo-corpus", "excel-corpus"]:
        workspace_id, records = build_documents(ROOT / "workspaces" / workspace)
        excel_records = [record for record in records if record.get("document_type") == "excel"]
        build_summary[workspace] = {
            "workspace_id": workspace_id,
            "record_count": len(records),
            "excel_record_count": len(excel_records),
            "first_excel_ref": {
                "source_file": excel_records[0].get("source_file") if excel_records else None,
                "sheet": excel_records[0].get("sheet") if excel_records else None,
                "row": excel_records[0].get("row") if excel_records else None,
            },
        }

    upload_results = {
        "demo-corpus": upload_index(ROOT / "workspaces" / "demo-corpus", os.environ.get("SEARCH_INDEX_NAME", "dataforge-workspaces")),
        "excel-corpus": upload_index(ROOT / "workspaces" / "excel-corpus", os.environ.get("SEARCH_INDEX_NAME", "dataforge-workspaces")),
    }

    demo_hits = cloud_search("Nightly batch cost customer pilot", workspace_id="demo-corpus", top=5)
    excel_hits = cloud_search("Manual review hours rework cost supplier batch", workspace_id="excel-corpus", top=5)
    excel_events = await _collect("Evaluate a data product for manufacturing quality inspection teams from this spreadsheet corpus.", "excel-corpus")
    excel_final = _event_data(excel_events, "final")[-1]
    model_events = _event_data(excel_events, "model_response")

    checks = {
        "schema_has_chinese_analyzer": analyzer_fields.get("title") in {"zh-Hans.microsoft", "zh-Hans.lucene"}
        and analyzer_fields.get("content") in {"zh-Hans.microsoft", "zh-Hans.lucene"},
        "schema_has_sheet_row": sheet_fields == {"sheet", "row"},
        "demo_excel_records": build_summary["demo-corpus"]["excel_record_count"] > 0,
        "second_excel_corpus_records": build_summary["excel-corpus"]["excel_record_count"] > 0,
        "demo_cloud_sheet_row_hit": any(hit.get("sheet") and hit.get("row") for hit in demo_hits),
        "excel_cloud_sheet_row_hit": any(hit.get("sheet") and hit.get("row") for hit in excel_hits),
        "excel_model_variation": bool(excel_final["artifact"]["feasibility"].get("opportunity_id"))
        and bool(model_events)
        and all(event.get("response_id") for event in model_events),
    }
    result = {
        "ok": all(checks.values()),
        "loaded_env": loaded_env,
        "checks": checks,
        "analyzer_fields": analyzer_fields,
        "build_summary": build_summary,
        "upload_results": upload_results,
        "demo_first_hit": demo_hits[0] if demo_hits else None,
        "excel_first_hit": excel_hits[0] if excel_hits else None,
        "excel_feasibility": excel_final["artifact"]["feasibility"],
        "excel_model_responses": model_events,
    }
    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
