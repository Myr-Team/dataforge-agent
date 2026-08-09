from __future__ import annotations

from datetime import timezone
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .evidence import operation_code_for_event, operation_label
from .models import FinOpsRequestEvent


SubjectType = Literal["metric", "risk", "roi_stage", "assistant_answer"]
DataStatus = Literal["complete", "partial", "unavailable"]


class EvidenceSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str = Field(min_length=1, max_length=80)
    value: float | str | None = None
    unit: str = Field(default="", max_length=32)


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_ref: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{7,127}$")
    request_name: str = Field(min_length=1, max_length=160)
    occurred_at: str
    operation: str = Field(min_length=1, max_length=80)
    model_label: str = Field(min_length=1, max_length=160)
    signal: EvidenceSignal
    status: str = Field(min_length=1, max_length=32)
    error_category: str | None = Field(default=None, max_length=64)
    latency_ms: int | None = Field(default=None, ge=0)
    cache_state: str = Field(default="unavailable", max_length=24)
    tokens_total: int | None = Field(default=None, ge=0)
    estimated_cost: float | None = Field(default=None, ge=0)
    cost_status: str = Field(default="unavailable", max_length=24)
    gateway_coverage: str = Field(default="unknown", max_length=32)
    visible_answer_summary: str | None = Field(default=None, max_length=400)
    technical_refs: dict[str, str] = Field(default_factory=dict)


class EvidenceSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_type: SubjectType
    subject_id: str = Field(min_length=1, max_length=96)
    reason: str = Field(min_length=1, max_length=160)
    metric_id: str | None = Field(default=None, max_length=96)
    policy_type: str | None = Field(default=None, max_length=64)
    items: list[EvidenceItem] = Field(default_factory=list, max_length=3)
    data_status: DataStatus = "unavailable"
    evidence_state: Literal["observed", "partial", "unavailable"] = "unavailable"


_POLICY_REASON = {
    "p95_latency": "响应时延代表证据",
    "error_rate": "调用失败代表证据",
    "unpriced_requests": "未计价调用代表证据",
    "cache_hit_rate": "缓存效率代表证据",
    "token_spike": "Token 异常代表证据",
    "apim_coverage": "统一入口覆盖代表证据",
    "daily_cost_budget": "预算消耗代表证据",
}

_METRIC_REASON = {
    "cost": "成本指标代表证据",
    "estimated_cost": "成本指标代表证据",
    "requests": "调用指标代表证据",
    "success_rate": "调用质量代表证据",
    "tokens": "Token 指标代表证据",
    "total": "Token 指标代表证据",
    "p95": "响应时延代表证据",
    "latency": "响应时延代表证据",
    "cache": "缓存指标代表证据",
    "cache_savings": "缓存收益代表证据",
}


def select_policy_evidence(
    events: Sequence[FinOpsRequestEvent],
    policy_type: str,
    limit: int = 3,
) -> EvidenceSet:
    policy = str(policy_type or "").strip()
    bounded_limit = _limit(limit)
    candidates, signal = _policy_evidence_candidates(events, policy)
    if signal is None:
        return _empty_set(
            "risk",
            policy or "unknown",
            _POLICY_REASON.get(policy, "风险证据"),
            policy_type=policy or None,
        )
    return _evidence_set(
        subject_type="risk",
        subject_id=policy,
        reason=_POLICY_REASON.get(policy, "风险代表证据"),
        events=candidates[:bounded_limit],
        signal=signal,
        policy_type=policy,
    )


def policy_evidence_candidates(
    events: Sequence[FinOpsRequestEvent],
    policy_type: str,
) -> list[FinOpsRequestEvent]:
    """Return all semantically relevant events in policy ranking order."""
    candidates, _signal = _policy_evidence_candidates(
        events,
        str(policy_type or "").strip(),
    )
    return candidates


