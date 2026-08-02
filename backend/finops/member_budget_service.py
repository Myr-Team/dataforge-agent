from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import math
import re
from typing import Any, Protocol
from uuid import uuid4

from .acs_email import AcsEmailSender, EmailDeliveryResult, EmailMessage, render_template, validate_template
from .member_budget_repository import MemberBudgetRepository
from .member_budgets import MemberBudget, MemberBudgetDraft, NotificationSetting
from .member_directory import MemberDirectory, MemberMonthlyCost


_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s.]{1,190}(?:\.[^@\s.]{1,63})+$")


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
        members = {
            item.subject_ref: item
            for item in self._budget_subjects(
                tenant_ref=tenant_ref,
                workspace_ids=workspace_ids,
                include_disabled=True,
            )
        }
        now = datetime.now(timezone.utc)
        costs = self._costs.summarize_month(tenant_ref, _month_start(now), _next_month(now), workspace_ids)
        rows = tuple(
            budget
            for budget in self._repository.list_budgets(tenant_ref, include_disabled=True)
            if budget.member_ref in members
        )
        start = _cursor_offset(cursor)
        selected = rows[start : start + limit]
        items: list[dict[str, Any]] = []
        for budget in selected:
            member = members[budget.member_ref]
            progress = costs.get(budget.member_ref)
            safe_member = {
                "member_ref": member.subject_ref,
                "display_name": member.display_name,
                "role": "member",
                "identity_state": "active" if member.enabled else "inactive",
                "workspace_ids": (member.workspace_id,),
                "department_labels": (member.department_label,) if member.department_label else (),
                "primary_model": member.primary_model,
            }
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
            "data_status": _aggregate_status(items),
            "currency": "USD",
        }

    def list_eligible_members(
        self, *, tenant_ref: str, identity_tenant_id: str, workspace_ids: tuple[str, ...], cursor: str | None, limit: int
    ) -> dict[str, Any]:
        rows = list(
            self._budget_subjects(
                tenant_ref=tenant_ref,
                workspace_ids=workspace_ids,
                include_disabled=True,
            )
        )
        start = _cursor_offset(cursor)
        selected = rows[start : start + limit]
        items = [
            {
                "member_ref": item.subject_ref,
                "display_name": item.display_name,
                "role": "member",
                "identity_state": "active" if item.enabled else "inactive",
                "workspace_ids": (item.workspace_id,),
                "department_labels": (item.department_label,) if item.department_label else (),
                "primary_model": item.primary_model,
            }
            for item in selected
        ]
        return {
            "items": items,
            "cursor": {"next": str(start + limit) if start + limit < len(rows) else None, "limit": limit},
            "freshness": "recorded",
            "coverage": "trusted_member_directory",
            "data_status": "complete",
            "currency": "USD",
        }

    def is_eligible_member(
        self,
        *,
        member_ref: str,
        tenant_ref: str,
        identity_tenant_id: str,
        workspace_ids: tuple[str, ...],
    ) -> bool:
        return any(
            item.subject_ref == member_ref and item.enabled
            for item in self._budget_subjects(
                tenant_ref=tenant_ref,
                workspace_ids=workspace_ids,
            )
        )

    def is_budget_member_authorized(
        self,
        *,
        tenant_ref: str,
        budget_id: str,
        identity_tenant_id: str,
        workspace_ids: tuple[str, ...],
    ) -> bool:
        budget = self._repository.get_budget(tenant_ref, budget_id)
        if budget is None:
            return False
        return any(
            item.subject_ref == budget.member_ref
            for item in self._budget_subjects(
                tenant_ref=tenant_ref,
                workspace_ids=workspace_ids,
                include_disabled=True,
            )
        )

    def save_budget(
        self, *, tenant_ref: str, actor_ref: str, payload: dict[str, Any], budget_id: str | None = None
    ) -> MemberBudget:
        allowed = {"member_ref", "amount_usd", "thresholds_pct", "enabled", "base_revision"}
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise ValueError("unsupported member budget fields")
        current = self._repository.get_budget(tenant_ref, budget_id) if budget_id else None
        if budget_id and current is None:
            raise KeyError(budget_id)
        if type(payload.get("base_revision")) is not int or payload["base_revision"] < 0:
            raise ValueError("base_revision must be a non-negative integer")
        base_revision = payload["base_revision"]
        member_ref = payload.get("member_ref", current.member_ref if current else "")
        if type(member_ref) is not str:
            raise ValueError("member_ref must be a string")
        amount = payload.get("amount_usd", current.amount_usd if current else None)
        if isinstance(amount, bool) or not isinstance(amount, (int, float, Decimal)):
            raise ValueError("amount_usd must be a number")
        if isinstance(amount, float) and not math.isfinite(amount):
            raise ValueError("amount_usd must be finite")
        enabled = payload.get("enabled", current.enabled if current else True)
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        thresholds = payload.get("thresholds_pct", current.thresholds_pct if current else (80, 95, 100))
        if not isinstance(thresholds, (tuple, list)) or any(type(value) is not int for value in thresholds):
            raise ValueError("thresholds_pct must be an integer array")
        draft = MemberBudgetDraft(
            member_ref=member_ref,
            amount_usd=Decimal(str(amount)),
            thresholds_pct=tuple(thresholds),
            enabled=enabled,
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
        self,
        *,
        tenant_ref: str,
        actor_ref: str,
        payload: dict[str, Any],
        active_admins: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        allowed = {"recipient_email", "sender_display_name", "subject_template", "body_template", "enabled", "base_revision"}
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise ValueError("unsupported notification fields")
        if type(payload.get("base_revision")) is not int or payload["base_revision"] < 0:
            raise ValueError("base_revision must be a non-negative integer")
        current = self._repository.get_notification_setting(tenant_ref)
        base_revision = payload["base_revision"]
        email = payload.get("recipient_email", current.recipient_email if current else "")
        if type(email) is not str or len(email.strip()) > 320 or not _EMAIL.fullmatch(email.strip()):
            raise ValueError("recipient_email must be a valid email address")
        email = email.strip()
        now = datetime.now(timezone.utc)
        enabled = payload.get("enabled", current.enabled if current else False)
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        sender_display_name = payload.get("sender_display_name", current.sender_display_name if current else "DataForge")
        subject_template = payload.get("subject_template", current.subject_template if current else "Member budget alert")
        body_template = payload.get("body_template", current.body_template if current else "Budget threshold reached")
        if any(type(value) is not str for value in (sender_display_name, subject_template, body_template)):
            raise ValueError("notification template fields must be strings")
        validate_template(subject_template)
        validate_template(body_template)
        configuration_changed = bool(
            current is not None
            and (
                email.casefold() != current.recipient_email.casefold()
                or sender_display_name != current.sender_display_name
                or subject_template != current.subject_template
                or body_template != current.body_template
            )
        )
        tested_at = (
            None
            if current is None or configuration_changed
            else current.test_email_succeeded_at
        )
        if enabled and tested_at is None:
            raise ValueError("test_email_required")
        value = NotificationSetting(
            recipient_actor_ref=actor_ref,
            recipient_email=email,
            sender_display_name=sender_display_name,
            subject_template=subject_template,
            body_template=body_template,
            enabled=enabled,
            test_email_succeeded_at=tested_at,
            revision=base_revision + 1,
            created_by_ref=current.created_by_ref if current else actor_ref,
            updated_by_ref=actor_ref,
            created_at=current.created_at if current else now,
            updated_at=now,
        )
        return _safe_notification(self._repository.save_notification_setting(tenant_ref, value, base_revision=base_revision))

    def list_alerts(
        self,
        *,
        tenant_ref: str,
        identity_tenant_id: str,
        workspace_ids: tuple[str, ...],
        budget_id: str | None = None,
        cursor: str | None,
        limit: int,
    ) -> dict[str, Any]:
        authorized_actor_refs = tuple(sorted({
            item.subject_ref
            for item in self._budget_subjects(
                tenant_ref=tenant_ref,
                workspace_ids=workspace_ids,
                include_disabled=True,
            )
        }))
        if budget_id is not None:
            budget = self._repository.get_budget(tenant_ref, budget_id)
            if budget is None or budget.member_ref not in authorized_actor_refs:
                raise KeyError(budget_id)
        start = _cursor_offset(cursor)
        rows = self._repository.list_alerts(
            tenant_ref,
            budget_id=budget_id,
            actor_refs=authorized_actor_refs,
            offset=start,
            limit=limit + 1,
        )
        selected = rows[:limit]
        data_status = "complete" if all(item.pricing_coverage_pct == 100 for item in selected) else "partial"
        return {
            "items": [item.model_dump(mode="json") for item in selected],
            "cursor": {
                "next": str(start + limit) if len(rows) > limit else None,
                "limit": limit,
            },
            "currency": "USD",
            "freshness": "recorded",
            "coverage": "request_estimated_cost",
            "data_status": data_status,
        }

    def send_test_email(
        self,
        *,
        tenant_ref: str,
        sender: AcsEmailSender,
        active_admins: dict[str, str] | None = None,
    ) -> EmailDeliveryResult:
        """Send a configuration probe only; it never creates a budget alert."""
        setting = self._repository.get_notification_setting(tenant_ref)
        if setting is None:
            raise KeyError("notification_setting")
        recipient = setting.recipient_email
        values = {
            "member_name": "Member", "budget_amount": "-", "estimated_spend": "-", "usage_percent": "-",
            "threshold_percent": "-", "period_label": "-", "pricing_coverage": "-", "portal_url": "-",
        }
        message = EmailMessage(
            recipient=recipient,
            sender_display_name=setting.sender_display_name,
            subject=f"[测试] {render_template(setting.subject_template, values)}",
            plain_text=render_template(setting.body_template, values),
        )
        operation_id = str(uuid4())
        result = sender.send(message, operation_id=operation_id)
        if result.state == "sent":
            self._repository.mark_notification_tested(
                tenant_ref,
                revision=setting.revision,
                sent_at=result.sent_at or datetime.now(timezone.utc),
            )
        return result

    def _budget_subjects(
        self,
        *,
        tenant_ref: str,
        workspace_ids: tuple[str, ...],
        include_disabled: bool = False,
    ) -> tuple[Any, ...]:
        values: list[Any] = []
        for workspace_id in workspace_ids:
            values.extend(
                self._repository.list_budget_subjects(
                    tenant_ref,
                    workspace_id,
                    include_disabled=include_disabled,
                )
            )
        return tuple(values)


def _safe_notification(value: NotificationSetting) -> dict[str, Any]:
    return value.model_dump(mode="json")


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


def _aggregate_status(items: list[dict[str, Any]]) -> str:
    statuses = {str(item["data_status"]) for item in items}
    if not statuses or statuses == {"unavailable"}:
        return "unavailable"
    return "complete" if statuses == {"complete"} else "partial"


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
