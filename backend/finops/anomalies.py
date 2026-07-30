from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import FinOpsRequestEvent


class AnomalyEvaluationInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    events: list[FinOpsRequestEvent]
    daily_budget_usd: float | None = Field(default=None, gt=0)
    budget_warning_pct: float = Field(default=80, gt=0, le=100)
    budget_critical_pct: float = Field(default=100, ge=100)
    trailing_token_median: float | None = Field(default=None, ge=0)
    token_spike_multiplier: float = Field(default=2, gt=1)
    error_rate_threshold_pct: float = Field(default=5, ge=0, le=100)
    error_rate_minimum_requests: int = Field(default=20, ge=1)
    error_rate_window_minutes: int = Field(default=15, ge=1, le=1440)
    p95_latency_threshold_ms: int = Field(default=2000, ge=1)
    p95_latency_minimum_requests: int = Field(default=20, ge=1)
    p95_latency_window_minutes: int = Field(default=15, ge=1, le=1440)
    apim_coverage_threshold_pct: float = Field(default=95, ge=0, le=100)
    unpriced_threshold_pct: float = Field(default=5, ge=0, le=100)
    cache_hit_rate_threshold_pct: float = Field(default=20, ge=0, le=100)
    cache_minimum_requests: int = Field(default=20, ge=1)


