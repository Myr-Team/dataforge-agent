from __future__ import annotations

from types import SimpleNamespace

import backend.control_plane as control_plane
import backend.experiment_store as experiment_store
import backend.migrate_lineage_sql as migrate_lineage_sql
import backend.run_store as run_store


class _SqlReadRepository:
    def __init__(self) -> None:
        self.versions = (
            SimpleNamespace(
                version_id="99999999-9999-4999-8999-999999999999",
                workspace_id="ws-sql-led-read",
                generation=1,
                ordinal=9,
                canonical_run_id="analysis-v9",
                decision_fingerprint="9" * 64,
                evidence_fingerprint="8" * 64,
                created=False,
            ),
            SimpleNamespace(
                version_id="33333333-3333-4333-8333-333333333333",
                workspace_id="ws-sql-led-read",
                generation=1,
                ordinal=3,
                canonical_run_id="analysis-v3",
                decision_fingerprint="3" * 64,
                evidence_fingerprint="2" * 64,
                created=False,
            ),
        )

    def workspace_exists(self, *, workspace_id: str) -> bool:
        return workspace_id == "ws-sql-led-read"

    def current_generation(self, *, workspace_id: str) -> int:
        assert workspace_id == "ws-sql-led-read"
        return 1

    def list_versions(self, *, workspace_id: str, generation: int):
        assert workspace_id == "ws-sql-led-read"
        assert generation == 1
        return self.versions

    def list_attachments(self, *, workspace_id: str, generation: int):
        assert workspace_id == "ws-sql-led-read"
        assert generation == 1
        return ()


class _NoSqlWorkspaceRepository:
    def workspace_exists(self, *, workspace_id: str) -> bool:
        assert workspace_id
        return False


class _RacingSqlReadRepository(_SqlReadRepository):
    def __init__(self) -> None:
        super().__init__()
        self.existence_reads = 0

    def workspace_exists(self, *, workspace_id: str) -> bool:
        assert workspace_id == "ws-sql-led-read"
        self.existence_reads += 1
        return self.existence_reads > 1


def _analysis_payload(run_id: str) -> dict:
    artifact = {
        "feasibility": {
            "opportunity_id": run_id,
            "verdict": "conditional",
            "overall_confidence": "data_confirmed",
            "dimensions": [],
            "gap_list": [],
        }
    }
    return {
        "run_id": run_id,
        "workspace_id": "ws-sql-led-read",
        "status": "completed",
        "completed_at": "2026-07-15T00:00:00Z",
        "artifact": artifact,
        "final": {"artifact": artifact},
    }


def _trusted_legacy_payload(run_id: str) -> dict:
    payload = _analysis_payload(run_id)
    payload["workspace_id"] = "ws-legacy-read"
    run_store._mark_canonical_lineage_trusted(payload, run_id, sequence=1)
    return payload


def test_public_ledger_reads_sql_ordinals_before_blob_run_listing(monkeypatch) -> None:
    repository = _SqlReadRepository()
    payloads = {
        "analysis-v3": _analysis_payload("analysis-v3"),
        "analysis-v9": _analysis_payload("analysis-v9"),
    }
    monkeypatch.setattr(experiment_store, "_LINEAGE_REPOSITORY_PROVIDER", lambda: repository)
    monkeypatch.setattr(
        control_plane,
        "list_runs",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Blob listing ran before SQL")),
    )
    monkeypatch.setattr(run_store, "get_run", lambda run_id: payloads[run_id])
    monkeypatch.setattr(
        control_plane,
        "list_outcome_events",
        lambda _workspace_id: (_ for _ in ()).throw(AssertionError("Outcome hydration ran before SQL")),
    )
    monkeypatch.setattr(experiment_store, "upload_blob_json", lambda *_args, **_kwargs: {})

    ledger = control_plane.workspace_experiment_ledger("ws-sql-led-read")

    assert ledger["source"] == "sql_lineage"
    assert [item["ordinal"] for item in ledger["versions"]] == [3, 9]
    assert [item["label"] for item in ledger["versions"]] == ["V3", "V9"]


def test_public_ledger_rechecks_sql_after_legacy_validation(monkeypatch) -> None:
    repository = _RacingSqlReadRepository()
    legacy = _analysis_payload("legacy-v1")
    run_store._mark_canonical_lineage_trusted(legacy, "legacy-v1", sequence=1)
    payloads = {
        "legacy-v1": legacy,
        "analysis-v3": _analysis_payload("analysis-v3"),
        "analysis-v9": _analysis_payload("analysis-v9"),
    }
    monkeypatch.setattr(run_store, "get_run", payloads.__getitem__)
    monkeypatch.setattr(experiment_store, "upload_blob_json", lambda *_args, **_kwargs: {})

    ledger = experiment_store.sync_experiment_ledger(
        "ws-sql-led-read",
        [],
        lineage_repository=repository,
        legacy_registry_state={
            "read_status": "present",
            "history_truncated": False,
            "runs": [run_store._run_summary(legacy)],
        },
        legacy_run_loader=payloads.get,
    )

    assert repository.existence_reads == 2
    assert ledger["source"] == "sql_lineage"
    assert [item["ordinal"] for item in ledger["versions"]] == [3, 9]


