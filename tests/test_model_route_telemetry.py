from __future__ import annotations

import json

import backend.foundry_client as foundry_client
import backend.maf_agents as maf_agents
import backend.run_store as run_store
from backend.model_policy import model_route_scope, select_text_route, select_text_route_record


def test_followup_run_persists_selected_route_model_usage_and_latency(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "DF_MODEL_ROUTE_ALLOWLIST",
        json.dumps(
            [
                {
                    "id": "analysis",
                    "deployment": "gpt-5.1",
                    "label": "Analysis",
                    "capabilities": ["analysis", "chat"],
                },
                {
                    "id": "followup",
                    "deployment": "gpt-5-mini",
                    "label": "Follow-up",
                    "capabilities": ["followup"],
                },
            ]
        ),
    )
    monkeypatch.setenv("DF_DEFAULT_MODEL_ROUTE", "analysis")
    monkeypatch.setattr(
        "backend.model_policy.context_optimization_gate",
        lambda route_id="followup": {
            "status": "evaluated",
            "sample_count": 24,
            "evaluator_version": "context-v1",
            "eligible": route_id == "followup",
        },
    )
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "upload_blob_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_store, "download_blob_json", lambda *args, **kwargs: {})
    run_store._ACTIVE.clear()

    route = select_text_route("follow_up", candidate_enabled=True)
    response = type(
        "Response",
        (),
        {
            "id": "resp-followup-1",
            "usage": {"input_tokens": 40, "output_tokens": 10, "total_tokens": 50},
            "_dataforge_latency_ms": 120,
        },
    )()

    with model_route_scope(route=route, execution_kind="follow_up"):
        meta = foundry_client._response_meta(response, "followup_assessment")
        headers = foundry_client._gateway_request_headers()

    assert headers["x-dataforge-model-route"] == "followup"

    run_store.start_run("run-followup-telemetry", "workspace-followup", "follow up")
    run_store.record_event(
        "run-followup-telemetry",
        "model_response",
        {
            "agent": "df-coordinator",
            **meta,
        },
    )
    run_store.complete_run("run-followup-telemetry", final={"text": "done"}, artifact={})

    model = run_store.get_run("run-followup-telemetry")["models"][0]
    assert model["route"] == "followup"
    assert model["deployment"] == "gpt-5-mini"
    assert model["selection"] == "policy"
    assert model["fallback_reason"] is None
    assert model["usage"] == {"prompt": 40, "completion": 10, "total": 50}
    assert model["latency_ms"] == 120


def test_model_response_pins_workspace_price_card_estimate(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "DF_MODEL_ROUTE_ALLOWLIST",
        json.dumps(
            [
                {
                    "id": "terra",
                    "deployment": "gpt-5.6-terra",
                    "label": "Terra",
                    "capabilities": ["chat", "analysis"],
                }
            ]
        ),
    )
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "upload_blob_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_store, "download_blob_json", lambda *args, **kwargs: {})
    run_store._ACTIVE.clear()
    price_card = {
        "revision": 7,
        "currency": "USD",
        "entries": [
            {
                "route_id": "terra",
                "input_per_million": 2,
                "output_per_million": 8,
                "source_label": "test price card",
                "updated_at": "2026-07-23T00:00:00Z",
            }
        ],
    }
    selected = select_text_route_record(
        "direct_reply",
        policy={"revision": 4, "assignments": {"direct_reply": {"primary_route_id": "terra"}}},
        price_card=price_card,
    )
    response = type(
        "Response",
        (),
        {
            "id": "resp-priced-1",
            "usage": {"input_tokens": 1_000, "output_tokens": 500, "total_tokens": 1_500},
        },
    )()

    with model_route_scope(route=selected, price_card=price_card):
        meta = foundry_client._response_meta(response, "unit")

    assert meta["policy_revision"] == 4
    assert meta["price_card_revision"] == 7
    assert meta["cost_estimate"]["status"] == "estimated"
    assert meta["cost_estimate"]["amount"] == 0.006
    run_store.start_run("run-priced-telemetry", "workspace-priced", "price route")
    run_store.record_event("run-priced-telemetry", "model_response", {"agent": "df-coordinator", **meta})
    run_store.complete_run("run-priced-telemetry", final={"text": "done"}, artifact={})
    stored = run_store.get_run("run-priced-telemetry")["models"][0]
    assert stored["cost_estimate"]["amount"] == 0.006
    assert set(stored["cost_estimate"]) == {
        "status",
        "currency",
        "amount",
        "price_card_revision",
        "route_id",
        "formula",
    }


