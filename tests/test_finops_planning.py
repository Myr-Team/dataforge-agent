from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from backend.finops.planning import (
    BudgetDefinition,
    FinOpsPlanningService,
    InMemoryPlanningRepository,
    allocate_workspaces,
)


def test_budget_progress_forecasts_from_priced_evidence_and_thresholds() -> None:
    service = FinOpsPlanningService(InMemoryPlanningRepository())
    budget = service.create_budget(
        tenant_ref="tenant-a",
        actor_ref="actor-a",
        value=BudgetDefinition(
            name="七月平台预算",
            scope_type="workspace",
            scope_id="ws-a",
            period_start="2026-07-01T00:00:00Z",
            period_end="2026-08-01T00:00:00Z",
            amount=100,
        ),
    )

    progress = service.progress(
        budget,
        spent_amount=40,
        priced_requests=80,
        total_requests=100,
        as_of=datetime(2026, 7, 16, 12, tzinfo=timezone.utc),
    )

    assert progress.usage_pct == 40
    assert progress.forecast_amount == pytest.approx(80)
    assert progress.forecast_status == "estimated"
    assert progress.confidence == "partial"
    assert progress.threshold_state == "normal"


def test_budget_progress_marks_warning_and_critical_without_inventing_forecast() -> None:
    budget = BudgetDefinition(
        name="部门预算",
        scope_type="department",
        scope_id="finance",
        period_start="2026-07-01T00:00:00Z",
        period_end="2026-08-01T00:00:00Z",
        amount=100,
    )
    service = FinOpsPlanningService(InMemoryPlanningRepository())

    warning = service.progress(budget, spent_amount=80, priced_requests=10, total_requests=10)
    critical = service.progress(budget, spent_amount=101, priced_requests=10, total_requests=10)
    unavailable = service.progress(budget, spent_amount=None, priced_requests=0, total_requests=10)

    assert warning.threshold_state == "warning"
    assert critical.threshold_state == "critical"
    assert unavailable.forecast_amount is None
    assert unavailable.forecast_status == "unavailable"


def test_workspace_allocation_is_unique_and_unmapped_is_unassigned() -> None:
    rows = allocate_workspaces(
        ["ws-a", "ws-b", "ws-a", "ws-c"],
        {"ws-a": "engineering", "ws-b": "finance"},
    )

    assert rows == {
        "engineering": ["ws-a"],
        "finance": ["ws-b"],
        "unassigned": ["ws-c"],
    }


def test_budget_rejects_scope_without_required_identifier() -> None:
    with pytest.raises(ValidationError):
        BudgetDefinition(
            name="无效预算",
            scope_type="workspace",
            period_start="2026-07-01T00:00:00Z",
            period_end="2026-08-01T00:00:00Z",
            amount=100,
        )
