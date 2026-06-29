from __future__ import annotations

import argparse
import csv
import io
import json
import time
from pathlib import Path
from typing import Any

import requests


DEFAULT_DATASET_URL = "https://raw.githubusercontent.com/imsreecharan/datasets_/master/amazon_reviews.csv"


class Harness:
    def __init__(self, api_base: str) -> None:
        self.api_base = api_base.rstrip("/")
        self.session = requests.Session()
        self.session.trust_env = False

    def get(self, path: str, *, timeout: int = 60) -> dict[str, Any]:
        response = self.session.get(f"{self.api_base}{path}", timeout=timeout)
        response.raise_for_status()
        return response.json()

    def delete(self, path: str, *, timeout: int = 60) -> dict[str, Any]:
        response = self.session.delete(f"{self.api_base}{path}", timeout=timeout)
        response.raise_for_status()
        return response.json()

    def post_json(self, path: str, payload: dict[str, Any], *, timeout: int = 360) -> dict[str, Any]:
        response = self.session.post(f"{self.api_base}{path}", json=payload, timeout=timeout)
        response.raise_for_status()
        return response.json()

    def upload_csv(self, path: Path, name: str) -> dict[str, Any]:
        with path.open("rb") as handle:
            files = [("file", (path.name, handle, "text/csv"))]
            response = self.session.post(f"{self.api_base}/api/upload", data={"name": name}, files=files, timeout=180)
        response.raise_for_status()
        return response.json()

    def collect_chat(self, workspace_id: str, message: str, *, timeout: int = 420) -> dict[str, Any]:
        events: list[dict[str, Any]] = []
        deltas: list[str] = []
        first_delta_seconds: float | None = None
        start = time.perf_counter()
        with self.session.post(
            f"{self.api_base}/api/chat",
            json={"workspace_id": workspace_id, "message": message},
            headers={"Accept": "text/event-stream"},
            stream=True,
            timeout=(15, timeout),
        ) as response:
            response.raise_for_status()
            buffer = ""
            for chunk in response.iter_content(chunk_size=None, decode_unicode=True):
                if not chunk:
                    continue
                buffer += chunk
                while "\n\n" in buffer:
                    frame, buffer = buffer.split("\n\n", 1)
                    parsed = _parse_sse_frame(frame)
                    if not parsed:
                        continue
                    events.append(parsed)
                    if parsed["event"] == "answer_delta":
                        delta = str((parsed.get("data") or {}).get("delta") or "")
                        if delta:
                            if first_delta_seconds is None:
                                first_delta_seconds = round(time.perf_counter() - start, 3)
                            deltas.append(delta)
        final = _last_event(events, "final")
        return {
            "workspace_id": workspace_id,
            "message": message,
            "events": events,
            "event_names": [item["event"] for item in events],
            "deltas": deltas,
            "first_delta_seconds": first_delta_seconds,
            "elapsed_seconds": round(time.perf_counter() - start, 3),
            "final": final,
        }


def _parse_sse_frame(frame: str) -> dict[str, Any] | None:
    event = ""
    data: Any = None
    for raw in frame.splitlines():
        if raw.startswith("event: "):
            event = raw.removeprefix("event: ").strip()
        elif raw.startswith("data: "):
            payload = raw.removeprefix("data: ")
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                data = payload
    if not event:
        return None
    return {"event": event, "data": data}


def _last_event(events: list[dict[str, Any]], event_name: str) -> dict[str, Any]:
    for item in reversed(events):
        if item.get("event") == event_name:
            data = item.get("data")
            return data if isinstance(data, dict) else {"value": data}
    return {}


def _download_dataset(url: str, rows: int, output: Path) -> dict[str, Any]:
    session = requests.Session()
    session.trust_env = False
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = session.get(url, timeout=60)
            response.raise_for_status()
            break
        except Exception as exc:
            last_error = exc
            if attempt == 2:
                raise
            time.sleep(1 + attempt)
    else:
        raise RuntimeError(f"dataset download failed: {last_error}")
    text = response.text
    reader = csv.DictReader(io.StringIO(text))
    selected: list[dict[str, Any]] = []
    for index, row in enumerate(reader):
        if index >= rows:
            break
        selected.append(
            {
                "asin": row.get("asin", ""),
                "overall": row.get("overall", ""),
                "helpful": row.get("helpful", ""),
                "reviewTime": row.get("reviewTime", ""),
                "reviewText": (row.get("reviewText") or "")[:900],
            }
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["asin", "overall", "helpful", "reviewTime", "reviewText"])
        writer.writeheader()
        writer.writerows(selected)
    return {"url": url, "rows": len(selected), "bytes": output.stat().st_size, "path": str(output)}


def _wait_for_ingest(harness: Harness, workspace_id: str, timeout: int = 240) -> dict[str, Any]:
    deadline = time.time() + timeout
    last: dict[str, Any] = {}
    while time.time() < deadline:
        try:
            last = harness.get(f"/api/workspaces/{workspace_id}/ingest-status", timeout=60)
        except Exception as exc:
            last = {"error": f"{type(exc).__name__}: {exc}"}
        status = str(last.get("status") or last.get("state") or "").lower()
        files = [item for item in last.get("files") or [] if isinstance(item, dict)]
        file_statuses = [str(item.get("status") or item.get("state") or "").lower() for item in files]
        indexed = sum(int(item.get("indexed_count") or 0) for item in files)
        processing = any(
            "processing" in status
            or "解析中" in status
            or "running" in status
            or "pending" in status
            for status in file_statuses
        )
        pct = float(last.get("pct") or 0)
        if status in {"complete", "completed", "ready", "idle", "done"} and not processing:
            return last
        if files and indexed > 0 and not processing:
            return last
        if pct >= 100 and not processing:
            return last
        time.sleep(5)
    return last


