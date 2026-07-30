from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.finops.demo_seed_repository import InMemoryDemoSeedRepository
from backend.finops.demo_workspace_seed import seed_demo_workspace
from backend.finops.member_budget_repository import InMemoryMemberBudgetRepository
from backend.finops.repository import InMemoryFinOpsRepository


NOW = datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc)


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

    assert first.event_count >= 120
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

    persisted = ledger.list_events(
        tenant_ref="tenant_demo",
        workspace_ids=("ws-demo",),
        from_value="2026-06-01T00:00:00Z",
        to_value="2026-08-01T00:00:00Z",
    )
    assert len(persisted) == first.event_count


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
