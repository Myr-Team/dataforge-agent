from __future__ import annotations

import pytest

import backend.control_plane as control_plane
import backend.monitoring_dashboard as monitoring_dashboard
from backend.app import app
from fastapi.testclient import TestClient

from auth_fixtures import trusted_headers


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DF_WEB_PROXY_SECRET", "test-proxy-secret")
    return TestClient(app)


@pytest.fixture
def seeded_owner_runs() -> list[dict[str, object]]:
    return [
        {
            "run_id": "run-1",
            "workspace_id": "ws-owner",
            "status": "completed",
            "completed_at": "2026-07-02T10:00:00Z",
            "models": [
                {
                    "route": "analysis",
                    "deployment": "gpt-5.1",
                    "usage": {"prompt": 80, "completion": 20, "total": 100},
                }
            ],
        },
        {
            "run_id": "run-2",
            "workspace_id": "ws-owner",
            "status": "completed",
            "completed_at": "2026-07-03T12:00:00Z",
            "models": [
                {
                    "route": "followup",
                    "deployment": "gpt-5-mini",
                    "usage": {"prompt": 40, "completion": 10, "total": 50},
                },
                {
                    "route": "analysis",
                    "deployment": "gpt-5.1",
                    "usage": {"prompt": 25, "completion": 5, "total": 30},
                },
            ],
        },
        {
            "run_id": "run-3",
            "workspace_id": "ws-owner",
            "status": "failed",
            "completed_at": "2026-07-04T09:30:00Z",
            "error": "provider timeout",
        },
    ]


def test_monitor_api_rejects_non_owner_and_limits_portfolio_to_owned_workspaces(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(control_plane, "_owned_workspace_ids", lambda _request: ["ws-owned"], raising=False)
    monkeypatch.setattr(
        control_plane,
        "build_monitor_dashboard",
        lambda workspace_ids, **_kwargs: {
            "scope": {"kind": "portfolio", "workspace_ids": workspace_ids},
            "window": {"from": "2026-07-01T00:00:00Z", "to": "2026-07-08T00:00:00Z", "timezone": "UTC"},
            "freshness": {"generated_at": "2026-07-08T00:00:00Z", "sources": ["run_store"]},
            "summary": {
                "calls": {"observed": 0, "succeeded": 0, "failed": 0, "unknown": 0},
                "tokens": {"input": None, "output": None, "total": None, "known_runs": 0, "unknown_runs": 0},
                "cost": {"status": "unavailable", "amount": None, "currency": "USD", "price_catalog_version": None},
                "quality": {
                    "evidence_coverage_pct": None,
                    "audited_runs": 0,
                    "rework_runs": 0,
                    "evaluator_coverage_pct": None,
                },
                "roi": {
                    "status": "pending_verification",
                    "verified_value": None,
                    "model_cost": None,
                    "evaluator_cost": None,
                    "roi_pct": None,
                },
            },
            "series": {"daily": []},
            "models": [],
            "routes": [],
            "members": [],
            "opportunity": {"status": "unavailable", "kind": None, "message": "No eligible optimization evidence yet."},
            "coverage": {"governed_text_calls": 0, "out_of_scope_image_calls": 0},
        },
        raising=False,
    )

    denied = client.get(
        "/api/monitoring?scope=portfolio&workspace_id=ws-other&from=2026-07-01T00:00:00Z&to=2026-07-08T00:00:00Z",
        headers=trusted_headers(actor_id="owner-oid", tenant_id="tenant-a"),
    )

    assert denied.status_code == 403

    allowed = client.get(
        "/api/monitoring?scope=portfolio&workspace_id=ws-owned&from=2026-07-01T00:00:00Z&to=2026-07-08T00:00:00Z",
        headers=trusted_headers(actor_id="owner-oid", tenant_id="tenant-a"),
    )

    assert allowed.status_code == 200
    assert allowed.json()["scope"]["workspace_ids"] == ["ws-owned"]


def test_monitor_api_uses_requested_window_for_gateway_evidence(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, str, str | None, str | None]] = []

    monkeypatch.setattr(control_plane, "_owned_workspace_ids", lambda _request: ["ws-owned"], raising=False)
    monkeypatch.setenv("DF_APIM_GATEWAY_ENABLED", "true")
    monkeypatch.setenv("DF_APIM_EXPECTED_GATEWAY_ID", "dfmonapim721")
    monkeypatch.setattr(
        control_plane,
        "build_monitor_dashboard",
        lambda workspace_ids, **_kwargs: {
            "scope": {"kind": "current", "workspace_ids": workspace_ids},
            "window": {"from": "2026-07-03T01:00:00Z", "to": "2026-07-05T09:30:00Z", "timezone": "UTC"},
            "freshness": {"generated_at": "2026-07-08T00:00:00Z", "sources": ["run_store"]},
            "summary": {
                "calls": {"observed": 0, "succeeded": 0, "failed": 0, "unknown": 0},
                "tokens": {"input": None, "output": None, "total": None, "known_runs": 0, "unknown_runs": 0},
                "cost": {"status": "unavailable", "amount": None, "currency": "USD", "price_catalog_version": None},
                "quality": {
                    "evidence_coverage_pct": None,
                    "audited_runs": None,
                    "rework_runs": 0,
                    "evaluator_coverage_pct": None,
                },
                "roi": {
                    "status": "pending_verification",
                    "verified_value": None,
                    "model_cost": None,
                    "evaluator_cost": None,
                    "roi_pct": None,
                },
            },
            "series": {"daily": []},
            "models": [],
            "routes": [],
            "members": [],
            "opportunity": {"status": "unavailable", "kind": None, "message": "No eligible optimization evidence yet."},
            "coverage": {"governed_text_calls": 0, "out_of_scope_image_calls": 0},
        },
        raising=False,
    )
    monkeypatch.setattr(
        control_plane,
        "get_gateway_metric_evidence",
        lambda workspace_id, gateway_id, *, from_value=None, to_value=None: captured.append(
            (workspace_id, gateway_id, from_value, to_value)
        )
        or {"state": "verified", "governed_calls": 7, "provenance": "apim_custom_metric"},
        raising=False,
    )

    response = client.get(
        "/api/monitoring?scope=current&workspace_id=ws-owned&from=2026-07-03T01:00:00Z&to=2026-07-05T09:30:00Z",
        headers=trusted_headers(actor_id="owner-oid", tenant_id="tenant-a"),
    )

    assert response.status_code == 200
    assert response.json()["coverage"]["governed_text_calls"] == 7
    assert captured == [("ws-owned", "dfmonapim721", "2026-07-03T01:00:00Z", "2026-07-05T09:30:00Z")]


