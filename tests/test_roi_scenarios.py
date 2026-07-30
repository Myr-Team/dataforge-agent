from __future__ import annotations

from pathlib import Path

import backend.roi_scenario_store as roi_scenario_store
import backend.control_plane as control_plane
import backend.workspace_authz as workspace_authz
import pytest
from backend.app import app
from fastapi.testclient import TestClient


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": "Pilot A",
        "currency": "USD",
        "expected_revenue": 12000,
        "expected_avoided_cost": 3000,
        "pilot_cost": 5000,
        "expected_saved_hours": 24,
        "time_horizon_days": 90,
        "linked_run_id": "run-1",
        "evidence_revision": 2,
    }
    payload.update(overrides)
    return payload


def _dataforge_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": "运营自动化情景",
        "currency": "USD",
        "hours_saved": 40,
        "hourly_value": 50,
        "avoided_loss_or_revenue": 1000,
        "implementation_cost": 6000,
        "monthly_fixed_cost": 200,
        "model_cost": 100,
        "evaluation_months": 12,
        "evidence_revision": 3,
    }
    payload.update(overrides)
    return payload


def _configure_store(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(roi_scenario_store, "SCENARIO_DIR", tmp_path / "scenarios")
    monkeypatch.setattr(roi_scenario_store, "blob_configured", lambda: False)
    monkeypatch.setattr(roi_scenario_store, "upload_blob_json", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(roi_scenario_store, "download_blob_json_strict", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(roi_scenario_store, "_linked_run_is_valid", lambda *_args, **_kwargs: True)


def test_scenario_is_immutable_estimate_and_new_revision_is_linked(monkeypatch, tmp_path: Path) -> None:
    _configure_store(monkeypatch, tmp_path)
    actor = {"actor_id": "owner-1", "tenant_id": "tenant-1", "source": "easy_auth"}

    first = roi_scenario_store.create_roi_scenario("ws-1", _payload(), actor)
    second = roi_scenario_store.create_roi_scenario(
        "ws-1",
        _payload(expected_revenue=15000),
        actor,
        previous_id=first["scenario_id"],
    )

    assert first["status"] == second["status"] == "estimated"
    assert second["revision"] == 2
    assert second["previous_id"] == first["scenario_id"]
    assert first["result"] == {
        "status": "estimated",
        "currency": "USD",
        "estimated_business_value": 15000.0,
        "pilot_cost": 5000.0,
        "net_value": 10000.0,
        "roi_ratio": 2.0,
        "saved_hours": 24.0,
        "formula_version": "roi-scenario-v1",
    }
    assert second["result"]["estimated_business_value"] == 18000.0
    assert roi_scenario_store.list_roi_scenarios("ws-1")[-1]["scenario_id"] == second["scenario_id"]


def test_dataforge_roi_scenario_persists_inputs_formula_and_revision(monkeypatch, tmp_path: Path) -> None:
    _configure_store(monkeypatch, tmp_path)

    first = roi_scenario_store.create_roi_scenario(
        "ws-1",
        _dataforge_payload(),
        {"actor_id": "owner-1"},
    )
    second = roi_scenario_store.create_roi_scenario(
        "ws-1",
        _dataforge_payload(hours_saved=48),
        {"actor_id": "owner-1"},
        previous_id=first["scenario_id"],
        base_revision=1,
    )

    assert first["result"]["monthly_benefit"] == 3000
    assert first["result"]["monthly_total_cost"] == 800
    assert first["result"]["formula_revision"] == "dataforge-roi-v1"
    assert second["revision"] == 2
    assert second["inputs"]["hours_saved"] == 48

    with pytest.raises(roi_scenario_store.ScenarioRevisionConflict):
        roi_scenario_store.create_roi_scenario(
            "ws-1",
            _dataforge_payload(hours_saved=60),
            {"actor_id": "owner-2"},
            previous_id=first["scenario_id"],
            base_revision=1,
        )


def test_demo_roi_scenario_upsert_is_stable_and_keeps_internal_seed_batch(monkeypatch, tmp_path: Path) -> None:
    _configure_store(monkeypatch, tmp_path)
    payload = {**_dataforge_payload(), "seed_batch": "operations-v1"}

    first = roi_scenario_store.upsert_demo_roi_scenario(
        "ws-1",
        payload,
        {"actor_id": "seed-owner"},
        seed_key="operations-v1",
    )
    second = roi_scenario_store.upsert_demo_roi_scenario(
        "ws-1",
        payload,
        {"actor_id": "seed-owner"},
        seed_key="operations-v1",
    )

    assert first["scenario_id"] == second["scenario_id"]
    assert first["result"]["formula_revision"] == "dataforge-roi-v1"
    assert second["seed_batch"] == "operations-v1"
    assert len(roi_scenario_store.list_roi_scenarios("ws-1")) == 1


def test_scenario_projection_omits_email_and_rejects_nonfinite_or_foreign_revision(monkeypatch, tmp_path: Path) -> None:
    _configure_store(monkeypatch, tmp_path)
    actor = {"actor_id": "owner-1", "tenant_id": "tenant-1", "email": "owner@example.com", "source": "easy_auth"}
    scenario = roi_scenario_store.create_roi_scenario("ws-1", _payload(), actor)

    public = roi_scenario_store.scenario_projection("ws-1", scenario)

    assert "owner@example.com" not in str(public)
    assert public["status"] == "estimated"
    try:
        roi_scenario_store.create_roi_scenario("ws-1", _payload(expected_revenue=float("inf")), actor)
    except ValueError as exc:
        assert "finite" in str(exc)
    else:
        raise AssertionError("non-finite scenario values must be rejected")
    try:
        roi_scenario_store.create_roi_scenario("ws-2", _payload(), actor, previous_id=scenario["scenario_id"])
    except ValueError as exc:
        assert "previous" in str(exc)
    else:
        raise AssertionError("a previous scenario must belong to the workspace")


def test_configured_blob_failure_does_not_report_scenario_as_saved(monkeypatch, tmp_path: Path) -> None:
    _configure_store(monkeypatch, tmp_path)
    monkeypatch.setattr(roi_scenario_store, "blob_configured", lambda: True)
    monkeypatch.setattr(roi_scenario_store, "download_blob_json_strict", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(roi_scenario_store, "upload_blob_json", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("storage unavailable")))

    with pytest.raises(roi_scenario_store.ScenarioPersistenceError):
        roi_scenario_store.create_roi_scenario("ws-1", _payload(), {"actor_id": "owner-1"})

    assert not (tmp_path / "scenarios" / "ws-1.json").exists()


def test_scenario_actions_allow_reader_reads_and_editor_writes() -> None:
    assert workspace_authz.authorize("viewer", "roi.scenario.read") is True
    assert workspace_authz.authorize("viewer", "roi.scenario.write") is False
    assert workspace_authz.authorize("editor", "roi.scenario.write") is True


def test_scenario_routes_use_explicit_governance_actions(monkeypatch, tmp_path: Path) -> None:
    _configure_store(monkeypatch, tmp_path)
    actions: list[str] = []
    monkeypatch.setattr(
        control_plane,
        "_require_workspace_owner",
        lambda _workspace_id, _request, action: actions.append(action) or "owner",
    )
    monkeypatch.setattr(control_plane, "_audit_required", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        control_plane,
        "actor_from_request",
        lambda _request: {"actor_id": "owner-1", "tenant_id": "tenant-1", "source": "easy_auth"},
    )
    client = TestClient(app)

    created = client.post("/api/workspaces/ws-1/governance/scenarios", json=_payload())
    listed = client.get("/api/workspaces/ws-1/governance/scenarios")

    assert created.status_code == 200, created.text
    assert created.json()["scenario"]["status"] == "estimated"
    assert listed.status_code == 200, listed.text
    assert listed.json()["scenarios"][0]["scenario_id"] == created.json()["scenario"]["scenario_id"]
    assert actions == ["roi.scenario.write", "roi.scenario.read"]


def test_scenario_route_returns_service_unavailable_when_durable_store_fails(monkeypatch, tmp_path: Path) -> None:
    _configure_store(monkeypatch, tmp_path)
    monkeypatch.setattr(roi_scenario_store, "blob_configured", lambda: True)
    monkeypatch.setattr(roi_scenario_store, "upload_blob_json", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("storage unavailable")))
    monkeypatch.setattr(control_plane, "_require_workspace_owner", lambda *_args, **_kwargs: "owner")
    monkeypatch.setattr(control_plane, "_audit_required", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(control_plane, "_audit_failed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(control_plane, "actor_from_request", lambda _request: {"actor_id": "owner-1"})
    client = TestClient(app)

    response = client.post("/api/workspaces/ws-1/governance/scenarios", json=_payload())

    assert response.status_code == 503
    assert response.json()["detail"] == "ROI scenario persistence is unavailable"


def test_scenario_route_returns_conflict_for_stale_revision(monkeypatch) -> None:
    monkeypatch.setattr(control_plane, "_require_workspace_owner", lambda *_args, **_kwargs: "owner")
    monkeypatch.setattr(control_plane, "_audit_required", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(control_plane, "_audit_failed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(control_plane, "actor_from_request", lambda _request: {"actor_id": "owner-1"})

    def conflict(*_args, **_kwargs):
        raise roi_scenario_store.ScenarioRevisionConflict(
            "ROI scenario revision has changed"
        )

    monkeypatch.setattr(control_plane, "create_roi_scenario", conflict)
    client = TestClient(app)

    response = client.post(
        "/api/workspaces/ws-1/governance/scenarios",
        json={
            **_dataforge_payload(),
            "previous_id": "roi_scenario_1234567890abcdef",
            "base_revision": 1,
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "ROI scenario revision has changed"
