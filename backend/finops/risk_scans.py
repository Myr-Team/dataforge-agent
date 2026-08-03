from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .anomalies import (
    AnomalyEvaluationInput,
    DetectedAnomaly,
    evaluate_default_anomalies,
)


RiskPolicyType = Literal[
    "error_rate",
    "p95_latency",
    "daily_cost_budget",
    "token_spike",
    "apim_coverage",
    "unpriced_requests",
    "cache_hit_rate",
]
RiskRuleStatus = Literal[
    "triggered",
    "clear",
    "insufficient_data",
    "unavailable",
]

POLICY_TYPES: tuple[RiskPolicyType, ...] = (
    "error_rate",
    "p95_latency",
    "daily_cost_budget",
    "token_spike",
    "apim_coverage",
    "unpriced_requests",
    "cache_hit_rate",
)


class RiskScanScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    from_value: str
    to_value: str
    department_id: str | None = None
    agent_id: str | None = None
    actor_ref: str | None = None
    model: str | None = None

    def fingerprint(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class RiskScanFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_type: RiskPolicyType
    status: RiskRuleStatus
    severity: Literal["info", "warning", "critical"] = "info"
    rule_revision: str
    observed_value: float | None = None
    threshold_value: float | None = None
    unit: str
    sample_count: int = Field(ge=0)
    minimum_samples: int = Field(ge=0)
    recommendation: str
    reason: str
    evidence_refs: list[str] = Field(default_factory=list, max_length=3)


class FinOpsRiskScan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scan_ref: str = Field(pattern=r"^rscan_[0-9a-f]{32}$")
    tenant_ref: str
    scope: RiskScanScope
    scope_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["running", "completed", "failed"]
    policy_revision: str
    ledger_revision: str
    rules_evaluated: int = Field(ge=0)
    rules_triggered: int = Field(ge=0)
    rules_clear: int = Field(ge=0)
    rules_insufficient: int = Field(ge=0)
    request_sample_count: int = Field(ge=0)
    evidence_coverage_pct: float = Field(ge=0, le=100)
    findings: list[RiskScanFinding]
    started_at: str
    finished_at: str | None = None
    initiated_by_ref: str
    safe_error_category: str | None = None


class RiskScanRepository(Protocol):
    def save(self, value: FinOpsRiskScan) -> FinOpsRiskScan: ...

    def get(self, tenant_ref: str, scan_ref: str) -> FinOpsRiskScan | None: ...

    def latest(
        self,
        tenant_ref: str,
        workspace_id: str,
        scope_fingerprint: str,
    ) -> FinOpsRiskScan | None: ...


class InMemoryRiskScanRepository:
    def __init__(self) -> None:
        self._lock = RLock()
        self._items: dict[tuple[str, str], FinOpsRiskScan] = {}

    def save(self, value: FinOpsRiskScan) -> FinOpsRiskScan:
        with self._lock:
            self._items[(value.tenant_ref, value.scan_ref)] = value.model_copy(deep=True)
        return value.model_copy(deep=True)

    def get(self, tenant_ref: str, scan_ref: str) -> FinOpsRiskScan | None:
        with self._lock:
            value = self._items.get((tenant_ref, scan_ref))
        return value.model_copy(deep=True) if value else None

    def latest(
        self,
        tenant_ref: str,
        workspace_id: str,
        scope_fingerprint: str,
    ) -> FinOpsRiskScan | None:
        with self._lock:
            candidates = [
                item.model_copy(deep=True)
                for (tenant, _), item in self._items.items()
                if tenant == tenant_ref
                and item.scope.workspace_id == workspace_id
                and item.scope_fingerprint == scope_fingerprint
            ]
        if not candidates:
            return None
        return max(candidates, key=lambda item: (item.started_at, item.scan_ref))


class RiskScanService:
    """Run and persist a deterministic, read-only FinOps rules evaluation."""

    def __init__(self, repository: RiskScanRepository) -> None:
        self._repository = repository

    def run(
        self,
        *,
        tenant_ref: str,
        scope: RiskScanScope,
        evaluation: AnomalyEvaluationInput,
        policy_revision: str,
        ledger_revision: str,
        initiated_by_ref: str,
        now: datetime | None = None,
    ) -> FinOpsRiskScan:
        timestamp = _as_utc(now or datetime.now(timezone.utc))
        started_at = _iso(timestamp)
        detected = {
            item.policy_type: item for item in evaluate_default_anomalies(evaluation)
        }
        findings = _evaluate_rule_basis(
            evaluation,
            detected=detected,
            rule_revision=policy_revision,
        )
        available = sum(item.status != "unavailable" for item in findings)
        scan = FinOpsRiskScan(
            scan_ref=f"rscan_{uuid.uuid4().hex}",
            tenant_ref=tenant_ref,
            scope=scope,
            scope_fingerprint=scope.fingerprint(),
            status="completed",
            policy_revision=policy_revision,
            ledger_revision=ledger_revision,
            rules_evaluated=len(findings),
            rules_triggered=sum(item.status == "triggered" for item in findings),
            rules_clear=sum(item.status == "clear" for item in findings),
            rules_insufficient=sum(
                item.status == "insufficient_data" for item in findings
            ),
            request_sample_count=len(evaluation.events),
            evidence_coverage_pct=round(available / len(findings) * 100, 2)
            if findings
            else 0,
            findings=findings,
            started_at=started_at,
            finished_at=started_at,
            initiated_by_ref=initiated_by_ref,
        )
        return self._repository.save(scan)

    def latest(
        self,
        *,
        tenant_ref: str,
        scope: RiskScanScope,
    ) -> FinOpsRiskScan | None:
        return self._repository.latest(
            tenant_ref,
            scope.workspace_id,
            scope.fingerprint(),
        )


def _evaluate_rule_basis(
    value: AnomalyEvaluationInput,
    *,
    detected: dict[str, DetectedAnomaly],
    rule_revision: str,
) -> list[RiskScanFinding]:
    events = value.events
    if not events:
        return [
            _finding(
                policy_type=policy,
                status="insufficient_data",
                rule_revision=rule_revision,
                unit=_unit(policy),
                sample_count=0,
                minimum_samples=_minimum_samples(policy, value),
                reason="当前筛选范围内没有可评估请求。",
                recommendation="扩大时间范围或等待更多请求后重新扫描。",
            )
            for policy in POLICY_TYPES
        ]

    latest = max(event.occurred_at for event in events)
    error_events = [
        event
        for event in events
        if event.occurred_at
        >= latest - timedelta(minutes=value.error_rate_window_minutes)
    ]
    latency_events = [
        event
        for event in events
        if event.occurred_at
        >= latest - timedelta(minutes=value.p95_latency_window_minutes)
        and event.latency_ms is not None
    ]
    daily_events = [event for event in events if event.occurred_at.date() == latest.date()]
    hourly_events = [
        event
        for event in daily_events
        if event.occurred_at.hour == latest.hour and event.tokens.total is not None
    ]
    eligible = [event for event in events if event.cache.eligible is True]

    failed = sum(event.status == "failed" for event in error_events)
    error_rate = failed / len(error_events) * 100 if error_events else None
    latencies = sorted(int(event.latency_ms or 0) for event in latency_events)
    p95 = _percentile(latencies, 0.95)
    costs = [
        float(event.estimated_cost.amount)
        for event in daily_events
        if event.estimated_cost.amount is not None
    ]
    cost_ratio = (
        sum(costs) / value.daily_budget_usd * 100
        if costs and value.daily_budget_usd
        else None
    )
    tokens = [float(event.tokens.total or 0) for event in hourly_events]
    token_ratio = (
        sum(tokens) / value.trailing_token_median
        if tokens and value.trailing_token_median and value.trailing_token_median > 0
        else None
    )
    governed = sum(event.gateway_coverage == "apim_governed" for event in events)
    coverage = governed / len(events) * 100
    priced = sum(event.estimated_cost.amount is not None for event in events)
    unpriced = (len(events) - priced) / len(events) * 100
    hits = sum(event.cache.state == "hit" for event in eligible)
    cache_rate = hits / len(eligible) * 100 if eligible else None

    results = [
        _basis(
            "error_rate",
            detected,
            rule_revision,
            observed=error_rate,
            threshold=value.error_rate_threshold_pct,
            unit="%",
            sample_count=len(error_events),
            minimum_samples=value.error_rate_minimum_requests,
            clear_reason="错误率处于当前策略阈值内。",
            recommendation="持续观察失败来源与模型路由。",
        ),
        _basis(
            "p95_latency",
            detected,
            rule_revision,
            observed=float(p95) if p95 is not None else None,
            threshold=float(value.p95_latency_threshold_ms),
            unit="ms",
            sample_count=len(latencies),
            minimum_samples=value.p95_latency_minimum_requests,
            clear_reason="P95 响应时间处于当前策略阈值内。",
            recommendation="持续观察慢请求、缓存与模型路由。",
        ),
        _basis(
            "daily_cost_budget",
            detected,
            rule_revision,
            observed=cost_ratio,
            threshold=value.budget_warning_pct if value.daily_budget_usd else None,
            unit="%",
            sample_count=len(costs),
            minimum_samples=1,
            unavailable=value.daily_budget_usd is None,
            clear_reason="当日估算成本未达到预算提醒阈值。",
            recommendation="配置预算后可评估当日成本消耗进度。",
        ),
        _basis(
            "token_spike",
            detected,
            rule_revision,
            observed=token_ratio,
            threshold=value.token_spike_multiplier
            if value.trailing_token_median is not None
            else None,
            unit="x",
            sample_count=len(tokens),
            minimum_samples=1,
            unavailable=value.trailing_token_median in {None, 0},
            clear_reason="当前小时 Token 用量未达到基线倍数阈值。",
            recommendation="积累相同时段历史基线后可识别用量突增。",
        ),
        _basis(
            "apim_coverage",
            detected,
            rule_revision,
            observed=coverage,
            threshold=value.apim_coverage_threshold_pct,
            unit="%",
            sample_count=len(events),
            minimum_samples=1,
            clear_reason="统一入口治理覆盖率达到当前策略要求。",
            recommendation="持续检查未纳管或来源未知的调用链。",
        ),
        _basis(
            "unpriced_requests",
            detected,
            rule_revision,
            observed=unpriced,
            threshold=value.unpriced_threshold_pct,
            unit="%",
            sample_count=len(events),
            minimum_samples=1,
            clear_reason="未计价请求占比处于当前策略阈值内。",
            recommendation="持续维护模型与价格版本映射。",
        ),
        _basis(
            "cache_hit_rate",
            detected,
            rule_revision,
            observed=cache_rate,
            threshold=value.cache_hit_rate_threshold_pct,
            unit="%",
            sample_count=len(eligible),
            minimum_samples=value.cache_minimum_requests,
            clear_reason="缓存命中率达到当前策略要求。",
            recommendation="持续观察可缓存请求的 miss 与 bypass 原因。",
        ),
    ]
    return results


def _basis(
    policy_type: RiskPolicyType,
    detected: dict[str, DetectedAnomaly],
    rule_revision: str,
    *,
    observed: float | None,
    threshold: float | None,
    unit: str,
    sample_count: int,
    minimum_samples: int,
    clear_reason: str,
    recommendation: str,
    unavailable: bool = False,
) -> RiskScanFinding:
    anomaly = detected.get(policy_type)
    if anomaly is not None:
        return RiskScanFinding(
            policy_type=policy_type,
            status="triggered",
            severity=anomaly.severity,
            rule_revision=rule_revision,
            observed_value=anomaly.observed_value,
            threshold_value=anomaly.threshold_value,
            unit=unit,
            sample_count=anomaly.sample_count,
            minimum_samples=minimum_samples,
            recommendation=anomaly.recommendation,
            reason="观测值已达到当前策略的风险判定条件。",
            evidence_refs=anomaly.evidence_refs[:3],
        )
    if unavailable:
        return _finding(
            policy_type=policy_type,
            status="unavailable",
            rule_revision=rule_revision,
            observed_value=observed,
            threshold_value=threshold,
            unit=unit,
            sample_count=sample_count,
            minimum_samples=minimum_samples,
            reason="当前缺少该规则所需的预算或历史基线。",
            recommendation=recommendation,
        )
    if sample_count < minimum_samples:
        return _finding(
            policy_type=policy_type,
            status="insufficient_data",
            rule_revision=rule_revision,
            observed_value=observed,
            threshold_value=threshold,
            unit=unit,
            sample_count=sample_count,
            minimum_samples=minimum_samples,
            reason=f"当前仅有 {sample_count} 个样本，至少需要 {minimum_samples} 个。",
            recommendation="积累更多请求后重新扫描，避免小样本误报。",
        )
    return _finding(
        policy_type=policy_type,
        status="clear",
        rule_revision=rule_revision,
        observed_value=observed,
        threshold_value=threshold,
        unit=unit,
        sample_count=sample_count,
        minimum_samples=minimum_samples,
        reason=clear_reason,
        recommendation=recommendation,
    )


def _finding(
    *,
    policy_type: RiskPolicyType,
    status: RiskRuleStatus,
    rule_revision: str,
    unit: str,
    sample_count: int,
    minimum_samples: int,
    reason: str,
    recommendation: str,
    observed_value: float | None = None,
    threshold_value: float | None = None,
) -> RiskScanFinding:
    return RiskScanFinding(
        policy_type=policy_type,
        status=status,
        rule_revision=rule_revision,
        observed_value=round(float(observed_value), 4)
        if observed_value is not None
        else None,
        threshold_value=float(threshold_value)
        if threshold_value is not None
        else None,
        unit=unit,
        sample_count=sample_count,
        minimum_samples=minimum_samples,
        reason=reason,
        recommendation=recommendation,
    )


def _minimum_samples(
    policy_type: RiskPolicyType,
    value: AnomalyEvaluationInput,
) -> int:
    return {
        "error_rate": value.error_rate_minimum_requests,
        "p95_latency": value.p95_latency_minimum_requests,
        "cache_hit_rate": value.cache_minimum_requests,
    }.get(policy_type, 1)


def _unit(policy_type: RiskPolicyType) -> str:
    if policy_type == "p95_latency":
        return "ms"
    if policy_type == "token_spike":
        return "x"
    return "%"


def _percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    index = max(0, min(len(values) - 1, int((len(values) - 1) * fraction + 0.999999)))
    return values[index]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")
