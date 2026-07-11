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