def test_complete_legacy_history_is_read_only_and_blob_attachments_are_not_membership() -> None:
    legacy = _trusted_legacy_payload("legacy-v1")
    snapshot = {
        "run_id": "legacy-artifact",
        "workspace_id": "ws-legacy-read",
        "status": "completed",
        "completed_at": "2026-07-15T01:00:00Z",
        "version_kind": "artifact_generation",
        "source_run_id": "legacy-v1",
        "experiment_version_id": "version:legacy-v1",
        "experiment_attachment": True,
        "attachment_commit_status": "confirmed",
        "produced_kinds": ["pdf"],
        "artifact": {"proposal": {"artifact_urls": {"pdf": "/api/artifacts/legacy.pdf"}}},
    }
    snapshot["attachment_payload_sha256"] = run_store._attachment_payload_hash(snapshot)
    snapshot["attachment_commit_id"] = run_store._attachment_commit_id(snapshot, legacy)
    registry = {
        "read_status": "present",
        "history_truncated": False,
        "runs": [run_store._run_summary(legacy), run_store._run_summary(snapshot)],
    }
    payloads = {"legacy-v1": legacy, "legacy-artifact": snapshot}

    ledger = experiment_store.sync_experiment_ledger(
        "ws-legacy-read",
        [],
        lineage_repository=_NoSqlWorkspaceRepository(),
        legacy_registry_state=registry,
        legacy_run_loader=payloads.get,
    )

    assert ledger["source"] == "legacy_blob"
    assert ledger["lineage_resolution"] == {
        "status": "read_only",
        "reason": "legacy_read_only",
    }
    assert [item["label"] for item in ledger["versions"]] == ["V1"]
    assert ledger["versions"][0]["attachments"] == {"plans": [], "artifacts": []}

    migration = migrate_lineage_sql.migrate_workspace(
        "ws-legacy-read",
        dry_run=True,
        lineage_repository=_NoSqlWorkspaceRepository(),
        registry_state=registry,
        run_loader=payloads.get,
    )
    assert migration["status"] == "ready"
    assert migration["legacy_attachment_count"] == 1
    assert migration["attachment_imported_count"] == 0


def test_mismatched_legacy_attachment_payload_exposes_no_partial_versions() -> None:
    legacy = _trusted_legacy_payload("legacy-v1")
    snapshot = {
        "run_id": "legacy-artifact",
        "workspace_id": "ws-legacy-read",
        "status": "completed",
        "version_kind": "artifact_generation",
        "source_run_id": "legacy-v1",
        "experiment_version_id": "version:legacy-v1",
        "experiment_attachment": True,
        "attachment_commit_status": "confirmed",
        "produced_kinds": ["pdf"],
        "artifact": {"proposal": {"artifact_urls": {"pdf": "/api/artifacts/original.pdf"}}},
    }
    snapshot["attachment_payload_sha256"] = run_store._attachment_payload_hash(snapshot)
    snapshot["attachment_commit_id"] = run_store._attachment_commit_id(snapshot, legacy)
    registry = {
        "read_status": "present",
        "history_truncated": False,
        "runs": [run_store._run_summary(legacy), run_store._run_summary(snapshot)],
    }
    snapshot["artifact"]["proposal"]["artifact_urls"]["pdf"] = "/api/artifacts/tampered.pdf"

    ledger = experiment_store.sync_experiment_ledger(
        "ws-legacy-read",
        [],
        lineage_repository=_NoSqlWorkspaceRepository(),
        legacy_registry_state=registry,
        legacy_run_loader={"legacy-v1": legacy, "legacy-artifact": snapshot}.get,
    )

    assert ledger["versions"] == []
    assert ledger["lineage_resolution"] == {
        "status": "unavailable",
        "reason": "legacy_unavailable",
    }


def test_incomplete_legacy_public_history_exposes_no_partial_versions() -> None:
    legacy = _trusted_legacy_payload("legacy-v1")
    ledger = experiment_store.sync_experiment_ledger(
        "ws-legacy-read",
        [],
        lineage_repository=_NoSqlWorkspaceRepository(),
        legacy_registry_state={
            "read_status": "present",
            "history_truncated": True,
            "runs": [run_store._run_summary(legacy)],
        },
        legacy_run_loader=lambda _run_id: legacy,
    )

    assert ledger["versions"] == []
    assert ledger["lineage_resolution"] == {
        "status": "unavailable",
        "reason": "legacy_unavailable",
    }
