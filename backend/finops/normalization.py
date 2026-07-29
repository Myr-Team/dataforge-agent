from __future__ import annotations

import hashlib
import hmac
import re
from datetime import datetime, timezone
from typing import Any, Mapping

from .models import (
    CacheEvidence,
    EstimatedCost,
    FinOpsRequestEvent,
    ProviderCacheEvidence,
    ResultCacheEvidence,
    TokenUsage,
)


_APIM_CORRELATION = re.compile(
    r"^(?:[0-9a-f]{32}|[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12})$",
    re.IGNORECASE,
)


def opaque_ref(prefix: str, *parts: object, secret: str) -> str:
    if not secret:
        raise ValueError("FinOps HMAC secret is required")
    material = "\x1f".join(str(part or "") for part in parts).encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), material, hashlib.sha256).hexdigest()[:24]
    return f"{prefix}_{digest}"


def canonical_actor_ref(
    raw_tenant_id: object,
    raw_actor_id: object,
    *,
    secret: str,
) -> str:
    """Derive the sole FinOps member key from trusted raw Entra identifiers."""
    tenant_id = str(raw_tenant_id or "").strip().lower()
    actor_id = str(raw_actor_id or "").strip().lower()
    if not tenant_id or not actor_id:
        raise ValueError("raw tenant and actor identifiers are required")
    return opaque_ref("actor", tenant_id, actor_id, secret=secret)


def normalize_run_event(
    run: Mapping[str, Any],
    *,
    model_index: int,
    tenant_id: str,
    hmac_secret: str,
    department_id: str | None = None,
    raw_tenant_id: str | None = None,
) -> FinOpsRequestEvent:
    models = run.get("models") if isinstance(run.get("models"), list) else []
    model_event = models[model_index] if 0 <= model_index < len(models) and isinstance(models[model_index], Mapping) else {}
    run_id = str(run.get("run_id") or "").strip()
    workspace_id = str(run.get("workspace_id") or "").strip()
    if not run_id or not workspace_id or not tenant_id:
        raise ValueError("run_id, workspace_id, and tenant_id are required")

    actor = run.get("actor") if isinstance(run.get("actor"), Mapping) else {}
    raw_actor = actor.get("actor_id")
    raw_correlation = _correlation_value(run, model_event)
    usage = _usage(model_event.get("usage"))
    cost = _cost(model_event.get("cost_estimate"))
    status = _status(run.get("status"))

    return FinOpsRequestEvent(
        request_ref=opaque_ref("req", tenant_id, workspace_id, run_id, model_index, secret=hmac_secret),
        occurred_at=_timestamp(
            model_event.get("time")
            or run.get("completed_at")
            or run.get("updated_at")
            or run.get("started_at")
        ),
        call_class="model",
        tenant_ref=tenant_id,
        department_id=department_id,
        workspace_id=workspace_id,
        actor_ref=(
            canonical_actor_ref(
                raw_tenant_id or tenant_id,
                raw_actor,
                secret=hmac_secret,
            )
            if raw_actor
            else None
        ),
        run_id=run_id,
        agent_id=_text(model_event.get("agent"), 128),
        model=_text(model_event.get("model") or model_event.get("deployment"), 160),
        deployment=_text(model_event.get("deployment") or model_event.get("model"), 160),
        route=_text(model_event.get("route") or model_event.get("model_route"), 128),
        execution_kind=_text(model_event.get("execution_kind"), 64),
        status=status,
        error_category=_safe_error_category(run, model_event) if status == "failed" else None,
        latency_ms=_non_negative_int(model_event.get("latency_ms")),
        tokens=usage,
        cache=_cache(model_event.get("cache") or model_event.get("result_cache")),
        result_cache=_result_cache(
            model_event.get("result_cache") or model_event.get("cache")
        ),
        provider_cache=_provider_cache(model_event.get("provider_cache")),
        gateway_coverage="app_observed",
        estimated_cost=cost,
        evidence_state="observed" if usage.observed else "partial",
        correlation_ref=opaque_ref("corr", tenant_id, raw_correlation, secret=hmac_secret) if raw_correlation else None,
        apim_correlation_id=(
            str(raw_correlation).strip().lower()
            if raw_correlation and _APIM_CORRELATION.fullmatch(str(raw_correlation).strip())
            else None
        ),
        usage_source="provider" if usage.observed else "application",
        streaming=_optional_bool(model_event.get("streaming")),
        internal_correlation_key=opaque_ref(
            "join", tenant_id, workspace_id, raw_correlation or run_id, model_index, secret=hmac_secret
        ),
    )


