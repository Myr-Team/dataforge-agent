from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid4, uuid5

from .acs_email import AcsEmailError, EmailMessage, render_template
from .member_budgets import BudgetAlert, MemberBudget, MemberCostSummary


_LEASE_DURATION = timedelta(minutes=15)
_BACKOFF_MINUTES = (5, 10)
_SAFE_MEMBER_NAME = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class EvaluationSummary:
    created: int = 0
    sent: int = 0
    skipped: int = 0
    failed: int = 0


class MemberBudgetEvaluator:
    """Create durable threshold claims and deliver them under exclusive leases."""

    def __init__(
        self,
        *,
        repository: Any,
        costs: Any,
        active_member_refs: Callable[..., set[str]],
        active_admins: Callable[..., dict[str, str]],
        sender: Any,
        member_names: Callable[..., dict[str, str]] | None = None,
        automatic_enabled: Callable[[], bool] | None = None,
        portal_url: str = "https://portal.azure.com/",
    ) -> None:
        self._repository = repository
        self._costs = costs
        self._active_member_refs = active_member_refs
        self._active_admins = active_admins
        self._member_names = member_names or (lambda *_args: {})
        self._sender = sender
        self._automatic_enabled = automatic_enabled or (
            lambda: str(os.getenv("DF_FINOPS_EMAIL_ALERTS_ENABLED", "0")).lower()
            in {"1", "true", "yes", "on"}
        )
        self._portal_url = _safe_portal_url(portal_url)

    def evaluate_tenant(
        self,
        tenant_ref: str,
        *,
        now: datetime,
        workspace_ids: tuple[str, ...] = (),
    ) -> EvaluationSummary:
        now = _utc(now)
        month_start = now.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        period_key = month_start.strftime("%Y-%m")
        budgets = self._repository.list_budgets(tenant_ref)
        costs = self._costs.summarize_month(
            tenant_ref, month_start, _next_month(month_start), workspace_ids
        )

        if not self._automatic_enabled():
            return EvaluationSummary(skipped=len(budgets))

        notification = self._repository.get_notification_setting(tenant_ref)
        active_members = self._active_member_refs(tenant_ref, workspace_ids)
        active_admins = self._active_admins(tenant_ref, workspace_ids)
        can_claim = (
            notification is not None
            and notification.enabled
            and notification.test_email_succeeded_at is not None
            and _active_recipient(notification) is not None
        )

        created = skipped = 0
        if can_claim:
            assert notification is not None
            for budget in budgets:
                cost = costs.get(budget.member_ref)
                if not _eligible_cost(cost) or budget.member_ref not in active_members:
                    skipped += 1
                    continue
                assert cost is not None and cost.estimated_spend_usd is not None
                usage_pct = Decimal(cost.estimated_spend_usd) / budget.amount_usd * 100
                crossed = [
                    threshold
                    for threshold in budget.thresholds_pct
                    if usage_pct >= threshold
                ]
                if not crossed:
                    continue
                highest = max(crossed)
                for threshold in crossed:
                    alert = BudgetAlert(
                        alert_id=(
                            "alert_"
                            + uuid5(
                                NAMESPACE_URL,
                                (
                                    f"{tenant_ref}:{budget.budget_id}:"
                                    f"{period_key}:{threshold}"
                                ),
                            ).hex
                        ),
                        tenant_ref=tenant_ref,
                        budget_id=budget.budget_id,
                        actor_ref=budget.member_ref,
                        period_key=period_key,
                        threshold_pct=threshold,
                        budget_amount_usd=budget.amount_usd,
                        estimated_spend_usd=cost.estimated_spend_usd,
                        pricing_coverage_pct=cost.pricing_coverage_pct,
                        budget_revision=budget.revision,
                        notification_revision=notification.revision,
                        delivery_state=(
                            "pending" if threshold == highest else "suppressed"
                        ),
                        triggered_at=now,
                        updated_at=now,
                    )
                    if self._repository.claim_alert(alert):
                        created += 1

        sent = failed = 0
        while True:
            lease_token = uuid4().hex
            alert = self._repository.acquire_due_alert(
                tenant_ref,
                now=now,
                lease_token=lease_token,
                lease_expires_at=now + _LEASE_DURATION,
            )
            if alert is None:
                break
            outcome = self._process_acquired(
                alert,
                lease_token=lease_token,
                now=now,
                workspace_ids=workspace_ids,
                costs=costs,
                period_key=period_key,
            )
            if outcome == "sent":
                sent += 1
            elif outcome == "failed":
                failed += 1
            else:
                skipped += 1

        return EvaluationSummary(
            created=created,
            sent=sent,
            skipped=skipped,
            failed=failed,
        )

    def _process_acquired(
        self,
        alert: BudgetAlert,
        *,
        lease_token: str,
        now: datetime,
        workspace_ids: tuple[str, ...],
        costs: dict[str, MemberCostSummary],
        period_key: str,
    ) -> str:
        eligibility = self._delivery_context(
            alert,
            workspace_ids=workspace_ids,
            costs=costs,
            period_key=period_key,
        )
        if eligibility is None:
            suppressed = _final_value(
                alert,
                state="suppressed",
                now=now,
                safe_error_category=None,
            )
            self._repository.finalize_alert(
                alert.tenant_ref,
                alert.alert_id,
                lease_token=lease_token,
                value=suppressed,
            )
            return "skipped"

        notification, recipient, member_name = eligibility
        values = _template_values(
            alert,
            member_name=member_name,
            portal_url=self._portal_url,
        )
        operation_id = str(
            uuid5(NAMESPACE_URL, f"dataforge-budget-alert:{alert.alert_id}")
        )
        try:
            result = self._sender.send(
                EmailMessage(
                    recipient=recipient,
                    sender_display_name=notification.sender_display_name,
                    subject=render_template(notification.subject_template, values),
                    plain_text=render_template(notification.body_template, values),
                ),
                operation_id,
            )
        except Exception as exc:
            category = (
                exc.category
                if isinstance(exc, AcsEmailError)
                else "service_unavailable"
            )
            next_attempt_at = (
                now + timedelta(minutes=_BACKOFF_MINUTES[alert.attempt_count - 1])
                if alert.attempt_count < 3
                else None
            )
            failed = _final_value(
                alert,
                state="failed",
                now=now,
                safe_error_category=category,
                next_attempt_at=next_attempt_at,
            )
            return (
                "failed"
                if self._repository.finalize_alert(
                    alert.tenant_ref,
                    alert.alert_id,
                    lease_token=lease_token,
                    value=failed,
                )
                else "skipped"
            )

        sent = _final_value(
            alert,
            state="sent",
            now=now,
            safe_error_category=None,
            sent_at=result.sent_at,
        )
        return (
            "sent"
            if self._repository.finalize_alert(
                alert.tenant_ref,
                alert.alert_id,
                lease_token=lease_token,
                value=sent,
            )
            else "skipped"
        )

    def _delivery_context(
        self,
        alert: BudgetAlert,
        *,
        workspace_ids: tuple[str, ...],
        costs: dict[str, MemberCostSummary],
        period_key: str,
    ) -> tuple[Any, str, str] | None:
        if not self._automatic_enabled() or alert.period_key != period_key:
            return None
        budget: MemberBudget | None = self._repository.get_budget(
            alert.tenant_ref, alert.budget_id
        )
        if (
            budget is None
            or not budget.enabled
            or budget.revision != alert.budget_revision
            or budget.member_ref != alert.actor_ref
            or budget.amount_usd != alert.budget_amount_usd
        ):
            return None
        notification = self._repository.get_notification_setting(alert.tenant_ref)
        if (
            notification is None
            or not notification.enabled
            or notification.test_email_succeeded_at is None
            or notification.revision != alert.notification_revision
        ):
            return None
        active_members = self._active_member_refs(
            alert.tenant_ref, workspace_ids
        )
        if alert.actor_ref not in active_members:
            return None
        recipient = _active_recipient(notification)
        if recipient is None:
            return None
        cost = costs.get(alert.actor_ref)
        if (
            not _eligible_cost(cost)
            or alert.pricing_coverage_pct is None
            or alert.pricing_coverage_pct <= 0
        ):
            return None
        names = self._member_names(alert.tenant_ref, workspace_ids)
        member_name = _friendly_member_name(names.get(alert.actor_ref))
        return notification, recipient, member_name


