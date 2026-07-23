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
