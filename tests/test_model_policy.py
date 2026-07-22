from __future__ import annotations

import json

import backend.foundry_client as foundry_client
import backend.orchestrator as orchestrator
import backend.run_store as run_store
from backend.model_policy import public_model_route_snapshot, select_text_route


def test_chat_model_uses_only_the_server_allowlist(monkeypatch) -> None:
    monkeypatch.setenv(
        "DF_MODEL_ROUTE_ALLOWLIST",
        json.dumps(
            [
                {
                    "id": "primary-analysis",
                    "deployment": "gpt-5.1",
                    "label": "Primary analysis",
                    "capabilities": ["chat", "analysis"],
                }
            ]
        ),
    )
    monkeypatch.setenv("DF_DEFAULT_MODEL_ROUTE", "primary-analysis")

    assert getattr(foundry_client, "_chat_model", lambda: "")() == "gpt-5.1"


def test_full_analysis_never_selects_followup_candidate_route(monkeypatch) -> None:
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
                {
                    "id": "reply",
                    "deployment": "gpt-5-nano",
                    "label": "Reply",
                    "capabilities": ["chat"],
                },
            ]
        ),
    )
    monkeypatch.setenv("DF_DEFAULT_MODEL_ROUTE", "analysis")

    assert select_text_route("full_analysis").route_id == "analysis"
    assert select_text_route("audit_repair").route_id == "analysis"
    assert select_text_route("follow_up", candidate_enabled=False).route_id == "reply"
    assert select_text_route("follow_up", candidate_enabled=True).route_id == "followup"


def test_response_metadata_records_effective_route_and_deployment(monkeypatch) -> None:
    monkeypatch.setenv(
        "DF_MODEL_ROUTE_ALLOWLIST",
        json.dumps(
            [
                {
                    "id": "primary-analysis",
                    "deployment": "gpt-5.1",
                    "label": "Primary analysis",
                    "capabilities": ["chat", "analysis"],
                }
            ]
        ),
    )
    monkeypatch.setenv("DF_DEFAULT_MODEL_ROUTE", "primary-analysis")

    response = type("Response", (), {"id": "resp-1", "usage": {"input_tokens": 8, "output_tokens": 3, "total_tokens": 11}})()

    assert foundry_client._response_meta(response, "unit-test") == {
        "mode": "unit-test",
        "response_id": "resp-1",
        "usage": {"input_tokens": 8, "output_tokens": 3, "total_tokens": 11},
        "route": "primary-analysis",
        "deployment": "gpt-5.1",
        "selection": "policy",
        "fallback_reason": None,
        "execution_kind": "direct_reply",
        "latency_ms": None,
        "model_route": "primary-analysis",
        "model_deployment": "gpt-5.1",
    }


def test_followup_persistence_metadata_keeps_effective_model_route() -> None:
    assert orchestrator._llm_result_metadata(
        {
            "mode": "answer_composer",
            "response_id": "resp-1",
            "usage": {"total_tokens": 11},
            "model_route": "primary-analysis",
            "model_deployment": "gpt-5.1",
        }
    ) == {
        "mode": "answer_composer",
        "response_id": "resp-1",
        "usage": {"total_tokens": 11},
        "model_route": "primary-analysis",
        "model_deployment": "gpt-5.1",
    }


def test_run_store_persists_effective_model_route_and_deployment(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "upload_blob_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_store, "download_blob_json", lambda *args, **kwargs: {})
    run_store._ACTIVE.clear()

    run_store.start_run("run-model-route", "workspace-model-route", "chat")
    run_store.record_event(
        "run-model-route",
        "model_response",
        {
            "agent": "df-coordinator",
            "model_route": "primary-analysis",
            "model_deployment": "gpt-5.1",
            "usage": {"input_tokens": 8, "output_tokens": 3, "total_tokens": 11},
        },
    )
    run_store.complete_run("run-model-route", final={"text": "done"}, artifact={})

    assert run_store.get_run("run-model-route")["models"] == [
        {
            "agent": "df-coordinator",
            "model": "gpt-5.1",
            "route": "primary-analysis",
            "deployment": "gpt-5.1",
            "selection": None,
            "fallback_reason": None,
            "execution_kind": None,
            "latency_ms": None,
            "model_route": "primary-analysis",
            "model_deployment": "gpt-5.1",
            "response_id": None,
            "usage": {"prompt": 8, "completion": 3, "total": 11},
            "provider_usage": {"input_tokens": 8, "output_tokens": 3, "total_tokens": 11},
            "mode": None,
            "time": run_store.get_run("run-model-route")["models"][0]["time"],
        }
    ]


def test_public_model_route_snapshot_exposes_only_allowlisted_routes(monkeypatch) -> None:
    monkeypatch.setenv(
        "DF_MODEL_ROUTE_ALLOWLIST",
        json.dumps(
            [
                {
                    "id": "primary-analysis",
                    "deployment": "gpt-5.1",
                    "label": "Primary analysis",
                    "capabilities": ["chat", "analysis"],
                }
            ]
        ),
    )
    monkeypatch.setenv("DF_DEFAULT_MODEL_ROUTE", "primary-analysis")

    assert public_model_route_snapshot() == {
        "state": "available",
        "default_route": "primary-analysis",
        "routes": [
            {
                "id": "primary-analysis",
                "deployment": "gpt-5.1",
                "label": "Primary analysis",
                "capabilities": ["analysis", "chat"],
            }
        ],
    }
