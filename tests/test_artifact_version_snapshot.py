import copy
import importlib.util
import json
import sys
import threading

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


def _isolated_run_store(name: str):
    module_name = f"backend.{name}"
    spec = importlib.util.spec_from_file_location(module_name, run_store.__file__)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _shared_blob_store(*, synchronize_initial_cas: bool = False):
    remote: dict[str, dict] = {}
    remote_lock = threading.Lock()
    initial_barrier = threading.Barrier(2) if synchronize_initial_cas else None

    def download(name: str):
        with remote_lock:
            value = remote.get(name)
            return copy.deepcopy(value) if value is not None else None

    def upload(name: str, value: dict):
        with remote_lock:
            remote[name] = copy.deepcopy(value)
        return {"blob_name": name}

    def compare_and_swap(name: str, *, expected_revision: int, changes: dict):
        if initial_barrier is not None and expected_revision == 0:
            initial_barrier.wait(timeout=5)
        with remote_lock:
            current = remote.get(name) or {}
            if int(current.get("revision") or 0) != expected_revision:
                return None
            updated = {**current, **copy.deepcopy(changes)}
            remote[name] = updated
            return copy.deepcopy(updated)

    return remote, download, upload, compare_and_swap


def test_two_instances_recompute_lineage_after_cas_loss(tmp_path) -> None:
    first_store = _isolated_run_store("run_store_instance_first")
    second_store = _isolated_run_store("run_store_instance_second")
    remote, download, upload, compare_and_swap = _shared_blob_store(synchronize_initial_cas=True)
    stores = [first_store, second_store]
    run_ids = ["analysis-instance-a", "analysis-instance-b"]
    workspace_id = "ws-cross-instance-lineage"
    for index, store in enumerate(stores):
        store.RUN_DIR = tmp_path / f"instance-{index}"
        store.blob_configured = lambda: True
        store.download_blob_json = download
        store.upload_blob_json = upload
        store.compare_and_swap_blob_json = compare_and_swap
        store.start_run(run_ids[index], workspace_id, "Analyze")

    with ThreadPoolExecutor(max_workers=2) as executor:
        completed = list(
            executor.map(
                lambda pair: pair[0].complete_run(
                    pair[1],
                    status="completed",
                    final={"artifact": _analysis_artifact(workspace_id, pair[1])},
                    artifact=_analysis_artifact(workspace_id, pair[1]),
                ),
                zip(stores, run_ids),
            )
        )

    registry = remote[run_store.RUN_REGISTRY_BLOB]
    entries = {item["run_id"]: item for item in registry["runs"]}
    canonical_ids = {entries[run_id]["canonical_experiment_run_id"] for run_id in run_ids}
    assert len(canonical_ids) == 1
    canonical_id = canonical_ids.pop()
    assert sum(entries[run_id]["run_id"] == entries[run_id]["canonical_experiment_run_id"] for run_id in run_ids) == 1
    assert all(entries[run_id]["canonical_lineage_status"] == "trusted" for run_id in run_ids)
    assert all(item["canonical_experiment_run_id"] == canonical_id for item in completed)
    assert first_store.resolve_canonical_experiment_source_run_id(workspace_id, run_ids[0]) == canonical_id
    assert second_store.resolve_canonical_experiment_source_run_id(workspace_id, run_ids[1]) == canonical_id


