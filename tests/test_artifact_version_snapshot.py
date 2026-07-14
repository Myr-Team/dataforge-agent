import copy
import importlib.util
import json
import sys
import threading
from datetime import datetime, timedelta, timezone

import backend.orchestrator as orchestrator
import backend.experiment_store as experiment_store
import backend.run_store as run_store
import pytest
from backend.blob_store import BlobJsonReadError
from concurrent.futures import ThreadPoolExecutor


@pytest.fixture(autouse=True)
def _restore_run_store_hooks():
    original = {
        "RUN_DIR": run_store.RUN_DIR,
        "blob_configured": run_store.blob_configured,
        "download_blob_json": run_store.download_blob_json,
        "download_blob_json_strict": run_store.download_blob_json_strict,
        "list_blob_json_named_strict": run_store.list_blob_json_named_strict,
        "upload_blob_json": run_store.upload_blob_json,
        "compare_and_swap_blob_json": run_store.compare_and_swap_blob_json,
        "delete_blob_name": run_store.delete_blob_name,
    }
    yield
    for name, value in original.items():
        setattr(run_store, name, value)
    run_store._ACTIVE.clear()


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
        if (
            initial_barrier is not None
            and name.startswith(run_store.WORKSPACE_LINEAGE_BLOB_PREFIX)
            and expected_revision == 0
        ):
            initial_barrier.wait(timeout=5)
        with remote_lock:
            current = remote.get(name) or {}
            if int(current.get("revision") or 0) != expected_revision:
                return None
            updated = {**current, **copy.deepcopy(changes)}
            remote[name] = updated
            return copy.deepcopy(updated)

    return remote, download, upload, compare_and_swap


def _configure_shared_store(store, root, download, upload, compare_and_swap) -> None:
    store.RUN_DIR = root
    store.blob_configured = lambda: True
    store.download_blob_json = download
    store.download_blob_json_strict = download
    store.list_blob_json_named_strict = lambda _prefix: []
    store.upload_blob_json = upload
    store.compare_and_swap_blob_json = compare_and_swap


def _older_than_pending_timeout() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


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
        store.download_blob_json_strict = download
        store.list_blob_json_named_strict = lambda _prefix: []
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
    monkeypatch.setattr(run_store, "download_blob_json_strict", lambda *_args, **_kwargs: {})
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


def test_registry_winner_with_candidate_blob_blocks_later_promotion(tmp_path, monkeypatch) -> None:
    remote, download, upload, compare_and_swap = _shared_blob_store()
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "blob_configured", lambda: True)
    monkeypatch.setattr(run_store, "download_blob_json", download)
    monkeypatch.setattr(run_store, "download_blob_json_strict", download)
    monkeypatch.setattr(run_store, "compare_and_swap_blob_json", compare_and_swap)
    run_store._ACTIVE.clear()
    workspace_id = "ws-candidate-final-blob"
    failed_run_id = "analysis-final-upload-failed"
    later_run_id = "analysis-later"

    def fail_trusted_final(name: str, value: dict):
        if value.get("run_id") == failed_run_id and value.get("canonical_lineage_status") == "trusted":
            raise RuntimeError("final run upload failed")
        return upload(name, value)

    monkeypatch.setattr(run_store, "upload_blob_json", fail_trusted_final)
    artifact = _analysis_artifact(workspace_id, failed_run_id)
    run_store.start_run(failed_run_id, workspace_id, "Analyze")
    failed = run_store.complete_run(
        failed_run_id,
        status="completed",
        final={"artifact": artifact},
        artifact=artifact,
    )
    monkeypatch.setattr(run_store, "upload_blob_json", upload)
    later_artifact = _analysis_artifact(workspace_id, later_run_id)
    run_store.start_run(later_run_id, workspace_id, "Analyze")
    later = run_store.complete_run(
        later_run_id,
        status="completed",
        final={"artifact": later_artifact},
        artifact=later_artifact,
    )

    assert failed["canonical_lineage_status"] == "candidate"
    assert later["canonical_lineage_status"] == "candidate"
    assert "canonical_experiment_run_id" not in later
    assert run_store.resolve_canonical_experiment_source_run_id(workspace_id, later_run_id) is None


def test_dormant_workspace_uses_durable_history_after_global_rows_evicted(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "blob_configured", lambda: False)
    run_store._ACTIVE.clear()
    workspace_id = "ws-dormant-history"
    canonical_id = "analysis-dormant-canonical"
    next_id = "analysis-dormant-next"
    artifact = _analysis_artifact(workspace_id, canonical_id)
    run_store.start_run(canonical_id, workspace_id, "Analyze")
    run_store.complete_run(canonical_id, status="completed", final={"artifact": artifact}, artifact=artifact)
    registry = run_store.authoritative_run_registry()
    registry["history_truncated"] = True
    registry["runs"] = [item for item in registry["runs"] if item.get("workspace_id") != workspace_id]
    run_store._write_local_registry(registry)

    next_artifact = _analysis_artifact(workspace_id, next_id)
    run_store.start_run(next_id, workspace_id, "Analyze")
    completed = run_store.complete_run(
        next_id,
        status="completed",
        final={"artifact": next_artifact},
        artifact=next_artifact,
    )

    assert completed["canonical_lineage_status"] == "trusted"
    assert completed["canonical_experiment_run_id"] == canonical_id


