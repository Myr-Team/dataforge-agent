import json

import backend.run_store as run_store


def test_list_runs_prefers_recomputed_local_summary_over_stale_registry(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "upload_blob_json", lambda *args, **kwargs: None)

    def download_blob(name, *args, **kwargs):
        if name == run_store.RUN_REGISTRY_BLOB:
            return {
                "runs": [
                    {
                        "run_id": "run-stale",
                        "workspace_id": "ws-dynamic",
                        "status": "completed",
                        "tokens": {"total": 1018, "prompt": 900, "completion": 118},
                        "duration_ms": 618000,
                        "time": "2026-07-01T10:18:00Z",
                    }
                ]
            }
        return {}

    monkeypatch.setattr(run_store, "download_blob_json", download_blob)
    run_store._ACTIVE.clear()

    run_store.start_run("run-stale", "ws-dynamic", "analyze")
    run_store.record_event(
        "run-stale",
        "model_response",
        {"agent": "df-coordinator", "usage": {"input_tokens": 12, "output_tokens": 7, "total_tokens": 19}},
    )
    run_store.complete_run("run-stale", final={"text": "done"}, artifact={})
    run_path = next((tmp_path / "runs").glob("*.json"))
    stored = json.loads(run_path.read_text(encoding="utf-8"))
    stored["duration_ms"] = 618000
    run_path.write_text(json.dumps(stored), encoding="utf-8")

    summary = run_store.list_runs("ws-dynamic")[0]

    assert summary["tokens"]["total"] == 19
    assert summary["duration_ms"] != 618000


def test_record_event_persists_only_safe_cache_metering(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "upload_blob_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_store, "download_blob_json", lambda *args, **kwargs: {})
    run_store._ACTIVE.clear()

    run_store.start_run("cache-run", "ws-a", "Analyze", {})
    run_store.record_event(
        "cache-run",
        "model_response",
        {
            "deployment": "gpt-5.6-sol",
            "route": "analysis",
            "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
            "cache": {
                "state": "hit",
                "provider": "redis",
                "elapsed_ms": 3,
                "source_usage": {"prompt": 10, "completion": 2, "total": 12, "raw_usage": "drop"},
                "source_cost_estimate": {
                    "status": "estimated",
                    "currency": "USD",
                    "amount": 0.001,
                    "price_card_revision": 4,
                    "route_id": "analysis",
                    "source_label": "drop",
                },
                "key_sample": "redis://secret-cache-key",
                "error": "drop",
            },
        },
    )

    result = run_store.complete_run("cache-run")

    assert result is not None
    assert result["models"][0]["cache"] == {
        "state": "hit",
        "provider": "redis",
        "elapsed_ms": 3,
        "source_usage": {"prompt": 10, "completion": 2, "total": 12},
        "source_cost_estimate": {
            "status": "estimated",
            "currency": "USD",
            "amount": 0.001,
            "price_card_revision": 4,
            "route_id": "analysis",
        },
    }
    assert "key_sample" not in result["steps"][0]["data"]["cache"]


def test_model_record_preserves_route_provider_and_both_cache_layers(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "upload_blob_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_store, "download_blob_json", lambda *args, **kwargs: {})
    run_store._ACTIVE.clear()

    run_store.start_run("deepseek-cache-run", "ws-a", "Analyze", {})
    run_store.record_event(
        "deepseek-cache-run",
        "model_response",
        {
            "agent": "df-feasibility-analyst",
            "route": "ds_flash",
            "deployment": "deepseek-v4-flash",
            "model_id": "deepseek-v4-flash",
            "provider_type": "deepseek",
            "provider_id": "provider-safe",
            "gateway_coverage": "apim_governed",
            "usage": {"input_tokens": 1000, "output_tokens": 20, "total_tokens": 1020},
            "result_cache": {
                "state": "miss",
                "provider": "redis",
                "eligible": True,
                "reason": "eligible",
                "policy_revision": 6,
            },
            "provider_cache": {
                "state": "partial_hit",
                "hit_tokens": 800,
                "miss_tokens": 200,
                "hit_rate_pct": 80,
                "evidence_state": "observed",
            },
        },
    )

    result = run_store.complete_run("deepseek-cache-run")
    model = result["models"][0]
    assert model["provider_type"] == "deepseek"
    assert model["provider_id"] == "provider-safe"
    assert model["model_id"] == "deepseek-v4-flash"
    assert model["gateway_coverage"] == "apim_governed"
    assert model["result_cache"]["state"] == "miss"
    assert model["provider_cache"]["hit_tokens"] == 800
