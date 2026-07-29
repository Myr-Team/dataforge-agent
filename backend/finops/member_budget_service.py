from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Protocol
from uuid import uuid4

from .member_budget_repository import MemberBudgetRepository
from .member_budgets import MemberBudget, MemberBudgetDraft, NotificationSetting
from .member_directory import MemberCostReader, MemberDirectory, MemberMonthlyCost


class _MemberCostReader(Protocol):
    def summarize_month(
        self, tenant_ref: str, month_start: datetime, month_end: datetime, workspace_ids: tuple[str, ...]
    ) -> dict[str, MemberMonthlyCost]: ...


class MemberBudgetService:
    """Tenant-scoped member budget projections and mutation validation."""

    def __init__(self, repository: MemberBudgetRepository, directory: MemberDirectory, costs: _MemberCostReader) -> None:
        self._repository, self._directory, self._costs = repository, directory, costs

    def list_budgets(
        self, *, tenant_ref: str, workspace_ids: tuple[str, ...], cursor: str | None, limit: int,
        identity_tenant_id: str | None = None,
    ) -> dict[str, Any]:
        # The directory needs the raw trusted tenant only to compare trusted
        # loader metadata. Persisted/query scope remains the opaque tenant ref.
        members = {item.member_ref: item for item in self._directory.list_members(identity_tenant_id or tenant_ref, workspace_ids)}
        now = datetime.now(timezone.utc)
        costs = self._costs.summarize_month(tenant_ref, _month_start(now), _next_month(now), workspace_ids)
        rows = self._repository.list_budgets(tenant_ref, include_disabled=True)
        start = _cursor_offset(cursor)
        selected = rows[start : start + limit]
        items: list[dict[str, Any]] = []
        for budget in selected:
            member = members.get(budget.member_ref)
            progress = costs.get(budget.member_ref)
            safe_member = (
                {
                    "member_ref": member.member_ref,
                    "display_name": member.display_name,
                    "role": member.role,
                    "identity_state": member.identity_state,
                    "workspace_ids": member.workspace_ids,
                    "department_labels": member.department_labels,
                }
                if member
                else {
                    "member_ref": budget.member_ref,
                    "display_name": "Former member",
                    "role": "viewer",
                    "identity_state": "inactive",
                    "workspace_ids": (),
                    "department_labels": (),
                }
            )
            safe_progress = _safe_progress(progress) if progress else _unavailable_progress()
            items.append(
                {
                    **_safe_budget(budget),
                    "member": safe_member,
                    "progress": safe_progress,
                    "currency": "USD",
                    "data_status": safe_progress["data_status"],
                    "freshness": "recorded",
                }
            )
        return {
            "items": items,
            "cursor": {"next": str(start + limit) if start + limit < len(rows) else None, "limit": limit},
            "freshness": "recorded",
            "coverage": "request_estimated_cost",
            "currency": "USD",
        }

    def save_budget(
        self, *, tenant_ref: str, actor_ref: str, payload: dict[str, Any], budget_id: str | None = None
    ) -> MemberBudget:
        allowed = {"member_ref", "amount_usd", "thresholds_pct", "enabled", "base_revision"}
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise ValueError("unsupported member budget fields")
        current = self._repository.get_budget(tenant_ref, budget_id) if budget_id else None
        if budget_id and current is None:
            raise KeyError(budget_id)
        if "base_revision" not in payload or isinstance(payload["base_revision"], bool):
            raise ValueError("base_revision is required")
        base_revision = int(payload["base_revision"])
        draft = MemberBudgetDraft(
            member_ref=str(payload.get("member_ref", current.member_ref if current else "")),
            amount_usd=Decimal(str(payload.get("amount_usd", current.amount_usd if current else ""))),
            thresholds_pct=tuple(payload.get("thresholds_pct", current.thresholds_pct if current else (80, 95, 100))),
            enabled=bool(payload.get("enabled", current.enabled if current else True)),
        )
        if current and draft.member_ref != current.member_ref:
            raise ValueError("member_ref cannot change")
        now = datetime.now(timezone.utc)
        value = MemberBudget(
            **draft.model_dump(),
            budget_id=budget_id or f"budget_{uuid4().hex}",
            revision=base_revision + 1,
            created_by_ref=current.created_by_ref if current else actor_ref,
            updated_by_ref=actor_ref,
            created_at=current.created_at if current else now,
            updated_at=now,
        )
        return self._repository.save_budget(tenant_ref, value, base_revision=base_revision)

    def disable_budget(self, *, tenant_ref: str, actor_ref: str, budget_id: str, base_revision: int) -> MemberBudget:
        if self._repository.get_budget(tenant_ref, budget_id) is None:
            raise KeyError(budget_id)
        return self._repository.disable_budget(tenant_ref, budget_id, base_revision=base_revision, updated_by_ref=actor_ref)

    def get_notification(self, *, tenant_ref: str) -> dict[str, Any] | None:
        value = self._repository.get_notification_setting(tenant_ref)
        if value is None:
            return None
        return _safe_notification(value)

    def save_notification(
        self, *, tenant_ref: str, actor_ref: str, payload: dict[str, Any], active_admins: dict[str, str]
    ) -> dict[str, Any]:
        allowed = {"recipient_actor_ref", "sender_display_name", "subject_template", "body_template", "enabled", "base_revision"}
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise ValueError("unsupported notification fields")
        recipient = str(payload.get("recipient_actor_ref") or "")
        email = active_admins.get(recipient)
        if not email:
            raise PermissionError("recipient must be an active tenant administrator")
        if "base_revision" not in payload or isinstance(payload["base_revision"], bool):
            raise ValueError("base_revision is required")
        current = self._repository.get_notification_setting(tenant_ref)
        base_revision = int(payload["base_revision"])
        now = datetime.now(timezone.utc)
        value = NotificationSetting(
            recipient_actor_ref=recipient,
            recipient_email=email,
            sender_display_name=str(payload.get("sender_display_name", current.sender_display_name if current else "DataForge")),
            subject_template=str(payload.get("subject_template", current.subject_template if current else "Member budget alert")),
            body_template=str(payload.get("body_template", current.body_template if current else "Budget threshold reached")),
            enabled=bool(payload.get("enabled", current.enabled if current else False)),
            revision=base_revision + 1,
            created_by_ref=current.created_by_ref if current else actor_ref,
            updated_by_ref=actor_ref,
            created_at=current.created_at if current else now,
            updated_at=now,
        )
        return _safe_notification(self._repository.save_notification_setting(tenant_ref, value, base_revision=base_revision))

    def list_alerts(self, *, tenant_ref: str, budget_id: str | None = None) -> dict[str, Any]:
        return {"items": [item.model_dump(mode="json") for item in self._repository.list_alerts(tenant_ref, budget_id=budget_id)], "currency": "USD", "freshness": "recorded"}


def _safe_notification(value: NotificationSetting) -> dict[str, Any]:
    return value.model_dump(mode="json", exclude={"recipient_email"})


def _safe_budget(value: MemberBudget) -> dict[str, Any]:
    result = value.model_dump(mode="json")
    result["amount_usd"] = float(value.amount_usd)
    return result


def _safe_progress(value: MemberMonthlyCost) -> dict[str, Any]:
    result = value.model_dump(mode="json")
    if value.estimated_spend_usd is not None:
        result["estimated_spend_usd"] = float(value.estimated_spend_usd)
    return result


def _unavailable_progress() -> dict[str, Any]:
    return {"estimated_spend_usd": None, "priced_requests": 0, "total_requests": 0, "unpriced_requests": 0, "pricing_coverage_pct": None, "currency": "USD", "data_status": "unavailable", "freshness": "recorded"}


def _cursor_offset(value: str | None) -> int:
    if value is None:
        return 0
    if not value.isdecimal():
        raise ValueError("invalid cursor")
    return int(value)


def _month_start(value: datetime) -> datetime:
    return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _next_month(value: datetime) -> datetime:
    start = _month_start(value)
    return start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)


__all__ = ["MemberBudgetService"]
