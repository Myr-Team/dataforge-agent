from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace

import backend.experiment_store as experiment_store
import backend.control_plane as control_plane
import backend.outcome_store as outcome_store
import backend.run_store as run_store
import pytest
from backend.app import app
from fastapi.testclient import TestClient


class _CommittedLineageRepository:
    def __init__(self, *, legacy_version_ids: bool = False) -> None:
        self.legacy_version_ids = legacy_version_ids
        self.versions: list[SimpleNamespace] = []
        self.attachments: list[SimpleNamespace] = []

    def commit_analysis(self, **values):
        workspace_versions = [
            item
            for item in self.versions
            if item.workspace_id == values["workspace_id"]
            and item.generation == values["generation"]
        ]
        latest = workspace_versions[-1] if workspace_versions else None
        if (
            latest is not None
            and latest.decision_fingerprint == values["decision_fingerprint"]
            and latest.evidence_fingerprint == values["evidence_fingerprint"]
        ):
            return SimpleNamespace(**{**vars(latest), "created": False})
        committed = SimpleNamespace(
            version_id=(
                f"version:{values['canonical_run_id']}"
                if self.legacy_version_ids
                else "11111111-1111-4111-8111-111111111111"
            ),
            workspace_id=values["workspace_id"],
            generation=values["generation"],
            ordinal=len(workspace_versions) + 1,
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

    def current_generation(self, *, workspace_id: str):
        workspace_generations = [
            item.generation for item in self.versions if item.workspace_id == workspace_id
        ]
        return max(workspace_generations, default=1)

    def list_attachments(self, *, workspace_id: str, generation: int):
        return tuple(
            item
            for item in self.attachments
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
    repository = _CommittedLineageRepository(legacy_version_ids=True)
    run_store._LINEAGE_REPOSITORY_PROVIDER = lambda: repository
    experiment_store._LINEAGE_REPOSITORY_PROVIDER = lambda: repository
    yield repository
    run_store._LINEAGE_REPOSITORY_PROVIDER = original_run_provider
    experiment_store._LINEAGE_REPOSITORY_PROVIDER = original_experiment_provider
    run_store._ACTIVE.clear()
    run_store._LINEAGE_GENERATION_HINTS.clear()


def _analysis_run(
    run_id: str,
    *,
    verdict: str,
    score: int,
    evidence_ref: str,
    iteration_inputs: list[dict] | None = None,
) -> dict:
    artifact = {
        "feasibility": {
            "opportunity_id": "workspace opportunity",
            "verdict": verdict,
            "overall_confidence": "data_confirmed",
            "dimensions": [
                {
                    "name": "asset_data",
                    "score": score,
                    "confidence": "data_confirmed",
                    "evidence": [
                        {
                            "source_type": "corpus",
                            "ref": evidence_ref,
                            "quote": f"Evidence for {evidence_ref}",
                        }
                    ],
                }
            ],
            "gap_list": ["Validate willingness to pay."],
        },
        "citations": [{"ref": evidence_ref, "source_file": evidence_ref.split("#", 1)[0]}],
        "iteration_inputs": iteration_inputs or [],
    }
    return {
        "run_id": run_id,
        "workspace_id": "ws-experiment",
        "status": "completed",
        "completed_at": f"2026-07-12T0{1 if run_id == 'run-v1' else 2}:00:00Z",
        "artifact": artifact,
        "final": {"artifact": artifact},
    }


class _LegacyMigrationRepository:
    def __init__(self, *, workspace_exists: bool = False) -> None:
        self._workspace_exists = workspace_exists
        self.transaction_calls = 0

    def workspace_exists(self, *, workspace_id: str) -> bool:
        assert workspace_id
        return self._workspace_exists

    def _transaction(self):
        self.transaction_calls += 1
        raise AssertionError("migration must not start a SQL transaction")


class _MigrationCursor:
    def __init__(self, *, fail_on_version: int | None = None) -> None:
        self.operations: list[tuple[str, tuple]] = []
        self.pending_versions: list[str] = []
        self.fail_on_version = fail_on_version

    def execute(self, operation: str, *parameters):
        self.operations.append((operation, parameters))
        if "lineage:migration-insert-version" in operation:
            self.pending_versions.append(str(parameters[4]))
            if self.fail_on_version == len(self.pending_versions):
                raise RuntimeError("simulated version insert failure")
        return self

    def fetchone(self):
        return None


class _AtomicMigrationRepository(_LegacyMigrationRepository):
    def __init__(self, *, fail_on_version: int | None = None) -> None:
        super().__init__()
        self.cursor = _MigrationCursor(fail_on_version=fail_on_version)
        self.persisted_versions: list[str] = []
        self.rolled_back = False

    @contextmanager
    def _transaction(self):
        self.transaction_calls += 1
        try:
            yield self.cursor
        except Exception:
            self.cursor.pending_versions.clear()
            self.rolled_back = True
            raise
        else:
            self.persisted_versions.extend(self.cursor.pending_versions)


def _trusted_legacy_run(run_id: str, *, sequence: int) -> dict:
    run = _analysis_run(
        run_id,
        verdict="conditional",
        score=3,
        evidence_ref=f"legacy.csv#row-{sequence}",
    )
    run_store._mark_canonical_lineage_trusted(run, run_id, sequence=sequence)
    return run


def _legacy_registry(*runs: dict) -> dict:
    return {
        "read_status": "present",
        "history_truncated": False,
        "runs": [run_store._run_summary(run) for run in runs],
    }


def test_legacy_migration_rejects_incomplete_history_without_partial_sql_write() -> None:
    from backend.migrate_lineage_sql import migrate_workspace

    repository = _LegacyMigrationRepository()
    result = migrate_workspace(
        "ws-experiment",
        dry_run=False,
        lineage_repository=repository,
        registry_state={
            "read_status": "present",
            "history_truncated": True,
            "runs": [],
        },
        run_loader=lambda run_id: (_ for _ in ()).throw(AssertionError(run_id)),
    )

    assert result == {
        "workspace_id": "ws-experiment",
        "status": "legacy_unavailable",
        "reason": "history_incomplete",
        "dry_run": False,
    }
    assert repository.transaction_calls == 0


def test_legacy_migration_rejects_registry_payload_mismatch_without_partial_sql_write() -> None:
    from backend.migrate_lineage_sql import migrate_workspace

    repository = _LegacyMigrationRepository()
    legacy = _trusted_legacy_run("legacy-v1", sequence=1)
    registry_state = {
        "read_status": "present",
        "history_truncated": False,
        "runs": [run_store._run_summary(legacy)],
    }
    tampered = json.loads(json.dumps(legacy))
    tampered["artifact"]["feasibility"]["verdict"] = "recommended"

    result = migrate_workspace(
        "ws-experiment",
        dry_run=False,
        lineage_repository=repository,
        registry_state=registry_state,
        run_loader=lambda run_id: tampered if run_id == "legacy-v1" else None,
    )

    assert result == {
        "workspace_id": "ws-experiment",
        "status": "legacy_unavailable",
        "reason": "payload_mismatch",
        "dry_run": False,
    }
    assert repository.transaction_calls == 0


def test_legacy_migration_rejects_existing_sql_lineage_before_reading_blob_history() -> None:
    from backend.migrate_lineage_sql import migrate_workspace

    repository = _LegacyMigrationRepository(workspace_exists=True)
    result = migrate_workspace(
        "ws-experiment",
        dry_run=False,
        lineage_repository=repository,
        registry_state={
            "read_status": "present",
            "history_truncated": False,
            "runs": [{"workspace_id": "ws-experiment", "run_id": "legacy-v1"}],
        },
        run_loader=lambda run_id: (_ for _ in ()).throw(AssertionError(run_id)),
    )

    assert result == {
        "workspace_id": "ws-experiment",
        "status": "rejected",
        "reason": "sql_lineage_exists",
        "dry_run": False,
    }
    assert repository.transaction_calls == 0


def test_legacy_migration_dry_run_validates_complete_history_without_sql_insert() -> None:
    from backend.migrate_lineage_sql import migrate_workspace

    repository = _LegacyMigrationRepository()
    first = _trusted_legacy_run("legacy-v1", sequence=1)
    second = _trusted_legacy_run("legacy-v2", sequence=2)
    runs = {item["run_id"]: item for item in (first, second)}

    result = migrate_workspace(
        "ws-experiment",
        dry_run=True,
        lineage_repository=repository,
        registry_state=_legacy_registry(first, second),
        run_loader=runs.get,
    )

    assert result == {
        "workspace_id": "ws-experiment",
        "status": "ready",
        "dry_run": True,
        "generation": 1,
        "version_count": 2,
        "legacy_analysis_count": 2,
        "legacy_attachment_count": 0,
        "attachment_imported_count": 0,
    }
    assert repository.transaction_calls == 0


def test_legacy_migration_imports_all_versions_in_one_transaction() -> None:
    from backend.migrate_lineage_sql import migrate_workspace

    repository = _AtomicMigrationRepository()
    first = _trusted_legacy_run("legacy-v1", sequence=1)
    second = _trusted_legacy_run("legacy-v2", sequence=2)
    runs = {item["run_id"]: item for item in (first, second)}

    result = migrate_workspace(
        "ws-experiment",
        dry_run=False,
        lineage_repository=repository,
        registry_state=_legacy_registry(first, second),
        run_loader=runs.get,
    )

    assert result["status"] == "migrated"
    assert result["version_count"] == 2
    assert repository.transaction_calls == 1
    assert repository.persisted_versions == ["legacy-v1", "legacy-v2"]
    assert sum("lineage:migration-insert-workspace" in item[0] for item in repository.cursor.operations) == 1
    assert sum("lineage:migration-insert-version" in item[0] for item in repository.cursor.operations) == 2
    assert not any("experiment_attachment" in item[0] for item in repository.cursor.operations)


def test_legacy_migration_rolls_back_every_version_when_one_insert_fails() -> None:
    from backend.migrate_lineage_sql import migrate_workspace

    repository = _AtomicMigrationRepository(fail_on_version=2)
    first = _trusted_legacy_run("legacy-v1", sequence=1)
    second = _trusted_legacy_run("legacy-v2", sequence=2)
    runs = {item["run_id"]: item for item in (first, second)}

    result = migrate_workspace(
        "ws-experiment",
        dry_run=False,
        lineage_repository=repository,
        registry_state=_legacy_registry(first, second),
        run_loader=runs.get,
    )

    assert result == {
        "workspace_id": "ws-experiment",
        "status": "unavailable",
        "reason": "lineage_unavailable",
        "dry_run": False,
    }
    assert repository.transaction_calls == 1
    assert repository.rolled_back is True
    assert repository.persisted_versions == []


def test_committed_sql_version_survives_post_commit_blob_publication_failure(
    tmp_path,
    monkeypatch,
) -> None:
    repository = _CommittedLineageRepository()
    workspace_id = "ws-sql-post-commit-payload"
    run_id = "analysis-sql-post-commit-payload"
    artifact = _analysis_run(
        run_id,
        verdict="conditional",
        score=3,
        evidence_ref="evidence.csv#row-1",
    )["artifact"]

    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "blob_configured", lambda: True)
    monkeypatch.setattr(run_store, "download_blob_json", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(run_store, "download_blob_json_strict", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        run_store,
        "upload_blob_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("blob failed")),
    )

    run_store.start_run(run_id, workspace_id, "Analyze")
    completed = run_store.complete_run(
        run_id,
        final={"artifact": artifact},
        artifact=artifact,
        lineage_repository=repository,
    )

    assert len(repository.versions) == 1
    assert completed is not None
    assert completed["canonical_experiment_version_id"] == repository.versions[0].version_id
    assert completed["persistence"] == {
        "mode": "degraded",
        "confirmed": True,
        "payload_state": "unavailable",
        "reason": "payload_publication_failed",
    }

    ledger = experiment_store.sync_experiment_ledger(
        workspace_id,
        [],
        lineage_repository=repository,
        generation=1,
    )

    assert ledger["count"] == 1
    assert ledger["versions"][0]["version_id"] == repository.versions[0].version_id
    assert ledger["versions"][0]["ordinal"] == 1
    assert ledger["versions"][0]["payload"] == {"status": "unavailable"}


def test_run_and_attachment_metadata_cannot_strengthen_sql_verdict_fingerprint() -> None:
    analysis = _analysis_run(
        "analysis-fingerprint",
        verdict="conditional",
        score=3,
        evidence_ref="evidence.csv#row-1",
    )
    decorated = {
        **analysis,
        "verdict": "recommended",
        "confidence": "data_confirmed",
        "version_kind": "artifact_generation",
        "experiment_attachment": True,
        "attachment_commit_status": "confirmed",
        "attachment_commit_id": "metadata-only",
    }

    assert experiment_store.analysis_lineage_fingerprints(decorated) == (
        experiment_store.analysis_lineage_fingerprints(analysis)
    )


def test_completed_analysis_payload_update_cannot_allocate_another_sql_ordinal(tmp_path, monkeypatch, _inject_lineage_repository) -> None:
    repository = _inject_lineage_repository
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "blob_configured", lambda: False)
    workspace_id = "ws-no-historical-promotion"
    artifacts = {
        "analysis-a": _analysis_run(
            "analysis-a", verdict="conditional", score=3, evidence_ref="evidence.csv#row-1"
        )["artifact"],
        "analysis-b": _analysis_run(
            "analysis-b", verdict="conditional", score=3, evidence_ref="evidence.csv#row-2"
        )["artifact"],
    }

    for run_id, artifact in artifacts.items():
        run_store.start_run(run_id, workspace_id, "Analyze")
        run_store.complete_run(run_id, artifact=artifact, final={"artifact": artifact})

    updated = run_store.update_run_proposal(
        "analysis-a", {"artifact_urls": {"pdf": "/api/artifacts/analysis-a.pdf"}}
    )

    assert updated is not None
    assert [item.canonical_run_id for item in repository.versions] == ["analysis-a", "analysis-b"]
    assert [item.ordinal for item in repository.versions] == [1, 2]


def test_sql_ledger_does_not_accept_blob_metadata_as_attachment_membership(_inject_lineage_repository) -> None:
    repository = _inject_lineage_repository
    first = _analysis_run("analysis-attachment-v1", verdict="conditional", score=3, evidence_ref="evidence.csv#row-1")
    second = _analysis_run("analysis-attachment-v2", verdict="conditional", score=3, evidence_ref="evidence.csv#row-2")
    for ordinal, run in enumerate((first, second), start=1):
        repository.versions.append(
            SimpleNamespace(
                version_id=f"version:{run['run_id']}",
                workspace_id="ws-experiment",
                generation=1,
                ordinal=ordinal,
                canonical_run_id=run["run_id"],
                decision_fingerprint=f"{ordinal:064x}",
                evidence_fingerprint=f"{ordinal + 10:064x}",
                created=True,
            )
        )
    forged = {
        "run_id": "forged-artifact",
        "workspace_id": "ws-experiment",
        "status": "completed",
        "completed_at": "2026-07-12T03:00:00Z",
        "version_kind": "artifact_generation",
        "source_run_id": "analysis-attachment-v2",
        "experiment_version_id": "version:analysis-attachment-v2",
        "experiment_attachment": True,
        "attachment_commit_status": "confirmed",
        "attachment_commit_id": "forged-attachment",
        "artifact": {"proposal": {"artifact_urls": {"pdf": "/api/artifacts/forged.pdf"}}},
    }
    forged["attachment_payload_sha256"] = experiment_store._snapshot_payload_sha256(forged)

    ledger = experiment_store.sync_experiment_ledger(
        "ws-experiment", [first, second, forged], lineage_repository=repository
    )

    assert ledger["versions"][1]["attachments"]["artifacts"] == []
    assert ledger["lineage_resolution"]["status"] == "unavailable"


def test_sql_ledger_does_not_expose_degraded_attachment_payload(_inject_lineage_repository) -> None:
    repository = _inject_lineage_repository
    analysis = _analysis_run(
        "analysis-degraded-attachment", verdict="conditional", score=3, evidence_ref="evidence.csv#row-1"
    )
    version = SimpleNamespace(
        version_id="version:analysis-degraded-attachment",
        workspace_id="ws-experiment",
        generation=1,
        ordinal=1,
        canonical_run_id="analysis-degraded-attachment",
        decision_fingerprint="1" * 64,
        evidence_fingerprint="2" * 64,
        created=True,
    )
    snapshot = {
        "run_id": "degraded-artifact",
        "workspace_id": "ws-experiment",
        "status": "completed",
        "completed_at": "2026-07-12T03:00:00Z",
        "version_kind": "artifact_generation",
        "source_run_id": "analysis-degraded-attachment",
        "experiment_version_id": version.version_id,
        "artifact": {"proposal": {"artifact_urls": {"pdf": "/api/artifacts/degraded.pdf"}}},
        "persistence": {
            "mode": "degraded",
            "confirmed": True,
            "payload_state": "unavailable",
            "reason": "payload_publication_failed",
        },
    }
    repository.versions.append(version)
    repository.attachments.append(
        SimpleNamespace(
            attachment_id="attachment-degraded",
            version_id=version.version_id,
            workspace_id="ws-experiment",
            generation=1,
            kind="artifact_generation",
            source_run_id="analysis-degraded-attachment",
            payload_sha256=experiment_store._snapshot_payload_sha256(snapshot),
            created=True,
        )
    )

    ledger = experiment_store.sync_experiment_ledger(
        "ws-experiment", [analysis, snapshot], lineage_repository=repository
    )

    assert ledger["versions"][0]["attachments"]["artifacts"] == []
    assert ledger["lineage_resolution"]["status"] == "unavailable"


def test_sql_ledger_projects_empty_recreated_generation_from_repository(monkeypatch, _inject_lineage_repository) -> None:
    repository = _inject_lineage_repository
    monkeypatch.setattr(repository, "current_generation", lambda **_values: 2)

    ledger = experiment_store.sync_experiment_ledger(
        "ws-empty-recreated-generation",
        [],
        lineage_repository=repository,
        generation=2,
    )

    assert ledger["generation"] == 2
    assert ledger["count"] == 0
    assert ledger["lineage_resolution"] == {"status": "resolved"}


def test_authoritative_observed_metric_changes_sql_evidence_fingerprint(monkeypatch) -> None:
    baseline = _analysis_run(
        "analysis-metric-v1", verdict="conditional", score=3, evidence_ref="evidence.csv#row-1"
    )
    reanalysis = _analysis_run(
        "analysis-metric-v2", verdict="conditional", score=3, evidence_ref="evidence.csv#row-1"
    )
    reanalysis["completed_at"] = "2026-07-12T03:00:00Z"
    outcome = {
        "event_id": "outcome-metric-v2",
        "workspace_id": "ws-experiment",
        "metric_name": "pilot_conversion_rate",
        "unit": "percent",
        "observed_value": 7.2,
        "observed_at": "2026-07-12T02:00:00Z",
        "created_at": "2026-07-12T02:00:00Z",
        "provenance": "observed",
        "source": {"run_id": "analysis-metric-v2", "file_id": "feedback.csv", "file_version": "2"},
        "verification": {"status": "verified", "trusted_identity": True},
    }
    monkeypatch.setattr(experiment_store, "list_outcome_events", lambda _workspace_id: [outcome], raising=False)
    monkeypatch.setattr(
        experiment_store,
        "outcome_is_authoritative",
        lambda workspace_id, item: workspace_id == "ws-experiment" and item.get("event_id") == "outcome-metric-v2",
    )

    assert experiment_store.analysis_lineage_fingerprints(baseline) != experiment_store.analysis_lineage_fingerprints(reanalysis)


def test_plan_and_artifact_snapshots_attach_without_creating_versions() -> None:
    analysis = _analysis_run(
        "run-v1",
        verdict="conditional",
        score=3,
        evidence_ref="evidence.csv#row-1",
    )
    plan = {
        "run_id": "run-plan",
        "workspace_id": "ws-experiment",
        "version_kind": "plan_draft",
        "source_run_id": "run-v1",
        "completed_at": "2026-07-12T02:00:00Z",
        "artifact": {"plan_draft": {"text": "Pilot plan"}},
    }
    artifact = {
        "run_id": "run-artifact",
        "workspace_id": "ws-experiment",
        "version_kind": "artifact_generation",
        "source_run_id": "run-v1",
        "completed_at": "2026-07-12T03:00:00Z",
        "artifact": {"proposal": {"artifact_urls": {"pdf": "/api/artifacts/project-v1.pdf"}}},
    }

    ledger = experiment_store.build_experiment_ledger(
        "ws-experiment",
        [analysis, plan, artifact],
        outcomes=[],
    )

    assert len(ledger["versions"]) == 1
    version = ledger["versions"][0]
    assert version["label"] == "V1"
    assert version["attachments"]["plans"][0]["run_id"] == "run-plan"
    assert version["attachments"]["artifacts"][0]["urls"]["pdf"].endswith("project-v1.pdf")


def test_client_declared_observed_metric_cannot_promote() -> None:
    first = _analysis_run(
        "run-v1",
        verdict="conditional",
        score=3,
        evidence_ref="evidence.csv#row-1",
    )
    second = _analysis_run(
        "run-v2",
        verdict="feasible",
        score=4,
        evidence_ref="evidence.csv#row-1",
        iteration_inputs=[
            {
                "label": "pilot_conversion_rate",
                "value": "7.2",
                "unit": "percent",
                "kind": "observed",
                "source": {"file_id": "feedback-v2.csv", "run_id": "run-v2"},
                "verification": {"status": "verified"},
            }
        ],
    )

    ledger = experiment_store.build_experiment_ledger("ws-experiment", [first, second], outcomes=[])
    assert len(ledger["versions"]) == 1
    assert ledger["versions"][0]["decision"]["verdict"] == "conditional"


def test_observed_outcome_promotes_only_after_completed_reanalysis(monkeypatch) -> None:
    first = _analysis_run(
        "run-v1",
        verdict="conditional",
        score=3,
        evidence_ref="evidence.csv#row-1",
    )
    outcome = {
        "event_id": "outcome-v2",
        "workspace_id": "ws-experiment",
        "metric_name": "pilot_conversion_rate",
        "unit": "percent",
        "observed_value": 7.2,
        "observed_at": "2026-07-12T01:30:00Z",
        "created_at": "2026-07-12T01:30:00Z",
        "provenance": "observed",
        "source": {
            "run_id": "run-v1",
            "file_id": "feedback.csv",
            "file_version": "2",
            "connector_id": "upload",
        },
        "verification": {"status": "verified"},
    }
    monkeypatch.setattr(
        experiment_store,
        "outcome_is_authoritative",
        lambda workspace_id, item: workspace_id == "ws-experiment" and item.get("event_id") == "outcome-v2",
        raising=False,
    )

    before_reanalysis = experiment_store.build_experiment_ledger(
        "ws-experiment",
        [first],
        outcomes=[outcome],
    )

    assert len(before_reanalysis["versions"]) == 1
    assert before_reanalysis["versions"][0]["metrics"] == []

    second = _analysis_run(
        "run-v2",
        verdict="conditional",
        score=3,
        evidence_ref="evidence.csv#row-1",
    )
    after_reanalysis = experiment_store.build_experiment_ledger(
        "ws-experiment",
        [first, second],
        outcomes=[outcome],
    )

    assert len(after_reanalysis["versions"]) == 2
    promoted = after_reanalysis["versions"][1]
    assert promoted["metrics"][0]["source"] == outcome["source"]
    assert promoted["evidence_changed"] is True
    assert promoted["decision_delta"]["reasons"] == ["Added 1 source-linked observed metric"]


def test_persisted_independently_verified_outcome_is_authoritative(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(outcome_store, "OUTCOME_DIR", tmp_path / "outcomes")
    monkeypatch.setattr(outcome_store, "download_blob_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(outcome_store, "upload_blob_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(outcome_store, "source_is_valid", lambda workspace_id, source: True)
    actor = {
        "name": "Owner",
        "email": "owner@example.invalid",
        "actor_id": "owner-id",
        "tenant_id": "tenant-id",
        "source": "easy_auth",
    }
    reviewer = {
        "name": "Reviewer",
        "email": "reviewer@example.invalid",
        "actor_id": "reviewer-id",
        "tenant_id": "tenant-id",
        "source": "easy_auth",
    }
    event = outcome_store.record_outcome_event(
        "ws-experiment",
        {
            "metric_name": "pilot_conversion_rate",
            "unit": "percent",
            "observed_value": 7.2,
            "observed_at": "2026-07-12T01:30:00Z",
            "provenance": "observed",
            "source": {"run_id": "run-v1", "file_id": "feedback.csv", "file_version": "2"},
        },
        actor,
    )
    verified = outcome_store.verify_outcome_event(
        "ws-experiment",
        event["event_id"],
        reviewer,
    )

    assert outcome_store.outcome_is_authoritative("ws-experiment", verified) is True
    assert outcome_store.outcome_is_authoritative(
        "ws-experiment",
        {**verified, "observed_value": 99},
    ) is False
    metrics = experiment_store._outcome_metrics(
        [verified],
        existing=[],
        workspace_id="ws-experiment",
    )
    assert metrics[0]["provenance"] == "observed"
    monkeypatch.setattr(outcome_store, "source_is_valid", lambda workspace_id, source: False)
    assert outcome_store.outcome_is_authoritative("ws-experiment", verified) is False


@pytest.mark.parametrize(
    ("provenance", "verification", "expected_metric_provenance"),
    [
        ("observed", {"status": "unverified"}, "reported_unverified"),
        ("synthetic", {"status": "verified"}, "synthetic"),
        ("target", {"status": "verified"}, "target"),
        ("assumption", {"status": "verified"}, "assumption"),
    ],
)
def test_non_verified_observed_or_non_observed_outcome_cannot_promote(
    provenance: str,
    verification: dict,
    expected_metric_provenance: str,
) -> None:
    first = _analysis_run("run-v1", verdict="conditional", score=3, evidence_ref="evidence.csv#row-1")
    second = _analysis_run("run-v2", verdict="conditional", score=3, evidence_ref="evidence.csv#row-1")
    outcome = {
        "event_id": f"outcome-{provenance}",
        "metric_name": "pilot_conversion_rate",
        "unit": "percent",
        "observed_value": 9.5,
        "observed_at": "2026-07-12T01:30:00Z",
        "created_at": "2026-07-12T01:30:00Z",
        "provenance": provenance,
        "source": {"run_id": "run-v1", "file_id": "feedback.csv", "file_version": "2"},
        "verification": verification,
    }

    metrics = experiment_store._outcome_metrics([outcome], existing=[])
    ledger = experiment_store.build_experiment_ledger("ws-experiment", [first, second], outcomes=[outcome])

    assert metrics[0]["provenance"] == expected_metric_provenance
    assert metrics[0]["verification"] == verification
    assert len(ledger["versions"]) == 1


def test_non_authoritative_feedback_cannot_create_downgraded_canonical_version() -> None:
    first = _analysis_run("run-v1", verdict="conditional", score=3, evidence_ref="evidence.csv#row-1")
    second = _analysis_run(
        "run-v2",
        verdict="not_yet_feasible",
        score=2,
        evidence_ref="evidence.csv#row-1",
        iteration_inputs=[
            {
                "label": "simulated_conversion_rate",
                "value": 1.0,
                "kind": "synthetic",
            }
        ],
    )

    ledger = experiment_store.build_experiment_ledger("ws-experiment", [first, second], outcomes=[])

    assert len(ledger["versions"]) == 1
    assert ledger["versions"][0]["decision"]["verdict"] == "conditional"


def test_wording_only_changes_do_not_promote_or_contradict_stable_evidence() -> None:
    first = _analysis_run("run-v1", verdict="conditional", score=3, evidence_ref="evidence.csv#row-1")
    second = _analysis_run("run-v2", verdict="conditional", score=3, evidence_ref="evidence.csv#row-1")
    for run, version, quote, rationale, gap in (
        (first, "1", "Measured 120 rows.", "Evidence supports the score.", "Validate pricing."),
        (second, "1", "The dataset contains one hundred twenty rows.", "Same result, rewritten.", "Reworded pricing validation."),
    ):
        dimension = run["artifact"]["feasibility"]["dimensions"][0]
        dimension["rationale"] = rationale
        dimension["evidence"][0].update({"file_id": "evidence.csv", "file_version": version, "quote": quote})
        run["artifact"]["feasibility"]["gap_list"] = [gap]
        run["final"]["artifact"] = run["artifact"]
    second["artifact"]["feasibility"]["opportunity_id"] = "Reworded workspace opportunity"

    ledger = experiment_store.build_experiment_ledger("ws-experiment", [first, second], outcomes=[])
    direct_delta = experiment_store._evidence_delta(
        experiment_store._evidence_set(first["artifact"], first["artifact"]["feasibility"]),
        experiment_store._evidence_set(second["artifact"], second["artifact"]["feasibility"]),
    )

    assert len(ledger["versions"]) == 1
    assert direct_delta["contradicted"] == []
    assert [item["ref"] for item in direct_delta["unchanged"]] == ["evidence.csv#row-1"]


def test_stable_evidence_dedupes_by_full_source_identity_not_ref() -> None:
    run = _analysis_run("run-v1", verdict="conditional", score=3, evidence_ref="feedback.csv#row-8")
    dimension = run["artifact"]["feasibility"]["dimensions"][0]
    dimension["evidence"] = [
        {
            "source_type": "corpus",
            "ref": "feedback.csv#row-8",
            "file_id": "feedback.csv",
            "file_version": version,
            "connector_id": "upload",
            "connector_version": "1",
            "confidence": "data_confirmed",
        }
        for version in ("1", "2")
    ]
    run["final"]["artifact"] = run["artifact"]

    ledger = experiment_store.build_experiment_ledger("ws-experiment", [run], outcomes=[])

    assert [item["source"]["file_version"] for item in ledger["versions"][0]["evidence"]] == ["1", "2"]


def test_synthetic_only_new_dimension_cannot_promote() -> None:
    first = _analysis_run("run-v1", verdict="conditional", score=3, evidence_ref="evidence.csv#row-1")
    second = _analysis_run("run-v2", verdict="conditional", score=3, evidence_ref="evidence.csv#row-1")
    second["artifact"]["feasibility"]["dimensions"].append(
        {
            "name": "synthetic_growth",
            "score": 5,
            "confidence": "data_confirmed",
            "rationale": "Simulation narrative",
            "evidence": [{"source_type": "synthetic", "ref": "simulation#growth", "quote": "Projected growth"}],
        }
    )
    second["final"]["artifact"] = second["artifact"]

    ledger = experiment_store.build_experiment_ledger("ws-experiment", [first, second], outcomes=[])

    assert len(ledger["versions"]) == 1
    assert [item["name"] for item in ledger["versions"][0]["decision"]["dimensions"]] == ["asset_data"]


def test_every_evidence_delta_item_has_structured_reason_and_quote_is_not_authority() -> None:
    def evidence(ref: str, version: str, confidence: str, **fields) -> dict:
        return {
            "ref": ref,
            "source_type": "corpus",
            "source": {"file_id": f"{ref}.csv", "file_version": version, "connector_id": "upload"},
            "confidence": confidence,
            **fields,
        }

    previous = [
        evidence("removed", "1", "data_confirmed"),
        evidence("contradicted", "1", "data_confirmed", value=10, unit="percent"),
        evidence("strengthened", "1", "market_inferred"),
        evidence("unchanged", "1", "data_confirmed", quote="Old wording"),
        evidence("lowered", "1", "data_confirmed"),
    ]
    current = [
        evidence("added", "1", "data_confirmed"),
        evidence("contradicted", "1", "data_confirmed", value=20, unit="percent"),
        evidence("strengthened", "1", "data_confirmed"),
        evidence("unchanged", "1", "data_confirmed", quote="Rewritten wording"),
        evidence("lowered", "1", "market_inferred"),
    ]
    delta = experiment_store._evidence_delta(
        previous,
        current,
        unverifiable=[{"ref": "simulation", "reason": "Source is synthetic and not traceable."}],
    )

    assert {item["ref"] for item in delta["contradicted"]} == {"contradicted", "lowered"}
    assert {item["ref"] for item in delta["unchanged"]} == {"unchanged"}
    assert {item["ref"] for item in delta["strengthened"]} == {"strengthened"}
    for category in ("added", "removed", "contradicted", "strengthened", "unchanged", "unverifiable"):
        assert delta[category]
        assert all(isinstance(item.get("reason"), str) and item["reason"].strip() for item in delta[category])


def test_unverified_or_synthetic_feedback_cannot_promote_effective_verdict() -> None:
    first = _analysis_run(
        "run-v1",
        verdict="conditional",
        score=3,
        evidence_ref="evidence.csv#row-1",
    )
    second = _analysis_run(
        "run-v2",
        verdict="feasible",
        score=4,
        evidence_ref="evidence.csv#row-1",
        iteration_inputs=[
            {
                "label": "simulated_conversion_rate",
                "value": "9.5",
                "unit": "percent",
                "kind": "observed",
            }
        ],
    )

    ledger = experiment_store.build_experiment_ledger("ws-experiment", [first, second], outcomes=[])
    assert len(ledger["versions"]) == 1
    assert ledger["versions"][0]["decision"]["verdict"] == "conditional"
    assert ledger["versions"][0]["decision"]["dimensions"][0]["score"] == 3


def test_synthetic_citation_cannot_promote_effective_verdict() -> None:
    first = _analysis_run(
        "run-v1",
        verdict="conditional",
        score=3,
        evidence_ref="evidence.csv#row-1",
    )
    second = _analysis_run(
        "run-v2",
        verdict="feasible",
        score=4,
        evidence_ref="synthetic#row-1",
    )
    second["artifact"]["feasibility"]["dimensions"][0]["evidence"][0]["source_type"] = "synthetic"
    second["final"]["artifact"] = second["artifact"]

    ledger = experiment_store.build_experiment_ledger("ws-experiment", [first, second], outcomes=[])

    assert len(ledger["versions"]) == 2
    version = ledger["versions"][1]
    assert version["decision"]["model_verdict"] == "feasible"
    assert version["decision"]["verdict"] == "conditional"
    assert version["decision"]["dimensions"][0]["score"] == 3
    assert [item["ref"] for item in version["evidence_delta"]["unverifiable"]] == ["synthetic#row-1"]


def test_identical_versions_report_no_comparable_new_evidence() -> None:
    first = _analysis_run(
        "run-v1",
        verdict="conditional",
        score=3,
        evidence_ref="evidence.csv#row-1",
    )
    second = _analysis_run(
        "run-v2",
        verdict="conditional",
        score=3,
        evidence_ref="evidence.csv#row-1",
    )

    ledger = experiment_store.build_experiment_ledger("ws-experiment", [first, second], outcomes=[])
    assert len(ledger["versions"]) == 1
    assert ledger["versions"][0]["run_id"] == "run-v1"


def test_evidence_delta_uses_source_version_and_reports_all_categories() -> None:
    first = _analysis_run(
        "run-v1",
        verdict="conditional",
        score=3,
        evidence_ref="feedback.csv#row-8",
    )
    first_evidence = first["artifact"]["feasibility"]["dimensions"][0]["evidence"][0]
    first_evidence.update(
        {
            "file_id": "feedback.csv",
            "file_version": "1",
            "connector_id": "upload",
            "confidence": "market_inferred",
        }
    )
    first["final"]["artifact"] = first["artifact"]

    second = _analysis_run(
        "run-v2",
        verdict="conditional",
        score=3,
        evidence_ref="feedback.csv#row-8",
    )
    second_evidence = second["artifact"]["feasibility"]["dimensions"][0]["evidence"][0]
    second_evidence.update(
        {
            "file_id": "feedback.csv",
            "file_version": "2",
            "connector_id": "upload",
            "confidence": "data_confirmed",
        }
    )
    second["artifact"]["feasibility"]["dimensions"][0]["evidence"].extend(
        [
            {
                "source_type": "corpus",
                "ref": "stable.csv#row-1",
                "file_id": "stable.csv",
                "file_version": "1",
                "quote": "Stable evidence",
                "confidence": "data_confirmed",
            },
            {
                "source_type": "synthetic",
                "ref": "simulation#1",
                "quote": "Simulated result",
            },
        ]
    )
    first["artifact"]["feasibility"]["dimensions"][0]["evidence"].append(
        {
            "source_type": "corpus",
            "ref": "stable.csv#row-1",
            "file_id": "stable.csv",
            "file_version": "1",
            "quote": "Stable evidence",
            "confidence": "data_confirmed",
        }
    )
    second["final"]["artifact"] = second["artifact"]
    first["final"]["artifact"] = first["artifact"]

    ledger = experiment_store.build_experiment_ledger("ws-experiment", [first, second], outcomes=[])
    delta = ledger["versions"][1]["evidence_delta"]

    assert {item["source"]["file_version"] for item in delta["added"]} == {"2"}
    assert {item["source"]["file_version"] for item in delta["removed"]} == {"1"}
    assert [item["ref"] for item in delta["unchanged"]] == ["stable.csv#row-1"]
    assert [item["ref"] for item in delta["unverifiable"]] == ["simulation#1"]
    assert set(delta) == {"added", "removed", "contradicted", "strengthened", "unchanged", "unverifiable"}


def test_mixed_authoritative_evidence_cannot_strengthen_unrelated_synthetic_dimension() -> None:
    first = _analysis_run(
        "run-v1",
        verdict="conditional",
        score=3,
        evidence_ref="asset.csv#row-1",
    )
    second = _analysis_run(
        "run-v2",
        verdict="feasible",
        score=4,
        evidence_ref="asset.csv#row-2",
    )
    second["artifact"]["feasibility"]["dimensions"].append(
        {
            "name": "synthetic_growth",
            "score": 5,
            "confidence": "data_confirmed",
            "evidence": [
                {
                    "source_type": "synthetic",
                    "ref": "simulation#growth",
                    "value": 99,
                }
            ],
        }
    )
    second["artifact"]["feasibility"]["dimensions"][0]["evidence"][0].update(
        {"status": "passed", "polarity": "positive"}
    )
    second["final"]["artifact"] = second["artifact"]

    ledger = experiment_store.build_experiment_ledger("ws-experiment", [first, second], outcomes=[])

    assert len(ledger["versions"]) == 2
    promoted = ledger["versions"][1]
    assert promoted["decision"]["verdict"] == "feasible"
    assert promoted["decision"]["dimensions"] == [
        {"name": "asset_data", "score": 4, "confidence": "data_confirmed"}
    ]
    assert promoted["decision"]["unverifiable_dimensions"] == [
        {
            "name": "synthetic_growth",
            "reason": "New or strengthened dimension has no new traceable evidence linked to its identity.",
        }
    ]


def test_strengthened_evidence_authorizes_only_its_linked_dimension() -> None:
    first = _analysis_run(
        "run-v1",
        verdict="conditional",
        score=3,
        evidence_ref="asset.csv#row-1",
    )
    second = _analysis_run(
        "run-v2",
        verdict="feasible",
        score=4,
        evidence_ref="asset.csv#row-1",
    )
    for run, value, status in ((first, 8, "failed"), (second, 10, "passed")):
        evidence = run["artifact"]["feasibility"]["dimensions"][0]["evidence"][0]
        evidence.update({"value": value, "direction": "higher", "status": status})
        run["final"]["artifact"] = run["artifact"]

    ledger = experiment_store.build_experiment_ledger("ws-experiment", [first, second], outcomes=[])

    promoted = ledger["versions"][1]
    assert promoted["evidence_delta"]["strengthened"]
    assert promoted["decision"]["dimensions"][0]["score"] == 4
    assert "unverifiable_dimensions" not in promoted["decision"]


def test_semantically_equivalent_decision_normalization_does_not_promote() -> None:
    first = _analysis_run(
        "run-v1",
        verdict="Conditional",
        score=3,
        evidence_ref="asset.csv#row-1",
    )
    first["artifact"]["feasibility"]["overall_confidence"] = " Data_Confirmed "
    first["artifact"]["feasibility"]["dimensions"][0]["confidence"] = " Data_Confirmed "
    first["final"]["artifact"] = first["artifact"]
    second = _analysis_run(
        "run-v2",
        verdict=" conditional ",
        score="3",
        evidence_ref="asset.csv#row-1",
    )

    ledger = experiment_store.build_experiment_ledger("ws-experiment", [first, second], outcomes=[])

    assert len(ledger["versions"]) == 1
    assert ledger["versions"][0]["decision"]["verdict"] == "conditional"
    assert ledger["versions"][0]["decision"]["confidence"] == "data_confirmed"
    assert ledger["versions"][0]["decision"]["dimensions"][0]["score"] == 3


def test_dimension_name_case_and_whitespace_do_not_promote() -> None:
    first = _analysis_run(
        "run-v1",
        verdict="conditional",
        score=3,
        evidence_ref="asset.csv#row-1",
    )
    first["artifact"]["feasibility"]["dimensions"][0]["name"] = "Asset_Data"
    first["final"]["artifact"] = first["artifact"]
    second = _analysis_run(
        "run-v2",
        verdict="conditional",
        score=3,
        evidence_ref="asset.csv#row-1",
    )
    second["artifact"]["feasibility"]["dimensions"][0]["name"] = "  asset_data  "
    second["final"]["artifact"] = second["artifact"]

    ledger = experiment_store.build_experiment_ledger("ws-experiment", [first, second], outcomes=[])

    assert len(ledger["versions"]) == 1


@pytest.mark.parametrize(
    ("first_verdict", "second_verdict"),
    [
        ("not_feasible", "not_yet_feasible"),
        ("feasible", "recommended"),
    ],
)
def test_equal_rank_verdict_aliases_do_not_promote(first_verdict: str, second_verdict: str) -> None:
    first = _analysis_run(
        "run-v1",
        verdict=first_verdict,
        score=3,
        evidence_ref="asset.csv#row-1",
    )
    second = _analysis_run(
        "run-v2",
        verdict=second_verdict,
        score=3,
        evidence_ref="asset.csv#row-1",
    )

    ledger = experiment_store.build_experiment_ledger("ws-experiment", [first, second], outcomes=[])

    assert len(ledger["versions"]) == 1


def test_equivalent_evidence_status_and_direction_spellings_are_unchanged() -> None:
    identity = {
        "ref": "metric.csv#row-1",
        "source_type": "computed",
        "source": {"file_id": "metric.csv", "file_version": "1", "connector_id": "upload"},
        "value": 10,
    }
    first = experiment_store._normalized_evidence(
        {**identity, "status": "pass", "direction": "higher"}
    )
    second = experiment_store._normalized_evidence(
        {**identity, "status": "verified", "direction": "higher_is_better"}
    )

    delta = experiment_store._evidence_delta([first], [second])

    assert delta["contradicted"] == []
    assert delta["strengthened"] == []
    assert len(delta["unchanged"]) == 1


def test_equivalent_evidence_aliases_still_detect_actual_opposing_transition() -> None:
    identity = {
        "ref": "metric.csv#row-1",
        "source_type": "computed",
        "source": {"file_id": "metric.csv", "file_version": "1", "connector_id": "upload"},
        "value": 10,
        "direction": "higher_is_better",
    }
    first = experiment_store._normalized_evidence({**identity, "status": "verified"})
    second = experiment_store._normalized_evidence({**identity, "status": "failed"})

    delta = experiment_store._evidence_delta([first], [second])

    assert len(delta["contradicted"]) == 1
    assert "passed to failed" in delta["contradicted"][0]["reason"]
    assert delta["unchanged"] == []


def test_evidence_normalization_preserves_independent_polarity_delta() -> None:
    identity = {
        "ref": "metric.csv#row-1",
        "source_type": "computed",
        "source": {"file_id": "metric.csv", "file_version": "1", "connector_id": "upload"},
        "value": 10,
        "direction": "higher_is_better",
        "status": "passed",
    }
    first = experiment_store._normalized_evidence({**identity, "polarity": "positive"})
    second = experiment_store._normalized_evidence({**identity, "polarity": "negative"})

    delta = experiment_store._evidence_delta([first], [second])

    assert first["direction"] == second["direction"] == "higher"
    assert first["polarity"] == "positive"
    assert second["polarity"] == "negative"
    assert len(delta["contradicted"]) == 1
    assert "polarity changed adversely from positive to negative" in delta["contradicted"][0]["reason"]


def test_mixed_favorable_status_and_adverse_value_fail_closed() -> None:
    first = _analysis_run(
        "run-v1",
        verdict="conditional",
        score=3,
        evidence_ref="metric.csv#row-1",
    )
    second = _analysis_run(
        "run-v2",
        verdict="feasible",
        score=4,
        evidence_ref="metric.csv#row-1",
    )
    first_evidence = first["artifact"]["feasibility"]["dimensions"][0]["evidence"][0]
    second_evidence = second["artifact"]["feasibility"]["dimensions"][0]["evidence"][0]
    first_evidence.update({"status": "failed", "direction": "higher", "value": 10})
    second_evidence.update({"status": "passed", "direction": "higher_is_better", "value": 8})
    first["final"]["artifact"] = first["artifact"]
    second["final"]["artifact"] = second["artifact"]

    ledger = experiment_store.build_experiment_ledger("ws-experiment", [first, second], outcomes=[])

    promoted = ledger["versions"][1]
    assert promoted["evidence_delta"]["strengthened"] == []
    assert len(promoted["evidence_delta"]["contradicted"]) == 1
    assert "conflict" in promoted["evidence_delta"]["contradicted"][0]["reason"].lower()
    assert promoted["decision"]["verdict"] == "conditional"
    assert promoted["decision"]["dimensions"][0]["score"] == 3


def test_analysis_order_uses_run_id_as_stable_timestamp_tiebreaker() -> None:
    canonical = _analysis_run(
        "analysis-a",
        verdict="conditional",
        score=3,
        evidence_ref="asset.csv#row-1",
    )
    duplicate = _analysis_run(
        "analysis-z",
        verdict="conditional",
        score=3,
        evidence_ref="asset.csv#row-1",
    )
    canonical["completed_at"] = duplicate["completed_at"] = "2026-07-12T02:00:00Z"

    ledger = experiment_store.build_experiment_ledger(
        "ws-experiment",
        [duplicate, canonical],
        outcomes=[],
    )

    assert [item["run_id"] for item in ledger["versions"]] == ["analysis-a"]


def test_decision_only_promotion_has_reason_for_each_normalized_field_change() -> None:
    first = _analysis_run(
        "run-v1",
        verdict="conditional",
        score=3,
        evidence_ref="asset.csv#row-1",
    )
    second = _analysis_run(
        "run-v2",
        verdict="not_yet_feasible",
        score=2,
        evidence_ref="asset.csv#row-1",
    )

    ledger = experiment_store.build_experiment_ledger("ws-experiment", [first, second], outcomes=[])

    assert len(ledger["versions"]) == 2
    decision_delta = ledger["versions"][1]["decision_delta"]
    assert {item["field"] for item in decision_delta["changes"]} == {"verdict", "dimensions"}
    assert all(item.get("reason") for item in decision_delta["changes"])
    assert all(item["reason"] in decision_delta["reasons"] for item in decision_delta["changes"])
    assert "conditional" in next(item["reason"] for item in decision_delta["changes"] if item["field"] == "verdict")
    assert "not_yet_feasible" in next(item["reason"] for item in decision_delta["changes"] if item["field"] == "verdict")
    assert decision_delta["summary"] != "No comparable evidence or normalized decision change"


def test_untrusted_nonself_lineage_is_bounded_unavailable_not_a_ledger_alias(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "blob_configured", lambda: False)
    run_store._ACTIVE.clear()
    canonical_id = "analysis-canonical"
    canonical_artifact = _analysis_run(
        canonical_id,
        verdict="conditional",
        score=3,
        evidence_ref="asset.csv#row-1",
    )["artifact"]
    run_store.start_run(canonical_id, "ws-experiment", "Analyze")
    canonical = run_store.complete_run(
        canonical_id,
        status="completed",
        final={"artifact": canonical_artifact},
        artifact=canonical_artifact,
    )
    fabricated_alias = _analysis_run(
        "analysis-fabricated-alias",
        verdict="conditional",
        score=3,
        evidence_ref="asset.csv#row-1",
    )
    fabricated_alias.update(
        {
            "canonical_experiment_run_id": "analysis-canonical",
            "canonical_experiment_version_id": "version:analysis-canonical",
            "canonical_resolution_status": "resolved",
            "canonical_lineage_status": "trusted",
        }
    )

    ledger = experiment_store.build_experiment_ledger(
        "ws-experiment",
        [canonical, fabricated_alias],
        outcomes=[],
    )

    assert [item["run_id"] for item in ledger["versions"]] == ["analysis-canonical"]
    assert ledger["lineage_resolution"] == {
        "status": "unavailable",
        "unresolved_run_ids": ["analysis-fabricated-alias"],
    }


def test_control_plane_hydrates_trusted_canonical_target_and_attachment(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(experiment_store, "EXPERIMENT_DIR", tmp_path / "experiments")
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "blob_configured", lambda: False)
    monkeypatch.setattr(
        control_plane,
        "require_sensitive_workspace_permission",
        lambda *_args, **_kwargs: "viewer",
    )
    workspace_id = "ws-hydrated-lineage"
    canonical_id = "analysis-outside-window"
    alias_id = "analysis-recent-alias"
    run_store._ACTIVE.clear()
    for run_id in (canonical_id, alias_id):
        artifact = _analysis_run(
            run_id,
            verdict="conditional",
            score=3,
            evidence_ref="asset.csv#row-1",
        )["artifact"]
        artifact["workspace_id"] = workspace_id
        run_store.start_run(run_id, workspace_id, "Analyze")
        run_store.complete_run(run_id, status="completed", final={"artifact": artifact}, artifact=artifact)
    canonical = run_store.get_run(canonical_id)
    alias = run_store.get_run(alias_id)
    snapshot = run_store.record_artifact_version(
        workspace_id=workspace_id,
        source_run_id=canonical_id,
        experiment_version_id=f"version:{canonical_id}",
        artifact=canonical["artifact"],
        proposal={"artifact_urls": {"pdf": "/api/artifacts/report.pdf"}},
        kinds=["pdf"],
    )
    assert snapshot is not None
    details = {canonical_id: canonical, alias_id: alias, snapshot["run_id"]: snapshot}
    monkeypatch.setattr(control_plane, "list_runs", lambda workspace_id=None: [snapshot, alias])
    monkeypatch.setattr(control_plane, "get_run", lambda run_id: details[run_id])
    monkeypatch.setattr(run_store, "get_run", lambda run_id: details[run_id])
    monkeypatch.setattr(control_plane, "list_outcome_events", lambda workspace_id: [])

    response = TestClient(app).get(f"/api/workspaces/{workspace_id}/experiments")

    assert response.status_code == 200
    body = response.json()
    assert [item["run_id"] for item in body["versions"]] == [canonical_id]
    assert body["versions"][0]["attachments"]["artifacts"][0]["run_id"] == snapshot["run_id"]
    assert body["lineage_resolution"]["status"] == "resolved"


def test_favorable_status_with_adverse_confidence_is_not_strengthening() -> None:
    first = _analysis_run("run-v1", verdict="conditional", score=3, evidence_ref="metric.csv#row-1")
    second = _analysis_run("run-v2", verdict="feasible", score=4, evidence_ref="metric.csv#row-1")
    before = first["artifact"]["feasibility"]["dimensions"][0]["evidence"][0]
    after = second["artifact"]["feasibility"]["dimensions"][0]["evidence"][0]
    before.update({"status": "failed", "confidence": "data_confirmed"})
    after.update({"status": "passed", "confidence": "market_inferred"})
    first["final"]["artifact"] = first["artifact"]
    second["final"]["artifact"] = second["artifact"]

    ledger = experiment_store.build_experiment_ledger("ws-experiment", [first, second], outcomes=[])

    promoted = ledger["versions"][1]
    assert promoted["evidence_delta"]["strengthened"] == []
    assert "conflict" in promoted["evidence_delta"]["contradicted"][0]["reason"].lower()
    assert promoted["decision"]["verdict"] == "conditional"
    assert promoted["decision"]["dimensions"][0]["score"] == 3


def test_truncated_registry_rejects_legacy_analysis_without_trusted_lineage() -> None:
    legacy = _analysis_run(
        "legacy-analysis",
        verdict="conditional",
        score=3,
        evidence_ref="asset.csv#row-1",
    )

    ledger = experiment_store.build_experiment_ledger(
        "ws-experiment",
        [legacy],
        outcomes=[],
        registry_state={"history_truncated": True, "runs": []},
    )

    assert ledger["versions"] == []
    assert ledger["lineage_resolution"] == {
        "status": "unavailable",
        "unresolved_run_ids": ["legacy-analysis"],
    }


def test_malformed_snapshot_is_not_exposed_as_public_attachment(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(experiment_store, "EXPERIMENT_DIR", tmp_path / "experiments")
    monkeypatch.setattr(run_store, "blob_configured", lambda: False)
    monkeypatch.setattr(run_store, "upload_blob_json", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(run_store, "download_blob_json", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        control_plane,
        "require_sensitive_workspace_permission",
        lambda *_args, **_kwargs: "viewer",
    )
    run_store._ACTIVE.clear()
    workspace_id = "ws-untrusted-snapshot"
    source_run_id = "analysis-public-source"
    artifact = _analysis_run(
        source_run_id,
        verdict="conditional",
        score=3,
        evidence_ref="asset.csv#row-1",
    )["artifact"]
    artifact["workspace_id"] = workspace_id
    run_store.start_run(source_run_id, workspace_id, "Analyze")
    source = run_store.complete_run(source_run_id, status="completed", final={"artifact": artifact}, artifact=artifact)
    malformed = {
        "run_id": "artifact-malformed",
        "workspace_id": workspace_id,
        "status": "completed",
        "completed_at": "2026-07-12T05:00:00Z",
        "version_kind": "artifact_generation",
        "source_run_id": source_run_id,
        "experiment_version_id": f"version:{source_run_id}",
        "experiment_attachment": True,
        "attachment_commit_status": "unresolved",
        "artifact": {"proposal": {"artifact_urls": {"pdf": "/api/artifacts/untrusted.pdf"}}},
    }
    details = {source_run_id: source, malformed["run_id"]: malformed}
    monkeypatch.setattr(control_plane, "list_runs", lambda workspace_id=None: [malformed, source])
    monkeypatch.setattr(control_plane, "get_run", lambda run_id: details[run_id])
    monkeypatch.setattr(run_store, "get_run", lambda run_id: details[run_id])
    monkeypatch.setattr(control_plane, "list_outcome_events", lambda workspace_id: [])

    response = TestClient(app).get(f"/api/workspaces/{workspace_id}/experiments")

    assert response.status_code == 200
    version = response.json()["versions"][0]
    assert version["attachments"]["artifacts"] == []
    assert response.json()["lineage_resolution"]["status"] == "resolved"


def test_tampered_confirmed_snapshot_payload_is_hidden_from_public_ledger(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(experiment_store, "EXPERIMENT_DIR", tmp_path / "experiments")
    monkeypatch.setattr(run_store, "blob_configured", lambda: False)
    monkeypatch.setattr(
        control_plane,
        "require_sensitive_workspace_permission",
        lambda *_args, **_kwargs: "viewer",
    )
    run_store._ACTIVE.clear()
    workspace_id = "ws-tampered-snapshot"
    source_run_id = "analysis-tamper-source"
    artifact = _analysis_run(
        source_run_id,
        verdict="conditional",
        score=3,
        evidence_ref="asset.csv#row-1",
    )["artifact"]
    artifact["workspace_id"] = workspace_id
    run_store.start_run(source_run_id, workspace_id, "Analyze")
    source = run_store.complete_run(source_run_id, status="completed", final={"artifact": artifact}, artifact=artifact)
    snapshot = run_store.record_artifact_version(
        workspace_id=workspace_id,
        source_run_id=source_run_id,
        experiment_version_id=f"version:{source_run_id}",
        artifact=artifact,
        proposal={"artifact_urls": {"pdf": "/api/artifacts/original.pdf"}},
        kinds=["pdf"],
    )
    assert snapshot is not None
    snapshot_path = tmp_path / "runs" / f"{run_store._safe_name(snapshot['run_id'])}.json"
    tampered = json.loads(snapshot_path.read_text(encoding="utf-8"))
    tampered["artifact"]["proposal"]["artifact_urls"]["pdf"] = "/api/artifacts/tampered.pdf"
    snapshot_path.write_text(json.dumps(tampered), encoding="utf-8")
    details = {
        source_run_id: source,
        snapshot["run_id"]: run_store.get_run(snapshot["run_id"]),
    }
    monkeypatch.setattr(control_plane, "list_runs", lambda workspace_id=None: [details[snapshot["run_id"]], source])
    monkeypatch.setattr(control_plane, "get_run", lambda run_id: details[run_id])
    monkeypatch.setattr(control_plane, "list_outcome_events", lambda workspace_id: [])

    response = TestClient(app).get(f"/api/workspaces/{workspace_id}/experiments")

    assert response.status_code == 200
    assert response.json()["versions"][0]["attachments"]["artifacts"] == []
    assert response.json()["lineage_resolution"]["status"] == "unavailable"


def test_undirected_value_and_unit_change_conflicts_with_higher_confidence() -> None:
    first = _analysis_run("run-v1", verdict="conditional", score=3, evidence_ref="metric.csv#row-1")
    second = _analysis_run("run-v2", verdict="feasible", score=4, evidence_ref="metric.csv#row-1")
    before = first["artifact"]["feasibility"]["dimensions"][0]["evidence"][0]
    after = second["artifact"]["feasibility"]["dimensions"][0]["evidence"][0]
    before.update({"value": 10, "unit": "count", "confidence": "market_inferred"})
    after.update({"value": 12, "unit": "percent", "confidence": "data_confirmed"})
    first["final"]["artifact"] = first["artifact"]
    second["final"]["artifact"] = second["artifact"]

    ledger = experiment_store.build_experiment_ledger("ws-experiment", [first, second], outcomes=[])

    promoted = ledger["versions"][1]
    assert promoted["evidence_delta"]["strengthened"] == []
    assert "conflict" in promoted["evidence_delta"]["contradicted"][0]["reason"].lower()
    assert "without a direction" in promoted["evidence_delta"]["contradicted"][0]["reason"]
    assert "unit changed" in promoted["evidence_delta"]["contradicted"][0]["reason"]
    assert promoted["decision"]["verdict"] == "conditional"
    assert promoted["decision"]["dimensions"][0]["score"] == 3


def test_new_traceable_adverse_evidence_cannot_authorize_strengthening() -> None:
    first = _analysis_run("run-v1", verdict="conditional", score=3, evidence_ref="baseline.csv#row-1")
    second = _analysis_run("run-v2", verdict="feasible", score=4, evidence_ref="baseline.csv#row-1")
    dimension = second["artifact"]["feasibility"]["dimensions"][0]
    dimension["evidence"].append(
        {
            "source_type": "corpus",
            "ref": "pilot.csv#row-2",
            "file_id": "pilot.csv",
            "file_version": "2",
            "connector_id": "upload",
            "status": "failed",
            "polarity": "negative",
            "confidence": "data_confirmed",
        }
    )
    second["final"]["artifact"] = second["artifact"]

    ledger = experiment_store.build_experiment_ledger("ws-experiment", [first, second], outcomes=[])

    promoted = ledger["versions"][1]
    adverse = next(item for item in promoted["evidence_delta"]["added"] if item["ref"] == "pilot.csv#row-2")
    assert adverse["change_class"] == "adverse"
    assert "failed" in adverse["reason"] and "negative" in adverse["reason"]
    assert promoted["decision"]["verdict"] == "conditional"
    assert promoted["decision"]["dimensions"][0]["score"] == 3


@pytest.mark.parametrize(
    ("before", "after", "expected_category", "reason_fragment"),
    [
        ({"value": 10, "direction": "higher"}, {"value": 12, "direction": "higher"}, "strengthened", "favorably"),
        ({"value": 10, "direction": "lower"}, {"value": 8, "direction": "lower"}, "strengthened", "favorably"),
        ({"value": 10, "direction": "higher"}, {"value": 8, "direction": "higher"}, "contradicted", "adversely"),
        ({"value": 10, "direction": "lower"}, {"value": 12, "direction": "lower"}, "contradicted", "adversely"),
        ({"status": "failed"}, {"status": "passed"}, "strengthened", "failed to passed"),
        ({"status": "passed"}, {"status": "failed"}, "contradicted", "passed to failed"),
    ],
)
def test_evidence_delta_uses_direction_and_status_semantics(
    before: dict,
    after: dict,
    expected_category: str,
    reason_fragment: str,
) -> None:
    identity = {
        "ref": "metric.csv#row-1",
        "source_type": "computed",
        "source": {"file_id": "metric.csv", "file_version": "1", "connector_id": "upload"},
    }

    delta = experiment_store._evidence_delta([{**identity, **before}], [{**identity, **after}])

    assert len(delta[expected_category]) == 1
    assert reason_fragment in delta[expected_category][0]["reason"]
    opposite = "contradicted" if expected_category == "strengthened" else "strengthened"
    assert delta[opposite] == []


def test_experiment_api_rejects_fabricated_verified_inputs_and_outcomes(monkeypatch) -> None:
    monkeypatch.setenv("DF_ENVIRONMENT", "test")
    monkeypatch.setenv("DF_SENSITIVE_AUTH_LOCAL_DEV_BYPASS", "1")
    monkeypatch.setenv("DF_MEMBER_PSEUDONYM_SALT", "experiment-api-trust-boundary-salt")
    runs = [
        _analysis_run(
            "run-v1",
            verdict="conditional",
            score=3,
            evidence_ref="evidence.csv#row-1",
        ),
        _analysis_run(
            "run-v2",
            verdict="feasible",
            score=4,
            evidence_ref="evidence.csv#row-1",
            iteration_inputs=[
                {
                    "label": "pilot_conversion_rate",
                    "value": "7.2",
                    "unit": "percent",
                    "kind": "observed",
                    "source": {"file_id": "feedback-v2.csv", "run_id": "run-v2"},
                    "verification": {"status": "verified"},
                }
            ],
        ),
    ]
    monkeypatch.setattr(control_plane, "list_runs", lambda workspace_id=None: runs)
    monkeypatch.setattr(control_plane, "get_run", lambda run_id: next(item for item in runs if item["run_id"] == run_id))
    fabricated_outcome = {
        "event_id": "fabricated-outcome",
        "workspace_id": "ws-experiment",
        "metric_name": "pilot_conversion_rate",
        "unit": "percent",
        "observed_value": 7.2,
        "observed_at": "2026-07-12T01:30:00Z",
        "created_at": "2026-07-12T01:30:00Z",
        "provenance": "observed",
        "source": {"run_id": "run-v1"},
        "verification": {
            "status": "verified",
            "verification_event_id": "fabricated-verification",
            "trusted_identity": True,
        },
    }
    monkeypatch.setattr(control_plane, "list_outcome_events", lambda workspace_id: [fabricated_outcome])
    monkeypatch.setattr(
        control_plane,
        "sync_experiment_ledger",
        lambda workspace_id, _runs, outcomes=None: experiment_store.build_experiment_ledger(
            workspace_id,
            runs,
            outcomes=outcomes,
            registry_state={"history_truncated": False, "read_status": "present", "runs": []},
        ),
    )

    response = TestClient(app).get("/api/workspaces/ws-experiment/experiments")

    assert response.status_code == 200
    assert [item["label"] for item in response.json()["versions"]] == ["V1"]
    assert response.json()["versions"][0]["decision"]["verdict"] == "conditional"


def test_experiment_and_compare_api_recursively_redact_outcome_verification_identity(monkeypatch) -> None:
    monkeypatch.setenv("DF_MEMBER_PSEUDONYM_SALT", "experiment-api-redaction-salt")
    runs = [
        _analysis_run("run-v1", verdict="conditional", score=3, evidence_ref="evidence.csv#row-1"),
        _analysis_run("run-v2", verdict="feasible", score=4, evidence_ref="feedback.csv#row-2"),
    ]
    raw_outcome = {
        "event_id": "outcome-private",
        "workspace_id": "ws-experiment",
        "metric_name": "pilot_conversion_rate",
        "unit": "percent",
        "observed_value": 8.2,
        "observed_at": "2026-07-12T02:00:00Z",
        "provenance": "observed",
        "source": {"run_id": "run-v1"},
        "actor": {
            "email": "author.private@example.com",
            "name": "Author Private Name",
            "actor_id": "author-private-oid",
            "tenant_id": "tenant-private",
        },
        "verification": {
            "status": "verified",
            "verification_event_id": "verification-private",
            "reviewer": {
                "email": "reviewer.private@example.com",
                "name": "Reviewer Private Name",
                "actor_id": "reviewer-private-oid",
                "tenant_id": "tenant-private",
            },
            "note": "Contact reviewer.private@example.com about this result",
            "event": {
                "event_id": "verification-private",
                "workspace_id": "ws-experiment",
                "kind": "outcome_verification",
                "outcome_event_id": "outcome-private",
                "actor": {
                    "email": "nested.private@example.com",
                    "name": "Nested Private Name",
                    "actor_id": "nested-private-oid",
                    "tenant_id": "tenant-private",
                },
                "note": "Nested note for nested.private@example.com",
            },
        },
    }
    actions = []
    monkeypatch.setattr(control_plane, "require_workspace_permission", lambda _workspace_id, _actor, action: actions.append(action) or "viewer")
    monkeypatch.setattr(control_plane, "list_runs", lambda workspace_id=None: runs)
    monkeypatch.setattr(control_plane, "get_run", lambda run_id: next(item for item in runs if item["run_id"] == run_id))
    monkeypatch.setattr(control_plane, "list_outcome_events", lambda _workspace_id: [raw_outcome])
    monkeypatch.setattr(
        control_plane,
        "sync_experiment_ledger",
        lambda workspace_id, _runs, outcomes=None: experiment_store.build_experiment_ledger(
            workspace_id,
            runs,
            outcomes=outcomes,
            registry_state={"history_truncated": False, "read_status": "present", "runs": []},
        ),
    )
    client = TestClient(app)

    ledger_response = client.get("/api/workspaces/ws-experiment/experiments")
    compare_response = client.get(
        "/api/workspaces/ws-experiment/experiments/compare",
        params={"from": "version:run-v1", "to": "version:run-v2"},
    )

    assert ledger_response.status_code == compare_response.status_code == 200
    assert actions == ["run.read", "run.read"]
    for response in (ledger_response, compare_response):
        serialized = json.dumps(response.json())
        assert "subject_label" in serialized
        for raw in (
            "author.private@example.com", "Author Private Name", "author-private-oid",
            "reviewer.private@example.com", "Reviewer Private Name", "reviewer-private-oid",
            "nested.private@example.com", "Nested Private Name", "nested-private-oid",
            "tenant-private", "Contact reviewer", "Nested note",
        ):
            assert raw not in serialized