def test_followup_run_falls_back_when_candidate_route_is_not_eligible(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "DF_MODEL_ROUTE_ALLOWLIST",
        json.dumps(
            [
                {
                    "id": "analysis",
                    "deployment": "gpt-5.1",
                    "label": "Analysis",
                    "capabilities": ["analysis", "chat"],
                },
                {
                    "id": "followup",
                    "deployment": "gpt-5-mini",
                    "label": "Follow-up",
                    "capabilities": ["followup"],
                },
            ]
        ),
    )
    monkeypatch.setenv("DF_DEFAULT_MODEL_ROUTE", "analysis")
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "upload_blob_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_store, "download_blob_json", lambda *args, **kwargs: {})
    run_store._ACTIVE.clear()

    selected_route = select_text_route_record("follow_up", candidate_enabled=True)
    response = type(
        "Response",
        (),
        {
            "id": "resp-followup-fallback-1",
            "usage": {"input_tokens": 22, "output_tokens": 8, "total_tokens": 30},
            "_dataforge_latency_ms": 95,
        },
    )()

    with model_route_scope(route=selected_route):
        meta = foundry_client._response_meta(response, "followup_assessment")
        headers = foundry_client._gateway_request_headers()

    assert headers["x-dataforge-model-route"] == "analysis"

    run_store.start_run("run-followup-fallback-telemetry", "workspace-followup", "follow up")
    run_store.record_event(
        "run-followup-fallback-telemetry",
        "model_response",
        {
            "agent": "df-coordinator",
            **meta,
        },
    )
    run_store.complete_run("run-followup-fallback-telemetry", final={"text": "done"}, artifact={})

    model = run_store.get_run("run-followup-fallback-telemetry")["models"][0]
    assert model["route"] == "analysis"
    assert model["deployment"] == "gpt-5.1"
    assert model["selection"] == "policy"
    assert model["fallback_reason"] == "candidate_not_eligible"
    assert model["usage"] == {"prompt": 22, "completion": 8, "total": 30}
    assert model["latency_ms"] == 95


def test_maf_gateway_client_uses_selected_analysis_route_header(monkeypatch) -> None:
    created = []
    provider_calls = []

    class FakeGatewayClient:
        def __init__(self, **kwargs) -> None:
            created.append(kwargs)

    class Credential:
        pass

    monkeypatch.setenv(
        "DF_MODEL_ROUTE_ALLOWLIST",
        json.dumps(
            [
                {
                    "id": "analysis",
                    "deployment": "gpt-5.1",
                    "label": "Analysis",
                    "capabilities": ["analysis", "chat"],
                },
                {
                    "id": "followup",
                    "deployment": "gpt-5-mini",
                    "label": "Follow-up",
                    "capabilities": ["followup"],
                },
            ]
        ),
    )
    monkeypatch.setenv("DF_DEFAULT_MODEL_ROUTE", "analysis")
    monkeypatch.setenv("DF_APIM_GATEWAY_ENABLED", "true")
    monkeypatch.setenv("DF_APIM_GATEWAY_URL", "https://gateway.example.invalid/")
    monkeypatch.setenv("DF_APIM_AUDIENCE", "api://dataforge-gateway")
    monkeypatch.setattr(maf_agents, "OpenAIChatClient", FakeGatewayClient)
    monkeypatch.setattr(maf_agents, "ManagedIdentityCredential", Credential)
    monkeypatch.setattr(
        maf_agents,
        "gateway_request_headers",
        lambda: {"x-dataforge-workspace-hash": "w" * 64, "x-dataforge-correlation-id": "c" * 32},
    )
    monkeypatch.setattr(
        maf_agents,
        "get_bearer_token_provider",
        lambda credential, scope: provider_calls.append((credential, scope)) or "gateway-token-provider",
    )

    route = select_text_route("full_analysis")
    with model_route_scope(route=route, execution_kind="full_analysis"):
        client = maf_agents._create_maf_chat_client()

    assert isinstance(client, FakeGatewayClient)
    assert provider_calls[0][1] == "api://dataforge-gateway/.default"
    assert created[0]["model"] == "gpt-5.1"
    assert created[0]["default_headers"]["x-dataforge-model-route"] == "analysis"
