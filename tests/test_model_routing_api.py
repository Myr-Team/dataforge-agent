from __future__ import annotations

import json

import pytest

import backend.control_plane as control_plane
from backend.app import app
from backend.model_policy import ModelRoute
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DF_WEB_PROXY_SECRET", "test-proxy-secret")
    return TestClient(app)


def test_owner_saves_model_routing_policy_and_price_card(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    saved: dict[str, object] = {}
    meta = {"workspace_id": "ws-model"}
    routes = [
        {"id": "sol", "deployment": "gpt-5.6-sol", "label": "GPT-5.6 Sol", "capabilities": ["chat", "analysis"]},
        {"id": "luna", "deployment": "gpt-5.6-luna", "label": "GPT-5.6 Luna", "capabilities": ["chat", "analysis"]},
    ]
    route_objects = [
        ModelRoute("sol", "gpt-5.6-sol", "GPT-5.6 Sol", frozenset({"chat", "analysis"})),
        ModelRoute("luna", "gpt-5.6-luna", "GPT-5.6 Luna", frozenset({"chat", "analysis"})),
    ]
    monkeypatch.setattr(control_plane, "_require_workspace_owner", lambda *_args: "owner")
    monkeypatch.setattr(control_plane, "_load_workspace_meta", lambda _id: dict(meta))
    monkeypatch.setattr(control_plane, "_save_workspace_meta", lambda _id, value: saved.update(value))
    monkeypatch.setattr(control_plane, "public_model_route_snapshot", lambda: {"state": "available", "default_route": "sol", "routes": routes})
    monkeypatch.setattr(control_plane, "list_allowed_model_routes", lambda: route_objects)
    monkeypatch.setattr(control_plane, "record_audit_event", lambda *_args, **_kwargs: {"event_id": "audit-1"})

    policy = client.put(
        "/api/workspaces/ws-model/governance/model-routing",
        json={"assignments": {"full_analysis": {"primary_route_id": "sol", "fallback_route_id": "luna"}}},
    )
    price_card = client.put(
        "/api/workspaces/ws-model/governance/model-price-card",
        json={
            "currency": "USD",
            "entries": [{"route_id": "sol", "input_per_million": 2, "output_per_million": 8, "source_label": "Owner-maintained pricing reference"}],
        },
    )

    assert policy.status_code == 200
    assert policy.json()["policy"]["revision"] == 1
    assert policy.json()["policy"]["assignments"]["full_analysis"]["primary_route_id"] == "sol"
    assert price_card.status_code == 200
    assert price_card.json()["price_card"]["revision"] == 1
    assert saved["model_price_card"]["entries"][0]["route_id"] == "sol"
    assert "gpt-5.6-sol" not in json.dumps(policy.json()["policy"])


def test_model_routing_endpoint_denies_non_owner(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        control_plane,
        "_require_workspace_owner",
        lambda *_args: (_ for _ in ()).throw(control_plane.HTTPException(status_code=403, detail="workspace permission denied for model_routing.read")),
    )

    response = client.get("/api/workspaces/ws-model/governance/model-routing")

    assert response.status_code == 403
    assert response.json()["detail"] == "workspace permission denied for model_routing.read"
