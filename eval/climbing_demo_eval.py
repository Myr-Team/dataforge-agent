from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterable

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


DEFAULT_MESSAGE = (
    "我们要基于当前攀岩馆数据策划一场拉新活动。目标是提升新客到店和会员复购；"
    "请先判断是否还缺关键转化目标，然后给出活动方向、证据、风险、下一步，"
    "并说明是否建议生成项目书、活动海报和周边设计图。"
)

MOJIBAKE_RE = re.compile(r"[锛鎴涓绋璧鏁]{2,}|鈥|�")
INTERNAL_RE = re.compile(r"raw_docs/|source_file|chunk_id|content_vector|workspace_id|data_confirmed|market_inferred|speculative")
NEXT_STEP_RE = re.compile(r"下一步|建议|可以先|先做|验证|试点|生成|项目书|海报|周边")


def upload_file(api_base: str, path: Path, *, name: str, workspace_id: str | None = None, asset_role: str | None = None, timeout: int = 180) -> dict[str, Any]:
    data: dict[str, str] = {"name": name}
    if workspace_id:
        data["workspace_id"] = workspace_id
    if asset_role:
        data["asset_role"] = asset_role
    with path.open("rb") as handle:
        files = [("file", (path.name, handle, content_type(path)))]
        response = requests.post(f"{api_base.rstrip('/')}/api/upload", data=data, files=files, timeout=timeout)
    response.raise_for_status()
    return response.json()


def content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return "application/json"
    if suffix == ".csv":
        return "text/csv"
    if suffix in {".xlsx", ".xls"}:
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if suffix == ".png":
        return "image/png"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    return "application/octet-stream"


def parse_sse(lines: Iterable[str]) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    current_event = ""
    for raw in lines:
        line = raw.strip()
        if not line:
            current_event = ""
            continue
        if line.startswith("event:"):
            current_event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            payload = line.split(":", 1)[1].strip()
            try:
                data: dict[str, Any] = json.loads(payload)
            except json.JSONDecodeError:
                data = {"raw": payload}
            events.append((current_event or "message", data))
            if events[-1][0] in {"final", "clarify", "error"}:
                break
    return events


def run_chat(api_base: str, workspace_id: str, message: str, timeout: int) -> dict[str, Any]:
    payload = {
        "workspace_id": workspace_id,
        "message": message,
        "playbook": "experiment",
        "artifact_mode": "report",
        "ui_context": {"demo": "banana_climbing_campaign", "customer_stage": "campaign_planning"},
    }
    started = time.perf_counter()
    response = requests.post(f"{api_base.rstrip('/')}/api/chat", json=payload, stream=True, timeout=timeout)
    response.raise_for_status()
    events = parse_sse(response.iter_lines(decode_unicode=True))
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return analyze_events(events, elapsed_ms)


def analyze_events(events: list[tuple[str, dict[str, Any]]], elapsed_ms: int) -> dict[str, Any]:
    deltas = "".join(str(data.get("delta") or "") for event, data in events if event == "answer_delta")
    final = next((data for event, data in reversed(events) if event == "final"), {})
    clarify = next((data for event, data in reversed(events) if event == "clarify"), {})
    final_text = str(final.get("text") or "")
    clarify_text = str(clarify.get("question") or "")
    visible = "\n".join([deltas, final_text, clarify_text])
    artifact = final.get("artifact") if isinstance(final, dict) else {}
    artifact = artifact if isinstance(artifact, dict) else {}
    tool_calls = [data for event, data in events if event == "tool_call"]
    checks = {
        "no_event_error": not any(event == "error" for event, _ in events),
        "delta_matches_final": not deltas or deltas == final_text,
        "has_final_or_clarify": bool(final_text or clarify_text),
        "has_next_step": bool(NEXT_STEP_RE.search(visible)),
        "no_mojibake": not MOJIBAKE_RE.search(visible),
        "no_internal_terms": not INTERNAL_RE.search(visible),
        "has_reference_to_campaign_context": any(token in visible for token in ["攀岩", "活动", "新客", "会员", "周边", "海报", "赞助"]),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "event_count": len(events),
        "events": [event for event, _ in events],
        "tool_calls": [{"agent": item.get("agent"), "name": item.get("name")} for item in tool_calls],
        "final_text_chars": len(final_text),
        "clarify_text_chars": len(clarify_text),
        "elapsed_ms": elapsed_ms,
        "artifact_keys": sorted(artifact.keys()),
    }


