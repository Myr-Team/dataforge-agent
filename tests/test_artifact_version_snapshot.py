import backend.orchestrator as orchestrator
import backend.experiment_store as experiment_store
import backend.run_store as run_store
import pytest
from concurrent.futures import ThreadPoolExecutor


def _analysis_artifact(workspace_id: str, conversation_id: str) -> dict:
    return {
        "workspace_id": workspace_id,
        "conversation_id": conversation_id,
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


def test_concurrent_completions_assign_and_persist_lineage_under_one_lock(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "upload_blob_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_store, "download_blob_json", lambda *args, **kwargs: {})
    run_store._ACTIVE.clear()
    workspace_id = "ws-atomic-lineage"
    run_ids = ["analysis-a", "analysis-b"]
    for run_id in run_ids:
        run_store.start_run(run_id, workspace_id, "Analyze")

    persisted_while_locked: list[bool] = []
    original_persist = run_store._persist_run

    def persist_with_lock_observation(run: dict) -> dict:
        persisted_while_locked.append(run_store._LOCK._is_owned())
        return original_persist(run)

    monkeypatch.setattr(run_store, "_persist_run", persist_with_lock_observation)

    with ThreadPoolExecutor(max_workers=2) as executor:
        completed = list(
            executor.map(
                lambda run_id: run_store.complete_run(
                    run_id,
                    status="completed",
                    final={"artifact": _analysis_artifact(workspace_id, run_id)},
                    artifact=_analysis_artifact(workspace_id, run_id),
                ),
                run_ids,
            )
        )

    assert all(persisted_while_locked)
    canonical_ids = {item["canonical_experiment_run_id"] for item in completed}
    assert len(canonical_ids) == 1
    canonical_id = canonical_ids.pop()
    assert sum(item["run_id"] == canonical_id for item in completed) == 1
    assert all(item["canonical_lineage_status"] == "trusted" for item in completed)
    assert all(item["canonical_experiment_version_id"] == f"version:{canonical_id}" for item in completed)


def test_persisted_alias_requires_trusted_exact_experiment_lineage(monkeypatch) -> None:
    workspace_id = "ws-strict-lineage"
    canonical_id = "analysis-canonical"
    duplicate_id = "analysis-duplicate"
    canonical = {
        "run_id": canonical_id,
        "workspace_id": workspace_id,
        "status": "completed",
        "canonical_experiment_run_id": canonical_id,
        "canonical_experiment_version_id": f"version:{canonical_id}",
        "canonical_resolution_status": "resolved",
        "canonical_lineage_status": "trusted",
        "artifact": _analysis_artifact(workspace_id, canonical_id),
    }
    duplicate = {
        **canonical,
        "run_id": duplicate_id,
        "canonical_experiment_run_id": canonical_id,
        "canonical_experiment_version_id": f"version:{duplicate_id}",
    }
    details = {canonical_id: canonical, duplicate_id: duplicate}
    monkeypatch.setattr(run_store, "get_run", lambda run_id: details[run_id])

    assert run_store.resolve_canonical_experiment_source_run_id(workspace_id, duplicate_id) is None


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


def test_attachment_requires_existing_non_deduplicated_canonical_version(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "upload_blob_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_store, "download_blob_json", lambda *args, **kwargs: {})
    run_store._ACTIVE.clear()
    artifact = {
        "workspace_id": "ws-attach",
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
    }

    unknown = run_store.record_artifact_version(
        workspace_id="ws-attach",
        source_run_id="missing-run",
        experiment_version_id="version:missing-run",
        artifact=artifact,
        proposal={"artifact_urls": {"pdf": "/api/artifacts/missing.pdf"}},
        kinds=["pdf"],
    )

    for run_id in ("run-v1", "run-v2"):
        run_store.start_run(run_id, "ws-attach", "Analyze")
        run_store.complete_run(run_id, status="completed", final={"artifact": artifact}, artifact=artifact)
    deduplicated = run_store.record_artifact_version(
        workspace_id="ws-attach",
        source_run_id="run-v2",
        experiment_version_id="version:run-v2",
        artifact=artifact,
        proposal={"artifact_urls": {"pdf": "/api/artifacts/deduplicated.pdf"}},
        kinds=["pdf"],
    )

    assert unknown is None
    assert deduplicated is None
    assert all(item.get("version_kind") != "artifact_generation" for item in run_store.list_runs("ws-attach"))


def test_produce_omits_experiment_version_id_when_attachment_is_unavailable(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "upload_blob_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_store, "download_blob_json", lambda *args, **kwargs: {})
    run_store._ACTIVE.clear()
    artifact = {
        "workspace_id": "ws-unavailable",
        "conversation_id": "run-unavailable",
        "feasibility": {
            "opportunity_id": "workspace opportunity",
            "verdict": "conditional",
            "dimensions": [],
        },
    }
    run_store.start_run("run-unavailable", "ws-unavailable", "Analyze")
    run_store.complete_run(
        "run-unavailable",
        status="completed",
        final={"artifact": artifact},
        artifact=artifact,
    )
    monkeypatch.setattr(
        orchestrator,
        "_run_producer",
        lambda artifact, kinds=None: {"artifact_urls": {"pdf": "/api/artifacts/unavailable.pdf"}},
    )
    monkeypatch.setattr(orchestrator, "record_artifact_version", lambda **kwargs: None)

    result = orchestrator.produce_from_existing_report(
        {
            "workspace_id": "ws-unavailable",
            "conversation_id": "run-unavailable",
            "feasibility": artifact["feasibility"],
            "kinds": ["pdf"],
        }
    )

    assert result["persisted_run_id"] == "run-unavailable"
    assert "experiment_version_id" not in result
    assert "version_run_id" not in result
    assert result["experiment_attachment"] == {
        "status": "unavailable",
        "reason": "canonical_version_unavailable",
    }
    warning = next(item for item in result["warnings"] if item["kind"] == "version_snapshot")
    assert warning["error"] == "canonical_version_unavailable"


def test_strict_writer_never_accepts_duplicate_when_canonical_is_outside_300_run_view(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "upload_blob_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_store, "download_blob_json", lambda *args, **kwargs: {})
    workspace_id = "ws-long-history"
    canonical_id = "analysis-canonical-old"
    duplicate_id = "analysis-duplicate-new"
    canonical = {
        "run_id": canonical_id,
        "conversation_id": canonical_id,
        "workspace_id": workspace_id,
        "status": "completed",
        "completed_at": "2026-01-01T00:00:00Z",
        "canonical_experiment_run_id": canonical_id,
        "canonical_experiment_version_id": f"version:{canonical_id}",
        "canonical_resolution_status": "resolved",
        "canonical_lineage_status": "trusted",
        "artifact": _analysis_artifact(workspace_id, canonical_id),
    }
    duplicate = {
        **canonical,
        "run_id": duplicate_id,
        "conversation_id": duplicate_id,
        "completed_at": "2026-07-01T00:00:00Z",
        "canonical_experiment_run_id": canonical_id,
        "canonical_experiment_version_id": f"version:{canonical_id}",
        "artifact": _analysis_artifact(workspace_id, duplicate_id),
    }
    fillers = [
        {
            "run_id": f"snapshot-{index:03d}",
            "workspace_id": workspace_id,
            "status": "completed",
            "completed_at": f"2026-06-01T00:{index % 60:02d}:00Z",
            "version_kind": "artifact_generation",
            "source_run_id": canonical_id,
        }
        for index in range(299)
    ]
    details = {canonical_id: canonical, duplicate_id: duplicate, **{item["run_id"]: item for item in fillers}}
    summaries = [duplicate, *fillers, canonical]
    monkeypatch.setattr(run_store, "list_runs", lambda requested_workspace=None: summaries)
    monkeypatch.setattr(run_store, "get_run", lambda run_id: details[run_id])

    resolved = run_store.resolve_canonical_experiment_source_run_id(workspace_id, duplicate_id)
    attached = run_store.record_artifact_version(
        workspace_id=workspace_id,
        source_run_id=duplicate_id,
        experiment_version_id=f"version:{duplicate_id}",
        artifact=duplicate["artifact"],
        proposal={"artifact_urls": {"pdf": "/api/artifacts/duplicate.pdf"}},
        kinds=["pdf"],
    )
    truncated_ledger = experiment_store.build_experiment_ledger(
        workspace_id,
        [details[item["run_id"]] for item in summaries[:300]],
        outcomes=[],
    )

    assert len(summaries) == 301
    assert summaries[300]["run_id"] == canonical_id
    assert resolved == canonical_id
    assert attached is None
    assert truncated_ledger["versions"] == []
    assert run_store._canonical_experiment_version_exists(workspace_id, f"version:{duplicate_id}") is False


def test_persisted_alias_fails_closed_when_target_is_not_self_resolved(monkeypatch) -> None:
    workspace_id = "ws-unproven-history"
    canonical_id = "legacy-canonical-unproven"
    duplicate_id = "duplicate-with-unproven-link"
    canonical = {
        "run_id": canonical_id,
        "workspace_id": workspace_id,
        "status": "completed",
        "artifact": _analysis_artifact(workspace_id, canonical_id),
    }
    duplicate = {
        "run_id": duplicate_id,
        "workspace_id": workspace_id,
        "status": "completed",
        "canonical_experiment_run_id": canonical_id,
        "canonical_resolution_status": "resolved",
        "artifact": _analysis_artifact(workspace_id, duplicate_id),
    }
    details = {canonical_id: canonical, duplicate_id: duplicate}
    monkeypatch.setattr(run_store, "get_run", lambda run_id: details[run_id])
    monkeypatch.setattr(
        run_store,
        "download_blob_json",
        lambda blob_name: {"history_truncated": True, "runs": [duplicate]},
    )

    assert run_store.resolve_canonical_experiment_source_run_id(workspace_id, duplicate_id) is None
    assert run_store._canonical_experiment_version_exists(workspace_id, f"version:{duplicate_id}") is False


@pytest.mark.parametrize("kind", ["pdf", "roadmap", "validation_plan"])
def test_producer_resolves_duplicate_source_for_every_artifact_path(kind, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "upload_blob_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_store, "download_blob_json", lambda *args, **kwargs: {})
    run_store._ACTIVE.clear()
    workspace_id = f"ws-{kind}"
    canonical_id = f"canonical-{kind}"
    duplicate_id = f"duplicate-{kind}"
    for run_id in (canonical_id, duplicate_id):
        artifact = _analysis_artifact(workspace_id, run_id)
        run_store.start_run(run_id, workspace_id, "Analyze")
        run_store.complete_run(run_id, status="completed", final={"artifact": artifact}, artifact=artifact)
    monkeypatch.setattr(
        orchestrator,
        "_run_producer",
        lambda artifact, kinds=None: {
            "artifact_urls": {kind: f"/api/artifacts/{kind}.md"},
            kind: {"artifact_url": f"/api/artifacts/{kind}.md"},
        },
    )

    result = orchestrator.produce_from_existing_report(
        {
            "workspace_id": workspace_id,
            "conversation_id": duplicate_id,
            "feasibility": _analysis_artifact(workspace_id, duplicate_id)["feasibility"],
            "kinds": [kind],
        }
    )

    assert result["persisted_run_id"] == canonical_id
    assert result["experiment_version_id"] == f"version:{canonical_id}"
    version = run_store.get_run(result["version_run_id"])
    assert version["source_run_id"] == canonical_id
    assert version["experiment_version_id"] == f"version:{canonical_id}"
    assert version["produced_kinds"] == [kind]
