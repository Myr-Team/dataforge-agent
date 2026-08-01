from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from backend.finops.member_budget_repository import (
    InMemoryMemberBudgetRepository,
    MemberBudgetConflictError,
)
from backend.finops.member_budgets import (
    BudgetAlert,
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


@pytest.mark.parametrize("period_key", ("2026-00", "2026-13", "2026-1", "2026-001"))
def test_budget_alert_requires_a_real_utc_calendar_month(period_key: str) -> None:
    now = datetime(2026, 7, 29, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="period_key"):
        BudgetAlert(
            alert_id="alert_safe",
            tenant_ref="tenant_safe",
            budget_id="budget_safe",
            actor_ref="actor_safe",
            period_key=period_key,
            threshold_pct=80,
            budget_amount_usd=Decimal("200"),
            estimated_spend_usd=Decimal("190"),
            pricing_coverage_pct=95,
            budget_revision=1,
            notification_revision=1,
            delivery_state="pending",
            triggered_at=now,
            updated_at=now,
        )


@pytest.mark.parametrize("period_key", ("2026-01", "2026-12"))
def test_budget_alert_accepts_utc_calendar_month_boundaries(period_key: str) -> None:
    now = datetime(2026, 7, 29, tzinfo=timezone.utc)
    value = BudgetAlert(
        alert_id="alert_safe",
        tenant_ref="tenant_safe",
        budget_id="budget_safe",
        actor_ref="actor_safe",
        period_key=period_key,
        threshold_pct=80,
        budget_amount_usd=Decimal("200"),
        estimated_spend_usd=Decimal("190"),
        pricing_coverage_pct=95,
        budget_revision=1,
        notification_revision=1,
        delivery_state="pending",
        triggered_at=now,
        updated_at=now,
    )
    assert value.period_key == period_key


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


def test_repository_rejects_second_enabled_budget_for_the_same_member() -> None:
    repository = InMemoryMemberBudgetRepository()
    repository.save_budget("tenant_a", _budget(revision=1), base_revision=0)

    with pytest.raises(MemberBudgetConflictError, match="active member budget"):
        repository.save_budget(
            "tenant_a",
            _budget(revision=1, budget_id="second_budget"),
            base_revision=0,
        )


def test_inmemory_repository_reports_missing_monthly_ledger_as_unavailable() -> None:
    repository = InMemoryMemberBudgetRepository()

    assert repository.summarize_month(
        "tenant_a",
        datetime(2026, 7, 1, tzinfo=timezone.utc),
        datetime(2026, 8, 1, tzinfo=timezone.utc),
        ("ws-a",),
    ) == {}


def test_repository_treats_only_matching_alert_threshold_as_idempotent() -> None:
    repository = InMemoryMemberBudgetRepository()
    now = datetime(2026, 7, 29, tzinfo=timezone.utc)
    first = BudgetAlert(
        alert_id="alert_safe",
        tenant_ref="tenant_safe",
        budget_id="budget_safe",
        actor_ref="actor_safe",
        period_key="2026-07",
        threshold_pct=80,
        budget_amount_usd=Decimal("200"),
        estimated_spend_usd=Decimal("190"),
        pricing_coverage_pct=95,
        budget_revision=1,
        notification_revision=1,
        delivery_state="pending",
        triggered_at=now,
        updated_at=now,
    )
    assert repository.claim_alert(first) is True
    assert repository.claim_alert(first.model_copy(update={"alert_id": "other_alert"})) is False

    with pytest.raises(MemberBudgetConflictError, match="alert id"):
        repository.claim_alert(first.model_copy(update={"threshold_pct": 95}))