def _active_recipient(notification: Any) -> str | None:
    email = str(notification.recipient_email or "").strip()
    return email or None


def _eligible_cost(cost: MemberCostSummary | None) -> bool:
    return bool(
        cost is not None
        and cost.estimated_spend_usd is not None
        and cost.data_status in {"complete", "partial"}
        and cost.pricing_coverage_pct is not None
        and cost.pricing_coverage_pct > 0
    )


def _final_value(
    alert: BudgetAlert,
    *,
    state: str,
    now: datetime,
    safe_error_category: str | None,
    sent_at: datetime | None = None,
    next_attempt_at: datetime | None = None,
) -> BudgetAlert:
    return alert.model_copy(
        update={
            "delivery_state": state,
            "safe_error_category": safe_error_category,
            "sent_at": sent_at,
            "updated_at": now,
            "lease_token": None,
            "lease_expires_at": None,
            "next_attempt_at": next_attempt_at,
        }
    )


def _template_values(
    alert: BudgetAlert,
    *,
    member_name: str,
    portal_url: str,
) -> dict[str, str]:
    usage = alert.estimated_spend_usd / alert.budget_amount_usd * 100
    return {
        "member_name": member_name,
        "budget_amount": _decimal_text(alert.budget_amount_usd),
        "estimated_spend": _decimal_text(alert.estimated_spend_usd),
        "usage_percent": _decimal_text(usage.quantize(Decimal("0.01"))),
        "threshold_percent": str(alert.threshold_pct),
        "period_label": alert.period_key,
        "pricing_coverage": str(float(alert.pricing_coverage_pct or 0)),
        "portal_url": portal_url,
    }


def _friendly_member_name(value: Any) -> str:
    text = _SAFE_MEMBER_NAME.sub("", str(value or "")).strip()
    return text[:120] or "Member"


def _safe_portal_url(value: str) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 2048:
        raise ValueError("invalid FinOps portal URL")
    parsed = urlsplit(text)
    local_http = parsed.scheme == "http" and parsed.hostname in {
        "localhost",
        "127.0.0.1",
        "::1",
    }
    if (
        (parsed.scheme != "https" and not local_http)
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or _SAFE_MEMBER_NAME.search(text) is not None
    ):
        raise ValueError("invalid FinOps portal URL")
    return text


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("now must be UTC")
    return value.astimezone(timezone.utc)


def _next_month(value: datetime) -> datetime:
    return (
        value.replace(year=value.year + 1, month=1)
        if value.month == 12
        else value.replace(month=value.month + 1)
    )


__all__ = ["EvaluationSummary", "MemberBudgetEvaluator"]
