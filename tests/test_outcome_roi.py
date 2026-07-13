from __future__ import annotations

from pathlib import Path
from urllib.parse import quote
import base64
import json

import pytest

import backend.control_plane as control_plane
import backend.outcome_store as outcome_store
from backend.app import app
from fastapi.testclient import TestClient


def _configure_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(outcome_store, "OUTCOME_DIR", tmp_path / "outcomes")
    monkeypatch.setattr(outcome_store, "download_blob_json", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(outcome_store, "upload_blob_json", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(outcome_store, "_source_reference_exists", lambda *_args, **_kwargs: True)


def _actor(name: str = "Owner") -> dict[str, str]:
    return {
        "name": name,
        "email": f"{name.lower()}@contoso.com",
        "actor_id": f"oid-{name.lower()}",
        "source": "easy_auth",
    }


def _easy_headers(name: str) -> dict[str, str]:
    payload = {"claims": [{"typ": "name", "val": name}, {"typ": "preferred_username", "val": f"{name.lower()}@contoso.com"}, {"typ": "oid", "val": f"oid-{name.lower()}"}]}
    principal = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return {"x-ms-client-principal": principal, "x-dataforge-proxy-secret": "test-proxy-secret"}


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


def test_observed_outcome_rejects_forged_cross_workspace_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    monkeypatch.setattr(outcome_store, "_source_reference_exists", lambda *_args, **_kwargs: False)

    with pytest.raises(ValueError, match="real same-workspace"):
        outcome_store.record_outcome_event("ws-roi", _observed_payload(), _actor())


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
    assert verified["verification"]["verification_event_id"].startswith("verification_")
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
        "actor": {"actor_id": "oid-owner"},
        "verification": {"status": "unverified"},
    }
    measured = control_plane._workspace_roi_summary(usage, audit, [observed])
    assert measured["status"] == "measured"
    assert measured["outcomes"]["observed_count"] == 1

    verified_event = {
        **observed,
        "verification": {
            "status": "verified",
            "verified_at": "2026-07-12T03:00:00Z",
            "verification_event_id": "verification-1",
            "reviewer": {"actor_id": "oid-reviewer"},
        },
    }
    verified = control_plane._workspace_roi_summary(usage, audit, [verified_event])
    assert verified["status"] == "verified"
    assert verified["outcomes"]["verified_count"] == 1


def test_outcome_verification_requires_independent_actor_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_store(tmp_path, monkeypatch)
    event = outcome_store.record_outcome_event("ws-roi", _observed_payload(), _actor())

    with pytest.raises(ValueError, match="independent"):
        outcome_store.verify_outcome_event("ws-roi", event["event_id"], _actor())


def test_business_value_requires_source_formula_and_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    payload = _observed_payload()
    payload["business_value"] = {"value": 100, "currency": "USD", "source": "finance-ledger"}

    with pytest.raises(ValueError, match="business_value"):
        outcome_store.record_outcome_event("ws-roi", payload, _actor())


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
    monkeypatch.setenv("DF_WEB_PROXY_SECRET", "test-proxy-secret")
    client = TestClient(app)
    owner_headers = _easy_headers("Owner")
    reviewer_headers = _easy_headers("Reviewer")

    created = client.post("/api/workspaces/ws-roi/outcomes", json=_observed_payload(), headers=owner_headers)
    assert created.status_code == 200
    event_id = created.json()["event"]["event_id"]

    listed = client.get("/api/workspaces/ws-roi/outcomes")
    assert listed.status_code == 200
    assert listed.json()["count"] == 1

    verified = client.post(
        f"/api/workspaces/ws-roi/outcomes/{event_id}/verify",
        json={"note": "Reviewed against the imported feedback file."},
        headers=reviewer_headers,
    )
    assert verified.status_code == 200
    assert verified.json()["event"]["verification"]["status"] == "verified"
