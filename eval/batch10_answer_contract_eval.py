from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import requests
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "batch10_answer_contract_eval.json"
ENV_CANDIDATES = [
    Path(r"C:\Users\12140\.dataforge-codex.env"),
    ROOT.parent / "数据产品化Agent" / ".dataforge-codex.env",
]

sys.path.insert(0, str(ROOT))


RAW_FIELDS_A = [
    "home_branch",
    "also_visits",
    "collection",
    "category",
    "topic",
    "detail",
    "monthly_visits",
]
RAW_FIELDS_B = [
    "plan_type",
    "usage_score",
    "renewal_risk",
    "support_channel",
    "contract_value",
]


class Harness:
    def __init__(self, api_base: str | None = None) -> None:
        self.api_base = api_base.rstrip("/") if api_base else None
        if self.api_base:
            self.client = None
        else:
            from backend.app import app

            self.client = TestClient(app)

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

    def workspace(self, workspace_id: str) -> dict[str, Any]:
        if self.api_base:
            response = requests.get(f"{self.api_base}/api/workspaces/{workspace_id}", timeout=90)
        else:
            response = self.client.get(f"/api/workspaces/{workspace_id}")  # type: ignore[union-attr]
        response.raise_for_status()
        return response.json()

    def chat(self, workspace_id: str, message: str, *, conversation_id: str | None = None) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"workspace_id": workspace_id, "message": message}
        if conversation_id:
            payload["conversation_id"] = conversation_id
        if self.api_base:
            with requests.post(f"{self.api_base}/api/chat", json=payload, stream=True, timeout=280) as response:
                response.raise_for_status()
                return _parse_stream(response.iter_content(chunk_size=None, decode_unicode=True))
        with self.client.stream("POST", "/api/chat", json=payload) as response:  # type: ignore[union-attr]
            response.raise_for_status()
            return _parse_stream(response.iter_text())

    def delete(self, workspace_id: str) -> dict[str, Any]:
        if self.api_base:
            response = requests.delete(f"{self.api_base}/api/workspaces/{workspace_id}", timeout=120)
        else:
            response = self.client.delete(f"/api/workspaces/{workspace_id}")  # type: ignore[union-attr]
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


def _prepare_local_env() -> None:
    os.environ["DF_COORDINATOR_ALLOW_DETERMINISTIC_FALLBACK"] = "1"
    os.environ["DF_DISABLE_VECTOR_SEARCH"] = "1"
    os.environ["DF_DISABLE_REDIS_CACHE"] = "1"
    os.environ["DF_FORCE_LOCAL_SEARCH"] = "1"
    for key in (
        "FOUNDRY_PROJECT_ENDPOINT",
        "SEARCH_ENDPOINT",
        "SEARCH_KEY",
        "DF_SEARCH_SERVICE",
        "AZURE_OPENAI_API_KEY",
        "OPENAI_API_KEY",
        "OPENAI_ENDPOINT",
    ):
        os.environ.pop(key, None)


def _parse_stream(chunks: Any) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    buffer = ""
    for chunk in chunks:
        if not chunk:
            continue
        buffer += chunk
        while "\n\n" in buffer:
            frame, buffer = buffer.split("\n\n", 1)
            parsed = _parse_frame(frame)
            if parsed:
                events.append(parsed)
            if parsed and parsed["event"] in {"final", "error", "clarify"}:
                return events
    parsed = _parse_frame(buffer)
    if parsed:
        events.append(parsed)
    return events


def _parse_frame(frame: str) -> dict[str, Any] | None:
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
        payload = json.loads(data) if data else {}
    except json.JSONDecodeError:
        payload = {"raw": data}
    return {"event": event, "data": payload}


