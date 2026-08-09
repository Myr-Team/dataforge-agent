from __future__ import annotations

import math
from collections import defaultdict
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .models import FinOpsRequestEvent


class FinOpsRollup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bucket_kind: Literal["hour", "day"]
    bucket_at: str
    tenant_ref: str
    department_id: str
    workspace_id: str
    agent_id: str
    model_deployment: str
    request_count: int
    failure_count: int
    total_tokens: int | None
    estimated_cost: float | None
    p50_latency_ms: int | None
    p95_latency_ms: int | None
    apim_governed_count: int
    unpriced_count: int
    cache_hit_count: int = 0
    cache_miss_count: int = 0
    cache_bypassed_count: int = 0
    cache_unavailable_count: int = 0
    cache_avoided_tokens: int | None = None


def aggregate_rollups(
    events: list[FinOpsRequestEvent],
) -> tuple[list[FinOpsRollup], list[FinOpsRollup]]:
    return _aggregate(events, "hour"), _aggregate(events, "day")


def _aggregate(
    events: list[FinOpsRequestEvent],
    bucket_kind: Literal["hour", "day"],
) -> list[FinOpsRollup]:
    grouped: dict[tuple[str, ...], list[FinOpsRequestEvent]] = defaultdict(list)
    for event in events:
        bucket_at = (
            event.occurred_at.strftime("%Y-%m-%dT%H:00:00Z")
            if bucket_kind == "hour"
            else event.occurred_at.strftime("%Y-%m-%d")
        )
        key = (
            bucket_at,
            _sql_dimension(event.tenant_ref, "unassigned"),
            _sql_dimension(event.department_id, "unassigned"),
            _sql_dimension(event.workspace_id, "unassigned"),
            _sql_dimension(event.agent_id, "unrecorded"),
            _sql_dimension(event.deployment or event.model, "unrecorded"),
        )
        grouped[key].append(event)

    result: list[FinOpsRollup] = []
    for key, rows in grouped.items():
        bucket_at, tenant_ref, department_id, workspace_id, agent_id, deployment = key
        tokens = [row.tokens.total for row in rows if row.tokens.total is not None]
        costs = [row.estimated_cost.amount for row in rows if row.estimated_cost.amount is not None]
        latencies = sorted(row.latency_ms for row in rows if row.latency_ms is not None)
        avoided_tokens = [
            row.cache.avoided_tokens
            for row in rows
            if row.result_cache.state == "hit"
            and row.cache.avoided_tokens is not None
        ]
        result.append(
            FinOpsRollup(
                bucket_kind=bucket_kind,
                bucket_at=bucket_at,
                tenant_ref=tenant_ref,
                department_id=department_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                model_deployment=deployment,
                request_count=len(rows),
                failure_count=sum(row.status == "failed" for row in rows),
                total_tokens=sum(tokens) if tokens else None,
                estimated_cost=round(sum(costs), 10) if costs else None,
                p50_latency_ms=_nearest_rank(latencies, 0.50),
                p95_latency_ms=_nearest_rank(latencies, 0.95),
                apim_governed_count=sum(row.gateway_coverage == "apim_governed" for row in rows),
                unpriced_count=len(rows) - len(costs),
                cache_hit_count=sum(row.result_cache.state == "hit" for row in rows),
                cache_miss_count=sum(row.result_cache.state == "miss" for row in rows),
                cache_bypassed_count=sum(row.result_cache.state == "bypassed" for row in rows),
                cache_unavailable_count=sum(row.result_cache.state == "unavailable" for row in rows),
                cache_avoided_tokens=sum(avoided_tokens) if avoided_tokens else None,
            )
        )
    return sorted(
        result,
        key=lambda item: (
            item.bucket_at,
            item.tenant_ref,
            item.department_id,
            item.workspace_id,
            item.agent_id,
            item.model_deployment,
        ),
    )


def _sql_dimension(value: str | None, fallback: str) -> str:
    return str(value or fallback).strip().casefold()


def _nearest_rank(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    index = max(0, min(len(values) - 1, math.ceil(fraction * len(values)) - 1))
    return values[index]