class DetectedAnomaly(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anomaly_id: str
    policy_type: Literal[
        "error_rate",
        "p95_latency",
        "daily_cost_budget",
        "token_spike",
        "apim_coverage",
        "unpriced_requests",
        "cache_hit_rate",
    ]
    severity: Literal["info", "warning", "critical"]
    status: Literal["open", "acknowledged", "suppressed", "resolved"] = "open"
    observed_value: float
    threshold_value: float
    sample_count: int
    workspace_ids: list[str]
    recommendation: str
    evidence_refs: list[str] = Field(default_factory=list, max_length=5)


def evaluate_default_anomalies(value: AnomalyEvaluationInput) -> list[DetectedAnomaly]:
    events = value.events
    count = len(events)
    if not count:
        return []
    results: list[DetectedAnomaly] = []
    workspace_ids = sorted({event.workspace_id for event in events})
    latest = max(event.occurred_at for event in events)
    error_events = [
        event
        for event in events
        if event.occurred_at >= latest - timedelta(minutes=value.error_rate_window_minutes)
    ]
    error_count = len(error_events)
    failed = sum(event.status == "failed" for event in error_events)
    error_rate = failed / error_count * 100 if error_count else 0
    if (
        error_count >= value.error_rate_minimum_requests
        and error_rate > value.error_rate_threshold_pct
    ):
        results.append(
            _finding(
                "error_rate",
                "critical" if error_rate >= 10 else "warning",
                error_rate,
                value.error_rate_threshold_pct,
                error_count,
                workspace_ids,
                "检查失败来源、模型路由与上游限流证据。",
                _event_refs(
                    [event for event in error_events if event.status == "failed"],
                    key=lambda event: event.occurred_at.timestamp(),
                ),
            )
        )

    latency_events = [
        event
        for event in events
        if event.occurred_at >= latest - timedelta(minutes=value.p95_latency_window_minutes)
    ]
    latencies = sorted(
        event.latency_ms for event in latency_events if event.latency_ms is not None
    )
    p95 = _percentile(latencies, 0.95)
    if (
        len(latencies) >= value.p95_latency_minimum_requests
        and p95 is not None
        and p95 > value.p95_latency_threshold_ms
    ):
        results.append(
            _finding(
                "p95_latency",
                "critical" if p95 >= 5000 else "warning",
                p95,
                value.p95_latency_threshold_ms,
                len(latencies),
                workspace_ids,
                "核对慢请求路由、缓存状态及 APIM 后端耗时。",
                _event_refs(
                    [event for event in latency_events if event.latency_ms is not None],
                    key=lambda event: float(event.latency_ms or 0),
                ),
            )
        )

    daily_events = [
        event for event in events if event.occurred_at.date() == latest.date()
    ]
    costs = [
        event.estimated_cost.amount
        for event in daily_events
        if event.estimated_cost.amount is not None
    ]
    cost_total = sum(costs)
    if value.daily_budget_usd:
        ratio = cost_total / value.daily_budget_usd * 100
        if ratio >= value.budget_warning_pct:
            results.append(
                _finding(
                    "daily_cost_budget",
                    "critical" if ratio >= value.budget_critical_pct else "warning",
                    ratio,
                    value.budget_critical_pct
                    if ratio >= value.budget_critical_pct
                    else value.budget_warning_pct,
                    len(costs),
                    workspace_ids,
                    "审阅当日高成本 Agent 与模型，再决定是否提交限额或路由动作。",
                    _event_refs(
                        [
                            event for event in daily_events
                            if event.estimated_cost.amount is not None
                        ],
                        key=lambda event: float(event.estimated_cost.amount or 0),
                    ),
                )
            )

    token_values = [
        event.tokens.total
        for event in events
        if event.occurred_at.date() == latest.date()
        and event.occurred_at.hour == latest.hour
        and event.tokens.total is not None
    ]
    token_total = sum(token_values)
    if value.trailing_token_median is not None and value.trailing_token_median > 0:
        ratio = token_total / value.trailing_token_median
        if ratio > value.token_spike_multiplier:
            results.append(
                _finding(
                    "token_spike",
                    "warning",
                    ratio,
                    value.token_spike_multiplier,
                    len(token_values),
                    workspace_ids,
                    "检查相同时段的调用量、上下文长度和重试行为。",
                    _event_refs(
                        [
                            event for event in events
                            if event.occurred_at.date() == latest.date()
                            and event.occurred_at.hour == latest.hour
                            and event.tokens.total is not None
                        ],
                        key=lambda event: float(event.tokens.total or 0),
                    ),
                )
            )

    governed = sum(event.gateway_coverage == "apim_governed" for event in events)
    coverage = governed / count * 100
    if coverage < value.apim_coverage_threshold_pct:
        results.append(
            _finding(
                "apim_coverage",
                "critical" if coverage < 90 else "warning",
                coverage,
                value.apim_coverage_threshold_pct,
                count,
                workspace_ids,
                "定位 app_observed、unmanaged 或 unknown 调用链。",
                _event_refs(
                    [
                        event for event in events
                        if event.gateway_coverage != "apim_governed"
                    ],
                    key=lambda event: event.occurred_at.timestamp(),
                ),
            )
        )

    priced = sum(event.estimated_cost.amount is not None for event in events)
    unpriced = (count - priced) / count * 100
    if unpriced > value.unpriced_threshold_pct:
        results.append(
            _finding(
                "unpriced_requests",
                "warning",
                unpriced,
                value.unpriced_threshold_pct,
                count,
                workspace_ids,
                "补充对应模型价目表 revision，禁止以零成本代替未知价格。",
                _event_refs(
                    [
                        event for event in events
                        if event.estimated_cost.amount is None
                    ],
                    key=lambda event: event.occurred_at.timestamp(),
                ),
            )
        )

    eligible = [event for event in events if event.cache.eligible is True]
    hits = sum(event.cache.state == "hit" for event in eligible)
    cache_rate = hits / len(eligible) * 100 if eligible else None
    if (
        len(eligible) >= value.cache_minimum_requests
        and cache_rate is not None
        and cache_rate < value.cache_hit_rate_threshold_pct
    ):
        results.append(
            _finding(
                "cache_hit_rate",
                "warning",
                cache_rate,
                value.cache_hit_rate_threshold_pct,
                len(eligible),
                workspace_ids,
                "检查同 workspace 分析的缓存键、TTL 与 bypass 原因。",
                _event_refs(
                    [
                        event for event in events
                        if event.cache.state in {"miss", "bypassed"}
                    ],
                    key=lambda event: event.occurred_at.timestamp(),
                ),
            )
        )
    return results


def _finding(
    policy_type: str,
    severity: str,
    observed: float,
    threshold: float,
    sample_count: int,
    workspace_ids: list[str],
    recommendation: str,
    evidence_refs: list[str] | None = None,
) -> DetectedAnomaly:
    # Identity represents the governed scope and rule, not a particular sample.
    # This lets acknowledgements/suppressions survive normal metric movement.
    identity = f"{policy_type}|{','.join(workspace_ids)}"
    anomaly_id = f"anomaly_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:20]}"
    return DetectedAnomaly(
        anomaly_id=anomaly_id,
        policy_type=policy_type,
        severity=severity,
        observed_value=round(float(observed), 4),
        threshold_value=float(threshold),
        sample_count=sample_count,
        workspace_ids=workspace_ids,
        recommendation=recommendation,
        evidence_refs=list(evidence_refs or [])[:5],
    )


def _event_refs(
    events: list[FinOpsRequestEvent],
    *,
    key,
) -> list[str]:
    refs: list[str] = []
    for event in sorted(events, key=key, reverse=True):
        request_ref = str(event.request_ref or "").strip()
        if request_ref and request_ref not in refs:
            refs.append(request_ref)
        if len(refs) >= 5:
            break
    return refs


def _percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    index = max(0, min(len(values) - 1, int((len(values) - 1) * fraction + 0.999999)))
    return values[index]
