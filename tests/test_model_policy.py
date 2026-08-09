from __future__ import annotations

import json

import backend.foundry_client as foundry_client
import backend.orchestrator as orchestrator
import backend.run_store as run_store
from backend.model_policy import (
    ModelRoute,
    SelectedTextRoute,
    ModelPolicyError,
    current_text_route,
    list_allowed_model_routes,
    model_route_scope,
    public_model_route_snapshot,
    select_text_route,
    select_text_route_record,
    workspace_model_policy_scope,
)
import pytest


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


def test_legacy_azure_route_gets_provider_neutral_defaults(monkeypatch) -> None:
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

    route = list_allowed_model_routes()[0]

    assert route.provider_type == "azure_foundry"
    assert route.provider_id is None
    assert route.model_id == "gpt-5.1"
    assert route.deployment == "gpt-5.1"


def test_governed_deepseek_route_is_provider_aware(monkeypatch) -> None:
    monkeypatch.setenv(
        "DF_MODEL_ROUTE_ALLOWLIST",
        json.dumps(
            [
                {
                    "id": "deepseek-analysis",
                    "provider_type": "deepseek",
                    "provider_id": "provider-deepseek",
                    "model_id": "deepseek-v4-pro",
                    "label": "DeepSeek V4 Pro",
                    "capabilities": ["chat", "analysis"],
                    "connection_state": "connected",
                    "governance_state": "governed",
                }
            ]
        ),
    )

    route = list_allowed_model_routes()[0]

    assert route.provider_type == "deepseek"
    assert route.provider_id == "provider-deepseek"
    assert route.model_id == "deepseek-v4-pro"
    assert route.deployment == "deepseek-v4-pro"


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("enabled", False),
        ("connection_state", "degraded"),
        ("governance_state", "unmanaged"),
    ],
)
def test_external_route_rejects_disabled_or_ungoverned_provider(
    monkeypatch,
    field_name: str,
    field_value: object,
) -> None:
    item = {
        "id": "deepseek-analysis",
        "provider_type": "deepseek",
        "provider_id": "provider-deepseek",
        "model_id": "deepseek-v4-pro",
        "label": "DeepSeek V4 Pro",
        "capabilities": ["analysis"],
        "connection_state": "connected",
        "governance_state": "governed",
        field_name: field_value,
    }
    monkeypatch.setenv("DF_MODEL_ROUTE_ALLOWLIST", json.dumps([item]))

    with pytest.raises(ModelPolicyError):
        list_allowed_model_routes()


def test_external_route_is_not_selected_while_runtime_flag_is_off(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "DF_MODEL_ROUTE_ALLOWLIST",
        json.dumps(
            [
                {
                    "id": "deepseek-analysis",
                    "provider_type": "deepseek",
                    "provider_id": "provider-deepseek",
                    "model_id": "deepseek-v4-pro",
                    "label": "DeepSeek V4 Pro",
                    "capabilities": ["chat", "analysis"],
                    "connection_state": "connected",
                    "governance_state": "governed",
                },
                {
                    "id": "azure-analysis",
                    "deployment": "gpt-5.1",
                    "label": "Azure Analysis",
                    "capabilities": ["chat", "analysis"],
                },
            ]
        ),
    )
    monkeypatch.setenv("DF_EXTERNAL_PROVIDER_ROUTING_ENABLED", "0")
    monkeypatch.setenv("DF_DEFAULT_MODEL_ROUTE", "deepseek-analysis")

    selected = select_text_route_record("full_analysis")

    assert selected.route.route_id == "azure-analysis"