def _make_csv(path: Path) -> Path:
    target = path / "batch10_activity_signals.csv"
    target.write_text(
        "home_branch,also_visits,collection,category,topic,detail,monthly_visits\n"
        "后海旗舰店,福田店,运营语料,活动,会员裂变,38% 老会员愿意邀请朋友体验入门课,6\n"
        "福田店,南山店,运营语料,周边,Logo T恤,打卡照片里品牌露出高，适合作为活动奖品,4\n"
        "南山店,后海旗舰店,运营语料,赞助,护手产品,本地运动品牌愿意赞助护手产品和体验券,5\n",
        encoding="utf-8",
    )
    return target


def _make_json(path: Path) -> Path:
    target = path / "batch10_retention_signals.json"
    target.write_text(
        json.dumps(
            [
                {
                    "plan_type": "家庭会员",
                    "usage_score": 74,
                    "renewal_risk": "中",
                    "support_channel": "微信群",
                    "contract_value": 1680,
                },
                {
                    "plan_type": "企业团课",
                    "usage_score": 42,
                    "renewal_risk": "高",
                    "support_channel": "客户经理",
                    "contract_value": 9800,
                },
                {
                    "plan_type": "青少年季卡",
                    "usage_score": 81,
                    "renewal_risk": "低",
                    "support_channel": "小程序",
                    "contract_value": 2580,
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return target


def _mime(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return "text/csv"
    if suffix == ".json":
        return "application/json"
    if suffix == ".xlsx":
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return "application/octet-stream"


def _final(events: list[dict[str, Any]]) -> dict[str, Any]:
    finals = [event["data"] for event in events if event.get("event") == "final"]
    return finals[-1] if finals else {}


def _clarify(events: list[dict[str, Any]]) -> dict[str, Any]:
    frames = [event["data"] for event in events if event.get("event") == "clarify"]
    return frames[-1] if frames else {}


def _answer_deltas(events: list[dict[str, Any]]) -> str:
    return "".join(str(event["data"].get("delta") or "") for event in events if event.get("event") == "answer_delta")


def _citations(final: dict[str, Any]) -> list[dict[str, Any]]:
    artifact = final.get("artifact") or {}
    citations = artifact.get("citations") or (artifact.get("answer") or {}).get("citations") or []
    return [item for item in citations if isinstance(item, dict)]


def _raw_hits(text: Any, fields: list[str]) -> list[str]:
    value = str(text or "")
    return [field for field in fields if field in value]


def _has_customer_label(detail: dict[str, Any]) -> bool:
    return all(bool((column or {}).get("friendly_label")) for column in detail.get("columns") or [])


def _first_paragraph(text: str) -> str:
    return text.strip().split("\n\n", 1)[0].strip()


def _variant_ids(prefix: str, count: int) -> list[str]:
    selected: list[str] = []
    seen: set[int] = set()
    index = 0
    while len(selected) < count and index < 100:
        candidate = f"{prefix}-{index}"
        variant = hashlib.sha256(candidate.encode("utf-8")).digest()[0] % 3
        if variant not in seen:
            seen.add(variant)
            selected.append(candidate)
        index += 1
    while len(selected) < count:
        selected.append(f"{prefix}-extra-{len(selected)}")
    return selected


def _renderer_feasibility_check(workspace_id: str, message: str) -> dict[str, Any]:
    from backend.orchestrator import _run_corpus_analyst, _structured_answer_v10
    from backend.schemas import ChatRequest, RoutingDecision

    req = ChatRequest(workspace_id=workspace_id, message=message)
    artifact = {
        "workspace_id": workspace_id,
        "conversation_id": "batch10-local-renderer",
        "corpus": _run_corpus_analyst(req),
        "feasibility": {
            "opportunity_id": "membership-data-product",
            "dimensions": [
                {
                    "name": "asset_data",
                    "score": 3,
                    "rationale": "命中资料已覆盖活动、会员或续费相关线索，但还需要补充真实转化结果。",
                    "evidence": [],
                    "confidence": "data_confirmed",
                }
            ],
            "verdict": "conditional",
            "overall_confidence": "speculative",
            "gap_list": ["还缺少成本、转化和复购结果的连续记录。"],
        },
        "market": {"positioning_note": ""},
        "audit": {"verdict": "pass"},
    }
    decision = RoutingDecision(
        workspace_id=workspace_id,
        intent="feasibility_analysis",
        experts=["df-corpus-analyst", "df-feasibility-analyst", "df-auditor"],
        output_mode="report",
        needs_clarification=False,
        reason="Batch10 renderer contract check.",
    )
    rendered = _structured_answer_v10(req, decision, artifact)
    text = str(rendered.get("markdown") or "")
    return {
        "text": text,
        "has_contract": rendered.get("output_contract", {}).get("version") == "batch10.customer_text.v1",
        "first_not_heading": not _first_paragraph(text).startswith("##"),
    }


def run(api_base: str | None = None, cleanup: bool = True) -> dict[str, Any]:
    loaded_env = _load_env() if api_base else []
    if not api_base:
        _prepare_local_env()
    harness = Harness(api_base)
    uploaded_ids: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        csv_path = _make_csv(tmpdir)
        json_path = _make_json(tmpdir)
        upload_a = harness.upload(csv_path, name=f"batch10 activity {int(time.time())}", description="Batch10 answer contract CSV")
        upload_b = harness.upload(json_path, name=f"batch10 retention {int(time.time())}", description="Batch10 answer contract JSON")
        workspace_a = upload_a["workspace_id"]
        workspace_b = upload_b["workspace_id"]
        uploaded_ids.extend([workspace_a, workspace_b])
        conversation_prefix = f"batch10-{int(time.time() * 1000)}"

        detail_a = harness.workspace(workspace_a)
        detail_b = harness.workspace(workspace_b)
        clarify_events = harness.chat(workspace_a, "随便看看", conversation_id=f"{conversation_prefix}-clarify")
        question = "请基于资料给我一版运营活动方案建议，重点看会员和门店。"
        variant_ids = _variant_ids(f"{conversation_prefix}-repeat", 3)
        repeat_events = [harness.chat(workspace_a, question, conversation_id=item) for item in variant_ids]
        other_events = harness.chat(workspace_b, "请基于资料给家庭会员做一版运营建议。", conversation_id=f"{conversation_prefix}-other-dataset")

        if api_base:
            feasibility_events = harness.chat(
                workspace_a,
                "请评估能不能把这些资料做成一个会员经营数据产品。",
                conversation_id=f"{conversation_prefix}-feasibility",
            )
            feasibility_sample = {"events": feasibility_events, "local_renderer": None}
        else:
            feasibility_sample = {
                "events": [],
                "local_renderer": _renderer_feasibility_check(workspace_a, "请评估能不能把这些资料做成一个会员经营数据产品。"),
            }

    clarify = _clarify(clarify_events)
    clarify_struct = clarify.get("clarify") or {}
    repeat_finals = [_final(events) for events in repeat_events]
    other_final = _final(other_events)
    repeat_texts = [str(final.get("text") or "") for final in repeat_finals]
    other_text = str(other_final.get("text") or "")
    repeat_delta_matches = [
        _answer_deltas(events) == str(final.get("text") or "")
        for events, final in zip(repeat_events, repeat_finals)
    ]
    feasibility_final = _final(feasibility_sample["events"]) if feasibility_sample["events"] else {}
    feasibility_text = str(feasibility_final.get("text") or (feasibility_sample["local_renderer"] or {}).get("text") or "")

    all_raw_fields = RAW_FIELDS_A + RAW_FIELDS_B
    raw_field_hits = {
        "workspace_a_summary": _raw_hits(detail_a.get("customer_summary"), RAW_FIELDS_A),
        "workspace_b_summary": _raw_hits(detail_b.get("customer_summary"), RAW_FIELDS_B),
        "clarify_question": _raw_hits(clarify_struct.get("question") or clarify.get("question"), all_raw_fields),
        "clarify_options": _raw_hits(json.dumps(clarify_struct.get("options") or [], ensure_ascii=False), all_raw_fields),
        "repeat_answers": _raw_hits("\n".join(repeat_texts), RAW_FIELDS_A),
        "other_answer": _raw_hits(other_text, RAW_FIELDS_B),
        "feasibility_answer": _raw_hits(feasibility_text, all_raw_fields),
        "citation_snippets": _raw_hits(
            json.dumps([_citations(final) for final in repeat_finals] + [_citations(other_final)], ensure_ascii=False),
            all_raw_fields,
        ),
    }
    event_runs = [clarify_events, other_events, *repeat_events]
    if feasibility_sample["events"]:
        event_runs.append(feasibility_sample["events"])
    errors = [event for events in event_runs for event in events if event.get("event") == "error"]
    output_contracts = [
        (final.get("output_contract") or (final.get("artifact") or {}).get("output_contract") or {}).get("version")
        for final in repeat_finals + [other_final, feasibility_final]
        if final
    ]
    checks = {
        "no_errors": not errors,
        "workspace_detail_customer_summary": bool(detail_a.get("customer_summary")) and bool(detail_b.get("customer_summary")),
        "workspace_summary_changes_by_dataset": detail_a.get("customer_summary") != detail_b.get("customer_summary"),
        "workspace_columns_have_friendly_labels": _has_customer_label(detail_a) and _has_customer_label(detail_b),
        "clarify_structured": isinstance(clarify_struct.get("options"), list)
        and 2 <= len(clarify_struct.get("options") or []) <= 5
        and clarify_struct.get("allow_multi") is True
        and clarify_struct.get("allow_freeform") is True,
        "clarify_options_chinese": all(
            any("\u4e00" <= char <= "\u9fff" for char in str(item.get("label") or ""))
            for item in clarify_struct.get("options") or []
        ),
        "stream_delta_equals_final": all(repeat_delta_matches),
        "repeat_question_not_identical": len(set(repeat_texts)) > 1,
        "first_paragraph_not_heading": all(not _first_paragraph(text).startswith("##") for text in repeat_texts + [other_text, feasibility_text] if text),
        "corpus_answers_have_citations": all(_citations(final) for final in repeat_finals + [other_final]),
        "answer_contract_present": all(version == "batch10.customer_text.v1" for version in output_contracts)
        and ((feasibility_sample["local_renderer"] or {}).get("has_contract") is not False),
        "no_raw_paths_in_body": all("raw_docs/" not in text and "profile.json" not in text for text in repeat_texts + [other_text, feasibility_text]),
        "no_raw_field_names": not any(raw_field_hits.values()),
        "different_dataset_answer_changes": bool(repeat_texts and other_text and repeat_texts[0] != other_text),
    }

    delete_results: dict[str, Any] = {}
    if cleanup:
        for workspace_id in uploaded_ids:
            try:
                delete_results[workspace_id] = harness.delete(workspace_id)
            except Exception as exc:
                delete_results[workspace_id] = {"error": f"{type(exc).__name__}: {exc}"}

    result = {
        "ok": all(checks.values()),
        "api_base": api_base,
        "loaded_env": loaded_env,
        "checks": checks,
        "workspace_ids": uploaded_ids,
        "raw_field_hits": raw_field_hits,
        "clarify_sample": clarify,
        "workspace_summary_samples": {
            "csv": detail_a.get("customer_summary"),
            "json": detail_b.get("customer_summary"),
        },
        "text_samples": {
            "repeat": [text[:1000] for text in repeat_texts],
            "other_dataset": other_text[:1000],
            "feasibility": feasibility_text[:1000],
        },
        "citation_counts": [len(_citations(final)) for final in repeat_finals + [other_final, feasibility_final] if final],
        "delete_results": delete_results,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch10 customer answer contract regression.")
    parser.add_argument("--api-base", default=None)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    result = run(args.api_base, cleanup=not args.no_cleanup)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
