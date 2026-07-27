from __future__ import annotations

from backend.provider_usage import normalize_deepseek_usage, provider_cache_evidence


def test_deepseek_usage_preserves_official_cache_fields() -> None:
    usage = normalize_deepseek_usage(
        {
            "prompt_tokens": 100,
            "completion_tokens": 40,
            "total_tokens": 140,
            "prompt_cache_hit_tokens": 70,
            "prompt_cache_miss_tokens": 30,
            "completion_tokens_details": {"reasoning_tokens": 12},
        }
    )

    assert usage.input_tokens == 100
    assert usage.output_tokens == 40
    assert usage.reasoning_tokens == 12
    assert usage.provider_cache_hit_tokens == 70
    assert usage.provider_cache_miss_tokens == 30
    assert usage.total_tokens == 140

    evidence = provider_cache_evidence(usage)
    assert evidence.state == "partial_hit"
    assert evidence.hit_rate_pct == 70
    assert evidence.evidence_state == "observed"


def test_deepseek_usage_never_infers_missing_cache_fields() -> None:
    usage = normalize_deepseek_usage(
        {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}
    )

    assert usage.provider_cache_hit_tokens is None
    assert usage.provider_cache_miss_tokens is None
    assert provider_cache_evidence(usage).state == "unavailable"


def test_deepseek_usage_rejects_negative_or_boolean_token_values() -> None:
    usage = normalize_deepseek_usage(
        {
            "prompt_tokens": -1,
            "completion_tokens": True,
            "prompt_cache_hit_tokens": "20",
            "prompt_cache_miss_tokens": 5,
        }
    )

    assert usage.input_tokens is None
    assert usage.output_tokens is None
    assert usage.provider_cache_hit_tokens is None
    assert usage.provider_cache_miss_tokens == 5

