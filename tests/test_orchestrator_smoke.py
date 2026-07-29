from __future__ import annotations

import backend.orchestrator as orchestrator
from backend.schemas import ChatRequest


def test_feasibility_cache_hit_restores_only_safe_source_meter(monkeypatch) -> None:
    monkeypatch.setattr(orchestrator, "_evidence_catalog", lambda _artifact: [{"id": "evidence-1"}])
    monkeypatch.setattr(
        orchestrator.cache_store,
        "get_json",
        lambda _key: (
                {
                    "result": {"opportunity_id": "safe-result", "dimensions": []},
                    "source_result_version": "result-safe-v1",
                "meter": {
                    "source_usage": {"prompt": 10, "completion": 2, "total": 12, "raw_usage": "drop"},
                    "source_cost_estimate": {
                        "status": "estimated",
                        "currency": "USD",
                        "amount": 0.001,
                        "price_card_revision": 4,
                        "route_id": "analysis",
                        "source_label": "drop",
                    },
                    "cache_key": "redis://secret-cache-key",
                },
            },
            {"provider": "redis", "status": "hit", "elapsed_ms": 3, "error": "drop"},
        ),
    )

    result = orchestrator._run_feasibility_analyst(
        ChatRequest(workspace_id="ws-a", message="Analyze"),
        {"corpus": {"hits": []}},
    )

    assert result["_llm"]["cache"] == {
        "state": "hit",
        "provider": "redis",
            "elapsed_ms": 3,
            "eligible": True,
            "reason": "eligible",
            "policy_revision": 0,
            "source_result_version": "result-safe-v1",
        "source_usage": {"prompt": 10, "completion": 2, "total": 12},
        "source_cost_estimate": {
            "status": "estimated",
            "currency": "USD",
            "amount": 0.001,
            "price_card_revision": 4,
            "route_id": "analysis",
        },
    }
    assert "cache_key" not in result["_llm"]["cache"]
    assert orchestrator._has_model_response_data(result["_llm"])


def test_feasibility_cache_miss_stores_only_safe_result_and_source_meter(monkeypatch) -> None:
    stored: dict[str, object] = {}
    report = orchestrator.FeasibilityReport(
        opportunity_id="safe-result",
        dimensions=[],
        verdict="conditional",
        overall_confidence="speculative",
        gap_list=[],
    )
    monkeypatch.setattr(orchestrator, "_evidence_catalog", lambda _artifact: [{"id": "evidence-1"}])
    monkeypatch.setattr(orchestrator, "run_agent", lambda *_args, **_kwargs: {
        "structured": report.model_dump(),
        "response_id": "response-should-not-be-cached",
        "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
        "cost_estimate": {
            "status": "estimated",
            "currency": "USD",
            "amount": 0.001,
            "price_card_revision": 4,
            "route_id": "analysis",
            "source_label": "drop",
        },
    })
    monkeypatch.setattr(orchestrator, "_verify_evidence", lambda value, _catalog: (value, []))
    monkeypatch.setattr(orchestrator, "_normalize_feasibility_confidence", lambda value: value)
    monkeypatch.setattr(orchestrator, "_normalize_feasibility_opportunity", lambda value, _req, _artifact: value)
    monkeypatch.setattr(orchestrator, "_diversify_feasibility_scores", lambda value: value)
    monkeypatch.setattr(orchestrator, "apply_pre_audit_guardrails", lambda data, *_args: dict(data))
    monkeypatch.setattr(
        orchestrator.cache_store,
        "get_json",
        lambda _key: (None, {"provider": "redis", "status": "miss", "elapsed_ms": 3}),
    )
    monkeypatch.setattr(
        orchestrator.cache_store,
        "set_json",
        lambda key, value: stored.update({"key": key, "value": value}) or {"status": "stored"},
    )

    result = orchestrator._run_feasibility_analyst(
        ChatRequest(workspace_id="ws-a", message="Analyze"),
        {"corpus": {"hits": []}},
    )

    cached = stored["value"]
    assert isinstance(cached, dict)
    assert set(cached) == {"result", "meter", "source_result_version"}
    assert str(cached["source_result_version"]).startswith("result-")
    assert cached["result"] == {key: value for key, value in result.items() if key != "_llm"}
    assert "_llm" not in cached["result"]
    assert cached["meter"] == {
        "source_usage": {"prompt": 10, "completion": 2, "total": 12},
        "source_cost_estimate": {
            "status": "estimated",
            "currency": "USD",
            "amount": 0.001,
            "price_card_revision": 4,
            "route_id": "analysis",
        },
    }