def test_workspace_policy_and_manual_override_select_allowlisted_routes(monkeypatch) -> None:
    monkeypatch.setenv(
        "DF_MODEL_ROUTE_ALLOWLIST",
        json.dumps(
            [
                {"id": "sol", "deployment": "gpt-5.6-sol", "label": "Sol", "capabilities": ["chat", "analysis"]},
                {"id": "luna", "deployment": "gpt-5.6-luna", "label": "Luna", "capabilities": ["chat"]},
            ]
        ),
    )
    policy = {
        "revision": 4,
        "assignments": {
            "direct_reply": {"primary_route_id": "luna", "fallback_route_id": "sol"},
            "full_analysis": {"primary_route_id": "sol", "fallback_route_id": "sol"},
        },
    }

    policy_selected = select_text_route_record("direct_reply", policy=policy)
    manual_selected = select_text_route_record("direct_reply", policy=policy, manual_route_id="sol")

    assert policy_selected.route.route_id == "luna"
    assert policy_selected.selection == "workspace_policy"
    assert policy_selected.policy_revision == 4
    assert policy_selected.fallback_route is not None
    assert policy_selected.fallback_route.route_id == "sol"
    assert manual_selected.route.route_id == "sol"
    assert manual_selected.selection == "manual"
    assert manual_selected.policy_revision == 4


def test_agent_route_precedes_execution_kind_and_workspace_default(monkeypatch) -> None:
    monkeypatch.setenv(
        "DF_MODEL_ROUTE_ALLOWLIST",
        json.dumps(
            [
                {"id": "sol", "deployment": "gpt-5.6-sol", "label": "Sol", "capabilities": ["chat", "analysis"]},
                {"id": "luna", "deployment": "gpt-5.6-luna", "label": "Luna", "capabilities": ["chat", "analysis"]},
            ]
        ),
    )
    policy = {
        "revision": 5,
        "default_route_id": "luna",
        "agent_assignments": {
            "df-feasibility-analyst": {"primary_route_id": "sol"}
        },
        "assignments": {
            "full_analysis": {"primary_route_id": "luna"}
        },
    }

    selected = select_text_route_record(
        "full_analysis",
        agent_id="df-feasibility-analyst",
        policy=policy,
    )

    assert selected.route.route_id == "sol"
    assert selected.selection == "agent_policy"


def test_workspace_policy_scope_applies_to_nested_route_selection(monkeypatch) -> None:
    monkeypatch.setenv(
        "DF_MODEL_ROUTE_ALLOWLIST",
        json.dumps(
            [
                {"id": "sol", "deployment": "gpt-5.6-sol", "label": "Sol", "capabilities": ["chat", "analysis"]},
                {"id": "luna", "deployment": "gpt-5.6-luna", "label": "Luna", "capabilities": ["chat", "analysis"]},
            ]
        ),
    )
    with workspace_model_policy_scope(
        policy={"revision": 3, "assignments": {"full_analysis": {"primary_route_id": "sol"}}},
        price_card={"revision": 5, "currency": "USD", "entries": []},
    ):
        selected = select_text_route_record("full_analysis")

    assert selected.route.route_id == "sol"
    assert selected.selection == "workspace_policy"
    assert selected.policy_revision == 3
    assert selected.price_card_revision == 5


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
    monkeypatch.setattr(
        "backend.model_policy.context_optimization_gate",
        lambda route_id="followup": {
            "status": "evaluated",
            "sample_count": 24,
            "evaluator_version": "context-v1",
            "eligible": route_id == "followup",
        },
    )

    assert select_text_route("full_analysis").route_id == "analysis"
    assert select_text_route("audit_repair").route_id == "analysis"
    assert select_text_route("follow_up", candidate_enabled=False).route_id == "reply"
    assert select_text_route("follow_up", candidate_enabled=True).route_id == "followup"


def test_followup_candidate_requires_eligible_offline_evaluation(monkeypatch) -> None:
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
    monkeypatch.setattr(
        "backend.model_policy.context_optimization_gate",
        lambda route_id="followup": {
            "status": "evaluated",
            "sample_count": 24,
            "evaluator_version": "context-v1",
            "eligible": route_id == "followup",
        },
    )

    assert select_text_route("follow_up", candidate_enabled=True).route_id == "followup"

    monkeypatch.setattr(
        "backend.model_policy.context_optimization_gate",
        lambda route_id="followup": {
            "status": "evaluated",
            "sample_count": 24,
            "evaluator_version": "context-v1",
            "eligible": False,
        },
    )

    assert select_text_route("follow_up", candidate_enabled=True).route_id == "reply"