def _usage(value: object) -> TokenUsage:
    raw = value if isinstance(value, Mapping) else {}
    return TokenUsage(
        input=_non_negative_int(raw.get("input") if "input" in raw else raw.get("prompt")),
        output=_non_negative_int(raw.get("output") if "output" in raw else raw.get("completion")),
        cached_input=_non_negative_int(raw.get("cached_input")),
        reasoning=_non_negative_int(raw.get("reasoning")),
        total=_non_negative_int(raw.get("total")),
    )


def _cache(value: object) -> CacheEvidence:
    raw = value if isinstance(value, Mapping) else {}
    state = str(raw.get("state") or raw.get("status") or "").strip().lower()
    if state not in {"hit", "miss", "bypassed", "unavailable"}:
        state = "unavailable"
    eligible = _optional_bool(raw.get("eligible"))
    if eligible is None and state in {"hit", "miss"}:
        eligible = True
    return CacheEvidence(
        state=state,
        eligible=eligible,
        avoided_tokens=_non_negative_int(raw.get("avoided_tokens")),
    )


def _result_cache(value: object) -> ResultCacheEvidence:
    raw = value if isinstance(value, Mapping) else {}
    state = str(raw.get("state") or raw.get("status") or "").strip().lower()
    if state not in {"hit", "miss", "bypassed", "unavailable"}:
        state = "unavailable"
    eligible = _optional_bool(raw.get("eligible"))
    if eligible is None:
        eligible = state in {"hit", "miss"}
    allowed_reasons = {
        "eligible",
        "disabled",
        "live_data",
        "side_effecting_tools",
        "unstable_conversation",
        "data_revision_missing",
        "lookup_unavailable",
        "not_recorded",
    }
    reason = str(raw.get("reason") or "").strip().lower()
    if reason not in allowed_reasons:
        reason = "eligible" if state in {"hit", "miss"} else "not_recorded"
    return ResultCacheEvidence(
        eligible=eligible,
        state=state,
        reason=reason,
        lookup_latency_ms=_non_negative_int(
            raw.get("lookup_latency_ms")
            if "lookup_latency_ms" in raw
            else raw.get("elapsed_ms")
        ),
        policy_revision=_non_negative_int(raw.get("policy_revision")) or 0,
        source_result_version=_text(raw.get("source_result_version"), 160),
    )


def _provider_cache(value: object) -> ProviderCacheEvidence:
    raw = value if isinstance(value, Mapping) else {}
    hit = _non_negative_int(raw.get("hit_tokens"))
    miss = _non_negative_int(raw.get("miss_tokens"))
    if hit is None or miss is None:
        return ProviderCacheEvidence(
            state="unavailable",
            hit_tokens=hit,
            miss_tokens=miss,
            hit_rate_pct=None,
            evidence_state=(
                "unavailable" if hit is None and miss is None else "partial"
            ),
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


def _cost(value: object) -> EstimatedCost:
    raw = value if isinstance(value, Mapping) else {}
    amount = _non_negative_float(raw.get("amount"))
    revision = _text(raw.get("price_card_revision") or raw.get("revision"), 128)
    raw_status = str(raw.get("status") or "").strip().lower()
    status = raw_status if raw_status in {"estimated", "partial", "unavailable"} else ("estimated" if amount is not None else "unavailable")
    return EstimatedCost(amount=amount, status=status, price_card_revision=revision)


def _correlation_value(run: Mapping[str, Any], model_event: Mapping[str, Any]) -> str | None:
    for source in (model_event, run.get("trace"), run):
        raw = source if isinstance(source, Mapping) else {}
        for key in ("apim_correlation_id", "correlation_id", "trace_id"):
            value = str(raw.get(key) or "").strip()
            if value:
                return value
    return None


def _status(value: object) -> str:
    status = str(value or "").strip().lower()
    if status in {"completed", "succeeded", "success"}:
        return "succeeded"
    if status in {"failed", "error"}:
        return "failed"
    if status in {"cancelled", "canceled"}:
        return "cancelled"
    return "unknown"


def _safe_error_category(run: Mapping[str, Any], model_event: Mapping[str, Any]) -> str | None:
    for source in (model_event, run):
        raw = source if isinstance(source, Mapping) else {}
        category = raw.get("error_category")
        if category is None and isinstance(raw.get("error"), Mapping):
            category = raw["error"].get("category")
        text = _text(category, 64)
        if text and all(character.isalnum() or character in "_.-" for character in text):
            return text
    return "unknown"


def _timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return datetime.now(timezone.utc)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _text(value: object, limit: int) -> str | None:
    text = str(value or "").strip()
    return text[:limit] if text else None


def _non_negative_int(value: object) -> int | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _non_negative_float(value: object) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None
