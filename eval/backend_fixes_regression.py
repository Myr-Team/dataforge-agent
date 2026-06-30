from __future__ import annotations

import io
import os
import sys
import time
import urllib.error
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import dependency_health, orchestrator, run_store  # noqa: E402
from backend.schemas import ChatRequest  # noqa: E402


def _http_error(status: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://foundry.example/openai/models", status, "transient", {}, io.BytesIO(b"transient"))


def test_foundry_probe_transient_failure_uses_recent_success() -> None:
    original_request = dependency_health._request_status_sample
    original_sleep = dependency_health.time.sleep
    original_env = {key: os.environ.get(key) for key in ("OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY")}
    dependency_health._LAST_OK.clear()
    try:
        os.environ["OPENAI_ENDPOINT"] = "https://foundry.example"
        os.environ["AZURE_OPENAI_API_KEY"] = "test-key"
        dependency_health.time.sleep = lambda _: None

        calls = {"count": 0}

        def ok_sample(*_: Any, **__: Any) -> tuple[int, int]:
            calls["count"] += 1
            return 200, 64

        dependency_health._request_status_sample = ok_sample
        first = dependency_health._timed_probe("foundry", dependency_health._probe_foundry)
        assert first["ok"] is True
        assert first["state"] == "ok"
        assert calls["count"] == 1

        calls["count"] = 0

        def rate_limited(*_: Any, **__: Any) -> tuple[int, int]:
            calls["count"] += 1
            raise _http_error(429)

        dependency_health._request_status_sample = rate_limited
        second = dependency_health._timed_probe("foundry", dependency_health._probe_foundry)
        assert calls["count"] == 2
        assert second["ok"] is True
        assert second["state"] == "degraded"
        assert second["error_type"] == "rate_limited"
        assert second["degraded_reason"] == "transient_probe_failure_recent_success"
    finally:
        dependency_health._request_status_sample = original_request
        dependency_health.time.sleep = original_sleep
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        dependency_health._LAST_OK.clear()


def test_foundry_outer_timeout_uses_recent_success() -> None:
    original_probe_all = dependency_health._probe_all
    original_future_timeout = dependency_health._probe_future_timeout
    original_record = dependency_health._record_probe
    dependency_health._LAST_OK.clear()
    dependency_health._PROBE_HISTORY.clear()
    try:
        dependency_health._LAST_OK["foundry"] = {"at": time.time(), "detail": {"ok": True, "state": "ok"}}
        dependency_health._probe_future_timeout = lambda name: 0.01
        records: list[dict[str, Any]] = []
        dependency_health._record_probe = lambda name, detail: records.append({"name": name, **detail})

        def slow_foundry() -> dict[str, Any]:
            time.sleep(0.05)
            return {"ok": True, "state": "ok", "error_type": "none"}

        def ok_probe() -> dict[str, Any]:
            return {"ok": True, "state": "ok", "error_type": "none"}

        def probe_all_with_slow_foundry() -> dict[str, dict[str, Any]]:
            probes = {
                "foundry": slow_foundry,
                "search": ok_probe,
                "mcp": ok_probe,
                "speech": ok_probe,
                "blob": ok_probe,
                "content_safety": ok_probe,
            }
            results: dict[str, dict[str, Any]] = {}
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=len(probes), thread_name_prefix="test-health") as pool:
                futures = {name: pool.submit(dependency_health._timed_probe, name, func) for name, func in probes.items()}
                for name, future in futures.items():
                    try:
                        results[name] = future.result(timeout=dependency_health._probe_future_timeout(name))
                    except Exception as exc:
                        detail = {
                            "ok": False,
                            "state": "down",
                            "error_type": "timeout",
                            "error": f"{type(exc).__name__}: {exc}"[:500],
                            "latency_ms": int(dependency_health._probe_future_timeout(name) * 1000),
                        }
                        if name == "foundry":
                            detail = dependency_health._stale_ok_if_recent(name, detail)
                        dependency_health._record_probe(name, detail)
                        results[name] = detail
            return results

        dependency_health._probe_all = probe_all_with_slow_foundry
        result = dependency_health._probe_all()["foundry"]
        assert result["ok"] is True
        assert result["state"] == "degraded"
        assert result["degraded_reason"] == "transient_probe_failure_recent_success"
        assert any(record["name"] == "foundry" and record["state"] == "degraded" for record in records)
    finally:
        dependency_health._probe_all = original_probe_all
        dependency_health._probe_future_timeout = original_future_timeout
        dependency_health._record_probe = original_record
        dependency_health._LAST_OK.clear()
        dependency_health._PROBE_HISTORY.clear()


