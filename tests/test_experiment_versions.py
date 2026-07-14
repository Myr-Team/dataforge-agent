from __future__ import annotations

import json

import backend.experiment_store as experiment_store
import backend.control_plane as control_plane
from backend.app import app
from fastapi.testclient import TestClient


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


def test_new_observed_evidence_creates_evidence_and_decision_delta() -> None:
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
        evidence_ref="feedback-v2.csv#row-8",
        iteration_inputs=[
            {
                "label": "pilot_conversion_rate",
                "value": "7.2",
                "unit": "percent",
                "kind": "observed",
                "source": {"file_id": "feedback-v2.csv", "run_id": "run-v2"},
            }
        ],
    )

    ledger = experiment_store.build_experiment_ledger("ws-experiment", [first, second], outcomes=[])
    version = ledger["versions"][1]

    assert version["label"] == "V2"
    assert version["evidence_delta"]["added"]
    assert version["decision_delta"]["changed"] is True
    assert version["decision"]["verdict"] == "feasible"
    assert version["metrics"][0]["provenance"] == "observed"


def test_observed_outcome_promotes_only_after_completed_reanalysis() -> None:
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


def test_experiment_api_returns_canonical_versions(monkeypatch) -> None:
    monkeypatch.setenv("DF_ENVIRONMENT", "test")
    monkeypatch.setenv("DF_SENSITIVE_AUTH_LOCAL_DEV_BYPASS", "1")
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
            evidence_ref="feedback-v2.csv#row-8",
            iteration_inputs=[
                {
                    "label": "pilot_conversion_rate",
                    "value": "7.2",
                    "unit": "percent",
                    "kind": "observed",
                    "source": {"file_id": "feedback-v2.csv", "run_id": "run-v2"},
                }
            ],
        ),
    ]
    monkeypatch.setattr(control_plane, "list_runs", lambda workspace_id=None: runs)
    monkeypatch.setattr(control_plane, "get_run", lambda run_id: next(item for item in runs if item["run_id"] == run_id))
    monkeypatch.setattr(control_plane, "list_outcome_events", lambda workspace_id: [])
    monkeypatch.setattr(control_plane, "sync_experiment_ledger", experiment_store.build_experiment_ledger)

    response = TestClient(app).get("/api/workspaces/ws-experiment/experiments")

    assert response.status_code == 200
    assert [item["label"] for item in response.json()["versions"]] == ["V1", "V2"]


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
    monkeypatch.setattr(control_plane, "require_sensitive_workspace_permission", lambda _workspace_id, _actor, action, **_kwargs: actions.append(action) or "viewer")
    monkeypatch.setattr(control_plane, "list_runs", lambda workspace_id=None: runs)
    monkeypatch.setattr(control_plane, "get_run", lambda run_id: next(item for item in runs if item["run_id"] == run_id))
    monkeypatch.setattr(control_plane, "list_outcome_events", lambda _workspace_id: [raw_outcome])
    monkeypatch.setattr(control_plane, "sync_experiment_ledger", experiment_store.build_experiment_ledger)
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