def _produce_from_final(harness: Harness, workspace_id: str, chat: dict[str, Any]) -> dict[str, Any]:
    artifact = (chat.get("final") or {}).get("artifact") or {}
    return harness.post_json(
        "/api/produce",
        {
            "workspace_id": workspace_id,
            "conversation_id": (chat.get("final") or {}).get("conversation_id"),
            "feasibility": artifact.get("feasibility") or {},
            "corpus": artifact.get("corpus") or {},
            "market": artifact.get("market") or {},
            "audit": artifact.get("audit") or {},
            "answer": artifact.get("answer") or {},
            "reference_images": artifact.get("reference_images") or [],
            "kinds": ["pdf", "concept_image"],
        },
        timeout=420,
    )


def _chat_summary(chat: dict[str, Any]) -> dict[str, Any]:
    final = chat.get("final") or {}
    artifact = final.get("artifact") or {}
    feasibility = artifact.get("feasibility") or {}
    verdict = artifact.get("verdict") or {}
    return {
        "event_names": chat.get("event_names") or [],
        "first_delta_seconds": chat.get("first_delta_seconds"),
        "elapsed_seconds": chat.get("elapsed_seconds"),
        "text_length": len(str(final.get("text") or "")),
        "verdict": feasibility.get("verdict"),
        "confidence": feasibility.get("overall_confidence"),
        "downgrade": verdict.get("downgrade") or artifact.get("verdict_downgrade"),
        "gap_count": len(feasibility.get("gap_list") or []),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-base", required=True)
    parser.add_argument("--dataset-url", default=DEFAULT_DATASET_URL)
    parser.add_argument("--rows", type=int, default=120)
    parser.add_argument("--output", default="artifacts/hardening_live_eval.json")
    parser.add_argument("--keep-workspace", action="store_true")
    args = parser.parse_args()

    harness = Harness(args.api_base)
    output_path = Path(args.output)
    dataset_path = output_path.with_suffix(".amazon_reviews_sample.csv")
    uploaded_workspace_id = ""
    result: dict[str, Any] = {"ok": False, "stage": "init", "api_base": args.api_base}
    try:
        result["stage"] = "health"
        health = harness.get("/api/health")
        result["stage"] = "dataset"
        dataset = _download_dataset(args.dataset_url, args.rows, dataset_path)
        result["stage"] = "upload"
        upload = harness.upload_csv(dataset_path, "Amazon reviews noisy signal sample")
        uploaded_workspace_id = str(upload.get("workspace_id") or "")
        result["stage"] = "ingest"
        ingest = _wait_for_ingest(harness, uploaded_workspace_id)

        result["stage"] = "amazon_analysis"
        amazon_chat = harness.collect_chat(
            uploaded_workspace_id,
            "Identify one product opportunity from these Amazon review signals. Do not overstate feasibility; cite evidence strength and gaps.",
        )
        result["stage"] = "produce"
        produce = _produce_from_final(harness, uploaded_workspace_id, amazon_chat)

        result["stage"] = "weak_evidence_downgrade"
        weak_chat = harness.collect_chat(
            "demo-corpus",
            "Can this workspace justify a clinical diagnosis product? If the evidence is weak, downgrade the verdict and explain the evidence gap.",
        )

        amazon_summary = _chat_summary(amazon_chat)
        weak_summary = _chat_summary(weak_chat)
        checks = {
            "health_ok": bool(health.get("ok")),
            "uploaded_real_noisy_reviews": dataset.get("rows", 0) >= 50 and bool(uploaded_workspace_id),
            "analysis_final": amazon_summary["text_length"] > 200 and amazon_summary["verdict"] in {"feasible", "conditional", "not_yet_feasible"},
            "produce_pdf": bool((produce.get("artifact_urls") or {}).get("pdf")),
            "produce_concept_image_or_warning": bool((produce.get("artifact_urls") or {}).get("concept_image"))
            or any(item.get("kind") == "concept_image" for item in produce.get("warnings") or []),
            "weak_final": weak_summary["text_length"] > 200 and weak_summary["verdict"] in {"conditional", "not_yet_feasible"},
            "weak_has_downgrade_or_guardrail_gap": bool(weak_summary.get("downgrade")) or weak_summary["gap_count"] > 0,
        }
        result.update(
            {
                "ok": all(checks.values()),
                "stage": "done",
                "checks": checks,
                "health_dependencies": health.get("dependencies"),
                "dataset": dataset,
                "upload": upload,
                "ingest": ingest,
                "amazon_summary": amazon_summary,
                "weak_summary": weak_summary,
                "produce_summary": {
                    "artifact_urls": produce.get("artifact_urls"),
                    "degraded": produce.get("degraded"),
                    "warnings": produce.get("warnings"),
                    "pdf_mode": (produce.get("pdf") or {}).get("mode"),
                    "image_mode": (produce.get("concept_image") or {}).get("mode"),
                    "image_error": (produce.get("concept_image") or {}).get("error"),
                },
            }
        )
    except Exception as exc:
        result.update({"ok": False, "stage": result.get("stage"), "error": f"{type(exc).__name__}: {exc}"})
    finally:
        if uploaded_workspace_id and not args.keep_workspace:
            try:
                result["cleanup"] = harness.delete(f"/api/workspaces/{uploaded_workspace_id}")
            except Exception as exc:
                result["cleanup_error"] = f"{type(exc).__name__}: {exc}"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
