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
    def acquire_due_alert(
        self,
        tenant_ref: str,
        *,
        now: datetime,
        lease_token: str,
        lease_expires_at: datetime,
    ) -> BudgetAlert | None: ...
    def finalize_alert(
        self,
        tenant_ref: str,
        alert_id: str,
        *,
        lease_token: str,
        value: BudgetAlert,
    ) -> bool: ...
    def list_alerts(
        self,
        tenant_ref: str,
        *,
        budget_id: str | None = None,
        actor_refs: tuple[str, ...] | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[BudgetAlert, ...]: ...


class InMemoryMemberBudgetRepository:
    """Tenant-isolated deterministic implementation for tests and local preview."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._budgets: dict[tuple[str, str], MemberBudget] = {}
        self._notifications: dict[str, NotificationSetting] = {}
        self._alerts: dict[tuple[str, str, str, int], BudgetAlert] = {}
        self._alerts_by_id: dict[tuple[str, str], BudgetAlert] = {}

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
            if (value.tenant_ref, value.alert_id) in self._alerts_by_id:
                raise MemberBudgetConflictError("budget alert id conflict")
            self._alerts[key] = value
            self._alerts_by_id[(value.tenant_ref, value.alert_id)] = value
            return True

    def acquire_due_alert(
        self,
        tenant_ref: str,
        *,
        now: datetime,
        lease_token: str,
        lease_expires_at: datetime,
    ) -> BudgetAlert | None:
        if (
            now.tzinfo is None
            or lease_expires_at.tzinfo is None
            or lease_expires_at <= now
            or not 8 <= len(lease_token) <= 64
        ):
            raise ValueError("invalid budget alert lease")
        with self._lock:
            candidates = [
                alert
                for (tenant, _alert_id), alert in self._alerts_by_id.items()
                if tenant == tenant_ref
                and alert.attempt_count < 3
                and (
                    (
                        alert.delivery_state in {"pending", "failed"}
                        and (
                            alert.next_attempt_at is None
                            or alert.next_attempt_at <= now
                        )
                    )
                    or (
                        alert.delivery_state == "sending"
                        and alert.lease_expires_at is not None
                        and alert.lease_expires_at <= now
                    )
                )
            ]
            if not candidates:
                return None
            current = min(
                candidates,
                key=lambda alert: (
                    alert.next_attempt_at or alert.lease_expires_at or alert.triggered_at,
                    alert.triggered_at,
                    alert.alert_id,
                ),
            )
            acquired = current.model_copy(
                update={
                    "delivery_state": "sending",
                    "attempt_count": current.attempt_count + 1,
                    "lease_token": lease_token,
                    "lease_expires_at": lease_expires_at,
                    "next_attempt_at": None,
                    "updated_at": now,
                }
            )
            key = (
                tenant_ref,
                current.budget_id,
                current.period_key,
                current.threshold_pct,
            )
            self._alerts[key] = acquired
            self._alerts_by_id[(tenant_ref, current.alert_id)] = acquired
            return acquired

    def finalize_alert(
        self,
        tenant_ref: str,
        alert_id: str,
        *,
        lease_token: str,
        value: BudgetAlert,
    ) -> bool:
        with self._lock:
            current = self._alerts_by_id.get((tenant_ref, alert_id))
            if (
                current is None
                or current.delivery_state != "sending"
                or current.lease_token != lease_token
            ):
                return False
            key = (
                tenant_ref,
                current.budget_id,
                current.period_key,
                current.threshold_pct,
            )
            self._alerts[key] = value
            self._alerts_by_id[(tenant_ref, alert_id)] = value
            return True

    def list_alerts(
        self,
        tenant_ref: str,
        *,
        budget_id: str | None = None,
        actor_refs: tuple[str, ...] | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[BudgetAlert, ...]:
        if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 101:
            raise ValueError("invalid budget alert page")
        authorized_actors = None if actor_refs is None else frozenset(actor_refs)
        with self._lock:
            rows = [
                alert for (tenant, _budget, _period, _threshold), alert in self._alerts.items()
                if tenant == tenant_ref
                and (budget_id is None or alert.budget_id == budget_id)
                and (authorized_actors is None or alert.actor_ref in authorized_actors)
            ]
        return tuple(sorted(rows, key=lambda alert: (alert.triggered_at, alert.alert_id))[offset : offset + limit])


__all__ = [
    "InMemoryMemberBudgetRepository",
    "MemberBudgetConflictError",
    "MemberBudgetRepository",
]
