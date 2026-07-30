from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from .demo_seed_repository import DemoSeedRepository
from .models import FinOpsRequestEvent


_AGENTS = (
    "Product Architect",
    "Delivery Engineer",
    "Close Analyst",
    "Checkout Copilot",
    "Compliance Reviewer",
    "Support Triage",
)
_MODELS = (
    "gpt-5.6-terra",
    "gpt-5.1",
    "deepseek-chat",
    "gpt-4.1-mini",
)
_ACTORS = (
    "member_finance_admin",
    "member_product_owner",
    "member_delivery_lead",
    "member_operations",
)
_DEPARTMENTS = ("Finance", "AI Platform", "Delivery", "Operations")
_PRICE_FACTORS = (0.0000025, 0.0000060, 0.0000016, 0.0000009)


@dataclass(frozen=True)
class DemoSeedResult:
    batch: str
    event_count: int
    created: int
    updated: int
    events: tuple[FinOpsRequestEvent, ...]


def seed_demo_workspace(
    repository: Any,
    seed_repository: DemoSeedRepository,
    *,
    tenant_ref: str,
    workspace_id: str,
    allowed_workspace_id: str,
    batch: str = "operations-v1",
    now: datetime | None = None,
) -> DemoSeedResult:
    clean_workspace_id = str(workspace_id or "").strip()
    if not clean_workspace_id or clean_workspace_id != str(allowed_workspace_id or "").strip():
        raise PermissionError("demo workspace is not allowlisted")
    clean_tenant_ref = str(tenant_ref or "").strip()
    if not clean_tenant_ref:
        raise ValueError("tenant_ref is required")
    anchor = _utc(now or datetime.now(timezone.utc))
    events = tuple(_scenario_events(clean_tenant_ref, clean_workspace_id, batch, anchor))
    created, updated = seed_repository.replace_batch(
        tenant_ref=clean_tenant_ref,
        workspace_id=clean_workspace_id,
        batch=batch,
        request_refs=tuple(event.request_ref for event in events),
    )
    repository.upsert_events(events)
    return DemoSeedResult(
        batch=batch,
        event_count=len(events),
        created=created,
        updated=updated,
        events=events,
    )