def test_followup_candidate_does_not_enable_when_gate_is_unavailable(monkeypatch) -> None:
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
    monkeypatch.setattr(
        "backend.model_policy.context_optimization_gate",
        lambda route_id="followup": {
            "status": "unavailable",
            "sample_count": None,
            "evaluator_version": None,
            "eligible": False,
        },
    )

    assert select_text_route("follow_up", candidate_enabled=True).route_id == "reply"


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
        "provider_type": "azure_foundry",
        "provider_id": None,
        "model_id": "gpt-5.1",
        "selection": "policy",
        "fallback_reason": None,
        "execution_kind": "direct_reply",
        "latency_ms": None,
        "model_route": "primary-analysis",
        "model_deployment": "gpt-5.1",
        "policy_revision": None,
        "price_card_revision": None,
        "cost_estimate": {"status": "unavailable", "reason": "price_not_configured"},
    }


def test_external_response_metadata_records_provider_cache_populations() -> None:
    response = type(
        "Response",
        (),
        {
            "id": "resp-external",
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "prompt_cache_hit_tokens": 80,
                "prompt_cache_miss_tokens": 20,
            },
        },
    )()
    selected = SelectedTextRoute(
        route=ModelRoute(
            "deepseek-analysis",
            "deepseek-v4-pro",
            "DeepSeek V4 Pro",
            frozenset({"analysis"}),
            provider_id="provider-deepseek",
            provider_type="deepseek",
            model_id="deepseek-v4-pro",
        ),
        execution_kind="full_analysis",
    )

    with model_route_scope(route=selected):
        metadata = foundry_client._response_meta(response, "unit-test")

    assert metadata["usage"]["cached_input"] == 80
    assert metadata["provider_cache"] == {
        "state": "partial_hit",
        "hit_tokens": 80,
        "miss_tokens": 20,
        "hit_rate_pct": 80.0,
        "evidence_state": "observed",
    }


def test_response_metadata_preserves_unknown_allowlisted_usage_fields(monkeypatch) -> None:
    monkeypatch.setenv(
        "DF_MODEL_ROUTE_ALLOWLIST",
        json.dumps(
            [
                {
                    "id": "primary-analysis",
                    "deployment": "gpt-5.1",
                    "model_id": "gpt-5.1",
                    "provider_id": None,
                    "provider_type": "azure_foundry",
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
    assert run_store.get_run("run-model-route")["steps"][0]["data"] == {
        "agent": "df-coordinator",
        "model_route": "primary-analysis",
        "model_deployment": "gpt-5.1",
        "usage": {"prompt": 0, "completion": 3, "total": 3},
    }


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
    monkeypatch.setattr(
        "backend.model_policy.context_optimization_gate",
        lambda route_id="followup": {
            "status": "evaluated",
            "sample_count": 24,
            "evaluator_version": "context-v1",
            "eligible": route_id == "followup",
        },
    )

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
    monkeypatch.setattr(
        "backend.model_policy.context_optimization_gate",
        lambda route_id="followup": {
            "status": "evaluated",
            "sample_count": 24,
            "evaluator_version": "context-v1",
            "eligible": route_id == "followup",
        },
    )

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
                "model_id": "gpt-5.1",
                "provider_id": None,
                "provider_type": "azure_foundry",
                "label": "Primary analysis",
                "capabilities": ["analysis", "chat"],
            }
        ],
    }


def test_workspace_route_scope_can_select_a_tenant_resolved_external_route(monkeypatch) -> None:
    monkeypatch.setenv("DF_EXTERNAL_PROVIDER_ROUTING_ENABLED", "1")
    azure = ModelRoute(
        "default",
        "gpt-5.1",
        "GPT-5.1",
        frozenset({"chat", "analysis"}),
    )
    deepseek = ModelRoute(
        "ds_primary_flash",
        "deepseek-v4-flash",
        "DeepSeek V4 Flash",
        frozenset({"chat", "analysis"}),
        provider_id="provider_primary",
        provider_type="deepseek",
        model_id="deepseek-v4-flash",
    )
    policy = {
        "revision": 4,
        "assignments": {
            "full_analysis": {"primary_route_id": "ds_primary_flash"},
        },
    }

    with workspace_model_policy_scope(policy=policy, routes=[azure, deepseek]):
        selected = select_text_route_record("full_analysis")

    assert selected.route == deepseek
    assert selected.policy_revision == 4
