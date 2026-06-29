from __future__ import annotations

import argparse
import csv
import io
import json
import statistics
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

    def get_bytes(self, path: str, *, timeout: int = 120) -> dict[str, Any]:
        url = path if path.startswith("http") else f"{self.api_base}{path}"
        response = self.session.get(url, timeout=timeout)
        response.raise_for_status()
        content = response.content or b""
        return {
            "url": url,
            "status": response.status_code,
            "content_type": response.headers.get("content-type"),
            "bytes": len(content),
            "text_start": content[:500].decode("utf-8", errors="replace") if not content.startswith(b"%PDF") else "%PDF",
        }

    def delete(self, path: str, *, timeout: int = 60) -> dict[str, Any]:
        response = self.session.delete(f"{self.api_base}{path}", timeout=timeout)
        response.raise_for_status()
        return response.json()

    def post_json(self, path: str, payload: dict[str, Any], *, timeout: int = 420) -> dict[str, Any]:
        response = self.session.post(f"{self.api_base}{path}", json=payload, timeout=timeout)
        response.raise_for_status()
        return response.json()

    def upload_csv(self, path: Path, name: str) -> dict[str, Any]:
        with path.open("rb") as handle:
            files = [("file", (path.name, handle, "text/csv"))]
            response = self.session.post(f"{self.api_base}/api/upload", data={"name": name}, files=files, timeout=180)
        response.raise_for_status()
        return response.json()

    def collect_chat(
        self,
        workspace_id: str,
        message: str,
        *,
        conversation_id: str | None = None,
        artifact_mode: str | None = None,
        timeout: int = 480,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"workspace_id": workspace_id, "message": message}
        if conversation_id:
            payload["conversation_id"] = conversation_id
        if artifact_mode:
            payload["artifact_mode"] = artifact_mode
        events: list[dict[str, Any]] = []
        deltas: list[str] = []
        ready_conversation_id = conversation_id
        first_delta_seconds: float | None = None
        start = time.perf_counter()
        with self.session.post(
            f"{self.api_base}/api/chat",
            json=payload,
            headers={"Accept": "text/event-stream"},
            stream=True,
            timeout=(20, timeout),
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
                    data = parsed.get("data")
                    if parsed["event"] == "ready" and isinstance(data, dict):
                        ready_conversation_id = str(data.get("conversation_id") or ready_conversation_id or "")
                    if parsed["event"] == "answer_delta":
                        delta = str((data or {}).get("delta") if isinstance(data, dict) else "")
                        if delta:
                            if first_delta_seconds is None:
                                first_delta_seconds = round(time.perf_counter() - start, 3)
                            deltas.append(delta)
        final = _last_event(events, "final")
        route = _last_event(events, "route")
        followup = _last_event(events, "followup")
        return {
            "workspace_id": workspace_id,
            "conversation_id": ready_conversation_id,
            "message": message,
            "events": events,
            "event_names": [item["event"] for item in events],
            "role_change_count": sum(1 for item in events if item["event"] == "role_change"),
            "deltas": deltas,
            "first_delta_seconds": first_delta_seconds,
            "elapsed_seconds": round(time.perf_counter() - start, 3),
            "route": route,
            "followup": followup,
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
    session.trust_env = True
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
    reader = csv.DictReader(io.StringIO(response.text))
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


def _wait_for_ingest(harness: Harness, workspace_id: str, timeout: int = 300) -> dict[str, Any]:
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
        processing = any("processing" in item or "running" in item or "pending" in item for item in file_statuses)
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
            "conversation_id": chat.get("conversation_id"),
            "feasibility": artifact.get("feasibility") or {},
            "corpus": artifact.get("corpus") or {},
            "market": artifact.get("market") or {},
            "audit": artifact.get("audit") or {},
            "answer": artifact.get("answer") or {},
            "proposal": artifact.get("proposal") or {},
            "reference_images": artifact.get("reference_images") or [],
            "kinds": ["pdf", "concept_image", "pilot_plan", "action_plan"],
        },
        timeout=480,
    )