def test_first_analysis_in_truncated_global_registry_has_durable_genesis_proof(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "blob_configured", lambda: False)
    run_store._ACTIVE.clear()
    run_store._write_local_registry(
        {"version": 2, "revision": 9, "history_truncated": True, "runs": []}
    )
    workspace_id = "ws-new-after-global-truncation"
    run_store.initialize_workspace_lineage(workspace_id, no_prior_analysis=True)
    run_id = "analysis-first"
    artifact = _analysis_artifact(workspace_id, run_id)
    run_store.start_run(run_id, workspace_id, "Analyze")

    completed = run_store.complete_run(
        run_id,
        status="completed",
        final={"artifact": artifact},
        artifact=artifact,
    )
    state = run_store.workspace_lineage_state(workspace_id)

    assert completed["canonical_lineage_status"] == "trusted"
    assert state["genesis_proof"] == "no_prior_analysis"
    assert state["analysis_count"] == 1
    assert state["latest_run_id"] == run_id


def test_stale_local_candidate_cannot_overwrite_confirmed_remote_run(tmp_path, monkeypatch) -> None:
    remote, download, upload, compare_and_swap = _shared_blob_store()
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "blob_configured", lambda: True)
    monkeypatch.setattr(run_store, "download_blob_json", download)
    monkeypatch.setattr(run_store, "download_blob_json_strict", download)
    monkeypatch.setattr(run_store, "upload_blob_json", upload)
    monkeypatch.setattr(run_store, "compare_and_swap_blob_json", compare_and_swap)
    run_store._ACTIVE.clear()
    workspace_id = "ws-stale-local-protection"
    run_id = "analysis-confirmed-remote"
    artifact = _analysis_artifact(workspace_id, run_id)
    run_store.start_run(run_id, workspace_id, "Analyze")
    confirmed = run_store.complete_run(run_id, status="completed", final={"artifact": artifact}, artifact=artifact)
    blob_name = f"{run_store.RUN_BLOB_PREFIX}/{run_store._safe_name(run_id)}.json"
    confirmed_blob = copy.deepcopy(remote[blob_name])
    stale = copy.deepcopy(confirmed)
    for key in run_store._LINEAGE_ENVELOPE_KEYS:
        stale.pop(key, None)
    stale["canonical_resolution_status"] = "unresolved"
    stale["canonical_lineage_status"] = "candidate"
    local_path = tmp_path / "runs" / f"{run_store._safe_name(run_id)}.json"
    local_path.write_text(json.dumps(stale), encoding="utf-8")

    result = run_store.update_run_proposal(
        run_id,
        {"artifact_urls": {"pdf": "/api/artifacts/must-not-overwrite.pdf"}},
    )

    assert remote[blob_name] == confirmed_blob
    assert result["canonical_lineage_commit_id"] == confirmed["canonical_lineage_commit_id"]
    assert (result.get("persistence") or {}).get("update_status") == "unavailable"


def test_concurrent_purge_and_other_workspace_completion_preserve_registry(tmp_path) -> None:
    purge_store = _isolated_run_store("run_store_purge_instance")
    completion_store = _isolated_run_store("run_store_completion_instance")
    old_row = {
        "run_id": "old-purge-run",
        "workspace_id": "ws-purge",
        "status": "completed",
        "time": "2026-01-01T00:00:00Z",
    }
    remote = {
        run_store.RUN_REGISTRY_BLOB: {
            "version": 2,
            "revision": 7,
            "history_truncated": True,
            "runs": [old_row],
        }
    }
    remote_lock = threading.Lock()
    purge_read = threading.Event()
    completion_committed = threading.Event()

    def download(name: str):
        with remote_lock:
            value = copy.deepcopy(remote.get(name))
        if name == run_store.RUN_REGISTRY_BLOB and threading.current_thread().name == "purge-thread":
            purge_read.set()
        return value

    def upload(name: str, value: dict):
        if name == run_store.RUN_REGISTRY_BLOB and threading.current_thread().name == "purge-thread":
            completion_committed.wait(timeout=5)
        with remote_lock:
            remote[name] = copy.deepcopy(value)
        return {"blob_name": name}

    def compare_and_swap(name: str, *, expected_revision: int, changes: dict):
        if name == run_store.RUN_REGISTRY_BLOB and threading.current_thread().name == "purge-thread":
            completion_committed.wait(timeout=5)
        with remote_lock:
            current = remote.get(name) or {}
            if int(current.get("revision") or 0) != expected_revision:
                return None
            updated = {**current, **copy.deepcopy(changes)}
            remote[name] = updated
        if name == run_store.RUN_REGISTRY_BLOB and threading.current_thread().name != "purge-thread":
            completion_committed.set()
        return copy.deepcopy(updated)

    for index, store in enumerate((purge_store, completion_store)):
        store.RUN_DIR = tmp_path / f"instance-{index}"
        store.blob_configured = lambda: True
        store.download_blob_json = download
        store.download_blob_json_strict = download
        store.upload_blob_json = upload
        store.compare_and_swap_blob_json = compare_and_swap
        store.delete_blob_name = lambda *_args, **_kwargs: False
    purge_store.list_runs = lambda workspace_id=None: [old_row]
    completion_store.initialize_workspace_lineage("ws-complete", no_prior_analysis=True)

    purge_thread = threading.Thread(
        target=lambda: purge_store.purge_workspace_runs("ws-purge"),
        name="purge-thread",
    )
    purge_thread.start()
    assert purge_read.wait(timeout=5)
    workspace_id = "ws-complete"
    run_id = "analysis-concurrent-complete"
    artifact = _analysis_artifact(workspace_id, run_id)
    completion_store.start_run(run_id, workspace_id, "Analyze")
    completion_store.complete_run(run_id, status="completed", final={"artifact": artifact}, artifact=artifact)
    completion_committed.set()
    purge_thread.join(timeout=5)

    registry = remote[run_store.RUN_REGISTRY_BLOB]
    assert registry["history_truncated"] is True
    assert registry["revision"] > 7
    assert any(item.get("run_id") == run_id for item in registry["runs"])
    assert all(item.get("workspace_id") != "ws-purge" for item in registry["runs"])