def _policy_evidence_candidates(
    events: Sequence[FinOpsRequestEvent],
    policy: str,
) -> tuple[list[FinOpsRequestEvent], Any | None]:
    if policy == "p95_latency":
        candidates = sorted(
            (event for event in events if event.latency_ms is not None),
            key=lambda event: (_number(event.latency_ms), _timestamp(event)),
            reverse=True,
        )
        signal = lambda event: EvidenceSignal(metric="latency_ms", value=event.latency_ms, unit="ms")
    elif policy == "error_rate":
        candidates = sorted(
            (event for event in events if event.status == "failed"),
            key=_timestamp,
            reverse=True,
        )
        signal = lambda event: EvidenceSignal(metric="request_status", value="failed", unit="status")
    elif policy == "unpriced_requests":
        candidates = sorted(
            (event for event in events if event.estimated_cost.amount is None),
            key=_timestamp,
            reverse=True,
        )
        signal = lambda event: EvidenceSignal(
            metric="pricing_status",
            value=event.estimated_cost.status,
            unit="status",
        )
    elif policy == "cache_hit_rate":
        misses = sorted(
            (
                event
                for event in events
                if _cache_state(event) in {"miss", "bypassed"}
            ),
            key=lambda event: (_cache_state(event) != "miss", -_timestamp(event)),
        )
        hits = sorted(
            (event for event in events if _cache_state(event) == "hit"),
            key=_timestamp,
            reverse=True,
        )
        candidates = _unique_events([*misses[:1], *hits[:1], *misses[1:]])
        signal = lambda event: EvidenceSignal(metric="cache_state", value=_cache_state(event), unit="state")
    elif policy == "token_spike":
        candidates = sorted(
            (event for event in events if event.tokens.total is not None),
            key=lambda event: (_number(event.tokens.total), _timestamp(event)),
            reverse=True,
        )
        signal = lambda event: EvidenceSignal(metric="tokens_total", value=event.tokens.total, unit="token")
    elif policy == "apim_coverage":
        candidates = sorted(
            (event for event in events if event.gateway_coverage != "apim_governed"),
            key=lambda event: (_coverage_rank(event.gateway_coverage), _timestamp(event)),
            reverse=True,
        )
        signal = lambda event: EvidenceSignal(
            metric="gateway_coverage",
            value=event.gateway_coverage,
            unit="state",
        )
    elif policy == "daily_cost_budget":
        candidates = _cost_events(events)
        signal = _cost_signal
    else:
        return [], None
    return candidates, signal


def select_metric_evidence(
    events: Sequence[FinOpsRequestEvent],
    metric_id: str,
    limit: int = 3,
) -> EvidenceSet:
    metric = str(metric_id or "").strip()
    bounded_limit = _limit(limit)
    if metric in {"cost", "estimated_cost"}:
        candidates = _cost_events(events)
        signal = _cost_signal
    elif metric in {"tokens", "total"}:
        candidates = sorted(
            (event for event in events if event.tokens.total is not None),
            key=lambda event: (_number(event.tokens.total), _timestamp(event)),
            reverse=True,
        )
        signal = lambda event: EvidenceSignal(metric="tokens_total", value=event.tokens.total, unit="token")
    elif metric in {"p95", "latency"}:
        candidates = sorted(
            (event for event in events if event.latency_ms is not None),
            key=lambda event: (_number(event.latency_ms), _timestamp(event)),
            reverse=True,
        )
        signal = lambda event: EvidenceSignal(metric="latency_ms", value=event.latency_ms, unit="ms")
    elif metric in {"cache", "cache_savings"}:
        return select_policy_evidence(events, "cache_hit_rate", bounded_limit).model_copy(
            update={"subject_type": "metric", "subject_id": metric, "metric_id": metric, "policy_type": None}
        )
    elif metric in {"requests", "success_rate"}:
        candidates = sorted(
            events,
            key=lambda event: (event.status != "failed", _timestamp(event)),
            reverse=False,
        )
        signal = lambda event: EvidenceSignal(metric="request_status", value=event.status, unit="status")
    else:
        return _empty_set("metric", metric or "unknown", _METRIC_REASON.get(metric, "指标证据"), metric_id=metric or None)
    return _evidence_set(
        subject_type="metric",
        subject_id=metric,
        reason=_METRIC_REASON.get(metric, "指标代表证据"),
        events=candidates[:bounded_limit],
        signal=signal,
        metric_id=metric,
    )


