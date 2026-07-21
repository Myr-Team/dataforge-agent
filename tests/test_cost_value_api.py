from __future__ import annotations

import backend.control_plane as control_plane
from backend.app import app
from backend.roi_service import realized_roi_evidence
from fastapi.testclient import TestClient


WINDOW = {"from": "2026-07-10T00:00:00Z", "to": "2026-07-11T00:00:00Z"}


def test_realized_roi_requires_verified_outcomes_complete_cost_and_matching_currency() -> None:
    empty = realized_roi_evidence(
        {
            "status": "estimated",
            "outcome_event_ids": [],
            "verified_outcome_event_ids": [],
            "cost": {"status": "unknown", "total": None, "currency": None},
            "business_value": None,
        }
    )
    verified = realized_roi_evidence(
        {
            "status": "verified",
            "outcome_event_ids": ["outcome-1"],
            "verified_outcome_event_ids": ["outcome-1"],
            "cost": {"status": "complete", "total": 10.0, "currency": "USD"},
            "business_value": {"status": "measured", "total": 110.0, "currency": "USD"},
        }
    )

    assert empty == {"status": "not_recorded", "value": None, "currency": None, "net_value": None, "roi_ratio": None}
    assert verified == {"status": "verified", "value": 110.0, "currency": "USD", "net_value": 100.0, "roi_ratio": 10.0}


def test_cost_value_route_keeps_evidence_scenarios_and_integration_separate(monkeypatch) -> None:
    actions: list[str] = []
    monkeypatch.setattr(
        control_plane,
        "_require_workspace_owner",
        lambda _workspace_id, _request, action: actions.append(action) or "owner",
    )
    monkeypatch.setattr(
        control_plane,
        "workspace_roi_snapshot",
        lambda workspace_id, _from, _to: {
            "workspace_id": workspace_id,
            "window": WINDOW,
            "generated_at": "2026-07-11T00:00:00Z",
            "cost_evidence": {"status": "complete", "total": 10.0, "currency": "USD"},
            "outcome_evidence": {"status": "not_recorded"},
            "foundry_integration": {"state": "not_connected", "official_source": False},
            "status": "estimated",
            "outcome_event_ids": [],
            "verified_outcome_event_ids": [],
            "cost": {"status": "complete", "total": 10.0, "currency": "USD"},
            "business_value": None,
        },
    )
    monkeypatch.setattr(control_plane, "list_roi_scenarios", lambda _workspace_id: [{"scenario_id": "roi_scenario_1234567890abcdef"}])
    monkeypatch.setattr(
        control_plane,
        "scenario_projection",
        lambda _workspace_id, _scenario: {"scenario_id": "roi_scenario_1234567890abcdef", "status": "estimated"},
    )
    client = TestClient(app)

    response = client.get("/api/workspaces/ws-1/governance/cost-value?from=2026-07-10T00:00:00Z&to=2026-07-11T00:00:00Z")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["cost_evidence"]["status"] == "complete"
    assert body["outcome_evidence"]["status"] == "not_recorded"
    assert body["realized_roi"]["status"] == "not_recorded"
    assert body["scenarios"] == [{"scenario_id": "roi_scenario_1234567890abcdef", "status": "estimated"}]
    assert body["foundry_integration"]["state"] == "not_connected"
    assert actions == ["workspace.read"]
