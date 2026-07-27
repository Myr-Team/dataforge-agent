from __future__ import annotations

import backend.orchestrator as orchestrator
from backend.schemas import ChatRequest


def test_feasibility_result_cache_runs_real_miss_then_hit(
    monkeypatch,
) -> None:
    values: dict[str, dict[str, object]] = {}
    agent_calls = 0
    report = orchestrator.FeasibilityReport(
        opportunity_id="safe-result",
        dimensions=[],
        verdict="conditional",
        overall_confidence="speculative",
        gap_list=[],
    )

    def get_json(key: str):
        value = values.get(key)
        return value, {
            "provider": "redis",
            "status": "hit" if value else "miss",
            "elapsed_ms": 1,
        }

    def set_json(
        key: str,
        value: dict[str, object],
        *,
        ttl_seconds: int | None = None,
    ):
        values[key] = value
        return {"provider": "redis", "status": "stored", "ttl_seconds": ttl_seconds}

    def run_agent(*_args, **_kwargs):
        nonlocal agent_calls
        agent_calls += 1
        return {
            "structured": report.model_dump(),
            "response_id": "provider-response-hidden",
            "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
        }

    monkeypatch.setattr(
        orchestrator,
        "_evidence_catalog",
        lambda _artifact: [{"id": "evidence-1"}],
    )
    monkeypatch.setattr(orchestrator.cache_store, "get_json", get_json)
    monkeypatch.setattr(orchestrator.cache_store, "set_json", set_json)
    monkeypatch.setattr(orchestrator, "run_agent", run_agent)
    monkeypatch.setattr(
        orchestrator,
        "_verify_evidence",
        lambda value, _catalog: (value, []),
    )
    monkeypatch.setattr(
        orchestrator,
        "_normalize_feasibility_confidence",
        lambda value: value,
    )
    monkeypatch.setattr(
        orchestrator,
        "_normalize_feasibility_opportunity",
        lambda value, _req, _artifact: value,
    )
    monkeypatch.setattr(
        orchestrator,
        "_diversify_feasibility_scores",
        lambda value: value,
    )
    monkeypatch.setattr(
        orchestrator,
        "apply_pre_audit_guardrails",
        lambda data, *_args: dict(data),
    )

    request = ChatRequest(workspace_id="ws-a", message="Analyze")
    artifact = {"corpus": {"hits": []}}
    first = orchestrator._run_feasibility_analyst(request, artifact)
    second = orchestrator._run_feasibility_analyst(request, artifact)

    assert agent_calls == 1
    assert first["_llm"]["result_cache"]["state"] == "miss"
    assert second["_llm"]["result_cache"]["state"] == "hit"
    assert second["_llm"]["result_cache"]["source_result_version"].startswith(
        "result-"
    )
    assert second["_llm"]["provider_cache"]["state"] == "unavailable"
