from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any

import requests
from fastapi.testclient import TestClient
from openpyxl import Workbook


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "full_product_eval.json"
ENV_CANDIDATES = [
    Path(r"C:\Users\12140\.dataforge-codex.env"),
    ROOT.parent / "\u6570\u636e\u4ea7\u54c1\u5316Agent" / ".dataforge-codex.env",
]

sys.path.insert(0, str(ROOT))

from backend.app import app  # noqa: E402
from backend.orchestrator import orchestrate_chat  # noqa: E402
from backend.schemas import ChatRequest  # noqa: E402


logging.getLogger("dataforge.trace").setLevel(logging.WARNING)


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


def _make_files(root: Path) -> dict[str, Path]:
    csv_path = root / "growth_channels.csv"
    csv_path.write_text(
        "\n".join(
            [
                "region,segment,monthly_revenue,churn_rate,expansion_score,notes",
                "East,enterprise,188000,0.025,91,renewal expansion requests mention dashboard packaging",
                "South,midmarket,94000,0.083,54,training gaps slow rollout",
                "West,enterprise,143000,0.039,82,partner channel asks for benchmark reports",
                "North,smb,39000,0.118,31,high support load and weak upsell signal",
            ]
        ),
        encoding="utf-8",
    )
    json_path = root / "support_workflows.json"
    json_path.write_text(
        json.dumps(
            {
                "tickets": [
                    {"category": "billing", "resolution_hours": 7, "sentiment": "negative", "automation_gap": "contract explanation"},
                    {"category": "deployment", "resolution_hours": 21, "sentiment": "neutral", "automation_gap": "environment checklist"},
                    {"category": "training", "resolution_hours": 5, "sentiment": "positive", "automation_gap": "guided playbook"},
                    {"category": "deployment", "resolution_hours": 28, "sentiment": "negative", "automation_gap": "log triage"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    excel_path = root / "factory_quality.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "QualitySignals"
    sheet.append(["line", "shift", "defect_rate", "rework_cost", "supplier", "inspection_note"])
    sheet.append(["A", "day", 0.021, 3200, "S1", "stable output with low rework"])
    sheet.append(["A", "night", 0.064, 14200, "S2", "weld drift clusters at night shift"])
    sheet.append(["B", "day", 0.018, 2500, "S1", "low defect baseline"])
    sheet.append(["B", "night", 0.079, 19600, "S3", "incoming batch variance drives rework"])
    workbook.save(excel_path)
    return {"csv": csv_path, "json": json_path, "excel": excel_path}


def _mime(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return "text/csv"
    if suffix == ".json":
        return "application/json"
    if suffix == ".xlsx":
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return "application/octet-stream"


class ApiHarness:
    def __init__(self, api_base: str = "") -> None:
        self.api_base = api_base.rstrip("/")
        self.client = None if api_base else TestClient(app)

    def upload(self, path: Path, name: str) -> dict[str, Any]:
        with path.open("rb") as handle:
            if self.client:
                response = self.client.post(
                    "/api/upload",
                    data={"name": name},
                    files={"file": (path.name, handle, _mime(path))},
                )
            else:
                response = requests.post(
                    f"{self.api_base}/api/upload",
                    data={"name": name},
                    files={"file": (path.name, handle, _mime(path))},
                    timeout=180,
                )
        response.raise_for_status()
        return response.json()

    def get(self, path: str, **params: Any) -> dict[str, Any]:
        if self.client:
            response = self.client.get(path, params=params)
        else:
            response = requests.get(f"{self.api_base}{path}", params=params, timeout=90)
        response.raise_for_status()
        return response.json()

    def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self.client:
            response = self.client.post(path, json=payload)
        else:
            response = requests.post(f"{self.api_base}{path}", json=payload, timeout=240)
        response.raise_for_status()
        return response.json()

    def delete(self, path: str) -> dict[str, Any]:
        if self.client:
            response = self.client.delete(path)
        else:
            response = requests.delete(f"{self.api_base}{path}", timeout=180)
        response.raise_for_status()
        return response.json()

    def artifact_head(self, artifact_url: str | None) -> dict[str, Any]:
        if not artifact_url:
            return {"ok": False, "status_code": None, "content_type": None, "bytes": 0}
        if self.client:
            response = self.client.get(artifact_url)
            return {
                "ok": response.status_code == 200 and len(response.content) > 0,
                "status_code": response.status_code,
                "content_type": response.headers.get("content-type"),
                "bytes": len(response.content),
            }
        response = requests.get(f"{self.api_base}{artifact_url}", timeout=120)
        return {
            "ok": response.status_code == 200 and len(response.content) > 0,
            "status_code": response.status_code,
            "content_type": response.headers.get("content-type"),
            "bytes": len(response.content),
        }

    async def collect_chat(self, workspace_id: str, message: str) -> dict[str, Any]:
        if self.client:
            return await _collect_local(workspace_id, message, self)
        return _collect_http(self.api_base, workspace_id, message)


async def _collect_local(workspace_id: str, message: str, harness: ApiHarness) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    deltas: list[str] = []
    health_latency: float | None = None
    started = time.perf_counter()
    first_delta_at: float | None = None
    async for frame in orchestrate_chat(ChatRequest(workspace_id=workspace_id, message=message)):
        parsed = _parse_sse_frame(frame)
        if not parsed:
            continue
        events.append(parsed)
        if parsed["event"] == "answer_delta":
            deltas.append(str(parsed["data"].get("delta") or ""))
            if first_delta_at is None:
                first_delta_at = time.perf_counter() - started
                health_latency = _health_latency(harness)
    return {
        "events": events,
        "deltas": deltas,
        "first_delta_seconds": first_delta_at,
        "health_latency_seconds": health_latency,
        "elapsed_seconds": time.perf_counter() - started,
    }


def _collect_http(api_base: str, workspace_id: str, message: str) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    deltas: list[str] = []
    current_event: str | None = None
    current_data = ""
    stream_warning = ""
    started = time.perf_counter()
    first_delta_at: float | None = None
    health_latency: float | None = None
    response = requests.post(
        f"{api_base}/api/chat",
        json={"workspace_id": workspace_id, "message": message},
        stream=True,
        timeout=420,
    )
    response.raise_for_status()
    try:
        for raw in response.iter_lines(decode_unicode=True):
            if raw is None:
                continue
            if raw == "":
                if current_event:
                    parsed = _parse_sse_parts(current_event, current_data)
                    events.append(parsed)
                    if parsed["event"] == "answer_delta":
                        delta = str(parsed["data"].get("delta") or "")
                        deltas.append(delta)
                        if first_delta_at is None:
                            first_delta_at = time.perf_counter() - started
                            health_latency = _http_health_latency(api_base)
                current_event = None
                current_data = ""
                continue
            if raw.startswith("event: "):
                current_event = raw.removeprefix("event: ")
            elif raw.startswith("data: "):
                current_data += raw.removeprefix("data: ")
    except requests.exceptions.ChunkedEncodingError as exc:
        if current_event:
            events.append(_parse_sse_parts(current_event, current_data))
        if not any(item["event"] == "final" for item in events):
            raise
        stream_warning = f"stream ended after final with {type(exc).__name__}: {exc}"
    return {
        "events": events,
        "deltas": deltas,
        "first_delta_seconds": first_delta_at,
        "health_latency_seconds": health_latency,
        "elapsed_seconds": time.perf_counter() - started,
        "stream_warning": stream_warning,
    }


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
    return _parse_sse_parts(event, data)


def _parse_sse_parts(event: str, data: str) -> dict[str, Any]:
    try:
        payload: Any = json.loads(data) if data else {}
    except json.JSONDecodeError:
        payload = {"text": data}
    return {"event": event, "data": payload}


def _health_latency(harness: ApiHarness) -> float:
    samples: list[float] = []
    for _ in range(3):
        start = time.perf_counter()
        harness.get("/api/health")
        samples.append(time.perf_counter() - start)
        time.sleep(0.1)
    return min(samples)


def _http_health_latency(api_base: str) -> float:
    samples: list[float] = []
    last_error: Exception | None = None
    with requests.Session() as session:
        for _ in range(4):
            start = time.perf_counter()
            try:
                response = session.get(f"{api_base}/api/health", timeout=30)
                response.raise_for_status()
                samples.append(time.perf_counter() - start)
            except Exception as exc:
                last_error = exc
            time.sleep(0.1)
    if samples:
        return min(samples)
    if last_error:
        raise last_error
    raise RuntimeError("health check produced no samples")


def _events(data: dict[str, Any], event: str) -> list[dict[str, Any]]:
    return [item["data"] for item in data["events"] if item["event"] == event]


def _final(data: dict[str, Any]) -> dict[str, Any]:
    finals = _events(data, "final")
    return finals[-1] if finals else {}


def _proposal(final: dict[str, Any]) -> dict[str, Any]:
    return final.get("artifact", {}).get("proposal", {})


def _artifact_checks(harness: ApiHarness, proposal: dict[str, Any]) -> dict[str, Any]:
    urls = proposal.get("artifact_urls") or {}
    pdf = proposal.get("pdf") or {}
    image = proposal.get("concept_image") or {}
    audio = proposal.get("audio_summary") or {}
    return {
        "pdf": {
            "mode": pdf.get("mode"),
            "pdf_error": pdf.get("pdf_error"),
            "fetch": harness.artifact_head(urls.get("pdf")),
        },
        "image": {
            "mode": image.get("mode"),
            "image_error": image.get("image_error"),
            "fetch": harness.artifact_head(urls.get("concept_image")),
        },
        "audio": {
            "mode": audio.get("mode"),
            "speech_error": audio.get("speech_error"),
            "fetch": harness.artifact_head(urls.get("audio_summary")),
        },
    }


def _confidence_ok(final: dict[str, Any]) -> bool:
    feasibility = final.get("artifact", {}).get("feasibility", {})
    labels = {"data_confirmed", "market_inferred", "speculative"}
    if feasibility.get("overall_confidence") not in labels:
        return False
    return all((item.get("confidence") in labels) for item in feasibility.get("dimensions") or [])


def _market_ok(final: dict[str, Any]) -> bool:
    market = final.get("artifact", {}).get("market") or {}
    findings = market.get("external_findings") or []
    if findings:
        return all(item.get("confidence") == "market_inferred" for item in findings)
    mode = (market.get("_llm") or {}).get("mode")
    return mode in {"foundry_web_unavailable", "web_search_unavailable"}


def _web_trace_seen(data: dict[str, Any]) -> bool:
    for item in data["events"]:
        payload = item["data"]
        if payload.get("agent") == "df-market-researcher" and payload.get("name") == "foundry_native_web_search":
            return True
    return False


def _stream_text_ok(data: dict[str, Any]) -> bool:
    final = _final(data)
    return bool(data["deltas"]) and "".join(data["deltas"]) == final.get("text")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-base", default="", help="Cloud backend base URL, for example https://...azurecontainerapps.io")
    parser.add_argument("--output", default=str(OUT), help="Path to write the evaluation JSON report")
    args = parser.parse_args()
    loaded_env = _load_env()
    harness = ApiHarness(args.api_base)
    uploaded_ids: list[str] = []

    result: dict[str, Any] = {
        "ok": False,
        "mode": "cloud" if args.api_base else "local",
        "api_base": args.api_base or "in-process",
        "loaded_env": loaded_env,
        "stage": "init",
    }
    stage = "init"
    try:
        stage = "upload"
        result["stage"] = stage
        with tempfile.TemporaryDirectory() as tmp:
            files = _make_files(Path(tmp))
            uploads = {
                fmt: harness.upload(path, f"full eval {fmt} {path.stem}")
                for fmt, path in files.items()
            }
            uploaded_ids = [item["workspace_id"] for item in uploads.values()]

        stage = "workspace_details"
        result["stage"] = stage
        workspace_details = {
            fmt: harness.get(f"/api/workspaces/{upload['workspace_id']}")
            for fmt, upload in uploads.items()
        }
        stage = "search"
        result["stage"] = stage
        search_checks = {
            "csv": harness.post_json(
                "/api/search-pack-context",
                {"workspace_id": uploads["csv"]["workspace_id"], "query": "enterprise expansion benchmark reports", "top_k": 5},
            ),
            "json": harness.post_json(
                "/api/search-pack-context",
                {"workspace_id": uploads["json"]["workspace_id"], "query": "deployment resolution_hours log triage", "top_k": 5},
            ),
            "excel": harness.post_json(
                "/api/search-pack-context",
                {"workspace_id": uploads["excel"]["workspace_id"], "query": "night shift weld drift rework cost", "top_k": 5},
            ),
        }
        stage = "preflight_health"
        result["stage"] = stage
        preflight_health = harness.get("/api/health")

        stage = "zh_chat_full_package"
        result["stage"] = stage
        zh = await harness.collect_chat(
            uploads["csv"]["workspace_id"],
            "\u8bf7\u57fa\u4e8e\u8fd9\u4e2a\u5de5\u4f5c\u533a\u8bc4\u4f30\u9500\u552e\u8fd0\u8425\u6570\u636e\u4ea7\u54c1\uff0c\u5e76\u751f\u6210 PDF\u3001\u6982\u5ff5\u56fe\u548c\u8bed\u97f3\u4e09\u4ef6\u5957\u3002",
        )
        stage = "en_chat"
        result["stage"] = stage
        en = await harness.collect_chat(
            uploads["json"]["workspace_id"],
            "Evaluate a support workflow automation data product from this workspace and explain the feasibility.",
        )
        zh_final = _final(zh)
        en_final = _final(en)
        proposal = _proposal(zh_final)
        stage = "artifact_fetch"
        result["stage"] = stage
        artifact_checks = _artifact_checks(harness, proposal)

        run_id = (_events(zh, "ready")[-1] if _events(zh, "ready") else {}).get("conversation_id")
        stage = "runs"
        result["stage"] = stage
        run_detail = harness.get(f"/api/runs/{run_id}") if run_id else {}
        run_list = harness.get("/api/runs", workspace_id=uploads["csv"]["workspace_id"])
        stage = "health"
        result["stage"] = stage
        health = harness.get("/api/health")
        stage = "produce"
        result["stage"] = stage
        produce_again = harness.post_json(
            "/api/produce",
            {
                "workspace_id": uploads["csv"]["workspace_id"],
                "conversation_id": run_id,
                "feasibility": zh_final.get("artifact", {}).get("feasibility", {}),
                "corpus": zh_final.get("artifact", {}).get("corpus", {}),
                "market": zh_final.get("artifact", {}).get("market", {}),
                "audit": zh_final.get("artifact", {}).get("audit", {}),
                "answer": zh_final.get("artifact", {}).get("answer", {}),
            },
        )

        delete_results = {}
        delete_search_checks = {}
        stage = "delete_cleanup"
        result["stage"] = stage
        for workspace_id in uploaded_ids:
            delete_results[workspace_id] = harness.delete(f"/api/workspaces/{workspace_id}")
            delete_search_checks[workspace_id] = harness.post_json(
                "/api/search-pack-context",
                {"workspace_id": workspace_id, "query": "enterprise deployment defect", "top_k": 3},
            )
        remaining = harness.get("/api/workspaces")

        checks = {
            "uploads_csv_json_excel": set(uploads) == {"csv", "json", "excel"} and all(item.get("indexed_count", 0) > 0 for item in uploads.values()),
            "workspace_details_profiled": all(detail.get("doc_count", 0) > 0 and detail.get("columns") for detail in workspace_details.values()),
            "search_each_workspace": all(payload.get("count", 0) > 0 for payload in search_checks.values()),
            "zh_stream_delta_equals_final": _stream_text_ok(zh),
            "en_stream_delta_equals_final": _stream_text_ok(en),
            "health_under_1s_during_stream": (zh.get("health_latency_seconds") is not None and zh["health_latency_seconds"] < 1.0),
            "confidence_labels": _confidence_ok(zh_final) and _confidence_ok(en_final),
            "web_trace_seen": _web_trace_seen(zh),
            "market_confidence_or_graceful_degrade": _market_ok(zh_final),
            "pdf_reportlab_fetchable": artifact_checks["pdf"]["mode"] == "reportlab-project-proposal" and artifact_checks["pdf"]["fetch"]["ok"],
            "image_real_gpt_image_2": artifact_checks["image"]["mode"] == "gpt-image-2"
            and artifact_checks["image"]["fetch"]["ok"],
            "audio_real_azure_speech": artifact_checks["audio"]["mode"] == "azure-speech"
            and artifact_checks["audio"]["fetch"]["ok"],
            "produce_endpoint_outputs": bool(produce_again.get("artifact_urls", {}).get("pdf"))
            and bool(produce_again.get("artifact_urls", {}).get("concept_image"))
            and bool(produce_again.get("artifact_urls", {}).get("audio_summary")),
            "runs_list_and_detail": bool(run_detail.get("steps")) and any(item.get("run_id") == run_id for item in run_list.get("runs", [])),
            "health_dependencies_present": set((health.get("dependencies") or {}).keys()) >= {"foundry", "mcp", "speech", "blob"},
            "delete_cleanup": all(item.get("deleted") for item in delete_results.values())
            and not any(item.get("workspace_id") in uploaded_ids for item in remaining.get("workspaces", []))
            and all(payload.get("count", 0) == 0 for payload in delete_search_checks.values()),
        }

        result.update(
            {
                "ok": all(checks.values()),
                "checks": checks,
                "uploads": uploads,
                "workspace_details": workspace_details,
                "search_counts": {key: value.get("count") for key, value in search_checks.items()},
                "zh_events": [item["event"] for item in zh["events"]],
                "en_events": [item["event"] for item in en["events"]],
                "zh_timing": {
                    "first_delta_seconds": zh.get("first_delta_seconds"),
                    "health_latency_seconds": zh.get("health_latency_seconds"),
                    "elapsed_seconds": zh.get("elapsed_seconds"),
                },
                "en_timing": {
                    "first_delta_seconds": en.get("first_delta_seconds"),
                    "health_latency_seconds": en.get("health_latency_seconds"),
                    "elapsed_seconds": en.get("elapsed_seconds"),
                },
                "zh_final_summary": {
                    "text_length": len(str(zh_final.get("text") or "")),
                    "verdict": zh_final.get("artifact", {}).get("feasibility", {}).get("verdict"),
                    "confidence": zh_final.get("artifact", {}).get("feasibility", {}).get("overall_confidence"),
                },
                "en_final_summary": {
                    "text_length": len(str(en_final.get("text") or "")),
                    "verdict": en_final.get("artifact", {}).get("feasibility", {}).get("verdict"),
                    "confidence": en_final.get("artifact", {}).get("feasibility", {}).get("overall_confidence"),
                },
                "market": zh_final.get("artifact", {}).get("market"),
                "artifact_checks": artifact_checks,
                "produce_again_modes": {
                    "pdf": (produce_again.get("pdf") or {}).get("mode"),
                    "image": (produce_again.get("concept_image") or {}).get("mode"),
                    "audio": (produce_again.get("audio_summary") or {}).get("mode"),
                },
                "run_id": run_id,
                "run_detail_summary": {
                    "status": run_detail.get("status"),
                    "step_count": run_detail.get("step_count") or len(run_detail.get("steps") or []),
                    "answer_delta_summary": run_detail.get("answer_delta_summary"),
                    "model_count": len(run_detail.get("models") or []),
                },
                "health": health,
                "preflight_health": preflight_health,
                "delete_results": delete_results,
                "delete_search_counts": {key: value.get("count") for key, value in delete_search_checks.items()},
            }
        )
    except Exception as exc:
        result.update(
            {
                "ok": False,
                "stage": stage,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(limit=8),
                "uploaded_ids_before_error": uploaded_ids,
            }
        )
    finally:
        for workspace_id in uploaded_ids:
            try:
                harness.delete(f"/api/workspaces/{workspace_id}")
            except Exception:
                pass
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
