from datetime import datetime, timedelta, timezone

from backend.finops.evidence_selection import (
    select_metric_evidence,
    select_policy_evidence,
)
from backend.finops.models import FinOpsRequestEvent


_NOW = datetime(2026, 8, 3, 2, 0, tzinfo=timezone.utc)


def _event(
    suffix: str,
    *,
    seconds: int,
    status: str = "succeeded",
    latency_ms: int = 500,
    tokens: int = 100,
    cache_state: str = "unavailable",
    cache_eligible: bool | None = False,
    gateway_coverage: str = "apim_governed",
    cost: float | None = 0.001,
) -> FinOpsRequestEvent:
    return FinOpsRequestEvent.model_validate(
        {
            "request_ref": f"req_{suffix:0<16}",
            "occurred_at": _NOW + timedelta(seconds=seconds),
            "call_class": "model",
            "tenant_ref": "tenant-a",
            "workspace_id": "ws-a",
            "run_id": f"run-{suffix}",
            "agent_id": "agent-a",
            "deployment": "model-a",
            "route": "analysis",
            "status": status,
            "error_category": "provider_5xx" if status == "failed" else None,
            "latency_ms": latency_ms,
            "tokens": {"total": tokens},
            "cache": {"state": cache_state, "eligible": cache_eligible},
            "gateway_coverage": gateway_coverage,
            "estimated_cost": {
                "amount": cost,
                "currency": "USD",
                "status": "estimated" if cost is not None else "unavailable",
            },
            "evidence_state": "observed",
        }
    )


def _events() -> list[FinOpsRequestEvent]:
    return [
        _event("latency", seconds=1, latency_ms=9_000, tokens=800, cost=0.03),
        _event("failure", seconds=2, status="failed", latency_ms=1_100, tokens=700, cost=0.02),
        _event("unpriced", seconds=3, latency_ms=900, tokens=600, cost=None),
        _event("cachemiss", seconds=4, latency_ms=850, tokens=500, cache_state="miss", cache_eligible=True, cost=0.01),
        _event("tokenhigh", seconds=5, latency_ms=800, tokens=31_000, cost=0.04),
        _event("ungoverned", seconds=6, latency_ms=750, tokens=400, gateway_coverage="unmanaged", cost=0.008),
        _event("costhigh", seconds=7, latency_ms=700, tokens=300, cost=0.08),
        _event("cachehit", seconds=8, latency_ms=300, tokens=200, cache_state="hit", cache_eligible=True, cost=0.004),
    ]


def test_policy_selectors_return_semantic_and_distinct_first_refs() -> None:
    events = _events()
    policies = (
        "p95_latency",
        "error_rate",
        "unpriced_requests",
        "cache_hit_rate",
        "token_spike",
        "apim_coverage",
    )

    selected = {policy: select_policy_evidence(events, policy) for policy in policies}

    first_refs = {policy: value.items[0].request_ref for policy, value in selected.items()}
    assert len(set(first_refs.values())) == len(policies)
    assert first_refs == {
        "p95_latency": "req_latency000000000",
        "error_rate": "req_failure000000000",
        "unpriced_requests": "req_unpriced00000000",
        "cache_hit_rate": "req_cachemiss0000000",
        "token_spike": "req_tokenhigh0000000",
        "apim_coverage": "req_ungoverned000000",
    }
    assert selected["p95_latency"].items[0].signal.metric == "latency_ms"
    assert selected["error_rate"].items[0].status == "failed"
    assert selected["unpriced_requests"].items[0].cost_status == "unavailable"
    assert selected["unpriced_requests"].items[0].signal.model_dump() == {
        "metric": "pricing_status",
        "value": "unpriced",
        "unit": "status",
    }
    assert selected["cache_hit_rate"].items[0].cache_state == "miss"
    assert selected["token_spike"].items[0].signal.value == 31_000
    assert selected["apim_coverage"].items[0].signal.value == "unmanaged"


def test_cache_evidence_includes_miss_and_hit_as_a_comparison() -> None:
    evidence = select_policy_evidence(_events(), "cache_hit_rate", limit=3)

    assert [item.cache_state for item in evidence.items[:2]] == ["miss", "hit"]
    assert evidence.subject_type == "risk"
    assert evidence.subject_id == "cache_hit_rate"


def test_metric_selectors_use_the_metric_value_instead_of_generic_request_count() -> None:
    events = _events()

    cost = select_metric_evidence(events, "cost")
    tokens = select_metric_evidence(events, "tokens")
    latency = select_metric_evidence(events, "p95")

    assert cost.items[0].request_ref == "req_costhigh00000000"
    assert cost.items[0].signal.model_dump() == {
        "metric": "estimated_cost",
        "value": 0.08,
        "unit": "USD",
    }
    assert tokens.items[0].request_ref == "req_tokenhigh0000000"
    assert latency.items[0].request_ref == "req_latency000000000"


def test_unknown_subject_returns_an_honest_empty_evidence_set() -> None:
    evidence = select_metric_evidence(_events(), "unknown_metric")

    assert evidence.items == []
    assert evidence.data_status == "unavailable"
    assert evidence.evidence_state == "unavailable"
