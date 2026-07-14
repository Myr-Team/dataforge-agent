import backend.orchestrator as orchestrator
import backend.experiment_store as experiment_store
import backend.run_store as run_store


def test_produce_records_artifact_version_snapshot(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "upload_blob_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_store, "download_blob_json", lambda *args, **kwargs: {})
    run_store._ACTIVE.clear()

    base_artifact = {
        "workspace_id": "ws-demo",
        "conversation_id": "run-v1",
        "feasibility": {
            "opportunity_id": "小店选址建议服务",
            "verdict": "conditional",
            "overall_confidence": "market_inferred",
            "dimensions": [
                {"name": "market", "score": 3, "confidence": "market_inferred", "rationale": "有楼层级人流信号。"},
                {"name": "asset_data", "score": 4, "confidence": "data_confirmed", "rationale": "已有环境与信号字段。"},
            ],
            "gap_list": ["缺少成本/预算边界。"],
            "action_plan": ["先做一周低成本试点。"],
        },
        "answer": {"text": "建议先做低成本试点。", "citations": [{"marker": "[D1]", "source_file": "site.csv"}]},
        "citations": [{"marker": "[D1]", "source_file": "site.csv"}],
    }
    run_store.start_run("run-v1", "ws-demo", "自动分析")
    run_store.complete_run("run-v1", final={"text": "v1 分析完成", "artifact": base_artifact}, artifact=base_artifact)

    monkeypatch.setattr(
        orchestrator,
        "_run_producer",
        lambda artifact, kinds=None: {
            "opportunity_id": "小店选址建议服务",
            "generated_at": "2026-07-01T00:00:00Z",
            "artifact_urls": {"pdf": "/api/artifacts/site-v1.pdf"},
            "artifact_generated_at": {"pdf": "2026-07-01T00:00:00Z"},
            "pdf": {"artifact_url": "/api/artifacts/site-v1.pdf", "bytes": 1234},
        },
    )

    result = orchestrator.produce_from_existing_report(
        {
            "workspace_id": "ws-demo",
            "conversation_id": "run-v1",
            "feasibility": base_artifact["feasibility"],
            "answer": base_artifact["answer"],
            "kinds": ["pdf"],
        }
    )

    assert result["persisted_run_id"] == "run-v1"
    assert result["version_run_id"]
    assert result["experiment_version_id"] == "version:run-v1"

    updated = run_store.get_run("run-v1")
    assert updated["artifact"]["proposal"]["artifact_urls"]["pdf"] == "/api/artifacts/site-v1.pdf"

    version = run_store.get_run(result["version_run_id"])
    assert version["version_kind"] == "artifact_generation"
    assert version["source_run_id"] == "run-v1"
    assert version["experiment_version_id"] == "version:run-v1"
    assert version["experiment_attachment"] is True
    assert version["verdict"] == "conditional"
    assert version["artifact"]["proposal"]["artifact_urls"]["pdf"] == "/api/artifacts/site-v1.pdf"

    summaries = run_store.list_runs("ws-demo")
    assert any(item.get("version_kind") == "artifact_generation" for item in summaries)

    ledger = experiment_store.build_experiment_ledger(
        "ws-demo",
        [run_store.get_run(item["run_id"]) for item in summaries],
        outcomes=[],
    )
    assert [item["version_id"] for item in ledger["versions"]] == ["version:run-v1"]
    assert ledger["versions"][0]["attachments"]["artifacts"][0]["run_id"] == version["run_id"]