def test_transient_durable_reads_never_create_genesis_or_overwrite_confirmed_run(tmp_path, monkeypatch) -> None:
    remote, download, upload, compare_and_swap = _shared_blob_store()
    _configure_shared_store(run_store, tmp_path / "runs", download, upload, compare_and_swap)
    run_store._ACTIVE.clear()
    workspace_id = "ws-strict-read-history"
    run_id = "analysis-strict-confirmed"
    artifact = _analysis_artifact(workspace_id, run_id)
    run_store.start_run(run_id, workspace_id, "Analyze")
    confirmed = run_store.complete_run(run_id, status="completed", final={"artifact": artifact}, artifact=artifact)
    run_blob = f"{run_store.RUN_BLOB_PREFIX}/{run_store._safe_name(run_id)}.json"
    confirmed_blob = copy.deepcopy(remote[run_blob])

    def registry_unavailable(name: str):
        if name == run_store.RUN_REGISTRY_BLOB:
            raise BlobJsonReadError("transient registry read failure")
        return download(name)

    monkeypatch.setattr(run_store, "download_blob_json", registry_unavailable)
    monkeypatch.setattr(run_store, "download_blob_json_strict", registry_unavailable, raising=False)
    new_workspace = "ws-read-failure-must-not-genesis"
    initialized = run_store.initialize_workspace_lineage(new_workspace, no_prior_analysis=True)

    def unavailable(_name: str):
        raise BlobJsonReadError("transient durable read failure")

    monkeypatch.setattr(run_store, "download_blob_json", unavailable)
    monkeypatch.setattr(run_store, "download_blob_json_strict", unavailable, raising=False)
    stale_path = tmp_path / "runs" / f"{run_store._safe_name(run_id)}.json"
    stale = json.loads(stale_path.read_text(encoding="utf-8"))
    for key in run_store._LINEAGE_ENVELOPE_KEYS:
        stale.pop(key, None)
    stale["canonical_lineage_status"] = "candidate"
    stale["canonical_resolution_status"] = "unresolved"
    stale_path.write_text(json.dumps(stale), encoding="utf-8")
    result = run_store.update_run_proposal(run_id, {"artifact_urls": {"pdf": "/must-not-write.pdf"}})

    assert initialized == {}
    assert run_store._workspace_lineage_blob(new_workspace) not in remote
    assert remote[run_blob] == confirmed_blob
    assert "canonical_experiment_run_id" not in result
    assert (result.get("persistence") or {}).get("update_status") == "unavailable"


def test_truncated_global_registry_hydrates_all_canonical_versions_and_attachments(tmp_path, monkeypatch) -> None:
    remote, download, upload, compare_and_swap = _shared_blob_store()
    _configure_shared_store(run_store, tmp_path / "writer-runs", download, upload, compare_and_swap)
    monkeypatch.setattr(experiment_store, "EXPERIMENT_DIR", tmp_path / "experiments")
    run_store._ACTIVE.clear()
    workspace_id = "ws-durable-canonical-history"
    run_ids = ["analysis-history-v1", "analysis-history-v2"]
    artifacts = []
    for ordinal, run_id in enumerate(run_ids, start=1):
        artifact = _analysis_artifact(workspace_id, run_id)
        evidence = artifact["feasibility"]["dimensions"][0]["evidence"][0]
        evidence["ref"] = f"evidence.csv#row-{ordinal}"
        evidence["file_version"] = str(ordinal)
        artifacts.append(artifact)
        run_store.start_run(run_id, workspace_id, "Analyze")
        completed = run_store.complete_run(
            run_id,
            status="completed",
            final={"artifact": artifact},
            artifact=artifact,
        )
        assert completed["canonical_experiment_run_id"] == run_id
        snapshot = run_store.record_artifact_version(
            workspace_id=workspace_id,
            source_run_id=run_id,
            experiment_version_id=f"version:{run_id}",
            artifact=artifact,
            proposal={"artifact_urls": {"pdf": f"/api/artifacts/v{ordinal}.pdf"}},
            kinds=["pdf"],
        )
        assert snapshot is not None

    registry = copy.deepcopy(remote[run_store.RUN_REGISTRY_BLOB])
    remote[run_store.RUN_REGISTRY_BLOB] = {
        **registry,
        "revision": int(registry.get("revision") or 0) + 1,
        "history_truncated": True,
        "runs": [],
    }
    run_store.RUN_DIR = tmp_path / "fresh-instance-runs"

    ledger = experiment_store.sync_experiment_ledger(workspace_id, [], outcomes=[])

    assert [item["label"] for item in ledger["versions"]] == ["V1", "V2"]
    assert [item["run_id"] for item in ledger["versions"]] == run_ids
    assert ledger["versions"][0]["attachments"]["artifacts"][0]["urls"]["pdf"].endswith("v1.pdf")
    assert ledger["versions"][1]["attachments"]["artifacts"][0]["urls"]["pdf"].endswith("v2.pdf")
    assert ledger["lineage_resolution"]["status"] == "resolved"


