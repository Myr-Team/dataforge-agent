from __future__ import annotations

from backend.finops.normalization import (
    canonical_actor_ref,
    canonical_tenant_ref,
    normalize_run_event,
)


def test_canonical_tenant_ref_normalizes_raw_entra_tenant_case_and_whitespace() -> None:
    expected = canonical_tenant_ref("tenant-a", secret="secret-safe")

    assert canonical_tenant_ref(
        "  TENANT-A  ",
        secret="secret-safe",
    ) == expected


def test_canonical_actor_ref_normalizes_raw_entra_identifier_case_and_whitespace() -> None:
    expected = canonical_actor_ref(
        "tenant-a",
        "member-a",
        secret="secret-safe",
    )

    assert canonical_actor_ref(
        "  TENANT-A  ",
        "  MEMBER-A  ",
        secret="secret-safe",
    ) == expected


def test_normalization_never_uses_email_as_actor_ref_fallback() -> None:
    event = normalize_run_event(
        {
            "run_id": "run-safe",
            "workspace_id": "workspace-safe",
            "status": "completed",
            "started_at": "2026-07-28T01:00:00Z",
            "actor": {
                "tenant_id": "tenant-a",
                "email": "member-a@example.test",
            },
            "models": [{"model": "deepseek-v4-pro"}],
        },
        model_index=0,
        tenant_id="tenant-safe",
        raw_tenant_id="tenant-a",
        hmac_secret="secret-safe",
    )

    assert event.actor_ref is None


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
                    "policy_revision": 11,
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
    assert event.routing_policy_revision == 11
    assert event.routing_policy_revision != event.result_cache.policy_revision
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
    assert event.routing_policy_revision is None
