from __future__ import annotations

import json

import backend.foundry_client as foundry_client
import backend.maf_agents as maf_agents
import backend.run_store as run_store
from backend.model_policy import model_route_scope, select_text_route


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