def test_stale_pending_without_registry_commit_recovers_and_allows_next_analysis(tmp_path) -> None:
    remote, download, upload, compare_and_swap = _shared_blob_store()
    _configure_shared_store(run_store, tmp_path / "runs", download, upload, compare_and_swap)
    run_store._ACTIVE.clear()
    workspace_id = "ws-recover-reservation-crash"
    state = run_store.initialize_workspace_lineage(workspace_id, no_prior_analysis=True)
    crashed_artifact = _analysis_artifact(workspace_id, "analysis-crashed-reservation")
    pending = run_store._cas_workspace_lineage(
        state,
        {
            "status": "pending",
            "pending_run_id": "analysis-crashed-reservation",
            "pending_started_at": _older_than_pending_timeout(),
            "pending_candidate_content_sha256": run_store._analysis_content_hash({"artifact": crashed_artifact, "workspace_id": workspace_id}),
        },
    )
    assert pending
    next_id = "analysis-after-reservation-crash"
    next_artifact = _analysis_artifact(workspace_id, next_id)
    run_store.start_run(next_id, workspace_id, "Analyze")

    completed = run_store.complete_run(
        next_id,
        status="completed",
        final={"artifact": next_artifact},
        artifact=next_artifact,
    )
    recovered = run_store.workspace_lineage_state(workspace_id)

    assert completed["canonical_lineage_status"] == "trusted"
    assert completed["canonical_experiment_run_id"] == next_id
    assert recovered["status"] == "stable"
    assert recovered["latest_run_id"] == next_id
    assert recovered["last_failed_pending_run_id"] == "analysis-crashed-reservation"


def test_stale_pending_with_exact_final_blob_is_confirmed_before_next_reservation(tmp_path, monkeypatch) -> None:
    remote, download, upload, compare_and_swap = _shared_blob_store()
    _configure_shared_store(run_store, tmp_path / "runs", download, upload, compare_and_swap)
    run_store._ACTIVE.clear()
    workspace_id = "ws-recover-final-blob"
    first_id = "analysis-published-before-crash"
    first_artifact = _analysis_artifact(workspace_id, first_id)
    run_store.initialize_workspace_lineage(workspace_id, no_prior_analysis=True)
    original_finalize = run_store._finalize_workspace_lineage
    monkeypatch.setattr(run_store, "_finalize_workspace_lineage", lambda *_args, **_kwargs: {})
    run_store.start_run(first_id, workspace_id, "Analyze")
    interrupted = run_store.complete_run(
        first_id,
        status="completed",
        final={"artifact": first_artifact},
        artifact=first_artifact,
    )
    monkeypatch.setattr(run_store, "_finalize_workspace_lineage", original_finalize)
    state = run_store.workspace_lineage_state(workspace_id)
    state = run_store._cas_workspace_lineage(state, {"pending_started_at": _older_than_pending_timeout()})
    assert interrupted["canonical_lineage_status"] == "candidate"
    assert state and state["status"] == "pending"

    second_id = "analysis-after-final-blob-crash"
    second_artifact = _analysis_artifact(workspace_id, second_id)
    run_store.start_run(second_id, workspace_id, "Analyze")
    completed = run_store.complete_run(
        second_id,
        status="completed",
        final={"artifact": second_artifact},
        artifact=second_artifact,
    )
    recovered = run_store.workspace_lineage_state(workspace_id)

    assert completed["canonical_lineage_status"] == "trusted"
    assert completed["canonical_experiment_run_id"] == first_id
    assert recovered["status"] == "stable"
    assert [item["run_id"] for item in recovered["canonical_history"]] == [first_id]
    assert recovered["latest_run_id"] == second_id


