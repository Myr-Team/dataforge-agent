import asyncio
from types import SimpleNamespace

import backend.experiment_store as experiment_store
import backend.orchestrator as orchestrator
import backend.run_store as run_store
import pytest


class _PlanLineageRepository:
    def __init__(self) -> None:
        self.versions: list[SimpleNamespace] = []
        self.attachments: list[SimpleNamespace] = []

    def commit_analysis(self, **values):
        latest = self.versions[-1] if self.versions else None
        if (
            latest is not None
            and latest.workspace_id == values["workspace_id"]
            and latest.generation == values["generation"]
            and latest.decision_fingerprint == values["decision_fingerprint"]
            and latest.evidence_fingerprint == values["evidence_fingerprint"]
        ):
            return SimpleNamespace(**{**vars(latest), "created": False})
        committed = SimpleNamespace(
            version_id=f"version:{values['canonical_run_id']}",
            workspace_id=values["workspace_id"],
            generation=values["generation"],
            ordinal=len([item for item in self.versions if item.workspace_id == values["workspace_id"]]) + 1,
            canonical_run_id=values["canonical_run_id"],
            decision_fingerprint=values["decision_fingerprint"],
            evidence_fingerprint=values["evidence_fingerprint"],
            created=True,
        )
        self.versions.append(committed)
        return committed

    def list_versions(self, *, workspace_id: str, generation: int):
        return tuple(
            item
            for item in self.versions
            if item.workspace_id == workspace_id and item.generation == generation
        )

    def attach_snapshot(self, **values):
        if not any(
            item.version_id == values["version_id"]
            and item.workspace_id == values["workspace_id"]
            and item.generation == values["generation"]
            for item in self.versions
        ):
            raise RuntimeError("version is not available for attachment")
        committed = SimpleNamespace(
            attachment_id=f"attachment-{len(self.attachments) + 1}",
            version_id=values["version_id"],
            workspace_id=values["workspace_id"],
            generation=values["generation"],
            kind=values["kind"],
            source_run_id=values["source_run_id"],
            payload_sha256=values["payload_sha256"],
            created=True,
        )
        self.attachments.append(committed)
        return committed


