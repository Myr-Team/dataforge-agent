from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Protocol

from .member_budgets import BudgetAlert, MemberBudget, NotificationSetting
from .sql_repository import FinOpsPersistenceError


class MemberBudgetConflictError(FinOpsPersistenceError):
    """A revision or active-member uniqueness check prevented a write."""


class MemberBudgetRepository(Protocol):
    def get_budget(self, tenant_ref: str, budget_id: str) -> MemberBudget | None: ...
    def list_budgets(self, tenant_ref: str, *, include_disabled: bool = False) -> tuple[MemberBudget, ...]: ...
    def save_budget(self, tenant_ref: str, value: MemberBudget, *, base_revision: int) -> MemberBudget: ...
    def disable_budget(
        self, tenant_ref: str, budget_id: str, *, base_revision: int, updated_by_ref: str
    ) -> MemberBudget: ...
    def get_notification_setting(self, tenant_ref: str) -> NotificationSetting | None: ...
    def save_notification_setting(
        self, tenant_ref: str, value: NotificationSetting, *, base_revision: int
    ) -> NotificationSetting: ...
    def claim_alert(self, value: BudgetAlert) -> bool: ...


class InMemoryMemberBudgetRepository:
    """Tenant-isolated deterministic implementation for tests and local preview."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._budgets: dict[tuple[str, str], MemberBudget] = {}
        self._notifications: dict[str, NotificationSetting] = {}
        self._alerts: dict[tuple[str, str, str, int], BudgetAlert] = {}

    def get_budget(self, tenant_ref: str, budget_id: str) -> MemberBudget | None:
        with self._lock:
            return self._budgets.get((tenant_ref, budget_id))

    def list_budgets(self, tenant_ref: str, *, include_disabled: bool = False) -> tuple[MemberBudget, ...]:
        with self._lock:
            rows = [
                value for (tenant, _), value in self._budgets.items()
                if tenant == tenant_ref and (include_disabled or value.enabled)
            ]
        return tuple(sorted(rows, key=lambda value: (value.member_ref, value.created_at, value.budget_id)))

    def save_budget(self, tenant_ref: str, value: MemberBudget, *, base_revision: int) -> MemberBudget:
        key = (tenant_ref, value.budget_id)
        with self._lock:
            current = self._budgets.get(key)
            current_revision = current.revision if current else 0
            if current_revision != base_revision or value.revision != base_revision + 1:
                raise MemberBudgetConflictError("member budget revision conflict")
            if value.enabled:
                duplicate = next(
                    (
                        row for (tenant, budget_id), row in self._budgets.items()
                        if tenant == tenant_ref
                        and budget_id != value.budget_id
                        and row.member_ref == value.member_ref
                        and row.enabled
                    ),
                    None,
                )
                if duplicate is not None:
                    raise MemberBudgetConflictError("active member budget already exists")
            self._budgets[key] = value
        return value

    def disable_budget(
        self, tenant_ref: str, budget_id: str, *, base_revision: int, updated_by_ref: str
    ) -> MemberBudget:
        with self._lock:
            current = self._budgets.get((tenant_ref, budget_id))
            if current is None or current.revision != base_revision:
                raise MemberBudgetConflictError("member budget revision conflict")
            now = datetime.now(timezone.utc)
            disabled = current.model_copy(
                update={"enabled": False, "revision": current.revision + 1, "updated_by_ref": updated_by_ref, "updated_at": now}
            )
            self._budgets[(tenant_ref, budget_id)] = disabled
        return disabled

    def get_notification_setting(self, tenant_ref: str) -> NotificationSetting | None:
        with self._lock:
            return self._notifications.get(tenant_ref)

    def save_notification_setting(
        self, tenant_ref: str, value: NotificationSetting, *, base_revision: int
    ) -> NotificationSetting:
        with self._lock:
            current = self._notifications.get(tenant_ref)
            current_revision = current.revision if current else 0
            if current_revision != base_revision or value.revision != base_revision + 1:
                raise MemberBudgetConflictError("notification setting revision conflict")
            self._notifications[tenant_ref] = value
        return value

    def claim_alert(self, value: BudgetAlert) -> bool:
        key = (value.tenant_ref, value.budget_id, value.period_key, value.threshold_pct)
        with self._lock:
            if key in self._alerts:
                return False
            self._alerts[key] = value
            return True


__all__ = [
    "InMemoryMemberBudgetRepository",
    "MemberBudgetConflictError",
    "MemberBudgetRepository",
]