def test_same_workspace_purge_owns_lifecycle_before_deletion_and_blocks_analysis(tmp_path) -> None:
    purge_store = _isolated_run_store("run_store_r9_purge")
    analysis_store = _isolated_run_store("run_store_r9_analysis")
    remote, download, upload, compare_and_swap = _shared_blob_store()
    _configure_shared_store(purge_store, tmp_path / "purge", download, upload, compare_and_swap)
    _configure_shared_store(analysis_store, tmp_path / "analysis", download, upload, compare_and_swap)
    purge_store.delete_blob_name = lambda name: remote.pop(name, None) is not None
    workspace_id = "ws-same-workspace-purge"
    old_id = "analysis-before-purge"
    old_artifact = _analysis_artifact(workspace_id, old_id)
    analysis_store.start_run(old_id, workspace_id, "Analyze")
    analysis_store.complete_run(old_id, status="completed", final={"artifact": old_artifact}, artifact=old_artifact)
    entered_delete_phase = threading.Event()
    release_delete_phase = threading.Event()
    old_summary = copy.deepcopy(remote[run_store.RUN_REGISTRY_BLOB]["runs"][0])

    def blocked_list(_workspace_id=None):
        entered_delete_phase.set()
        release_delete_phase.wait(timeout=5)
        return [old_summary]

    purge_store.list_runs = blocked_list
    purge_result: dict[str, object] = {}
    purge_thread = threading.Thread(
        target=lambda: purge_result.update(purge_store.purge_workspace_runs(workspace_id)),
        name="same-workspace-purge",
    )
    purge_thread.start()
    assert entered_delete_phase.wait(timeout=5)
    new_id = "analysis-during-purge"
    new_artifact = _analysis_artifact(workspace_id, new_id)
    new_artifact["feasibility"]["dimensions"][0]["evidence"][0]["file_version"] = "2"
    analysis_store.start_run(new_id, workspace_id, "Analyze")
    concurrent = analysis_store.complete_run(
        new_id,
        status="completed",
        final={"artifact": new_artifact},
        artifact=new_artifact,
    )
    release_delete_phase.set()
    purge_thread.join(timeout=5)

    state = purge_store.workspace_lineage_state(workspace_id)
    new_blob = f"{run_store.RUN_BLOB_PREFIX}/{run_store._safe_name(new_id)}.json"
    assert concurrent["canonical_lineage_status"] == "candidate"
    assert new_blob not in remote
    assert all(item.get("workspace_id") != workspace_id for item in remote[run_store.RUN_REGISTRY_BLOB]["runs"])
    assert state["status"] == "purged"
    assert state["analysis_count"] == 0
    assert state["canonical_history"] == []
    assert purge_result["registry_updated"] is True


def test_purge_registry_retry_failure_leaves_purging_and_blocks_resurrection(tmp_path) -> None:
    purge_store = _isolated_run_store("run_store_r9_purge_failure")
    analysis_store = _isolated_run_store("run_store_r9_analysis_failure")
    remote, download, upload, compare_and_swap = _shared_blob_store()
    _configure_shared_store(purge_store, tmp_path / "purge", download, upload, compare_and_swap)
    _configure_shared_store(analysis_store, tmp_path / "analysis", download, upload, compare_and_swap)
    purge_store.delete_blob_name = lambda name: remote.pop(name, None) is not None
    workspace_id = "ws-purge-registry-failure"
    old_id = "analysis-before-failed-purge"
    artifact = _analysis_artifact(workspace_id, old_id)
    analysis_store.start_run(old_id, workspace_id, "Analyze")
    analysis_store.complete_run(old_id, status="completed", final={"artifact": artifact}, artifact=artifact)

    def fail_registry(name: str, *, expected_revision: int, changes: dict):
        if name == run_store.RUN_REGISTRY_BLOB:
            return None
        return compare_and_swap(name, expected_revision=expected_revision, changes=changes)

    purge_store.compare_and_swap_blob_json = fail_registry
    result = purge_store.purge_workspace_runs(workspace_id)
    state = purge_store.workspace_lineage_state(workspace_id)
    next_id = "analysis-after-failed-purge"
    next_artifact = _analysis_artifact(workspace_id, next_id)
    analysis_store.start_run(next_id, workspace_id, "Analyze")
    attempted = analysis_store.complete_run(
        next_id,
        status="completed",
        final={"artifact": next_artifact},
        artifact=next_artifact,
    )

    assert result["registry_updated"] is False
    assert state["status"] == "purging"
    assert attempted["canonical_lineage_status"] == "candidate"
    assert f"{run_store.RUN_BLOB_PREFIX}/{run_store._safe_name(next_id)}.json" not in remote


