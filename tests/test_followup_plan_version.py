import asyncio

import backend.orchestrator as orchestrator


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