def test_run_title_and_summary_are_content_derived_and_distinct() -> None:
    runs = []
    for idx, opportunity in enumerate(
        [
            "renewal risk warning",
            "support deflection package",
            "premium review insights",
            "inventory anomaly pilot",
            "merchant churn signal",
        ],
        start=1,
    ):
        run = {
            "run_id": f"run-{idx}",
            "workspace_id": "demo",
            "message": f"Assess {opportunity} with evidence gaps",
            "status": "completed",
            "verdict": "conditional" if idx % 2 else "not_yet_feasible",
            "confidence": "market_inferred",
            "artifact": {
                "feasibility": {
                    "opportunity_id": opportunity,
                    "verdict": "conditional" if idx % 2 else "not_yet_feasible",
                    "overall_confidence": "market_inferred",
                    "gap_list": [f"missing direct buyer evidence for {opportunity}"],
                    "recommendation": f"pilot {opportunity} before scaling",
                },
                "citations": [{"source_file": f"evidence-{idx}.csv"}],
            },
        }
        runs.append(run)

    titles = [run_store._run_title(run) for run in runs]
    summaries = [run_store._run_summary_text(run) for run in runs]
    assert len(set(titles)) == 5
    assert all("DataForge" not in title for title in titles)
    for opportunity, summary in zip([run["artifact"]["feasibility"]["opportunity_id"] for run in runs], summaries, strict=True):
        assert opportunity[:18] in summary
        assert "evidence-" in summary
        assert "missing direct buyer evidence" in summary

    corpus_qa_run = {
        "run_id": "qa-run",
        "workspace_id": "demo",
        "message": "Which reviews mention installation friction or missing parts?",
        "status": "completed",
        "artifact": {
            "corpus": {
                "opportunities": [{"title": "[0,0]"}],
                "hits": [{"source_file": "reviews.csv"}],
            }
        },
    }
    qa_title = run_store._run_title(corpus_qa_run)
    qa_summary = run_store._run_summary_text(corpus_qa_run)
    assert "[0,0]" not in qa_title
    assert "Which reviews" in qa_title
    assert "installation" in qa_title
    assert "installation frictio" in qa_summary


def test_completed_workspace_followup_uses_lightweight_route() -> None:
    original_context = orchestrator.workspace_context
    try:
        orchestrator.workspace_context = lambda workspace_id: {
            "workspace_id": workspace_id,
            "doc_count": 4,
            "last_analysis": {
                "opportunity_id": "renewal risk warning",
                "verdict": "conditional",
                "gap_list": ["Need direct renewal outcome labels."],
                "action_plan": ["Run a two-week threshold calibration."],
            },
        }
        req = ChatRequest(workspace_id="demo", conversation_id="conv-1", message="What if we pilot this for two weeks first?")
        history = [{"role": "user", "text": "analyze"}, {"role": "assistant", "text": "Previous analysis"}]
        routed = orchestrator._preflight_fast_route(req, history)
        assert routed is not None
        decision, meta = routed
        assert decision.intent == "followup_edit"
        assert decision.experts == []
        assert meta["fast_path"] == "lightweight_followup"

        full_req = ChatRequest(workspace_id="demo", conversation_id="conv-1", message="Generate the proposal PDF", artifact_mode="proposal")
        assert orchestrator._preflight_fast_route(full_req, history) is None
    finally:
        orchestrator.workspace_context = original_context