def test_hydration_replaces_local_candidate_with_exact_confirmed_blob_payload(tmp_path, monkeypatch) -> None:
    remote, download, upload, compare_and_swap = _shared_blob_store()
    _configure_shared_store(run_store, tmp_path / "runs", download, upload, compare_and_swap)
    monkeypatch.setattr(experiment_store, "EXPERIMENT_DIR", tmp_path / "experiments")
    monkeypatch.setattr(experiment_store, "upload_blob_json", lambda *_args, **_kwargs: {})
    workspace_id = "ws-authoritative-hydration-payload"
    run_id = "analysis-authoritative-payload"
    artifact = _analysis_artifact(workspace_id, run_id)
    run_store.start_run(run_id, workspace_id, "Analyze")
    confirmed = run_store.complete_run(
        run_id,
        status="completed",
        final={"artifact": artifact},
        artifact=artifact,
    )
    local_candidate = copy.deepcopy(confirmed)
    local_candidate["artifact"]["feasibility"]["verdict"] = "recommended"
    local_candidate["final"]["artifact"]["feasibility"]["verdict"] = "recommended"
    candidate_evidence = local_candidate["artifact"]["feasibility"]["dimensions"][0]["evidence"][0]
    candidate_evidence.update({"ref": "fabricated.csv#row-999", "file_id": "fabricated.csv", "file_version": "999"})
    local_candidate["final"]["artifact"] = copy.deepcopy(local_candidate["artifact"])
    local_candidate["canonical_lineage_status"] = "candidate"
    local_candidate["canonical_resolution_status"] = "unresolved"

    ledger = experiment_store.sync_experiment_ledger(workspace_id, [local_candidate], outcomes=[])

    version = ledger["versions"][0]
    assert version["decision"]["verdict"] == "conditional"
    assert [item["ref"] for item in version["evidence"]] == ["evidence.csv#row-1"]
    assert [item["source"]["file_version"] for item in version["evidence"]] == ["1"]
    assert ledger["lineage_resolution"]["status"] == "resolved"