def _chat_summary(chat: dict[str, Any]) -> dict[str, Any]:
    final = chat.get("final") or {}
    artifact = final.get("artifact") or {}
    feasibility = artifact.get("feasibility") or {}
    return {
        "conversation_id": chat.get("conversation_id"),
        "mode": final.get("mode"),
        "route_mode": ((chat.get("route") or {}).get("meta") or {}).get("mode") or (chat.get("route") or {}).get("mode"),
        "route_fast_path": ((chat.get("route") or {}).get("meta") or {}).get("fast_path"),
        "followup": chat.get("followup"),
        "event_names": chat.get("event_names") or [],
        "role_change_count": chat.get("role_change_count"),
        "first_delta_seconds": chat.get("first_delta_seconds"),
        "elapsed_seconds": chat.get("elapsed_seconds"),
        "text_length": len(str(final.get("text") or "")),
        "verdict": feasibility.get("verdict"),
        "confidence": feasibility.get("overall_confidence"),
        "gap_count": len(feasibility.get("gap_list") or []),
        "artifact_mode": artifact.get("mode"),
        "artifact_gaps": artifact.get("gaps"),
        "artifact_clarify": artifact.get("clarify"),
    }


def _runs_summary(runs: list[dict[str, Any]]) -> dict[str, Any]:
    selected = runs[:8]
    titles = [str(item.get("title") or "") for item in selected]
    summaries = [str(item.get("summary") or "") for item in selected]
    return {
        "count": len(runs),
        "selected": [
            {
                "run_id": item.get("run_id"),
                "title": item.get("title"),
                "summary": item.get("summary"),
                "message": item.get("message"),
                "status": item.get("status"),
                "verdict": item.get("verdict"),
            }
            for item in selected
        ],
        "distinct_titles_in_selected": len(set(title for title in titles if title)),
        "all_selected_have_title_summary": all(title and summary for title, summary in zip(titles, summaries, strict=False)),
    }


