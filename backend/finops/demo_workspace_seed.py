from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable

from .budget_subjects import BudgetSubject, budget_subject_ref
from .demo_seed_repository import DemoSeedRepository
from .member_budgets import MemberBudget
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
_SUBJECTS = (
    ("林晓 · 财务负责人", "财务", "gpt-5.6-terra"),
    ("陈屿 · 产品负责人", "AI 平台", "gpt-5.1"),
    ("周宁 · 交付负责人", "交付", "deepseek-chat"),
    ("苏禾 · 运营负责人", "运营", "gpt-4.1-mini"),
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
    roi_scenario: dict[str, Any]
    outcome_events: tuple[dict[str, Any], ...]
    run_evidence: tuple[dict[str, Any], ...]
    model_routing_policy: dict[str, Any]


def demo_operations_model_policy() -> dict[str, Any]:
    """Return the audited-settings payload recommended for the demo workspace.

    The seed exposes the policy but deliberately does not persist it. Applying
    it remains an Owner action through the existing audited model-routing API.
    """
    return {
        "assignments": {
            "direct_reply": {
                "primary_route_id": "analysis",
                "fallback_route_id": None,
            },
        },
        "agent_assignments": {
            "df-finops-analyst": {
                "primary_route_id": "terra",
                "fallback_route_id": "analysis",
            },
            "df-roi-analyst": {
                "primary_route_id": "terra",
                "fallback_route_id": "analysis",
            },
        },
    }


def seed_demo_workspace(
    repository: Any,
    seed_repository: DemoSeedRepository,
    *,
    tenant_ref: str,
    workspace_id: str,
    allowed_workspace_id: str,
    batch: str = "operations-v3",
    budget_repository: Any | None = None,
    hmac_secret: str | None = None,
    roi_scenario_writer: Any | None = None,
    outcome_events_writer: Any | None = None,
    run_evidence_writer: Any | None = None,
    now: datetime | None = None,
) -> DemoSeedResult:
    clean_workspace_id = str(workspace_id or "").strip()
    if not clean_workspace_id or clean_workspace_id != str(allowed_workspace_id or "").strip():
        raise PermissionError("demo workspace is not allowlisted")
    clean_tenant_ref = str(tenant_ref or "").strip()
    if not clean_tenant_ref:
        raise ValueError("tenant_ref is required")
    anchor = _utc(now or datetime.now(timezone.utc))
    subject_secret = str(hmac_secret or clean_tenant_ref).strip()
    subjects = tuple(
        BudgetSubject(
            subject_ref=budget_subject_ref(
                workspace_id=clean_workspace_id,
                display_name=display_name,
                secret=subject_secret,
            ),
            workspace_id=clean_workspace_id,
            display_name=display_name,
            department_label=department,
            primary_model=primary_model,
            enabled=True,
            revision=1,
            updated_at=anchor,
        )
        for display_name, department, primary_model in _SUBJECTS
    )
    actor_refs = tuple(subject.subject_ref for subject in subjects)
    events = tuple(
        _scenario_events(
            clean_tenant_ref,
            clean_workspace_id,
            batch,
            anchor,
            actor_refs=actor_refs,
        )
    )
    created, updated = seed_repository.replace_batch_events(
        tenant_ref=clean_tenant_ref,
        workspace_id=clean_workspace_id,
        batch=batch,
        events=events,
        event_repository=repository,
    )
    if budget_repository is not None:
        budget_repository.upsert_budget_subjects(clean_tenant_ref, subjects)
        _seed_budgets(
            budget_repository,
            tenant_ref=clean_tenant_ref,
            subjects=subjects,
            batch=batch,
            now=anchor,
        )
    roi_scenario = _roi_scenario_seed(batch)
    outcome_events = _outcome_event_seeds(batch, anchor)
    run_evidence = _run_evidence_seeds(events, batch)
    if roi_scenario_writer is not None:
        roi_scenario_writer(
            clean_workspace_id,
            roi_scenario,
            seed_key=batch,
        )
    if run_evidence_writer is not None:
        run_evidence_writer(
            clean_workspace_id,
            run_evidence,
            seed_key=batch,
        )
    if outcome_events_writer is not None:
        outcome_events_writer(
            clean_workspace_id,
            outcome_events,
            seed_key=batch,
        )
    return DemoSeedResult(
        batch=batch,
        event_count=len(events),
        created=created,
        updated=updated,
        events=events,
        roi_scenario=roi_scenario,
        outcome_events=outcome_events,
        run_evidence=run_evidence,
        model_routing_policy=demo_operations_model_policy(),
    )


def _scenario_events(
    tenant_ref: str,
    workspace_id: str,
    batch: str,
    now: datetime,
    *,
    actor_refs: tuple[str, ...],
) -> Iterable[FinOpsRequestEvent]:
    ordinal = 0
    anchor_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    cache_pattern = (
        "hit", "miss", "miss", "miss", "miss",
        "hit", "miss", "miss", "miss", "miss",
        "hit", "miss", "miss", "miss", "miss",
        "miss", "bypassed", "bypassed", "bypassed", "unavailable",
    )
    for day_offset in range(29, -1, -1):
        daily_count = 60 + ((day_offset * 17) % 31) + ((day_offset % 5) * 2)
        day_start = anchor_day - timedelta(days=day_offset)
        minutes_available = 1430 if day_offset else max(
            1,
            int((now - day_start).total_seconds() // 60) - 60,
        )
        for slot in range(daily_count):
            index = ordinal
            occurred_at = day_start + timedelta(
                minutes=(slot * 53 + day_offset * 29) % minutes_available,
            )
            agent_index = (index + day_offset) % len(_AGENTS)
            model_index = (index + index // 7 + day_offset) % len(_MODELS)
            actor_index = (index // 5 + day_offset) % len(actor_refs)
            input_tokens = 22_000 + ((index * 1789 + day_offset * 941) % 72_000)
            output_tokens = 2_000 + ((index * 977 + slot * 331) % 16_000)
            reasoning_tokens = (
                800 + ((index * 421 + day_offset * 113) % 6_200)
                if index % 3 == 0
                else 0
            )
            total_tokens = input_tokens + output_tokens + reasoning_tokens
            cache_state = cache_pattern[index % len(cache_pattern)]
            eligible = cache_state in {"hit", "miss"}
            avoided_tokens = int(input_tokens * 0.65) if cache_state == "hit" else None
            failed = index % 29 == 17
            unpriced = index % 16 == 11
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
            coverage_bucket = index % 100
            gateway_coverage = (
                "apim_governed"
                if coverage_bucket < 92
                else "app_observed"
                if coverage_bucket < 97
                else "unmanaged"
                if coverage_bucket < 99
                else "unknown"
            )
            latency_ms = 620 + ((index * 283 + day_offset * 41) % 2_900)
            if index % 37 == 0:
                latency_ms += 2_800
            yield _event(
                tenant_ref=tenant_ref,
                workspace_id=workspace_id,
                batch=batch,
                ordinal=ordinal,
                occurred_at=occurred_at,
                run_id=f"run_demo_{index:04d}",
                route=("analysis", "conversation", "artifact", "review")[index % 4],
                agent_id=_AGENTS[agent_index],
                model=_MODELS[model_index],
                actor_ref=actor_refs[actor_index],
                department_id=_DEPARTMENTS[actor_index],
                status="failed" if failed else "succeeded",
                error_category=("provider_5xx" if index % 2 else "client_4xx") if failed else None,
                latency_ms=latency_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens or None,
                cached_input_tokens=avoided_tokens,
                total_tokens=total_tokens,
                cache_state=cache_state,
                eligible=eligible,
                avoided_tokens=avoided_tokens,
                gateway_coverage=gateway_coverage,
                cost=cost,
                priced=not unpriced,
            )
            ordinal += 1

    chain_time = now - timedelta(minutes=25)
    for offset, cache_state in enumerate(("miss", "hit")):
        input_tokens = 6400
        output_tokens = 920
        avoided_tokens = 5400 if cache_state == "hit" else None
        yield _event(
            tenant_ref=tenant_ref,
            workspace_id=workspace_id,
            batch=batch,
            ordinal=ordinal,
            occurred_at=chain_time + timedelta(minutes=offset * 6),
            run_id=f"run_repeat_analysis_{offset + 1}",
            route="repeat-analysis",
            agent_id="Product Architect",
            model="gpt-5.6-terra",
            actor_ref=actor_refs[1],
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
        ordinal += 1

    recent_start = now - timedelta(minutes=12)
    for offset in range(24):
        event_ordinal = ordinal
        actor_index = offset % len(actor_refs)
        model_index = offset % len(_MODELS)
        failed = offset in {4, 13, 21}
        unpriced = offset < 8
        cache_state = "miss" if offset < 20 else "unavailable"
        input_tokens = 30_000 if offset == 20 else 7200 + offset * 190
        output_tokens = 900 + (offset % 5) * 140
        reasoning_tokens = 680 + (offset % 4) * 120
        route = {
            7: "model-price-review",
            19: "cache-review",
            20: "token-intensive-analysis",
            21: "failed-opportunity-extraction",
            22: "latency-diagnostic",
            23: "entry-coverage-review",
        }.get(
            offset,
            (
                "batch-analysis",
                "cache-review",
                "model-evaluation",
                "opportunity-extraction",
            )[offset % 4],
        )
        yield _event(
            tenant_ref=tenant_ref,
            workspace_id=workspace_id,
            batch=batch,
            ordinal=event_ordinal,
            occurred_at=recent_start + timedelta(seconds=offset * 30),
            run_id=f"run_demo_recent_{offset:03d}",
            route=route,
            agent_id=_AGENTS[offset % len(_AGENTS)],
            model=_MODELS[model_index],
            actor_ref=actor_refs[actor_index],
            department_id=_DEPARTMENTS[actor_index],
            status="failed" if failed else "succeeded",
            error_category="provider_5xx" if failed else None,
            latency_ms=8100 if offset == 22 else 2600 + offset * 145,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            cached_input_tokens=None,
            total_tokens=input_tokens + output_tokens + reasoning_tokens,
            cache_state=cache_state,
            eligible=cache_state == "miss",
            avoided_tokens=None,
            gateway_coverage=(
                "apim_governed"
                if offset < 18
                else "app_observed"
                if offset < 21
                else "unmanaged"
            ),
            cost=None if unpriced else round(0.012 + offset * 0.0017, 8),
            priced=not unpriced,
        )
        ordinal += 1

    for day_offset in range(1, 8):
        yield _event(
            tenant_ref=tenant_ref,
            workspace_id=workspace_id,
            batch=batch,
            ordinal=ordinal,
            occurred_at=now - timedelta(days=day_offset, minutes=5),
            run_id=f"run_demo_baseline_{day_offset:02d}",
            route="scheduled-summary",
            agent_id="Support Triage",
            model="gpt-4.1-mini",
            actor_ref=actor_refs[3],
            department_id="Operations",
            status="succeeded",
            error_category=None,
            latency_ms=780 + day_offset * 20,
            input_tokens=900,
            output_tokens=220,
            reasoning_tokens=None,
            cached_input_tokens=None,
            total_tokens=1120,
            cache_state="bypassed",
            eligible=False,
            avoided_tokens=None,
            gateway_coverage="apim_governed",
            cost=0.0014,
            priced=True,
        )
        ordinal += 1


def _roi_scenario_seed(batch: str) -> dict[str, Any]:
    return {
        "title": "运营自动化测算",
        "currency": "USD",
        "hours_saved": 40,
        "hourly_value": 50,
        "avoided_loss_or_revenue": 1000,
        "implementation_cost": 6000,
        "monthly_fixed_cost": 200,
        "model_cost": 450,
        "evaluation_months": 12,
        "evidence_revision": 1,
        "seed_batch": batch,
    }


def _outcome_event_seeds(
    batch: str,
    anchor: datetime,
) -> tuple[dict[str, Any], ...]:
    return (
        {
            "metric_name": "analysis_cycle_hours",
            "unit": "hours",
            "baseline_value": 16,
            "observed_value": 11.5,
            "observed_at": (anchor - timedelta(days=4)).isoformat(),
            "provenance": "observed",
            "verification_state": "unverified",
            "source": {"run_id": "run_demo_recent_000"},
            "seed_batch": batch,
        },
        {
            "metric_name": "manual_review_hours",
            "unit": "hours",
            "baseline_value": 24,
            "observed_value": 15,
            "observed_at": (anchor - timedelta(days=2)).isoformat(),
            "provenance": "observed",
            "verification_state": "unverified",
            "source": {"run_id": "run_demo_recent_001"},
            "seed_batch": batch,
        },
    )


def _run_evidence_seeds(
    events: tuple[FinOpsRequestEvent, ...],
    batch: str,
) -> tuple[dict[str, Any], ...]:
    copy = {
        "batch-analysis": (
            "批量分析本周客户反馈并生成归因摘要",
            "已完成反馈聚类，并将高频问题归纳为交付、价格和使用体验三类。",
        ),
        "cache-review": (
            "重新分析相同数据并检查能否复用上次结果",
            "已完成重复请求检查，本次未复用既有结果，建议复核缓存键与有效期。",
        ),
        "model-evaluation": (
            "评估候选模型在机会识别任务中的质量和响应速度",
            "候选模型已完成评估，质量达到要求，但响应时间仍有优化空间。",
        ),
        "opportunity-extraction": (
            "提取高价值客户机会并生成下一步建议",
            "已识别重点机会，并按影响范围和证据完整度给出后续建议。",
        ),
        "model-price-review": (
            "使用新接入模型评审候选机会并核对成本",
            "候选机会已完成评审，当前模型尚未关联价目。",
        ),
        "token-intensive-analysis": (
            "合并多份调研材料并生成完整市场分析",
            "已完成长上下文分析，建议复核输入材料范围与重复内容。",
        ),
        "failed-opportunity-extraction": (
            "提取重点客户机会并生成行动摘要",
            "本次调用未成功，尚未形成可用回答。",
        ),
        "latency-diagnostic": (
            "批量分析本周客户反馈并检查响应耗时",
            "分析已完成，但模型响应阶段耗时偏高。",
        ),
        "entry-coverage-review": (
            "核对本周模型调用是否均由统一入口管理",
            "已发现部分调用链未完成入口关联，需要继续复核来源。",
        ),
    }
    rows = []
    for event in events:
        if not str(event.run_id or "").startswith("run_demo_recent_"):
            continue
        request_text, response_text = copy.get(
            str(event.route or ""),
            ("分析当前工作区的运营数据", "已完成运营数据分析。"),
        )
        row = {
            "run_id": event.run_id,
            "message": request_text,
            "final_text": response_text if event.status == "succeeded" else None,
            "status": (
                "completed"
                if event.status == "succeeded"
                else "failed"
            ),
            "trace_id": hashlib.sha256(
                f"{batch}:{event.run_id}:trace".encode("utf-8")
            ).hexdigest()[:32],
            "trace_agent_id": event.agent_id,
            "seed_batch": batch,
        }
        if event.run_id == "run_demo_recent_000":
            row["artifact"] = {
                "kind": "pilot_plan",
                "title": "运营优化试点计划",
                "markdown": (
                    "# 运营优化试点计划\n\n"
                    "## 目标\n基于近期调用、成本和时延证据验证优化机会。\n\n"
                    "## 验收\n复核成本归因、缓存效果和业务结果证据。\n"
                ),
            }
        elif event.run_id == "run_demo_recent_001":
            row["artifact"] = {
                "kind": "action_plan",
                "title": "运营复盘行动清单",
                "markdown": (
                    "# 运营复盘行动清单\n\n"
                    "1. 核对未计价模型映射。\n"
                    "2. 复核慢请求与失败请求证据。\n"
                    "3. 比较重复分析的缓存命中效果。\n"
                ),
            }
        rows.append(row)
    return tuple(rows)


def _seed_budgets(
    repository: Any,
    *,
    tenant_ref: str,
    subjects: tuple[BudgetSubject, ...],
    batch: str,
    now: datetime,
) -> None:
    specs = (
        (subjects[0], Decimal("200"), (80, 95)),
        (subjects[1], Decimal("320"), (75, 90)),
        (subjects[2], Decimal("150"), (80, 95)),
    )
    updated_by = f"seed_{batch}"[:128]
    for index, (subject, amount, thresholds) in enumerate(specs, start=1):
        budget_id = f"budget_demo_{index}"
        current = repository.get_budget(tenant_ref, budget_id)
        if current is not None and not (
            str(current.created_by_ref).startswith("seed_operations-")
            and str(current.updated_by_ref).startswith("seed_operations-")
        ):
            # The initializer no longer owns a budget after an administrator
            # edits it. Preserve that revision on every future seed run.
            continue
        if (
            current is not None
            and current.member_ref == subject.subject_ref
            and current.amount_usd == amount
            and current.thresholds_pct == thresholds
            and current.enabled
        ):
            continue
        revision = current.revision + 1 if current else 1
        value = MemberBudget(
            member_ref=subject.subject_ref,
            amount_usd=amount,
            thresholds_pct=thresholds,
            enabled=True,
            budget_id=budget_id,
            revision=revision,
            created_by_ref=current.created_by_ref if current else updated_by,
            updated_by_ref=updated_by,
            created_at=current.created_at if current else now,
            updated_at=now,
        )
        repository.save_budget(
            tenant_ref,
            value,
            base_revision=current.revision if current else 0,
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