def test_vague_followup_returns_clarify_and_gaps() -> None:
    req = ChatRequest(workspace_id="demo", conversation_id="conv-1", message="ok?")
    result = orchestrator._fallback_followup_assessment(
        req,
        previous="conditional renewal risk warning analysis",
        last_analysis={
            "opportunity_id": "renewal risk warning",
            "verdict": "conditional",
            "gap_list": ["Need direct renewal outcome labels."],
            "action_plan": ["Run a two-week threshold calibration."],
        },
    )
    assert result["assessment"] == "unclear"
    assert result["should_clarify"] is True
    assert result["clarify"]
    assert result["gaps"] == ["Need direct renewal outcome labels."]


def test_producer_keeps_pdf_and_new_artifacts_when_image_fails() -> None:
    original_pdf = orchestrator.render_pdf_report
    original_image = orchestrator.generate_image
    original_refs = orchestrator.workspace_reference_images
    original_run_producer = orchestrator._run_producer
    try:
        orchestrator.render_pdf_report = lambda *_: {
            "artifact_url": "/api/artifacts/mock.pdf",
            "local_path": str(ROOT / "artifacts" / "mock.pdf"),
            "bytes": 1024,
            "mode": "test_pdf",
        }
        orchestrator.generate_image = lambda *_: (_ for _ in ()).throw(TimeoutError("image timeout"))
        orchestrator.workspace_reference_images = lambda *_: []
        result = orchestrator._run_producer(
            {
                "workspace_id": "demo",
                "answer": {"text": "A bounded opportunity based on evidence."},
                "corpus": {"hits": [], "top_hits": []},
                "market": {},
                "audit": {},
                "feasibility": {
                    "opportunity_id": "renewal risk warning",
                    "verdict": "conditional",
                    "overall_confidence": "market_inferred",
                    "dimensions": [{"name": "renewal labels", "score": 3}],
                    "gap_list": ["Need direct renewal outcome labels."],
                    "action_plan": ["Run a two-week threshold calibration."],
                },
                "risk_register": [
                    {
                        "gap": "Outcome labels are sparse.",
                        "impact": "The pilot cannot calibrate precision.",
                        "mitigation": "Sample recent accounts for manual labels.",
                        "severity": "medium",
                    }
                ],
            },
            ["pdf", "concept_image", "pilot_plan", "action_plan"],
        )
        assert result["artifact_urls"]["pdf"].endswith(".pdf")
        assert result["artifact_urls"]["pilot_plan"].endswith(".md")
        assert result["artifact_urls"]["action_plan"].endswith(".md")
        assert "concept_image" not in result["artifact_urls"]
        assert result["degraded"] is True
        assert any(item["kind"] == "concept_image" for item in result.get("warnings") or [])
        assert "Need direct renewal outcome labels" in result["pilot_plan"]["markdown"]
        assert "Need direct renewal outcome labels" in result["action_plan"]["markdown"]

        captured: dict[str, Any] = {}

        def fake_run_producer(artifact: dict[str, Any], kinds: list[str] | None = None) -> dict[str, Any]:
            captured["kinds"] = kinds
            return {"artifact_urls": {}}

        orchestrator._run_producer = fake_run_producer
        orchestrator.produce_from_existing_report({"workspace_id": "demo", "feasibility": {}})
        assert captured["kinds"] == ["pdf", "concept_image", "pilot_plan", "action_plan"]
    finally:
        orchestrator.render_pdf_report = original_pdf
        orchestrator.generate_image = original_image
        orchestrator.workspace_reference_images = original_refs
        orchestrator._run_producer = original_run_producer


def main() -> None:
    tests = [
        test_foundry_probe_transient_failure_uses_recent_success,
        test_foundry_outer_timeout_uses_recent_success,
        test_run_title_and_summary_are_content_derived_and_distinct,
        test_completed_workspace_followup_uses_lightweight_route,
        test_vague_followup_returns_clarify_and_gaps,
        test_producer_keeps_pdf_and_new_artifacts_when_image_fails,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")


if __name__ == "__main__":
    main()