def _scenario_events(
    tenant_ref: str,
    workspace_id: str,
    batch: str,
    now: datetime,
) -> Iterable[FinOpsRequestEvent]:
    for index in range(120):
        day_offset = 29 - index // 4
        slot = index % 4
        occurred_at = now - timedelta(
            days=day_offset,
            hours=(3 - slot) * 3,
            minutes=(index * 7) % 53,
        )
        agent_index = index % len(_AGENTS)
        model_index = (index + index // 5) % len(_MODELS)
        actor_index = (index // 5) % len(_ACTORS)
        input_tokens = 320 + ((index * 137) % 4200)
        output_tokens = 80 + ((index * 71) % 1300)
        reasoning_tokens = 40 + ((index * 29) % 620) if index % 3 == 0 else 0
        total_tokens = input_tokens + output_tokens + reasoning_tokens
        cache_state = ("miss", "hit", "bypassed", "hit", "miss")[index % 5]
        eligible = cache_state in {"hit", "miss"}
        avoided_tokens = int(input_tokens * 0.72) if cache_state == "hit" else None
        failed = index in {17, 44, 73, 91, 106}
        unpriced = index in {11, 58, 97}
        cost = None if unpriced else round(
            (
                input_tokens
                + output_tokens * 2.4
                + reasoning_tokens * 1.7
                - (avoided_tokens or 0) * 0.72
            )
            * _PRICE_FACTORS[model_index],
            8,
        )
        yield _event(
            tenant_ref=tenant_ref,
            workspace_id=workspace_id,
            batch=batch,
            ordinal=index,
            occurred_at=occurred_at,
            run_id=f"run_demo_{index:04d}",
            route=("analysis", "conversation", "artifact", "review")[index % 4],
            agent_id=_AGENTS[agent_index],
            model=_MODELS[model_index],
            actor_ref=_ACTORS[actor_index],
            department_id=_DEPARTMENTS[actor_index],
            status="failed" if failed else "succeeded",
            error_category=("provider_5xx" if index % 2 else "client_4xx") if failed else None,
            latency_ms=650 + ((index * 283) % 4300),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens or None,
            cached_input_tokens=avoided_tokens,
            total_tokens=total_tokens,
            cache_state=cache_state,
            eligible=eligible,
            avoided_tokens=avoided_tokens,
            gateway_coverage=("apim_governed", "app_observed", "unmanaged")[index % 3],
            cost=cost,
            priced=not unpriced,
        )

    chain_time = now - timedelta(minutes=25)
    for offset, cache_state in enumerate(("miss", "hit")):
        input_tokens = 6400
        output_tokens = 920
        avoided_tokens = 5400 if cache_state == "hit" else None
        yield _event(
            tenant_ref=tenant_ref,
            workspace_id=workspace_id,
            batch=batch,
            ordinal=120 + offset,
            occurred_at=chain_time + timedelta(minutes=offset * 6),
            run_id=f"run_repeat_analysis_{offset + 1}",
            route="repeat-analysis",
            agent_id="Product Architect",
            model="gpt-5.6-terra",
            actor_ref="member_product_owner",
            department_id="AI Platform",
            status="succeeded",
            error_category=None,
            latency_ms=2480 if cache_state == "miss" else 210,
            input_tokens=input_tokens,
            output_tokens=output_tokens if cache_state == "miss" else 60,
            reasoning_tokens=480 if cache_state == "miss" else None,
            cached_input_tokens=avoided_tokens,
            total_tokens=(input_tokens + output_tokens + (480 if cache_state == "miss" else 60)),
            cache_state=cache_state,
            eligible=True,
            avoided_tokens=avoided_tokens,
            gateway_coverage="apim_governed",
            cost=0.0714 if cache_state == "miss" else 0.0068,
            priced=True,
        )


def _event(
    *,
    tenant_ref: str,
    workspace_id: str,
    batch: str,
    ordinal: int,
    occurred_at: datetime,
    run_id: str,
    route: str,
    agent_id: str,
    model: str,
    actor_ref: str,
    department_id: str,
    status: str,
    error_category: str | None,
    latency_ms: int,
    input_tokens: int,
    output_tokens: int,
    reasoning_tokens: int | None,
    cached_input_tokens: int | None,
    total_tokens: int,
    cache_state: str,
    eligible: bool,
    avoided_tokens: int | None,
    gateway_coverage: str,
    cost: float | None,
    priced: bool,
) -> FinOpsRequestEvent:
    request_ref = _opaque_ref("req", batch, workspace_id, str(ordinal))
    return FinOpsRequestEvent.model_validate(
        {
            "request_ref": request_ref,
            "occurred_at": occurred_at,
            "call_class": "model",
            "tenant_ref": tenant_ref,
            "department_id": department_id,
            "workspace_id": workspace_id,
            "actor_ref": actor_ref,
            "run_id": run_id,
            "agent_id": agent_id,
            "model": model,
            "deployment": model,
            "route": route,
            "execution_kind": "maf_agent",
            "status": status,
            "error_category": error_category,
            "latency_ms": latency_ms,
            "tokens": {
                "input": input_tokens,
                "output": output_tokens,
                "cached_input": cached_input_tokens,
                "reasoning": reasoning_tokens,
                "total": total_tokens,
            },
            "cache": {
                "state": cache_state,
                "eligible": eligible,
                "avoided_tokens": avoided_tokens,
            },
            "result_cache": {
                "eligible": eligible,
                "state": cache_state,
                "reason": "eligible" if cache_state in {"hit", "miss"} else "not_recorded",
                "lookup_latency_ms": 35 if cache_state == "hit" else None,
                "policy_revision": 1,
                "source_result_version": (
                    "result_repeat_analysis_1"
                    if route == "repeat-analysis" and cache_state == "hit"
                    else None
                ),
            },
            "gateway_coverage": gateway_coverage,
            "estimated_cost": {
                "amount": cost,
                "currency": "USD",
                "status": "estimated" if priced else "unavailable",
                "price_card_revision": "official-demo-v1" if priced else None,
                "official_price_key": f"{model}:global" if priced else None,
            },
            "evidence_state": "observed" if priced else "partial",
            "correlation_ref": _opaque_ref("corr", batch, workspace_id, str(ordinal)),
            "usage_source": "provider",
            "streaming": ordinal % 4 == 1,
        }
    )


def _opaque_ref(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