def _observe_health(harness: Harness, minutes: float, interval_seconds: int) -> dict[str, Any]:
    if minutes <= 0:
        return {"enabled": False}
    deadline = time.time() + minutes * 60
    samples: list[dict[str, Any]] = []
    while True:
        sample_start = time.time()
        try:
            health = harness.get("/api/health", timeout=60)
            foundry = (health.get("dependency_details") or health.get("dependencies") or {}).get("foundry") or {}
            if not isinstance(foundry, dict):
                foundry = {"ok": bool(foundry), "state": "ok" if foundry else "down"}
            samples.append(
                {
                    "ok": health.get("ok"),
                    "foundry_ok": foundry.get("ok"),
                    "foundry_state": foundry.get("state"),
                    "foundry_error_type": foundry.get("error_type"),
                    "foundry_latency_ms": foundry.get("latency_ms"),
                    "observed_at": foundry.get("observed_at"),
                }
            )
        except Exception as exc:
            samples.append({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        if time.time() >= deadline:
            break
        time.sleep(max(1, interval_seconds - int(time.time() - sample_start)))
    bad = [
        item
        for item in samples
        if item.get("foundry_state") not in {"ok", "degraded"}
        or item.get("foundry_ok") is not True
        or item.get("ok") is not True
    ]
    return {
        "enabled": True,
        "minutes": minutes,
        "interval_seconds": interval_seconds,
        "sample_count": len(samples),
        "bad_sample_count": len(bad),
        "samples": samples,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-base", required=True)
    parser.add_argument("--dataset-url", default=DEFAULT_DATASET_URL)
    parser.add_argument("--rows", type=int, default=120)
    parser.add_argument("--output", default="artifacts/backend_fixes_live_eval.json")
    parser.add_argument("--keep-workspace", action="store_true")
    parser.add_argument("--observe-health-minutes", type=float, default=0)
    parser.add_argument("--health-interval-seconds", type=int, default=60)
    parser.add_argument("--health-only", action="store_true")
    args = parser.parse_args()

    harness = Harness(args.api_base)
    output_path = Path(args.output)
    dataset_path = output_path.with_suffix(".amazon_reviews_sample.csv")
    uploaded_workspace_id = ""
    result: dict[str, Any] = {"ok": False, "stage": "init", "api_base": args.api_base}
    try:
        if args.health_only:
            result["stage"] = "health_observe"
            health_observe = _observe_health(harness, args.observe_health_minutes, args.health_interval_seconds)
            checks = {
                "health_observe_enabled": bool(health_observe.get("enabled")),
                "health_observe_ok": int(health_observe.get("bad_sample_count") or 0) == 0,
            }
            result.update(
                {
                    "ok": all(checks.values()),
                    "stage": "done",
                    "checks": checks,
                    "health_observe": health_observe,
                }
            )
            return 0 if result.get("ok") else 1

        result["stage"] = "health_before"
        health_before = harness.get("/api/health", timeout=90)

        result["stage"] = "dataset"
        dataset = _download_dataset(args.dataset_url, args.rows, dataset_path)

        result["stage"] = "upload"
        upload = harness.upload_csv(dataset_path, "Backend fixes Amazon review noisy signal sample")
        uploaded_workspace_id = str(upload.get("workspace_id") or "")

        result["stage"] = "ingest"
        ingest = _wait_for_ingest(harness, uploaded_workspace_id)

        result["stage"] = "analysis"
        analysis = harness.collect_chat(
            uploaded_workspace_id,
            "Identify one product opportunity from these noisy Amazon review signals. Do not overstate feasibility; cite evidence strength, gaps, and the next validation step.",
            artifact_mode="report",
        )
        conversation_id = str(analysis.get("conversation_id") or "")

        result["stage"] = "followups"
        followup_messages = [
            "this one?",
            "What if we pilot this only with high-value customers?",
            "What evidence is missing before setting a price?",
            "Could support tickets be used as the first signal?",
            "Where would false positives hurt the rollout?",
        ]
        followups = [
            harness.collect_chat(uploaded_workspace_id, message, conversation_id=conversation_id, timeout=300)
            for message in followup_messages
        ]

        result["stage"] = "run_title_probes"
        run_title_probe_messages = [
            "In the uploaded reviews, which product issue appears most often?",
            "Which reviews mention installation friction or missing parts?",
            "Summarize price or value complaints in the review sample.",
            "Which evidence suggests a heavier-duty mount segment might matter?",
        ]
        run_title_probe_chats = [
            harness.collect_chat(uploaded_workspace_id, message, timeout=240)
            for message in run_title_probe_messages
        ]

        result["stage"] = "produce"
        produce = _produce_from_final(harness, uploaded_workspace_id, analysis)
        artifact_urls = produce.get("artifact_urls") or {}
        downloads = {
            key: harness.get_bytes(value)
            for key, value in artifact_urls.items()
            if key in {"pdf", "pilot_plan", "action_plan"} and isinstance(value, str)
        }

        result["stage"] = "runs"
        runs_response = harness.get(f"/api/runs?workspace_id={uploaded_workspace_id}", timeout=120)
        runs = [item for item in runs_response.get("runs") or [] if isinstance(item, dict)]

        result["stage"] = "health_observe"
        health_observe = _observe_health(harness, args.observe_health_minutes, args.health_interval_seconds)

        analysis_summary = _chat_summary(analysis)
        followup_summaries = [_chat_summary(item) for item in followups]
        run_title_probe_summaries = [_chat_summary(item) for item in run_title_probe_chats]
        followup_elapsed = [float(item.get("elapsed_seconds") or 0) for item in followup_summaries if item.get("elapsed_seconds")]
        median_followup_elapsed = round(statistics.median(followup_elapsed), 3) if followup_elapsed else None
        runs_summary = _runs_summary(runs)
        checks = {
            "health_before_ok": bool(health_before.get("ok")),
            "uploaded_real_noisy_reviews": dataset.get("rows", 0) >= 50 and bool(uploaded_workspace_id),
            "analysis_completed": analysis_summary["mode"] == "analysis" and analysis_summary["text_length"] > 300,
            "followups_completed": len(followup_summaries) == 5 and all(item["mode"] == "followup" for item in followup_summaries),
            "followups_lightweight": all(item["role_change_count"] == 0 for item in followup_summaries),
            "followup_clarifies_vague_input": bool(followup_summaries[0].get("artifact_clarify") or (followup_summaries[0].get("followup") or {}).get("clarify")),
            "followups_have_gaps": any(item.get("artifact_gaps") or (item.get("followup") or {}).get("gaps") for item in followup_summaries),
            "followup_faster_than_analysis": bool(median_followup_elapsed and analysis_summary["elapsed_seconds"] and median_followup_elapsed < analysis_summary["elapsed_seconds"]),
            "run_title_probes_completed": len(run_title_probe_summaries) == 4 and all(item["text_length"] > 80 for item in run_title_probe_summaries),
            "produce_pdf": bool(artifact_urls.get("pdf")) and (downloads.get("pdf") or {}).get("bytes", 0) > 500,
            "produce_pilot_plan": bool(artifact_urls.get("pilot_plan")) and "##" in str((downloads.get("pilot_plan") or {}).get("text_start") or ""),
            "produce_action_plan": bool(artifact_urls.get("action_plan")) and "##" in str((downloads.get("action_plan") or {}).get("text_start") or ""),
            "produce_concept_image_or_warning": bool(artifact_urls.get("concept_image"))
            or any(item.get("kind") == "concept_image" for item in produce.get("warnings") or []),
            "runs_have_titles_summaries": runs_summary["count"] >= 5 and runs_summary["all_selected_have_title_summary"],
            "runs_titles_distinct": runs_summary["distinct_titles_in_selected"] >= 5,
            "health_observe_ok": not health_observe.get("enabled") or int(health_observe.get("bad_sample_count") or 0) == 0,
        }
        result.update(
            {
                "ok": all(checks.values()),
                "stage": "done",
                "checks": checks,
                "health_before": health_before,
                "dataset": dataset,
                "upload": upload,
                "ingest": ingest,
                "analysis_summary": analysis_summary,
                "followup_summaries": followup_summaries,
                "run_title_probe_summaries": run_title_probe_summaries,
                "timing": {
                    "analysis_elapsed_seconds": analysis_summary["elapsed_seconds"],
                    "median_followup_elapsed_seconds": median_followup_elapsed,
                },
                "produce_summary": {
                    "artifact_urls": artifact_urls,
                    "degraded": produce.get("degraded"),
                    "warnings": produce.get("warnings"),
                    "pdf_mode": (produce.get("pdf") or {}).get("mode"),
                    "pilot_plan_bytes": (produce.get("pilot_plan") or {}).get("bytes"),
                    "action_plan_bytes": (produce.get("action_plan") or {}).get("bytes"),
                    "image_mode": (produce.get("concept_image") or {}).get("mode"),
                    "image_error": (produce.get("concept_image") or {}).get("error"),
                },
                "artifact_downloads": downloads,
                "runs_summary": runs_summary,
                "health_observe": health_observe,
            }
        )
    except Exception as exc:
        result.update({"ok": False, "stage": result.get("stage"), "error": f"{type(exc).__name__}: {exc}"})
    finally:
        if uploaded_workspace_id and not args.keep_workspace:
            try:
                result["cleanup"] = harness.delete(f"/api/workspaces/{uploaded_workspace_id}", timeout=120)
            except Exception as exc:
                result["cleanup_error"] = f"{type(exc).__name__}: {exc}"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
