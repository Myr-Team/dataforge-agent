from __future__ import annotations

from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field


class ProviderUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    provider_cache_hit_tokens: int | None = Field(default=None, ge=0)
    provider_cache_miss_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class ProviderCacheEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["hit", "partial_hit", "miss", "unavailable"]
    hit_tokens: int | None = Field(default=None, ge=0)
    miss_tokens: int | None = Field(default=None, ge=0)
    hit_rate_pct: float | None = Field(default=None, ge=0, le=100)
    evidence_state: Literal["observed", "partial", "unavailable"]


def normalize_deepseek_usage(value: object) -> ProviderUsage:
    raw = value if isinstance(value, Mapping) else {}
    completion_details = raw.get("completion_tokens_details")
    if not isinstance(completion_details, Mapping):
        completion_details = {}
    return ProviderUsage(
        input_tokens=_token(raw.get("prompt_tokens")),
        output_tokens=_token(raw.get("completion_tokens")),
        reasoning_tokens=_token(completion_details.get("reasoning_tokens")),
        provider_cache_hit_tokens=_token(raw.get("prompt_cache_hit_tokens")),
        provider_cache_miss_tokens=_token(raw.get("prompt_cache_miss_tokens")),
        total_tokens=_token(raw.get("total_tokens")),
    )


def provider_cache_evidence(usage: ProviderUsage) -> ProviderCacheEvidence:
    hit = usage.provider_cache_hit_tokens
    miss = usage.provider_cache_miss_tokens
    if hit is None or miss is None:
        return ProviderCacheEvidence(
            state="unavailable",
            hit_tokens=hit,
            miss_tokens=miss,
            hit_rate_pct=None,
            evidence_state="unavailable" if hit is None and miss is None else "partial",
        )
    denominator = hit + miss
    rate = round(hit / denominator * 100, 2) if denominator else None
    state = "partial_hit" if hit and miss else "hit" if hit else "miss"
    return ProviderCacheEvidence(
        state=state,
        hit_tokens=hit,
        miss_tokens=miss,
        hit_rate_pct=rate,
        evidence_state="observed",
    )


def _token(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


__all__ = [
    "ProviderCacheEvidence",
    "ProviderUsage",
    "normalize_deepseek_usage",
    "provider_cache_evidence",
]
