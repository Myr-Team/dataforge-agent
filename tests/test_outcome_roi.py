from __future__ import annotations

from pathlib import Path

import pytest

import backend.control_plane as control_plane
import backend.outcome_store as outcome_store
from backend.app import app
from fastapi.testclient import TestClient


def _configure_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(outcome_store, "OUTCOME_DIR", tmp_path / "outcomes")
    monkeypatch.setattr(outcome_store, "download_blob_json", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(outcome_store, "upload_blob_json", lambda *_args, **_kwargs: {})


def _actor(name: str = "Owner") -> dict[str, str]:
    return {
        "name": name,
        "email": f"{name.lower()}@contoso.com",
        "actor_id": f"oid-{name.lower()}",
    }


def _observed_payload() -> dict[str, object]:
    return {
        "metric_name": "pilot_conversion_rate",
        "unit": "percent",
        "baseline_value": 4.2,
        "target_value": 6.0,
        "observed_value": 5.4,
        "observed_at": "2026-07-12T02:00:00Z",
        "attribution_window_days": 14,
        "provenance": "observed",
        "source": {"file_id": "feedback-v2.csv", "run_id": "run-v2"},
    }


def test_observed_outcome_requires_source_lineage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    payload = _observed_payload()
    payload["source"] = {}

    with pytest.raises(ValueError, match="source lineage"):
        outcome_store.record_outcome_event("ws-roi", payload, _actor())


def test_client_cannot_create_verified_outcome(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    payload = _observed_payload()
    payload["verification"] = {"status": "verified"}

    with pytest.raises(ValueError, match="verified"):
        outcome_store.record_outcome_event("ws-roi", payload, _actor())


def test_outcome_verification_is_a_separate_reviewer_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_store(tmp_path, monkeypatch)
    event = outcome_store.record_outcome_event("ws-roi", _observed_payload(), _actor())

    assert event["provenance"] == "observed"
    assert event["verification"]["status"] == "unverified"

    verified = outcome_store.verify_outcome_event(
        "ws-roi",
        event["event_id"],
        _actor("Reviewer"),
        note="Source file and attribution window reviewed.",
    )

    assert verified["verification"]["status"] == "verified"
    assert verified["verification"]["reviewer"]["actor_id"] == "oid-reviewer"
    assert outcome_store.list_outcome_events("ws-roi")[0]["event_id"] == event["event_id"]


def test_roi_states_follow_outcome_evidence() -> None:
    usage = {
        "totals": {
            "runs": 1,
            "agent_runs": 1,
            "snapshot_runs": 0,
            "known_usage_runs": 1,
            "unknown_usage_runs": 0,
            "total_tokens": 1000,
            "usage_status": "complete",
        }
    }
    audit = {"events": []}

    estimated = control_plane._workspace_roi_summary(usage, audit, [])
    assert estimated["status"] == "estimated"

    observed = {
        **_observed_payload(),
        "event_id": "outcome-observed",
        "verification": {"status": "unverified"},
    }
    measured = control_plane._workspace_roi_summary(usage, audit, [observed])
    assert measured["status"] == "measured"
    assert measured["outcomes"]["observed_count"] == 1

    verified_event = {
        **observed,
        "verification": {"status": "verified", "verified_at": "2026-07-12T03:00:00Z"},
    }
    verified = control_plane._workspace_roi_summary(usage, audit, [verified_event])
    assert verified["status"] == "verified"
    assert verified["outcomes"]["verified_count"] == 1


def test_synthetic_outcome_does_not_promote_roi_state() -> None:
    usage = {"totals": {"runs": 0, "agent_runs": 0, "snapshot_runs": 0}}
    synthetic = {
        "event_id": "outcome-synthetic",
        "metric_name": "simulated_conversion_rate",
        "unit": "percent",
        "observed_value": 8.0,
        "provenance": "synthetic",
        "verification": {"status": "unverified"},
    }

    result = control_plane._workspace_roi_summary(usage, {"events": []}, [synthetic])

    assert result["status"] == "estimated"
    assert result["outcomes"]["synthetic_count"] == 1


def test_outcome_api_persists_lists_and_verifies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_store(tmp_path, monkeypatch)
    client = TestClient(app)

    created = client.post("/api/workspaces/ws-roi/outcomes", json=_observed_payload())
    assert created.status_code == 200
    event_id = created.json()["event"]["event_id"]

    listed = client.get("/api/workspaces/ws-roi/outcomes")
    assert listed.status_code == 200
    assert listed.json()["count"] == 1

    verified = client.post(
        f"/api/workspaces/ws-roi/outcomes/{event_id}/verify",
        json={"note": "Reviewed against the imported feedback file."},
    )
    assert verified.status_code == 200
    assert verified.json()["event"]["verification"]["status"] == "verified"