def test_empty_run_list_with_strict_lineage_read_error_is_explicitly_unavailable(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(experiment_store, "EXPERIMENT_DIR", tmp_path / "experiments")
    monkeypatch.setattr(run_store, "blob_configured", lambda: True)
    monkeypatch.setattr(
        run_store,
        "download_blob_json_strict",
        lambda _name: (_ for _ in ()).throw(BlobJsonReadError("transient durable read failure")),
    )
    monkeypatch.setattr(experiment_store, "upload_blob_json", lambda *_args, **_kwargs: {})

    ledger = experiment_store.sync_experiment_ledger("ws-empty-strict-read-error", [], outcomes=[])

    assert ledger["versions"] == []
    assert ledger["lineage_resolution"]["status"] == "unavailable"
    assert ledger["lineage_resolution"]["unresolved_run_ids"] == ["lineage-storage-unavailable"]


def test_persisted_canonical_history_membership_survives_normalization_context_drift(tmp_path, monkeypatch) -> None:
    remote, download, upload, compare_and_swap = _shared_blob_store()
    _configure_shared_store(run_store, tmp_path / "writer-runs", download, upload, compare_and_swap)
    monkeypatch.setattr(experiment_store, "EXPERIMENT_DIR", tmp_path / "experiments")
    monkeypatch.setattr(experiment_store, "upload_blob_json", lambda *_args, **_kwargs: {})
    workspace_id = "ws-canonical-history-normalization-drift"
    run_ids = ["analysis-drift-v1", "analysis-drift-v2"]
    artifacts: list[dict] = []
    for ordinal, run_id in enumerate(run_ids, start=1):
        artifact = _analysis_artifact(workspace_id, run_id)
        evidence = artifact["feasibility"]["dimensions"][0]["evidence"][0]
        evidence.update({"ref": f"evidence.csv#row-{ordinal}", "file_version": str(ordinal)})
        artifacts.append(artifact)
        run_store.start_run(run_id, workspace_id, "Analyze")
        completed = run_store.complete_run(
            run_id,
            status="completed",
            final={"artifact": artifact},
            artifact=artifact,
        )
        assert completed["canonical_experiment_run_id"] == run_id

    first_snapshot = experiment_store._evidence_snapshot(
        artifacts[0],
        artifacts[0]["feasibility"],
    )
    monkeypatch.setattr(
        experiment_store,
        "_evidence_snapshot",
        lambda *_args, **_kwargs: copy.deepcopy(first_snapshot),
    )
    run_store.RUN_DIR = tmp_path / "fresh-reader-runs"

    ledger = experiment_store.sync_experiment_ledger(workspace_id, [], outcomes=[])

    assert [item["run_id"] for item in ledger["versions"]] == run_ids
    assert [item["label"] for item in ledger["versions"]] == ["V1", "V2"]
    assert ledger["lineage_resolution"]["status"] == "resolved"


def test_concurrent_purge_blocks_existing_update_and_generic_completion(tmp_path) -> None:
    purge_store = _isolated_run_store("run_store_r10_purge_writers")
    writer_store = _isolated_run_store("run_store_r10_concurrent_writers")
    remote, download, upload, compare_and_swap = _shared_blob_store()
    _configure_shared_store(purge_store, tmp_path / "purge", download, upload, compare_and_swap)
    _configure_shared_store(writer_store, tmp_path / "writer", download, upload, compare_and_swap)
    purge_store.delete_blob_name = lambda name: remote.pop(name, None) is not None
    workspace_id = "ws-purge-blocks-all-writers"
    source_id = "analysis-before-writer-purge"
    artifact = _analysis_artifact(workspace_id, source_id)
    writer_store.start_run(source_id, workspace_id, "Analyze")
    writer_store.complete_run(source_id, status="completed", final={"artifact": artifact}, artifact=artifact)
    entered_delete_phase = threading.Event()
    release_delete_phase = threading.Event()
    source_summary = copy.deepcopy(remote[run_store.RUN_REGISTRY_BLOB]["runs"][0])

    def blocked_list(_workspace_id=None):
        entered_delete_phase.set()
        release_delete_phase.wait(timeout=5)
        return [source_summary]

    purge_store.list_runs = blocked_list
    purge_result: dict[str, object] = {}
    purge_thread = threading.Thread(
        target=lambda: purge_result.update(purge_store.purge_workspace_runs(workspace_id)),
        name="purge-blocks-generic-and-update",
    )
    purge_thread.start()
    assert entered_delete_phase.wait(timeout=5)
    updated = writer_store.update_run_proposal(
        source_id,
        {"artifact_urls": {"pdf": "/must-not-persist-during-purge.pdf"}},
    )
    generic_id = "generic-completion-during-purge"
    writer_store.start_run(generic_id, workspace_id, "Follow-up")
    generic = writer_store.complete_run(generic_id, status="completed", final={"text": "Follow-up"})
    release_delete_phase.set()
    purge_thread.join(timeout=5)

    assert updated is not None
    assert (updated.get("persistence") or {}).get("confirmed") is False
    assert generic is not None
    assert (generic.get("persistence") or {}).get("confirmed") is False
    assert f"{run_store.RUN_BLOB_PREFIX}/{run_store._safe_name(generic_id)}.json" not in remote
    assert purge_result["status"] == "purged"


def test_purge_deletion_verification_error_leaves_workspace_purging(tmp_path) -> None:
    remote, download, upload, compare_and_swap = _shared_blob_store()
    _configure_shared_store(run_store, tmp_path / "runs", download, upload, compare_and_swap)
    workspace_id = "ws-purge-delete-verification-error"
    run_id = "analysis-delete-verification-error"
    artifact = _analysis_artifact(workspace_id, run_id)
    run_store.start_run(run_id, workspace_id, "Analyze")
    run_store.complete_run(run_id, status="completed", final={"artifact": artifact}, artifact=artifact)
    run_blob = f"{run_store.RUN_BLOB_PREFIX}/{run_store._safe_name(run_id)}.json"
    deletion_attempted = threading.Event()

    def delete_without_confirmation(name: str) -> bool:
        if name == run_blob:
            deletion_attempted.set()
        return False

    def strict_download(name: str):
        if name == run_blob and deletion_attempted.is_set():
            raise BlobJsonReadError("delete verification unavailable")
        return download(name)

    run_store.delete_blob_name = delete_without_confirmation
    run_store.download_blob_json_strict = strict_download

    result = run_store.purge_workspace_runs(workspace_id)
    state = run_store.workspace_lineage_state(workspace_id)

    assert result["status"] == "unavailable"
    assert result["registry_updated"] is False
    assert state["status"] == "purging"
    assert run_blob in remote


def test_legacy_trusted_hydration_migrates_lineage_and_uses_full_remote_payload(tmp_path, monkeypatch) -> None:
    remote, download, upload, compare_and_swap = _shared_blob_store()
    _configure_shared_store(run_store, tmp_path / "writer-runs", download, upload, compare_and_swap)
    monkeypatch.setattr(experiment_store, "EXPERIMENT_DIR", tmp_path / "experiments")
    monkeypatch.setattr(experiment_store, "upload_blob_json", lambda *_args, **_kwargs: {})
    workspace_id = "ws-legacy-trusted-migration"
    run_id = "analysis-legacy-trusted"
    artifact = _analysis_artifact(workspace_id, run_id)
    run_store.start_run(run_id, workspace_id, "Analyze")
    confirmed = run_store.complete_run(
        run_id,
        status="completed",
        final={"artifact": artifact},
        artifact=artifact,
    )
    remote.pop(run_store._workspace_lineage_blob(workspace_id))
    supplied_local = copy.deepcopy(confirmed)
    supplied_local["artifact"]["feasibility"]["verdict"] = "recommended"
    supplied_local["artifact"]["feasibility"]["dimensions"][0]["evidence"][0].update(
        {"ref": "local-only.csv#row-9", "file_id": "local-only.csv", "file_version": "9"}
    )
    supplied_local["final"]["artifact"] = copy.deepcopy(supplied_local["artifact"])
    run_store.RUN_DIR = tmp_path / "fresh-reader-runs"

    ledger = experiment_store.sync_experiment_ledger(workspace_id, [supplied_local], outcomes=[])
    migrated = run_store.workspace_lineage_state(workspace_id)

    version = ledger["versions"][0]
    assert version["decision"]["verdict"] == "conditional"
    assert [item["ref"] for item in version["evidence"]] == ["evidence.csv#row-1"]
    assert version["ordinal"] == 1
    assert migrated["status"] == "stable"
    assert migrated["genesis_proof"] == "registry_history_complete"
    assert [item["run_id"] for item in migrated["canonical_history"]] == [run_id]
    assert migrated["canonical_history"][0]["ordinal"] == 1
    assert ledger["lineage_resolution"]["status"] == "resolved"


def test_late_writer_after_purge_enumeration_prevents_purged_success(tmp_path) -> None:
    purge_store = _isolated_run_store("run_store_r11_late_writer_purge")
    writer_store = _isolated_run_store("run_store_r11_late_writer")
    remote, download, upload, compare_and_swap = _shared_blob_store()
    _configure_shared_store(purge_store, tmp_path / "purge", download, upload, compare_and_swap)
    _configure_shared_store(writer_store, tmp_path / "writer", download, upload, compare_and_swap)
    workspace_id = "ws-late-writer-purge"
    source_id = "analysis-before-late-writer"
    source_artifact = _analysis_artifact(workspace_id, source_id)
    writer_store.start_run(source_id, workspace_id, "Analyze")
    writer_store.complete_run(
        source_id,
        status="completed",
        final={"artifact": source_artifact},
        artifact=source_artifact,
    )
    generic_id = "generic-publishes-after-enumeration"
    generic_blob = f"{run_store.RUN_BLOB_PREFIX}/{run_store._safe_name(generic_id)}.json"
    source_blob = f"{run_store.RUN_BLOB_PREFIX}/{run_store._safe_name(source_id)}.json"
    upload_entered = threading.Event()
    release_upload = threading.Event()
    registry_removed = threading.Event()
    release_registry_remove = threading.Event()

    def delayed_upload(name: str, value: dict):
        if name == generic_blob:
            upload_entered.set()
            release_upload.wait(timeout=5)
        return upload(name, value)

    def purge_compare_and_swap(name: str, *, expected_revision: int, changes: dict):
        committed = compare_and_swap(name, expected_revision=expected_revision, changes=changes)
        if (
            committed
            and name == run_store.RUN_REGISTRY_BLOB
            and all(
                str(item.get("workspace_id") or "") != workspace_id
                for item in changes.get("runs") or []
                if isinstance(item, dict)
            )
        ):
            registry_removed.set()
            release_registry_remove.wait(timeout=5)
        return committed

    def named_runs(_prefix: str):
        result = []
        for name in (source_blob, generic_blob):
            value = download(name)
            if isinstance(value, dict):
                result.append((name, value))
        return result

    writer_store.upload_blob_json = delayed_upload
    writer_store.delete_blob_name = lambda name: remote.pop(name, None) is not None
    purge_store.compare_and_swap_blob_json = purge_compare_and_swap
    purge_store.list_blob_json_named_strict = named_runs
    purge_store.delete_blob_name = lambda name: remote.pop(name, None) is not None
    writer_result: dict[str, object] = {}
    purge_result: dict[str, object] = {}
    writer_store.start_run(generic_id, workspace_id, "Follow-up")
    writer_thread = threading.Thread(
        target=lambda: writer_result.update(
            writer_store.complete_run(generic_id, status="completed", final={"text": "Follow-up"}) or {}
        ),
        name="late-generic-writer",
    )
    purge_thread = threading.Thread(
        target=lambda: purge_result.update(purge_store.purge_workspace_runs(workspace_id)),
        name="purge-with-late-writer",
    )
    writer_thread.start()
    assert upload_entered.wait(timeout=5)
    purge_thread.start()
    try:
        assert registry_removed.wait(timeout=5)
        release_upload.set()
        writer_thread.join(timeout=5)
        release_registry_remove.set()
        purge_thread.join(timeout=5)
    finally:
        release_upload.set()
        release_registry_remove.set()
        writer_thread.join(timeout=5)
        purge_thread.join(timeout=5)

    state = purge_store.workspace_lineage_state(workspace_id)
    assert (writer_result.get("persistence") or {}).get("confirmed") is False
    assert purge_result["status"] == "unavailable"
    assert state["status"] == "purging"
    assert state["last_rejected_writer_run_id"] == generic_id
    assert generic_blob not in remote


def test_failed_existing_analysis_update_preserves_confirmed_blob_and_envelope(tmp_path, monkeypatch) -> None:
    remote, download, upload, compare_and_swap = _shared_blob_store()
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "blob_configured", lambda: True)
    monkeypatch.setattr(run_store, "download_blob_json", download)
    monkeypatch.setattr(run_store, "download_blob_json_strict", download)
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
    monkeypatch.setattr(run_store, "download_blob_json_strict", download)
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
    monkeypatch.setattr(run_store, "download_blob_json_strict", lambda *args, **kwargs: {})
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
    monkeypatch.setattr(run_store, "download_blob_json_strict", lambda *args, **kwargs: {})
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
    monkeypatch.setattr(run_store, "download_blob_json_strict", lambda *args, **kwargs: {})
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
    monkeypatch.setattr(run_store, "download_blob_json_strict", lambda *args, **kwargs: {})
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
    monkeypatch.setattr(
        run_store,
        "download_blob_json_strict",
        lambda blob_name: {"history_truncated": True, "runs": [duplicate]},
    )

    assert run_store.resolve_canonical_experiment_source_run_id(workspace_id, duplicate_id) is None
    assert run_store._canonical_experiment_version_exists(workspace_id, f"version:{duplicate_id}") is False


@pytest.mark.parametrize("kind", ["pdf", "roadmap", "validation_plan"])
def test_producer_resolves_duplicate_source_for_every_artifact_path(kind, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "upload_blob_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_store, "download_blob_json", lambda *args, **kwargs: {})
    monkeypatch.setattr(run_store, "download_blob_json_strict", lambda *args, **kwargs: {})
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