def test_monitor_dashboard_reconciles_model_and_route_totals_with_run_records(
    client: TestClient,
    seeded_owner_runs: list[dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(control_plane, "_owned_workspace_ids", lambda _request: ["ws-owner"], raising=False)
    monkeypatch.setattr(
        control_plane,
        "list_runs",
        lambda workspace_id=None: seeded_owner_runs if workspace_id == "ws-owner" else [],
        raising=False,
    )
    monkeypatch.setattr(
        control_plane,
        "workspace_cost_value_snapshot",
        lambda _workspace_id, _from_value, _to_value: {},
        raising=False,
    )
    monkeypatch.setattr(
        control_plane,
        "workspace_audit_events",
        lambda _workspace_id: {"count": 0, "events": []},
        raising=False,
    )
    monkeypatch.setattr(control_plane, "list_outcome_events", lambda _workspace_id: [], raising=False)
    monkeypatch.setattr(
        control_plane,
        "workspace_member_chargeback",
        lambda _workspace_id, _from_value, _to_value: {"members": []},
        raising=False,
    )
    monkeypatch.setattr(control_plane, "_gateway_governed_calls", lambda *_args, **_kwargs: None, raising=False)
    monkeypatch.setattr(monitoring_dashboard, "context_optimization_gate", lambda _route_id: {}, raising=False)

    response = client.get(
        "/api/monitoring?scope=current&workspace_id=ws-owner&from=2026-07-01T00:00:00Z&to=2026-08-01T00:00:00Z",
        headers=trusted_headers(actor_id="owner-oid", tenant_id="tenant-a"),
    )

    assert response.status_code == 200
    body = response.json()
    model_calls = sum(int(row["calls"]) for row in body["models"])
    route_calls = sum(int(row["calls"]) for row in body["routes"])
    unknown_route_calls = sum(int(row["calls"]) for row in body["routes"] if row["route"] == "unknown")
    assert route_calls == model_calls + unknown_route_calls
    assert model_calls <= body["summary"]["calls"]["observed"]
    assert body["coverage"]["governed_text_calls"] == model_calls
    assert unknown_route_calls == 1
