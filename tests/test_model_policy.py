from __future__ import annotations

import json

import backend.foundry_client as foundry_client
import backend.orchestrator as orchestrator
import backend.run_store as run_store
from backend.model_policy import current_text_route, model_route_scope, public_model_route_snapshot, select_text_route


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

    response = type(
        "Response",
        (),
        {
            "id": "resp-1",
            "usage": {
                "input_tokens": 8,
                "output_tokens": 3,
                "total_tokens": 11,
                "cache_read_tokens": 99,
                "reasoning_content": "do not persist",
            },
        },
    )()

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


def test_response_metadata_preserves_unknown_allowlisted_usage_fields(monkeypatch) -> None:
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

    response = type(
        "Response",
        (),
        {
            "id": "resp-partial-usage",
            "usage": {
                "input_tokens": None,
                "output_tokens": 3,
                "total_tokens": None,
                "cache_read_tokens": 99,
            },
        },
    )()

    assert foundry_client._response_meta(response, "unit-test")["usage"] == {
        "input_tokens": None,
        "output_tokens": 3,
        "total_tokens": None,
    }


def test_response_metadata_omits_absent_usage_object_without_known_counters(monkeypatch) -> None:
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

    class EmptyUsage:
        pass

    response = type(
        "Response",
        (),
        {
            "id": "resp-empty-usage",
            "usage": EmptyUsage(),
        },
    )()

    assert foundry_client._usage_dict(response.usage) == {}
    assert foundry_client._response_meta(response, "unit-test")["usage"] == {}


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
            "usage": {
                "input_tokens": 0,
                "output_tokens": 3,
                "total_tokens": 3,
                "cache_read_tokens": 999,
                "sensitive_detail": "never-store-me",
            },
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
            "usage": {"prompt": 0, "completion": 3, "total": 3},
            "mode": None,
            "time": run_store.get_run("run-model-route")["models"][0]["time"],
        }
    ]


def test_run_store_persists_partial_usage_without_fabricating_unknown_counts(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "upload_blob_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_store, "download_blob_json", lambda *args, **kwargs: {})
    run_store._ACTIVE.clear()

    run_store.start_run("run-partial-usage", "workspace-model-route", "chat")
    run_store.record_event(
        "run-partial-usage",
        "model_response",
        {
            "agent": "df-coordinator",
            "model_route": "primary-analysis",
            "model_deployment": "gpt-5.1",
            "usage": {
                "input_tokens": None,
                "output_tokens": 3,
                "total_tokens": None,
                "cache_read_tokens": 999,
                "sensitive_detail": "never-store-me",
            },
        },
    )
    run_store.complete_run("run-partial-usage", final={"text": "done"}, artifact={})

    assert run_store.get_run("run-partial-usage")["models"] == [
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
            "usage": {"prompt": None, "completion": 3, "total": None},
            "mode": None,
            "time": run_store.get_run("run-partial-usage")["models"][0]["time"],
        }
    ]


def test_run_store_does_not_fabricate_unknown_counts_for_absent_usage(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "upload_blob_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_store, "download_blob_json", lambda *args, **kwargs: {})
    run_store._ACTIVE.clear()

    run_store.start_run("run-empty-usage", "workspace-model-route", "chat")
    run_store.record_event(
        "run-empty-usage",
        "model_response",
        {
            "agent": "df-coordinator",
            "model_route": "primary-analysis",
            "model_deployment": "gpt-5.1",
            "usage": foundry_client._response_meta(
                type("Response", (), {"id": "resp-empty-usage", "usage": type("EmptyUsage", (), {})()})(),
                "unit-test",
            )["usage"],
        },
    )
    run_store.complete_run("run-empty-usage", final={"text": "done"}, artifact={})

    assert run_store.get_run("run-empty-usage")["models"] == [
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
            "usage": {},
            "mode": None,
            "time": run_store.get_run("run-empty-usage")["models"][0]["time"],
        }
    ]


def test_model_route_scope_restores_default_after_exit(monkeypatch) -> None:
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

    assert current_text_route().route.route_id == "reply"
    with model_route_scope(route=select_text_route("full_analysis"), execution_kind="full_analysis"):
        assert current_text_route().route.route_id == "analysis"
    assert current_text_route().route.route_id == "reply"


def test_model_route_scope_restores_outer_route_when_nested(monkeypatch) -> None:
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

    with model_route_scope(route=select_text_route("full_analysis"), execution_kind="full_analysis"):
        assert current_text_route().route.route_id == "analysis"
        with model_route_scope(route=select_text_route("follow_up", candidate_enabled=True), execution_kind="follow_up"):
            assert current_text_route().route.route_id == "followup"
        assert current_text_route().route.route_id == "analysis"


def test_model_route_scope_restores_prior_route_after_exception(monkeypatch) -> None:
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
                    "id": "reply",
                    "deployment": "gpt-5-nano",
                    "label": "Reply",
                    "capabilities": ["chat"],
                },
            ]
        ),
    )
    monkeypatch.setenv("DF_DEFAULT_MODEL_ROUTE", "analysis")

    try:
        with model_route_scope(route=select_text_route("full_analysis"), execution_kind="full_analysis"):
            assert current_text_route().route.route_id == "analysis"
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    assert current_text_route().route.route_id == "reply"


def test_model_route_scope_does_not_bleed_between_requests(monkeypatch) -> None:
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

    with model_route_scope(route=select_text_route("follow_up", candidate_enabled=True), execution_kind="follow_up"):
        assert current_text_route().route.route_id == "followup"
    assert current_text_route().route.route_id == "reply"

    with model_route_scope(route=select_text_route("full_analysis"), execution_kind="full_analysis"):
        assert current_text_route().route.route_id == "analysis"
    assert current_text_route().route.route_id == "reply"


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
