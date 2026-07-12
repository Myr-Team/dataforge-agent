from __future__ import annotations

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
    version = ledger["versions"][1]

    assert version["decision"]["model_verdict"] == "feasible"
    assert version["decision"]["verdict"] == "conditional"
    assert version["decision"]["verdict_guard"]["reason"] == "no_new_traceable_evidence"
    assert version["metrics"][0]["provenance"] == "reported_unverified"


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
    version = ledger["versions"][1]

    assert version["evidence_changed"] is False
    assert version["decision_delta"]["changed"] is False
    assert version["decision_delta"]["summary"] == "暂无可比较的新证据"


def test_experiment_api_returns_canonical_versions(monkeypatch) -> None:
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