@pytest.fixture(autouse=True)
def _inject_lineage_repository():
    original_run_provider = run_store._LINEAGE_REPOSITORY_PROVIDER
    original_experiment_provider = experiment_store._LINEAGE_REPOSITORY_PROVIDER
    repository = _PlanLineageRepository()
    run_store._LINEAGE_REPOSITORY_PROVIDER = lambda: repository
    experiment_store._LINEAGE_REPOSITORY_PROVIDER = lambda: repository
    yield repository
    run_store._LINEAGE_REPOSITORY_PROVIDER = original_run_provider
    experiment_store._LINEAGE_REPOSITORY_PROVIDER = original_experiment_provider
    run_store._ACTIVE.clear()
    run_store._LINEAGE_GENERATION_HINTS.clear()


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
    monkeypatch.setattr(
        orchestrator,
        "resolve_canonical_experiment_source_run_id",
        lambda workspace_id, source_run_id: source_run_id,
    )
    monkeypatch.setattr(
        orchestrator,
        "_canonical_version_id_for_run",
        lambda source_run_id: f"version:{source_run_id}",
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
    followups = [item for item in details if item.get("status") == "followup_edit"]
    assert len(followups) == 1
    assert followups[0]["source_run_id"] == "conv-real-plan"
    assert followups[0]["experiment_version_id"] == "version:conv-real-plan"
    assert len(ledger["versions"]) == 1
    assert ledger["versions"][0]["version_id"] == "version:conv-real-plan"
    assert len(ledger["versions"][0]["attachments"]["plans"]) == 1


def test_new_conversation_followup_attaches_plan_to_workspace_last_analysis(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "upload_blob_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_store, "download_blob_json", lambda *args, **kwargs: {})
    monkeypatch.setattr(orchestrator, "_persist_assistant_message", lambda *args, **kwargs: None)
    run_store._ACTIVE.clear()
    workspace_id = "ws-new-conversation-plan"
    source_run_id = "analysis-conversation"
    followup_run_id = "new-followup-conversation"
    analysis_artifact = {
        "workspace_id": workspace_id,
        "conversation_id": source_run_id,
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
        },
        "answer": {"text": "Analysis", "citations": []},
    }
    run_store.start_run(source_run_id, workspace_id, "Analyze")
    run_store.complete_run(
        source_run_id,
        status="completed",
        final={"artifact": analysis_artifact},
        artifact=analysis_artifact,
    )
    run_store.start_run(followup_run_id, workspace_id, "Draft a plan")
    monkeypatch.setattr(
        orchestrator,
        "_last_analysis_for_workspace",
        lambda requested_workspace, context=None: run_store.get_run(source_run_id)
        if requested_workspace == workspace_id
        else {},
    )
    followup_artifact = {
        "workspace_id": workspace_id,
        "conversation_id": followup_run_id,
        "followup": {"answer_type": "plan", "route_hint": "plan_draft"},
        "output_contract": {"answer_style": "structured_plan"},
        "answer": {"text": "Pilot plan", "citations": []},
    }

    asyncio.run(
        orchestrator._persist_chat_completion(
            followup_run_id,
            workspace_id,
            "Pilot plan",
            "followup_edit",
            "followup_edit",
            {"text": "Pilot plan", "artifact": followup_artifact},
            followup_artifact,
        )
    )

    source = run_store.get_run(source_run_id)
    followup = run_store.get_run(followup_run_id)
    details = [run_store.get_run(item["run_id"]) for item in run_store.list_runs(workspace_id)]
    ledger = experiment_store.build_experiment_ledger(workspace_id, details, outcomes=[])
    assert source["status"] == "completed"
    assert followup["status"] == "followup_edit"
    assert followup.get("version_kind") is None
    assert followup["source_run_id"] == source_run_id
    assert followup["experiment_version_id"] == f"version:{source_run_id}"
    assert [item["version_id"] for item in ledger["versions"]] == [f"version:{source_run_id}"]
    assert ledger["versions"][0]["attachments"]["plans"][0]["text"] == "Pilot plan"


def test_new_conversation_plan_resolves_latest_duplicate_analysis_alias(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "upload_blob_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_store, "download_blob_json", lambda *args, **kwargs: {})
    monkeypatch.setattr(orchestrator, "_persist_assistant_message", lambda *args, **kwargs: None)
    run_store._ACTIVE.clear()
    workspace_id = "ws-duplicate-analysis-plan"
    canonical_run_id = "analysis-canonical"
    duplicate_run_id = "analysis-latest-duplicate"
    followup_run_id = "new-plan-conversation"
    analysis_artifact = {
        "workspace_id": workspace_id,
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
        },
        "answer": {"text": "Analysis", "citations": []},
    }
    for run_id in (canonical_run_id, duplicate_run_id):
        artifact = {**analysis_artifact, "conversation_id": run_id}
        run_store.start_run(run_id, workspace_id, "Analyze")
        run_store.complete_run(
            run_id,
            status="completed",
            final={"artifact": artifact},
            artifact=artifact,
        )
    assert orchestrator._last_analysis_for_workspace(workspace_id)["run_id"] == duplicate_run_id

    run_store.start_run(followup_run_id, workspace_id, "Draft a plan")
    followup_artifact = {
        "workspace_id": workspace_id,
        "conversation_id": followup_run_id,
        "followup": {"answer_type": "plan", "route_hint": "plan_draft"},
        "output_contract": {"answer_style": "structured_plan"},
        "answer": {"text": "Pilot plan from duplicate", "citations": []},
    }
    asyncio.run(
        orchestrator._persist_chat_completion(
            followup_run_id,
            workspace_id,
            "Pilot plan from duplicate",
            "followup_edit",
            "followup_edit",
            {"text": "Pilot plan from duplicate", "artifact": followup_artifact},
            followup_artifact,
        )
    )

    details = [run_store.get_run(item["run_id"]) for item in run_store.list_runs(workspace_id)]
    ledger = experiment_store.build_experiment_ledger(workspace_id, details, outcomes=[])
    plan_runs = [item for item in details if item.get("version_kind") == "plan_draft"]
    assert [item["version_id"] for item in ledger["versions"]] == [f"version:{canonical_run_id}"]
    assert len(plan_runs) == 1
    assert plan_runs[0]["source_run_id"] == canonical_run_id
    assert plan_runs[0]["experiment_version_id"] == f"version:{canonical_run_id}"
    assert ledger["versions"][0]["attachments"]["plans"][0]["text"] == "Pilot plan from duplicate"
    followup = run_store.get_run(followup_run_id)
    assert followup["source_run_id"] == canonical_run_id
    assert followup["experiment_version_id"] == f"version:{canonical_run_id}"


def test_workspace_latest_analysis_excludes_plan_and_artifact_snapshots(monkeypatch) -> None:
    workspace_id = "ws-analysis-selection"
    analysis = {
        "run_id": "analysis-real",
        "workspace_id": workspace_id,
        "status": "completed",
        "artifact": {
            "feasibility": {
                "opportunity_id": "canonical analysis",
                "verdict": "conditional",
                "dimensions": [{"name": "asset_data", "score": 3}],
            }
        },
    }
    snapshot = {
        **analysis,
        "run_id": "snapshot-latest",
        "version_kind": "artifact_generation",
        "source_run_id": "analysis-real",
        "experiment_version_id": "version:analysis-real",
        "artifact": {
            "feasibility": {
                "opportunity_id": "snapshot copy",
                "verdict": "feasible",
                "dimensions": [{"name": "asset_data", "score": 5}],
            }
        },
    }
    details = {analysis["run_id"]: analysis, snapshot["run_id"]: snapshot}
    monkeypatch.setattr(orchestrator, "list_runs", lambda requested_workspace=None: [snapshot, analysis])
    monkeypatch.setattr(orchestrator, "get_run", lambda run_id: details[run_id])

    selected = orchestrator._last_analysis_for_workspace(workspace_id, {})

    assert selected["run_id"] == analysis["run_id"]
    assert selected["opportunity_id"] == "canonical analysis"


def test_plan_attachment_failure_persists_bounded_warning_status(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "upload_blob_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_store, "download_blob_json", lambda *args, **kwargs: {})
    monkeypatch.setattr(orchestrator, "_persist_assistant_message", lambda *args, **kwargs: None)
    monkeypatch.setattr(orchestrator, "record_plan_version", lambda **kwargs: None)
    run_store._ACTIVE.clear()
    workspace_id = "ws-plan-warning"
    source_run_id = "analysis-plan-warning"
    followup_run_id = "followup-plan-warning"
    analysis_artifact = {
        "workspace_id": workspace_id,
        "conversation_id": source_run_id,
        "feasibility": {
            "opportunity_id": "workspace opportunity",
            "verdict": "conditional",
            "dimensions": [{"name": "asset_data", "score": 3}],
        },
    }
    run_store.start_run(source_run_id, workspace_id, "Analyze")
    run_store.complete_run(
        source_run_id,
        status="completed",
        final={"artifact": analysis_artifact},
        artifact=analysis_artifact,
    )
    run_store.start_run(followup_run_id, workspace_id, "Draft a plan")
    followup_artifact = {
        "workspace_id": workspace_id,
        "conversation_id": followup_run_id,
        "followup": {"answer_type": "plan", "route_hint": "plan_draft"},
        "output_contract": {"answer_style": "structured_plan"},
        "answer": {"text": "Pilot plan", "citations": []},
    }

    asyncio.run(
        orchestrator._persist_chat_completion(
            followup_run_id,
            workspace_id,
            "Pilot plan",
            "followup_edit",
            "followup_edit",
            {"text": "Pilot plan", "artifact": followup_artifact},
            followup_artifact,
        )
    )

    persisted = run_store.get_run(followup_run_id)
    assert persisted["artifact"]["experiment_attachment"] == {
        "status": "unavailable",
        "reason": "canonical_version_unavailable",
    }
    warning = next(item for item in persisted["artifact"]["warnings"] if item["kind"] == "plan_version_snapshot")
    assert warning == {
        "kind": "plan_version_snapshot",
        "message": "Plan generated, but no canonical experiment version was available for attachment.",
        "error": "canonical_version_unavailable",
    }
