from __future__ import annotations

import argparse
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
OUT = ROOT / "docs" / "batch9_polish_eval.json"
ENV_CANDIDATES = [
    Path(r"C:\Users\12140\.dataforge-codex.env"),
    ROOT.parent / "数据产品化Agent" / ".dataforge-codex.env",
]

sys.path.insert(0, str(ROOT))


class Harness:
    def __init__(self, api_base: str | None = None) -> None:
        self.api_base = api_base.rstrip("/") if api_base else None
        if self.api_base:
            self.client = None
        else:
            from backend.app import app

            self.client = TestClient(app)

    def upload(self, path: Path, *, name: str) -> dict[str, Any]:
        with path.open("rb") as handle:
            files = [("file", (path.name, handle, "application/json"))]
            data = {"name": name, "description": "Batch9 polish eval workspace"}
            if self.api_base:
                response = requests.post(f"{self.api_base}/api/upload", data=data, files=files, timeout=180)
            else:
                response = self.client.post("/api/upload", data=data, files=files)  # type: ignore[union-attr]
        response.raise_for_status()
        return response.json()

    def chat(self, workspace_id: str, message: str) -> list[dict[str, Any]]:
        payload = {"workspace_id": workspace_id, "message": message}
        if self.api_base:
            with requests.post(f"{self.api_base}/api/chat", json=payload, stream=True, timeout=260) as response:
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


def _demo_json(path: Path) -> Path:
    candidate = Path(r"D:\Demo\banana_climbing_shenzhen.json")
    if candidate.exists():
        return candidate
    target = path / "batch9_demo.json"
    target.write_text(
        json.dumps(
            {
                "signals": [
                    {"category": "活动", "topic": "会员裂变", "detail": "38% 会员愿意邀请朋友体验攀岩入门课"},
                    {"category": "周边", "topic": "Logo T恤", "detail": "打卡照片中品牌露出高，适合作为活动奖品"},
                    {"category": "赞助", "topic": "运动品牌", "detail": "本地运动品牌愿意赞助护手产品和体验券"},
                    {"category": "痛点", "topic": "手部护理", "detail": "新手用户反馈手部磨损，需要护手霜和教学提醒"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return target


def _final(events: list[dict[str, Any]]) -> dict[str, Any]:
    finals = [event["data"] for event in events if event.get("event") == "final"]
    return finals[-1] if finals else {}


def _citations(final: dict[str, Any]) -> list[dict[str, Any]]:
    artifact = final.get("artifact") or {}
    citations = artifact.get("citations") or (artifact.get("answer") or {}).get("citations") or []
    return [item for item in citations if isinstance(item, dict)]


def _route_intents(events: list[dict[str, Any]]) -> list[str]:
    return [str(event["data"].get("intent")) for event in events if event.get("event") == "route"]


def _title_check() -> dict[str, Any]:
    from backend.orchestrator import _clean_opportunity_label
    from backend.schemas import ChatRequest

    req = ChatRequest(workspace_id="upload-batch9", message="我想做一个活动让攀岩馆推广该怎么做")
    raw = "banana-climbing-shenzhen-banana-climbing-shenzhen-banana-climbing-shenzh"
    label = _clean_opportunity_label(raw, req, {"corpus": {"hits": []}})
    return {
        "raw": raw,
        "label": label,
        "not_repeated": label.lower().count("banana") <= 1,
        "readable_length": len(label) <= 60,
    }


def run(api_base: str | None = None, cleanup: bool = True) -> dict[str, Any]:
    loaded_env = _load_env() if api_base else []
    if not api_base:
        _prepare_local_env()
    harness = Harness(api_base)
    uploaded_ids: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        data_path = _demo_json(Path(tmp))
        upload = harness.upload(data_path, name=f"batch9 polish workspace {int(time.time())}")
        workspace_id = upload["workspace_id"]
        uploaded_ids.append(workspace_id)
        message = "我想做一个活动让攀岩馆推广该怎么做？请基于资料给出建议。"
        runs = [harness.chat(workspace_id, message) for _ in range(3)]

    finals = [_final(events) for events in runs]
    texts = [str(final.get("text") or "") for final in finals]
    citations = [_citations(final) for final in finals]
    intents = [_route_intents(events) for events in runs]
    clarify_counts = [sum(1 for event in events if event.get("event") == "clarify") for events in runs]
    errors = [event for events in runs for event in events if event.get("event") == "error"]
    title = _title_check()

    checks = {
        "no_errors": not errors,
        "three_runs_no_clarify": all(count == 0 for count in clarify_counts),
        "all_routed_to_answer": all(any(intent in {"corpus_qa", "feasibility_analysis"} for intent in item) for item in intents),
        "citations_at_least_three": all(len(items) >= 3 for items in citations),
        "natural_language_not_raw_dump": all("## 资料里浮现的信号" in text and "raw_docs/" not in text for text in texts),
        "opportunity_title_deduped": bool(title["not_repeated"] and title["readable_length"]),
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
        "workspace_id": uploaded_ids[0] if uploaded_ids else None,
        "clarify_counts": clarify_counts,
        "intents": intents,
        "citation_counts": [len(items) for items in citations],
        "title_check": title,
        "text_samples": [text[:900] for text in texts],
        "delete_results": delete_results,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-base")
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    result = run(args.api_base, cleanup=not args.keep)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