def produce(api_base: str, workspace_id: str, timeout: int) -> dict[str, Any]:
    payload = {
        "workspace_id": workspace_id,
        "feasibility": {
            "opportunity_id": "banana-climbing-campaign",
            "verdict": "pilot_first",
            "overall_confidence": "medium",
            "dimensions": [
                {"name": "customer_need", "score": 4, "confidence": "data_confirmed"},
                {"name": "go_to_market", "score": 4, "confidence": "data_confirmed"},
            ],
            "gap_list": ["需要确认本场活动的目标转化率与预算上限。"],
        },
        "corpus": {"profile": {"profile_summary": "攀岩馆运营、会员、社媒、周边和活动复盘数据。"}},
        "market": {"external_findings": []},
        "answer": {
            "text": "建议先做一场围绕攀岩馆会员周边、社交传播和新客体验的拉新活动，并用透明 PNG Logo 生成海报与周边设计图。"
        },
    }
    response = requests.post(f"{api_base.rstrip('/')}/api/produce", json=payload, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    urls = data.get("artifact_urls") or {}
    return {
        "ok": bool(urls.get("pdf") and urls.get("concept_image") and urls.get("audio_summary")),
        "artifact_urls": urls,
        "image_mode": (data.get("concept_image") or {}).get("mode"),
        "image_reference_count": (data.get("concept_image") or {}).get("reference_image_count"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Banana Climbing cloud demo flow.")
    parser.add_argument("--api-base", default="http://127.0.0.1:8000")
    parser.add_argument("--data", default="D:/Demo/banana_climbing_shenzhen.json")
    parser.add_argument("--reference-image", default="D:/Demo/climb.png")
    parser.add_argument("--message", default=DEFAULT_MESSAGE)
    parser.add_argument("--workspace-name", default=f"Banana Climbing Demo {int(time.time())}")
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--produce", action="store_true")
    parser.add_argument("--out", default=str(ROOT / "docs" / "climbing_demo_eval.json"))
    args = parser.parse_args()

    api_base = args.api_base.rstrip("/")
    data_path = Path(args.data)
    image_path = Path(args.reference_image)
    if not data_path.exists():
        raise FileNotFoundError(data_path)
    if not image_path.exists():
        raise FileNotFoundError(image_path)

    uploaded = upload_file(api_base, data_path, name=args.workspace_name, timeout=args.timeout)
    workspace_id = uploaded["workspace_id"]
    reference = upload_file(api_base, image_path, name=args.workspace_name, workspace_id=workspace_id, asset_role="logo", timeout=args.timeout)
    chat = run_chat(api_base, workspace_id, args.message, args.timeout)
    produced = produce(api_base, workspace_id, args.timeout) if args.produce else {}
    summary = {
        "api_base": api_base,
        "workspace_id": workspace_id,
        "workspace_name": reference.get("name") or uploaded.get("name"),
        "uploaded": {
            "format": uploaded.get("format"),
            "indexed_count": uploaded.get("indexed_count"),
            "documents": len(uploaded.get("documents") or []),
        },
        "reference_images": len(reference.get("reference_images") or []),
        "chat": chat,
        "produce": produced,
        "passed": bool(uploaded.get("workspace_id") and reference.get("reference_images") and chat.get("passed") and (not args.produce or produced.get("ok"))),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": summary["passed"], "workspace_id": workspace_id, "out": str(out)}, ensure_ascii=False))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
