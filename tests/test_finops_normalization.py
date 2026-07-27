from __future__ import annotations

from backend.finops.normalization import normalize_run_event


def test_normalization_keeps_redis_and_provider_cache_evidence_separate() -> None:
    event = normalize_run_event(
        {
            "run_id": "run-safe",
            "workspace_id": "workspace-safe",
            "status": "completed",
            "started_at": "2026-07-28T01:00:00Z",
            "models": [
                {
                    "agent": "df-feasibility-analyst",
                    "model": "deepseek-v4-pro",
                    "result_cache": {
                        "eligible": True,
                        "state": "hit",
                        "reason": "eligible",
                        "lookup_latency_ms": 4,
                        "policy_revision": 5,
                        "source_result_version": "result-v3",
                    },
                    "provider_cache": {
                        "state": "partial_hit",
                        "hit_tokens": 80,
                        "miss_tokens": 20,
                        "hit_rate_pct": 80,
                        "evidence_state": "observed",
                    },
                }
            ],
        },
        model_index=0,
        tenant_id="tenant-safe",
        hmac_secret="secret-safe",
    )

    assert event.result_cache.state == "hit"
    assert event.result_cache.source_result_version == "result-v3"
    assert event.provider_cache.state == "partial_hit"
    assert event.provider_cache.hit_tokens == 80
    assert event.provider_cache.hit_rate_pct == 80
    assert event.cache.state == "hit"


def test_provider_cache_unknown_populations_remain_unavailable() -> None:
    event = normalize_run_event(
        {
            "run_id": "run-safe",
            "workspace_id": "workspace-safe",
            "status": "completed",
            "started_at": "2026-07-28T01:00:00Z",
            "models": [{"model": "deepseek-v4-pro"}],
        },
        model_index=0,
        tenant_id="tenant-safe",
        hmac_secret="secret-safe",
    )

    assert event.provider_cache.state == "unavailable"
    assert event.provider_cache.hit_rate_pct is None
