from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BudgetDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    scope_type: Literal["organization", "department", "workspace"]
    scope_id: str | None = Field(default=None, max_length=160)
    period_start: str
    period_end: str
    amount: float = Field(gt=0)
    currency: Literal["USD"] = "USD"
    warning_pct: float = Field(default=80, gt=0, le=100)
    critical_pct: float = Field(default=100, ge=100)

    @model_validator(mode="after")
    def validate_scope_and_period(self) -> "BudgetDefinition":
        if self.scope_type != "organization" and not str(self.scope_id or "").strip():
            raise ValueError("scope_id is required for department and workspace budgets")
        if self.scope_type == "organization" and self.scope_id:
            raise ValueError("organization budget cannot set scope_id")
        if _parse(self.period_start) >= _parse(self.period_end):
            raise ValueError("budget period must be ordered")
        return self


class BudgetRecord(BudgetDefinition):
    budget_id: str
    version: int = Field(default=1, ge=1)
    created_by: str
    updated_at: str


class BudgetProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    budget_id: str | None
    amount: float
    spent_amount: float | None
    usage_pct: float | None
    forecast_amount: float | None
    forecast_status: Literal["estimated", "unavailable"]
    confidence: Literal["complete", "partial", "unavailable"]
    priced_requests: int
    total_requests: int
    threshold_state: Literal["normal", "warning", "critical", "unavailable"]
    currency: Literal["USD"] = "USD"


class PlanningRepository(Protocol):
    def save_budget(self, tenant_ref: str, value: BudgetRecord) -> BudgetRecord: ...
    def get_budget(self, tenant_ref: str, budget_id: str) -> BudgetRecord | None: ...
    def list_budgets(self, tenant_ref: str) -> list[BudgetRecord]: ...


class InMemoryPlanningRepository:
    def __init__(self) -> None:
        self._lock = RLock()
        self._budgets: dict[tuple[str, str], BudgetRecord] = {}

    def save_budget(self, tenant_ref: str, value: BudgetRecord) -> BudgetRecord:
        with self._lock:
            self._budgets[(tenant_ref, value.budget_id)] = value.model_copy(deep=True)
        return value.model_copy(deep=True)

    def get_budget(self, tenant_ref: str, budget_id: str) -> BudgetRecord | None:
        with self._lock:
            value = self._budgets.get((tenant_ref, budget_id))
        return value.model_copy(deep=True) if value else None

    def list_budgets(self, tenant_ref: str) -> list[BudgetRecord]:
        with self._lock:
            values = [
                value.model_copy(deep=True)
                for (tenant, _), value in self._budgets.items()
                if tenant == tenant_ref
            ]
        return sorted(values, key=lambda item: (item.period_start, item.name))


class FinOpsPlanningService:
    def __init__(self, repository: PlanningRepository) -> None:
        self._repository = repository

    def create_budget(
        self,
        *,
        tenant_ref: str,
        actor_ref: str,
        value: BudgetDefinition,
    ) -> BudgetRecord:
        record = BudgetRecord(
            **value.model_dump(),
            budget_id=f"budget_{uuid4().hex}",
            created_by=actor_ref,
            updated_at=_iso(datetime.now(timezone.utc)),
        )
        return self._repository.save_budget(tenant_ref, record)

    def update_budget(
        self,
        *,
        tenant_ref: str,
        actor_ref: str,
        budget_id: str,
        value: BudgetDefinition,
        base_version: int,
    ) -> BudgetRecord:
        current = self._repository.get_budget(tenant_ref, budget_id)
        if current is None:
            raise KeyError(budget_id)
        if current.version != base_version:
            raise RuntimeError("budget version conflict")
        record = BudgetRecord(
            **value.model_dump(),
            budget_id=budget_id,
            version=current.version + 1,
            created_by=actor_ref,
            updated_at=_iso(datetime.now(timezone.utc)),
        )
        return self._repository.save_budget(tenant_ref, record)

    def list(self, *, tenant_ref: str) -> list[BudgetRecord]:
        return self._repository.list_budgets(tenant_ref)

    def progress(
        self,
        budget: BudgetDefinition | BudgetRecord,
        *,
        spent_amount: float | None,
        priced_requests: int,
        total_requests: int,
        as_of: datetime | None = None,
    ) -> BudgetProgress:
        usage_pct = (
            round(spent_amount / budget.amount * 100, 4)
            if spent_amount is not None
            else None
        )
        if usage_pct is None:
            threshold_state = "unavailable"
        elif usage_pct >= budget.critical_pct:
            threshold_state = "critical"
        elif usage_pct >= budget.warning_pct:
            threshold_state = "warning"
        else:
            threshold_state = "normal"
        start = _parse(budget.period_start)
        end = _parse(budget.period_end)
        point = as_of or datetime.now(timezone.utc)
        elapsed = min(max((point - start).total_seconds(), 0), (end - start).total_seconds())
        forecast = None
        if spent_amount is not None and priced_requests > 0 and elapsed > 0:
            forecast = round(
                spent_amount * (end - start).total_seconds() / elapsed,
                4,
            )
        confidence = (
            "unavailable"
            if spent_amount is None or not total_requests
            else "complete"
            if priced_requests == total_requests
            else "partial"
        )
        return BudgetProgress(
            budget_id=getattr(budget, "budget_id", None),
            amount=budget.amount,
            spent_amount=spent_amount,
            usage_pct=usage_pct,
            forecast_amount=forecast,
            forecast_status="estimated" if forecast is not None else "unavailable",
            confidence=confidence,
            priced_requests=max(0, priced_requests),
            total_requests=max(0, total_requests),
            threshold_state=threshold_state,
        )


def allocate_workspaces(
    workspace_ids: list[str] | tuple[str, ...],
    assignments: dict[str, str | None],
) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for workspace_id in dict.fromkeys(str(item).strip() for item in workspace_ids):
        if not workspace_id:
            continue
        department = str(assignments.get(workspace_id) or "unassigned")
        result.setdefault(department, []).append(workspace_id)
    return result


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
