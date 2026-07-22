from __future__ import annotations

import pytest

import backend.control_plane as control_plane
from backend.app import app
from fastapi.testclient import TestClient

from auth_fixtures import trusted_headers


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DF_WEB_PROXY_SECRET", "test-proxy-secret")
    return TestClient(app)


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