def public_evidence_summary(
    event: FinOpsRequestEvent,
    *,
    signal: EvidenceSignal | Mapping[str, Any],
) -> EvidenceItem:
    observed = signal if isinstance(signal, EvidenceSignal) else EvidenceSignal.model_validate(signal)
    occurred_at = event.occurred_at.astimezone(timezone.utc)
    operation_code = operation_code_for_event(event)
    return EvidenceItem(
        request_ref=event.request_ref,
        request_name=f"Request {occurred_at:%Y-%m-%d %H:%M}",
        occurred_at=occurred_at.isoformat(),
        operation=operation_label(operation_code),
        model_label=event.deployment or event.model or "未记录模型",
        signal=observed,
        status=event.status,
        error_category=event.error_category,
        latency_ms=event.latency_ms,
        cache_state=_cache_state(event),
        tokens_total=event.tokens.total,
        estimated_cost=event.estimated_cost.amount,
        cost_status=event.estimated_cost.status,
        gateway_coverage=event.gateway_coverage,
        technical_refs={"request_ref": event.request_ref},
    )


def _evidence_set(
    *,
    subject_type: SubjectType,
    subject_id: str,
    reason: str,
    events: Sequence[FinOpsRequestEvent],
    signal: Any,
    metric_id: str | None = None,
    policy_type: str | None = None,
) -> EvidenceSet:
    items = [public_evidence_summary(event, signal=signal(event)) for event in events]
    return EvidenceSet(
        subject_type=subject_type,
        subject_id=subject_id,
        reason=reason,
        metric_id=metric_id,
        policy_type=policy_type,
        items=items,
        data_status="complete" if items else "unavailable",
        evidence_state="observed" if items else "unavailable",
    )


def _empty_set(
    subject_type: SubjectType,
    subject_id: str,
    reason: str,
    *,
    metric_id: str | None = None,
    policy_type: str | None = None,
) -> EvidenceSet:
    return EvidenceSet(
        subject_type=subject_type,
        subject_id=subject_id,
        reason=reason,
        metric_id=metric_id,
        policy_type=policy_type,
    )


def _cost_events(events: Sequence[FinOpsRequestEvent]) -> list[FinOpsRequestEvent]:
    return sorted(
        (event for event in events if event.estimated_cost.amount is not None),
        key=lambda event: (_number(event.estimated_cost.amount), _timestamp(event)),
        reverse=True,
    )


def _cost_signal(event: FinOpsRequestEvent) -> EvidenceSignal:
    return EvidenceSignal(metric="estimated_cost", value=event.estimated_cost.amount, unit="USD")


def _cache_state(event: FinOpsRequestEvent) -> str:
    state = str(event.result_cache.state or event.cache.state or "unavailable")
    return state if state in {"hit", "miss", "bypassed", "unavailable"} else "unavailable"


def _coverage_rank(value: str) -> int:
    return {"unmanaged": 3, "unknown": 2, "app_observed": 1}.get(str(value), 0)


def _timestamp(event: FinOpsRequestEvent) -> float:
    return event.occurred_at.timestamp()


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _limit(value: int) -> int:
    return max(1, min(3, int(value or 3)))


def _unique_events(events: Sequence[FinOpsRequestEvent]) -> list[FinOpsRequestEvent]:
    result: list[FinOpsRequestEvent] = []
    seen: set[str] = set()
    for event in events:
        if event.request_ref in seen:
            continue
        seen.add(event.request_ref)
        result.append(event)
    return result


__all__ = [
    "EvidenceItem",
    "EvidenceSet",
    "EvidenceSignal",
    "policy_evidence_candidates",
    "public_evidence_summary",
    "select_metric_evidence",
    "select_policy_evidence",
]
