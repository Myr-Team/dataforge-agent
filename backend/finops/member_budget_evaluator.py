from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable
from uuid import NAMESPACE_URL, uuid5

from .acs_email import AcsEmailError, EmailMessage, render_template
from .member_budgets import BudgetAlert, MemberCostSummary


@dataclass(frozen=True)
class EvaluationSummary:
    created: int = 0
    sent: int = 0
    skipped: int = 0
    failed: int = 0


class MemberBudgetEvaluator:
    """Claim first, then CAS-own delivery; a crash leaves a recoverable pending/sending alert."""
    def __init__(self, *, repository: Any, costs: Any, active_member_refs: Callable[..., set[str]], active_admins: Callable[..., dict[str, str]], sender: Any, automatic_enabled: Callable[[], bool] | None = None) -> None:
        self._repository, self._costs, self._active_member_refs, self._active_admins, self._sender = repository, costs, active_member_refs, active_admins, sender
        self._automatic_enabled = automatic_enabled or (lambda: str(os.getenv("DF_FINOPS_EMAIL_ALERTS_ENABLED", "0")).lower() in {"1", "true", "yes", "on"})

    def evaluate_tenant(self, tenant_ref: str, *, now: datetime, workspace_ids: tuple[str, ...] = ()) -> EvaluationSummary:
        if now.tzinfo is None: raise ValueError("now must be UTC")
        period = now.astimezone(timezone.utc).strftime("%Y-%m")
        notification = self._repository.get_notification_setting(tenant_ref)
        budgets = self._repository.list_budgets(tenant_ref)
        costs = self._costs.summarize_month(tenant_ref, now.replace(day=1,hour=0,minute=0,second=0,microsecond=0), _next_month(now), workspace_ids)
        active = self._active_member_refs(tenant_ref, workspace_ids)
        if not self._automatic_enabled():
            return EvaluationSummary(skipped=len(budgets))
        if not notification or not notification.enabled or notification.recipient_actor_ref not in self._active_admins(tenant_ref, workspace_ids):
            return EvaluationSummary(skipped=len(budgets))
        created = sent = skipped = failed = 0
        for budget in budgets:
            cost = costs.get(budget.member_ref)
            if budget.member_ref not in active or not cost or cost.estimated_spend_usd is None or cost.data_status not in {"complete", "partial"} or not cost.pricing_coverage_pct:
                skipped += 1; continue
            usage = Decimal(cost.estimated_spend_usd) / budget.amount_usd * 100
            crossed = [t for t in budget.thresholds_pct if usage >= t]
            if not crossed: continue
            highest = max(crossed)
            for threshold in crossed:
                alert = BudgetAlert(alert_id=f"alert_{uuid5(NAMESPACE_URL, f'{tenant_ref}:{budget.budget_id}:{period}:{threshold}').hex}", tenant_ref=tenant_ref, budget_id=budget.budget_id, actor_ref=budget.member_ref, period_key=period, threshold_pct=threshold, budget_amount_usd=budget.amount_usd, estimated_spend_usd=cost.estimated_spend_usd, pricing_coverage_pct=cost.pricing_coverage_pct, budget_revision=budget.revision, notification_revision=notification.revision, delivery_state="pending" if threshold == highest else "suppressed", triggered_at=now, updated_at=now)
                if self._repository.claim_alert(alert):
                    created += 1
                    if threshold == highest:
                        outcome = self._deliver(alert, notification, self._active_admins(tenant_ref, workspace_ids)[notification.recipient_actor_ref], now)
                        sent += outcome == "sent"; failed += outcome == "failed"
        # Reclaim failed/pending and lease-expired sending alerts. A successful
        # CAS is the only ownership proof, so concurrent workers fail closed.
        recipient = self._active_admins(tenant_ref, workspace_ids).get(notification.recipient_actor_ref)
        if recipient:
            for alert in self._repository.list_alerts(tenant_ref, offset=0, limit=101):
                if alert.delivery_state == "sent" or alert.attempt_count >= 3:
                    continue
                stale = alert.delivery_state == "sending" and (now - alert.updated_at).total_seconds() >= 15 * 60
                if alert.delivery_state in {"pending", "failed"} or stale:
                    outcome = self._deliver(alert, notification, recipient, now)
                    sent += outcome == "sent"; failed += outcome == "failed"
        return EvaluationSummary(created, sent, skipped, failed)

    def _deliver(self, alert: BudgetAlert, notification: Any, recipient: str, now: datetime) -> str:
        sending = alert.model_copy(update={"delivery_state":"sending", "attempt_count":alert.attempt_count+1, "updated_at":now})
        if not self._repository.transition_alert(alert.tenant_ref, alert.alert_id, expected_state=alert.delivery_state, value=sending): return "skipped"
        try:
            operation_id = str(uuid5(NAMESPACE_URL, f"dataforge-budget-alert:{alert.alert_id}"))
            values = {"member_name":"Member", "budget_amount":str(alert.budget_amount_usd), "estimated_spend":str(alert.estimated_spend_usd), "usage_percent":str(round(alert.estimated_spend_usd / alert.budget_amount_usd * 100, 2)), "threshold_percent":str(alert.threshold_pct), "period_label":alert.period_key, "pricing_coverage":str(alert.pricing_coverage_pct), "portal_url":"-"}
            result = self._sender.send(EmailMessage(recipient=recipient, sender_display_name=notification.sender_display_name, subject=render_template(notification.subject_template, values), plain_text=render_template(notification.body_template, values)), operation_id)
            final = sending.model_copy(update={"delivery_state":"sent", "sent_at":result.sent_at, "updated_at":now})
            return "sent" if self._repository.transition_alert(alert.tenant_ref, alert.alert_id, expected_state="sending", value=final) else "skipped"
        except Exception as exc:
            category = exc.category if isinstance(exc, AcsEmailError) else "service_unavailable"
            final = sending.model_copy(update={"delivery_state":"failed", "safe_error_category":category, "updated_at":now})
            self._repository.transition_alert(alert.tenant_ref, alert.alert_id, expected_state="sending", value=final)
            return "failed"

def _next_month(value: datetime) -> datetime:
    value=value.astimezone(timezone.utc).replace(day=1,hour=0,minute=0,second=0,microsecond=0)
    return value.replace(year=value.year+1, month=1) if value.month==12 else value.replace(month=value.month+1)
