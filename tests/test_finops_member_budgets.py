from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from backend.finops.member_budget_repository import (
    InMemoryMemberBudgetRepository,
    MemberBudgetConflictError,
)
from backend.finops.member_budgets import (
    MemberBudget,
    MemberBudgetDraft,
    MemberCostSummary,
)


def _budget(*, revision: int, amount: str = "200", budget_id: str = "budget_safe") -> MemberBudget:
    now = datetime(2026, 7, 29, tzinfo=timezone.utc)
    return MemberBudget(
        member_ref="actor_safe",
        amount_usd=Decimal(amount),
        thresholds_pct=(80, 95, 100),
        enabled=True,
        budget_id=budget_id,
        revision=revision,
        created_by_ref="admin_safe",
        updated_by_ref="admin_safe",
        created_at=now,
        updated_at=now,
    )


def test_member_budget_requires_sorted_unique_thresholds() -> None:
    value = MemberBudgetDraft(
        member_ref="actor_safe",
        amount_usd=200,
        thresholds_pct=[80, 95, 100],
        enabled=True,
    )

    assert value.thresholds_pct == (80, 95, 100)

    with pytest.raises(ValueError, match="thresholds"):
        MemberBudgetDraft(
            member_ref="actor_safe",
            amount_usd=200,
            thresholds_pct=[95, 80, 95],
            enabled=True,
        )


def test_member_cost_summary_preserves_unpriced_coverage() -> None:
    value = MemberCostSummary(
        actor_ref="actor_safe",
        estimated_spend_usd=190,
        priced_requests=19,
        total_requests=20,
    )

    assert value.pricing_coverage_pct == 95
    assert value.data_status == "partial"


def test_repository_isolates_member_budgets_and_rejects_stale_revisions() -> None:
    repository = InMemoryMemberBudgetRepository()
    created = repository.save_budget("tenant_a", _budget(revision=1), base_revision=0)

    assert repository.get_budget("tenant_a", created.budget_id) == created
    assert repository.get_budget("tenant_b", created.budget_id) is None

    with pytest.raises(MemberBudgetConflictError):
        repository.save_budget("tenant_a", _budget(revision=2, amount="201"), base_revision=0)


def test_repository_retains_disabled_budget_history_and_enforces_one_active_member_budget() -> None:
    repository = InMemoryMemberBudgetRepository()
    first = repository.save_budget("tenant_a", _budget(revision=1), base_revision=0)
    disabled = repository.disable_budget("tenant_a", first.budget_id, base_revision=1, updated_by_ref="admin_safe")

    assert disabled.enabled is False
    assert repository.get_budget("tenant_a", first.budget_id) == disabled
    replacement = repository.save_budget(
        "tenant_a",
        _budget(revision=1, budget_id="replacement_safe"),
        base_revision=0,
    )
    assert replacement.enabled is True
    assert len(repository.list_budgets("tenant_a", include_disabled=True)) == 2
