from __future__ import annotations

import json

import pytest

import backend.control_plane as control_plane
from backend.app import app
from backend.model_policy import ModelRoute
from backend.model_provider_routes import ProviderRouteCandidate
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


def _routing_env(monkeypatch: pytest.MonkeyPatch, meta: dict[str, object], saved: dict[str, object]) -> None:
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


def test_model_routing_rejects_stale_base_revision(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    saved: dict[str, object] = {}
    meta = {
        "workspace_id": "ws-model",
        "model_routing_policy": {"revision": 3, "assignments": {}, "agent_assignments": {}},
    }
    _routing_env(monkeypatch, meta, saved)

    response = client.put(
        "/api/workspaces/ws-model/governance/model-routing",
        json={
            "base_revision": 1,
            "assignments": {"full_analysis": {"primary_route_id": "sol", "fallback_route_id": "luna"}},
        },
    )

    assert response.status_code == 409
    # A stale write must not overwrite the current policy.
    assert "model_routing_policy" not in saved


def test_model_routing_accepts_matching_base_revision(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    saved: dict[str, object] = {}
    meta = {
        "workspace_id": "ws-model",
        "model_routing_policy": {"revision": 3, "assignments": {}, "agent_assignments": {}},
    }
    _routing_env(monkeypatch, meta, saved)

    response = client.put(
        "/api/workspaces/ws-model/governance/model-routing",
        json={
            "base_revision": 3,
            "assignments": {"full_analysis": {"primary_route_id": "sol", "fallback_route_id": "luna"}},
        },
    )

    assert response.status_code == 200
    assert response.json()["policy"]["revision"] == 4


def test_model_price_card_rejects_stale_base_revision(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    saved: dict[str, object] = {}
    meta = {
        "workspace_id": "ws-model",
        "model_price_card": {"revision": 2, "currency": "USD", "entries": []},
    }
    _routing_env(monkeypatch, meta, saved)

    response = client.put(
        "/api/workspaces/ws-model/governance/model-price-card",
        json={
            "base_revision": 0,
            "currency": "USD",
            "entries": [{"route_id": "sol", "input_per_million": 2, "output_per_million": 8, "source_label": "Owner-maintained pricing reference"}],
        },
    )

    assert response.status_code == 409
    assert "model_price_card" not in saved


def test_model_routing_endpoint_denies_non_owner(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        control_plane,
        "_require_workspace_owner",
        lambda *_args: (_ for _ in ()).throw(control_plane.HTTPException(status_code=403, detail="workspace permission denied for model_routing.read")),
    )

    response = client.get("/api/workspaces/ws-model/governance/model-routing")

    assert response.status_code == 403
    assert response.json()["detail"] == "workspace permission denied for model_routing.read"


def _deepseek_candidate(*, selectable: bool = True) -> ProviderRouteCandidate:
    route = ModelRoute(
        "ds_primary_flash",
        "deepseek-v4-flash",
        "DeepSeek V4 Flash",
        frozenset({"chat", "analysis"}),
        provider_id="provider_primary",
        provider_type="deepseek",
        model_id="deepseek-v4-flash",
    )
    return ProviderRouteCandidate(
        route=route,
        public={
            "id": route.route_id,
            "deployment": route.deployment,
            "model_id": route.model_id,
            "provider_id": route.provider_id,
            "provider_type": route.provider_type,
            "provider_label": "DeepSeek 原厂",
            "label": route.label,
            "capabilities": sorted(route.capabilities),
            "official_price_key": "deepseek:deepseek-v4-flash:official",
            "pricing_state": "priced",
            "health_state": "connected",
            "governance_state": "governed" if selectable else "pending",
            "selectable": selectable,
            "unavailable_reason": None if selectable else "governance_required",
        },
    )


def test_model_routing_lists_and_accepts_tenant_dynamic_provider_route(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: dict[str, object] = {}
    _routing_env(monkeypatch, {"workspace_id": "ws-model"}, saved)
    monkeypatch.setattr(
        control_plane,
        "_dynamic_model_route_candidates",
        lambda _request: [_deepseek_candidate()],
    )

    listed = client.get("/api/workspaces/ws-model/governance/model-routing")
    updated = client.put(
        "/api/workspaces/ws-model/governance/model-routing",
        json={
            "assignments": {
                "full_analysis": {"primary_route_id": "ds_primary_flash"},
            },
        },
    )

    deepseek = next(route for route in listed.json()["routes"] if route.get("provider_type") == "deepseek")
    assert deepseek["provider_label"] == "DeepSeek 原厂"
    assert deepseek["selectable"] is True
    assert updated.status_code == 200
    assert saved["model_routing_policy"]["assignments"]["full_analysis"]["primary_route_id"] == "ds_primary_flash"


def test_model_routing_shows_but_rejects_unavailable_provider_route(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: dict[str, object] = {}
    _routing_env(monkeypatch, {"workspace_id": "ws-model"}, saved)
    monkeypatch.setattr(
        control_plane,
        "_dynamic_model_route_candidates",
        lambda _request: [_deepseek_candidate(selectable=False)],
    )

    listed = client.get("/api/workspaces/ws-model/governance/model-routing")
    rejected = client.put(
        "/api/workspaces/ws-model/governance/model-routing",
        json={
            "assignments": {
                "full_analysis": {"primary_route_id": "ds_primary_flash"},
            },
        },
    )

    deepseek = next(route for route in listed.json()["routes"] if route.get("provider_type") == "deepseek")
    assert deepseek["selectable"] is False
    assert deepseek["unavailable_reason"] == "governance_required"
    assert rejected.status_code == 400
    assert "model_routing_policy" not in saved
