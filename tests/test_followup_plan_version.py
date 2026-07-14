import asyncio

import backend.experiment_store as experiment_store
import backend.orchestrator as orchestrator
import backend.run_store as run_store


def test_iteration_inputs_preserve_source_lineage_and_synthetic_kind() -> None:
    req = type(
        "Request",
        (),
        {
            "ui_context": {
                "iteration_inputs": [
                    {
                        "label": "pilot_conversion_rate",
                        "value": 7.2,
                        "unit": "percent",
                        "kind": "observed",
                        "source": {
                            "file_id": "feedback.csv",
                            "file_version": "2",
                            "connector_id": "upload",
                            "ignored": "not-public-lineage",
                        },
                        "verification": {"status": "verified", "note": "not persisted here"},
                    },
                    {
                        "label": "simulated_conversion_rate",
                        "value": 9.5,
                        "kind": "synthetic",
                    },
                ]
            }
        },
    )()

    metrics = orchestrator._iteration_inputs(req)

    assert metrics[0]["source"] == {
        "file_id": "feedback.csv",
        "file_version": "2",
        "connector_id": "upload",
    }
    assert metrics[0]["verification"] == {"status": "verified"}
    assert metrics[1]["kind"] == "synthetic"


def test_persist_chat_completion_records_plan_draft_version(monkeypatch):
    persisted_messages = []
    completed_runs = []
    recorded_versions = []

    monkeypatch.setattr(
        orchestrator,
        "_persist_assistant_message",
        lambda conversation_id, workspace_id, text, verdict, citations: persisted_messages.append(
            {
                "conversation_id": conversation_id,
                "workspace_id": workspace_id,
                "text": text,
                "verdict": verdict,
                "citations": citations,
            }
        ),
    )
    monkeypatch.setattr(
        orchestrator,
        "complete_run",
        lambda conversation_id, status, final, artifact: completed_runs.append(
            {
                "conversation_id": conversation_id,
                "status": status,
                "final": final,
                "artifact": artifact,
            }
        ),
    )
    monkeypatch.setattr(
        orchestrator,
        "record_plan_version",
        lambda **kwargs: recorded_versions.append(kwargs),
        raising=False,
    )

    artifact = {
        "workspace_id": "ws-plan",
        "conversation_id": "conv-plan",
        "mode": "analysis",
        "feasibility": {
            "verdict": "conditional",
            "overall_confidence": "market_inferred",
            "opportunity_id": "快闪店/小店选址建议服务",
            "dimensions": [{"name": "data_sufficiency", "score": 3}],
        },
        "answer": {
            "text": "## 一句话方案\n先选两个候选点位做低成本试点。",
            "markdown": "## 一句话方案\n先选两个候选点位做低成本试点。",
            "citations": [{"marker": "[D1]", "snippet": "楼层级人流信号可用于点位初筛。"}],
        },
        "followup": {"answer_type": "plan", "route_hint": "plan_draft"},
        "output_contract": {"answer_style": "structured_plan"},
    }

    asyncio.run(
        orchestrator._persist_chat_completion(
            "conv-plan",
            "ws-plan",
            "## 一句话方案\n先选两个候选点位做低成本试点。",
            "followup_edit",
            "followup_edit",
            {"text": "done", "artifact": artifact},
            artifact,
        )
    )

    assert persisted_messages
    assert completed_runs
    assert len(recorded_versions) == 1
    assert recorded_versions[0]["workspace_id"] == "ws-plan"
    assert recorded_versions[0]["source_run_id"] == "conv-plan"
    assert recorded_versions[0]["experiment_version_id"] == "version:conv-plan"
    assert recorded_versions[0]["text"].startswith("## 一句话方案")


def test_real_followup_preserves_analysis_run_and_attaches_plan(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "upload_blob_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_store, "download_blob_json", lambda *args, **kwargs: {})
    monkeypatch.setattr(orchestrator, "_persist_assistant_message", lambda *args, **kwargs: None)
    run_store._ACTIVE.clear()
    artifact = {
        "workspace_id": "ws-real-plan",
        "conversation_id": "conv-real-plan",
        "feasibility": {
            "opportunity_id": "workspace opportunity",
            "verdict": "conditional",
            "overall_confidence": "data_confirmed",
            "dimensions": [
                {
                    "name": "asset_data",
                    "score": 3,
                    "confidence": "data_confirmed",
                    "evidence": [
                        {
                            "source_type": "corpus",
                            "ref": "evidence.csv#row-1",
                            "file_id": "evidence.csv",
                            "file_version": "1",
                        }
                    ],
                }
            ],
            "gap_list": [],
        },
        "answer": {"text": "Analysis", "citations": []},
    }
    run_store.start_run("conv-real-plan", "ws-real-plan", "Analyze")
    run_store.complete_run("conv-real-plan", status="completed", final={"artifact": artifact}, artifact=artifact)

    followup_artifact = {
        **artifact,
        "followup": {"answer_type": "plan", "route_hint": "plan_draft"},
        "output_contract": {"answer_style": "structured_plan"},
        "answer": {"text": "Pilot plan", "citations": []},
    }
    run_store.start_run("conv-real-plan", "ws-real-plan", "Draft a plan")
    asyncio.run(
        orchestrator._persist_chat_completion(
            "conv-real-plan",
            "ws-real-plan",
            "Pilot plan",
            "followup_edit",
            "followup_edit",
            {"text": "Pilot plan", "artifact": followup_artifact},
            followup_artifact,
        )
    )

    source = run_store.get_run("conv-real-plan")
    summaries = run_store.list_runs("ws-real-plan")
    details = [run_store.get_run(item["run_id"]) for item in summaries]
    ledger = experiment_store.build_experiment_ledger("ws-real-plan", details, outcomes=[])

    assert source["status"] == "completed"
    assert source.get("version_kind") is None
    assert len(ledger["versions"]) == 1
    assert ledger["versions"][0]["version_id"] == "version:conv-real-plan"
    assert len(ledger["versions"][0]["attachments"]["plans"]) == 1