def test_failed_registry_confirmation_never_uploads_trusted_candidate(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "blob_configured", lambda: True)
    monkeypatch.setattr(run_store, "download_blob_json", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(run_store, "compare_and_swap_blob_json", lambda *_args, **_kwargs: None)
    uploads: list[dict] = []
    monkeypatch.setattr(
        run_store,
        "upload_blob_json",
        lambda name, value: uploads.append(copy.deepcopy(value)) or {"blob_name": name},
    )
    run_store._ACTIVE.clear()
    workspace_id = "ws-failed-lineage-confirmation"
    run_id = "analysis-candidate"
    run_store.start_run(run_id, workspace_id, "Analyze")

    completed = run_store.complete_run(
        run_id,
        status="completed",
        final={"artifact": _analysis_artifact(workspace_id, run_id)},
        artifact=_analysis_artifact(workspace_id, run_id),
    )

    run_uploads = [item for item in uploads if item.get("run_id") == run_id]
    assert run_uploads
    assert all(item.get("canonical_lineage_status") != "trusted" for item in run_uploads)
    assert completed["canonical_lineage_status"] in {"candidate", "unresolved"}
    assert "canonical_experiment_run_id" not in completed
    assert run_store.resolve_canonical_experiment_source_run_id(workspace_id, run_id) is None


def test_truncated_registry_alias_can_serialize_next_duplicate(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "blob_configured", lambda: False)
    run_store._ACTIVE.clear()
    workspace_id = "ws-truncated-confirmed-alias"
    canonical_id = "analysis-canonical"
    alias_id = "analysis-alias"
    next_id = "analysis-next-duplicate"
    for run_id in (canonical_id, alias_id):
        artifact = _analysis_artifact(workspace_id, run_id)
        run_store.start_run(run_id, workspace_id, "Analyze")
        run_store.complete_run(run_id, status="completed", final={"artifact": artifact}, artifact=artifact)
    registry = run_store.authoritative_run_registry()
    registry["history_truncated"] = True
    registry["runs"] = [item for item in registry["runs"] if item.get("run_id") != canonical_id]
    run_store._write_local_registry(registry)

    artifact = _analysis_artifact(workspace_id, next_id)
    run_store.start_run(next_id, workspace_id, "Analyze")
    completed = run_store.complete_run(
        next_id,
        status="completed",
        final={"artifact": artifact},
        artifact=artifact,
    )

    assert completed["canonical_lineage_status"] == "trusted"
    assert completed["canonical_experiment_run_id"] == canonical_id
    assert run_store.resolve_canonical_experiment_source_run_id(workspace_id, next_id) == canonical_id


def test_failed_existing_analysis_update_preserves_confirmed_blob_and_envelope(tmp_path, monkeypatch) -> None:
    remote, download, upload, compare_and_swap = _shared_blob_store()
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "blob_configured", lambda: True)
    monkeypatch.setattr(run_store, "download_blob_json", download)
    monkeypatch.setattr(run_store, "upload_blob_json", upload)
    monkeypatch.setattr(run_store, "compare_and_swap_blob_json", compare_and_swap)
    run_store._ACTIVE.clear()
    workspace_id = "ws-preserve-confirmed"
    run_id = "analysis-confirmed"
    run_store.start_run(run_id, workspace_id, "Analyze")
    confirmed = run_store.complete_run(
        run_id,
        status="completed",
        final={"artifact": _analysis_artifact(workspace_id, run_id)},
        artifact=_analysis_artifact(workspace_id, run_id),
    )
    blob_name = f"{run_store.RUN_BLOB_PREFIX}/{run_store._safe_name(run_id)}.json"
    confirmed_blob = copy.deepcopy(remote[blob_name])
    envelope_keys = (
        "canonical_experiment_run_id",
        "canonical_experiment_version_id",
        "canonical_resolution_status",
        "canonical_lineage_status",
    )
    expected_envelope = {key: confirmed.get(key) for key in envelope_keys}

    def fail_run_upload(name: str, value: dict):
        if name == blob_name:
            raise RuntimeError("run blob unavailable")
        return upload(name, value)

    monkeypatch.setattr(run_store, "upload_blob_json", fail_run_upload)
    run_store.update_run_proposal(run_id, {"artifact_urls": {"pdf": "/api/artifacts/new.pdf"}})

    persisted = json.loads((tmp_path / "runs" / f"{run_store._safe_name(run_id)}.json").read_text(encoding="utf-8"))
    assert {key: persisted.get(key) for key in envelope_keys} == expected_envelope
    assert remote[blob_name] == confirmed_blob
    assert not ((persisted.get("artifact") or {}).get("proposal") or {}).get("artifact_urls")


@pytest.mark.parametrize("snapshot_kind", ["artifact", "plan"])
def test_snapshot_requires_confirmed_registry_persistence(snapshot_kind, tmp_path, monkeypatch) -> None:
    remote, download, upload, compare_and_swap = _shared_blob_store()
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "blob_configured", lambda: True)
    monkeypatch.setattr(run_store, "download_blob_json", download)
    monkeypatch.setattr(run_store, "upload_blob_json", upload)
    monkeypatch.setattr(run_store, "compare_and_swap_blob_json", compare_and_swap)
    run_store._ACTIVE.clear()
    workspace_id = "ws-snapshot-transaction"
    source_run_id = "analysis-snapshot-source"
    artifact = _analysis_artifact(workspace_id, source_run_id)
    run_store.start_run(source_run_id, workspace_id, "Analyze")
    run_store.complete_run(source_run_id, status="completed", final={"artifact": artifact}, artifact=artifact)
    monkeypatch.setattr(run_store, "compare_and_swap_blob_json", lambda *_args, **_kwargs: None)

    if snapshot_kind == "artifact":
        snapshot = run_store.record_artifact_version(
            workspace_id=workspace_id,
            source_run_id=source_run_id,
            experiment_version_id=f"version:{source_run_id}",
            artifact=artifact,
            proposal={"artifact_urls": {"pdf": "/api/artifacts/report.pdf"}},
            kinds=["pdf"],
        )
    else:
        snapshot = run_store.record_plan_version(
            workspace_id=workspace_id,
            source_run_id=source_run_id,
            experiment_version_id=f"version:{source_run_id}",
            artifact=artifact,
            text="Pilot plan",
        )

    assert snapshot is None


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
    monkeypatch.setattr(run_store, "blob_configured", lambda: False)
    run_store._ACTIVE.clear()
    workspace_id = "ws-long-history"
    canonical_id = "analysis-canonical-old"
    duplicate_id = "analysis-duplicate-new"
    for run_id in (canonical_id, duplicate_id):
        artifact = _analysis_artifact(workspace_id, run_id)
        run_store.start_run(run_id, workspace_id, "Analyze")
        run_store.complete_run(run_id, status="completed", final={"artifact": artifact}, artifact=artifact)
    canonical = run_store.get_run(canonical_id)
    duplicate = run_store.get_run(duplicate_id)
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
