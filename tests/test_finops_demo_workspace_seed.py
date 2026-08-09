from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.finops.demo_seed_repository import InMemoryDemoSeedRepository
from backend.finops.demo_workspace_seed import seed_demo_workspace
from backend.finops.anomalies import AnomalyEvaluationInput, evaluate_default_anomalies
from backend.finops.member_budget_repository import InMemoryMemberBudgetRepository
from backend.finops.member_budgets import MemberBudget
from backend.finops.repository import InMemoryFinOpsRepository


NOW = datetime(2026, 8, 7, 8, 0, tzinfo=timezone.utc)


def test_seed_is_workspace_bounded_and_idempotent() -> None:
    ledger = InMemoryFinOpsRepository()
    seeds = InMemoryDemoSeedRepository()

    first = seed_demo_workspace(
        ledger,
        seeds,
        tenant_ref="tenant_demo",
        workspace_id="ws-demo",
        allowed_workspace_id="ws-demo",
        now=NOW,
    )
    second = seed_demo_workspace(
        ledger,
        seeds,
        tenant_ref="tenant_demo",
        workspace_id="ws-demo",
        allowed_workspace_id="ws-demo",
        now=NOW,
    )

    assert 2400 <= first.event_count <= 2500
    assert first.created == first.event_count
    assert first.updated == 0
    assert second.created == 0
    assert second.updated == first.event_count
    assert {event.workspace_id for event in first.events} == {"ws-demo"}
    assert {event.cache.state for event in first.events} >= {
        "hit",
        "miss",
        "bypassed",
    }
    assert len(
        {
            event.estimated_cost.amount
            for event in first.events
            if event.estimated_cost.amount is not None
        }
    ) >= 8
    assert len({event.agent_id for event in first.events}) >= 6
    assert len({event.model for event in first.events}) >= 4
    occurred_at = [event.occurred_at for event in first.events]
    assert max(occurred_at) <= NOW
    assert min(occurred_at) >= datetime(2026, 7, 9, tzinfo=timezone.utc)
    total_cost = sum(
        event.estimated_cost.amount or 0
        for event in first.events
    )
    assert 400 <= total_cost <= 550
    daily_costs: dict[str, float] = {}
    for event in first.events:
        day = event.occurred_at.date().isoformat()
        daily_costs[day] = daily_costs.get(day, 0) + (
            event.estimated_cost.amount or 0
        )
    assert len(daily_costs) == 30
    assert len({round(value, 2) for value in daily_costs.values()}) >= 12
    assert first.model_routing_policy == {
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

    persisted = ledger.list_events(
        tenant_ref="tenant_demo",
        workspace_ids=("ws-demo",),
        from_value="2026-07-01T00:00:00Z",
        to_value="2026-08-08T00:00:00Z",
    )
    assert len(persisted) == first.event_count


def test_seed_batch_upgrade_removes_retired_owned_request_facts() -> None:
    ledger = InMemoryFinOpsRepository()
    seeds = InMemoryDemoSeedRepository()
    result = seed_demo_workspace(
        ledger,
        seeds,
        tenant_ref="tenant_demo",
        workspace_id="ws-demo",
        allowed_workspace_id="ws-demo",
        now=NOW,
    )

    retained = result.events[0]
    seeds.replace_batch_events(
        tenant_ref="tenant_demo",
        workspace_id="ws-demo",
        batch="operations-v3",
        events=(retained,),
        event_repository=ledger,
    )

    persisted = ledger.list_events(
        tenant_ref="tenant_demo",
        workspace_ids=("ws-demo",),
        from_value="2026-07-01T00:00:00Z",
        to_value="2026-08-08T00:00:00Z",
    )
    assert [event.request_ref for event in persisted] == [
        retained.request_ref
    ]
    assert seeds.list_request_refs(
        tenant_ref="tenant_demo",
        workspace_id="ws-demo",
        batch="operations-v1",
    ) == ()
    assert seeds.list_request_refs(
        tenant_ref="tenant_demo",
        workspace_id="ws-demo",
        batch="operations-v3",
    ) == (retained.request_ref,)


def test_seed_contains_a_repeat_analysis_miss_to_hit_chain() -> None:
    result = seed_demo_workspace(
        InMemoryFinOpsRepository(),
        InMemoryDemoSeedRepository(),
        tenant_ref="tenant_demo",
        workspace_id="ws-demo",
        allowed_workspace_id="ws-demo",
        now=NOW,
    )

    repeat_chain = [
        event
        for event in result.events
        if event.route == "repeat-analysis"
    ]

    assert [event.cache.state for event in repeat_chain] == ["miss", "hit"]
    assert repeat_chain[1].cache.avoided_tokens
    assert repeat_chain[1].estimated_cost.amount < repeat_chain[0].estimated_cost.amount
    assert repeat_chain[1].run_id != repeat_chain[0].run_id


def test_seed_has_distinct_cache_evidence_and_priced_operational_coverage() -> None:
    result = seed_demo_workspace(
        InMemoryFinOpsRepository(),
        InMemoryDemoSeedRepository(),
        tenant_ref="tenant_demo",
        workspace_id="ws-demo",
        allowed_workspace_id="ws-demo",
        now=NOW,
    )

    cache_states = [event.cache.state for event in result.events]
    eligible = [event for event in result.events if event.cache.eligible]
    hit_count = cache_states.count("hit")
    assert hit_count >= 8
    assert cache_states.count("miss") >= 20
    assert cache_states.count("bypassed") >= 20
    assert cache_states.count("unavailable") >= 4
    assert len(eligible) >= 30
    assert hit_count / len(eligible) >= 0.15
    assert sum(
        1 for event in result.events
        if event.estimated_cost.amount is not None
    ) >= 120
    assert any(
        event.estimated_cost.amount is None
        and event.estimated_cost.status == "unavailable"
        for event in result.events
    )
    assert len({event.model for event in result.events}) == 4
    assert len({event.agent_id for event in result.events}) == 6
    assert len({event.department_id for event in result.events}) == 4
    assert len({event.latency_ms for event in result.events}) >= 20


def test_seed_drives_distinct_demo_risks_with_relevant_request_evidence() -> None:
    result = seed_demo_workspace(
        InMemoryFinOpsRepository(),
        InMemoryDemoSeedRepository(),
        tenant_ref="tenant_demo",
        workspace_id="ws-demo",
        allowed_workspace_id="ws-demo",
        now=NOW,
    )

    findings = evaluate_default_anomalies(
        AnomalyEvaluationInput(
            events=list(result.events),
            trailing_token_median=1120,
        )
    )
    by_policy = {item.policy_type: item for item in findings}

    assert {
        "error_rate",
        "p95_latency",
        "token_spike",
        "apim_coverage",
        "unpriced_requests",
        "cache_hit_rate",
    } <= set(by_policy)
    assert all(item.evidence_refs for item in by_policy.values())
    assert len({
        tuple(item.evidence_refs)
        for item in by_policy.values()
    }) >= 4
    assert len({
        item.evidence_refs[0]
        for item in by_policy.values()
    }) == 6
    events_by_ref = {event.request_ref: event for event in result.events}
    runs_by_id = {item["run_id"]: item for item in result.run_evidence}
    primary_messages = {
        runs_by_id[events_by_ref[item.evidence_refs[0]].run_id]["message"]
        for item in by_policy.values()
    }
    assert len(primary_messages) == 6


def test_seed_emits_versioned_roi_scenario_and_distinct_outcome_evidence() -> None:
    roi_writes: list[tuple[str, dict[str, object], str]] = []
    outcome_writes: list[tuple[str, tuple[dict[str, object], ...], str]] = []
    run_writes: list[tuple[str, tuple[dict[str, object], ...], str]] = []

    result = seed_demo_workspace(
        InMemoryFinOpsRepository(),
        InMemoryDemoSeedRepository(),
        tenant_ref="tenant_demo",
        workspace_id="ws-demo",
        allowed_workspace_id="ws-demo",
        roi_scenario_writer=lambda workspace_id, payload, *, seed_key: roi_writes.append(
            (workspace_id, payload, seed_key)
        ),
        outcome_events_writer=lambda workspace_id, payload, *, seed_key: outcome_writes.append(
            (workspace_id, payload, seed_key)
        ),
        run_evidence_writer=lambda workspace_id, payload, *, seed_key: run_writes.append(
            (workspace_id, payload, seed_key)
        ),
        now=NOW,
    )

    assert result.roi_scenario["evaluation_months"] == 12
    assert result.roi_scenario["seed_batch"] == "operations-v3"
    assert result.roi_scenario["model_cost"] == 450
    assert [item["verification_state"] for item in result.outcome_events] == [
        "unverified",
        "unverified",
    ]
    assert len({item["metric_name"] for item in result.outcome_events}) == 2
    assert roi_writes[0][0] == outcome_writes[0][0] == "ws-demo"
    assert run_writes[0][0] == "ws-demo"
    assert len(result.run_evidence) == 24
    artifact_specs = [
        item["artifact"]
        for item in result.run_evidence
        if isinstance(item.get("artifact"), dict)
    ]
    assert [item["kind"] for item in artifact_specs] == [
        "pilot_plan",
        "action_plan",
    ]
    assert all(str(item["markdown"]).startswith("# ") for item in artifact_specs)
    assert {
        item["message"] for item in result.run_evidence
    } >= {
        "批量分析本周客户反馈并生成归因摘要",
        "重新分析相同数据并检查能否复用上次结果",
        "评估候选模型在机会识别任务中的质量和响应速度",
        "提取高价值客户机会并生成下一步建议",
    }


def test_seed_adds_workspace_display_subjects_and_idempotent_budgets() -> None:
    budgets = InMemoryMemberBudgetRepository()

    first = seed_demo_workspace(
        InMemoryFinOpsRepository(),
        InMemoryDemoSeedRepository(),
        tenant_ref="tenant_demo",
        workspace_id="ws-demo",
        allowed_workspace_id="ws-demo",
        budget_repository=budgets,
        hmac_secret="test-secret",
        now=NOW,
    )
    second = seed_demo_workspace(
        InMemoryFinOpsRepository(),
        InMemoryDemoSeedRepository(),
        tenant_ref="tenant_demo",
        workspace_id="ws-demo",
        allowed_workspace_id="ws-demo",
        budget_repository=budgets,
        hmac_secret="test-secret",
        now=NOW,
    )

    subjects = budgets.list_budget_subjects("tenant_demo", "ws-demo")
    rows = budgets.list_budgets("tenant_demo")
    assert len(subjects) == 4
    assert {subject.display_name for subject in subjects} == {
        "林晓 · 财务负责人",
        "陈屿 · 产品负责人",
        "周宁 · 交付负责人",
        "苏禾 · 运营负责人",
    }
    assert len(rows) == 3
    assert any(
        row.amount_usd == 200 and row.thresholds_pct == (80, 95)
        for row in rows
    )
    assert {event.actor_ref for event in first.events} <= {
        subject.subject_ref for subject in subjects
    }
    assert [row.revision for row in rows] == [
        row.revision for row in budgets.list_budgets("tenant_demo")
    ]
    assert first.event_count == second.event_count


def test_seed_never_overwrites_an_administrator_modified_member_budget() -> None:
    budgets = InMemoryMemberBudgetRepository()
    seed_demo_workspace(
        InMemoryFinOpsRepository(),
        InMemoryDemoSeedRepository(),
        tenant_ref="tenant_demo",
        workspace_id="ws-demo",
        allowed_workspace_id="ws-demo",
        budget_repository=budgets,
        hmac_secret="test-secret",
        now=NOW,
    )
    current = budgets.get_budget("tenant_demo", "budget_demo_1")
    assert current is not None
    budgets.save_budget(
        "tenant_demo",
        MemberBudget(
            **current.model_dump(exclude={"amount_usd", "revision", "updated_by_ref", "updated_at"}),
            amount_usd=999,
            revision=current.revision + 1,
            updated_by_ref="actor-admin",
            updated_at=NOW.replace(hour=9),
        ),
        base_revision=current.revision,
    )

    seed_demo_workspace(
        InMemoryFinOpsRepository(),
        InMemoryDemoSeedRepository(),
        tenant_ref="tenant_demo",
        workspace_id="ws-demo",
        allowed_workspace_id="ws-demo",
        budget_repository=budgets,
        hmac_secret="test-secret",
        now=NOW.replace(hour=10),
    )

    preserved = budgets.get_budget("tenant_demo", "budget_demo_1")
    assert preserved is not None
    assert preserved.amount_usd == 999
    assert preserved.updated_by_ref == "actor-admin"
    assert preserved.revision == current.revision + 1


def test_seed_rejects_non_allowlisted_workspace_without_writes() -> None:
    ledger = InMemoryFinOpsRepository()
    seeds = InMemoryDemoSeedRepository()

    with pytest.raises(PermissionError, match="demo workspace"):
        seed_demo_workspace(
            ledger,
            seeds,
            tenant_ref="tenant_demo",
            workspace_id="ws-other",
            allowed_workspace_id="ws-demo",
            now=NOW,
        )

    assert seeds.list_request_refs(
        tenant_ref="tenant_demo",
        workspace_id="ws-other",
        batch="operations-v1",
    ) == ()
