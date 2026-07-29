from datetime import datetime, timezone
from decimal import Decimal

from backend.finops.member_budget_evaluator import MemberBudgetEvaluator
from backend.finops.member_budget_repository import InMemoryMemberBudgetRepository
from backend.finops.member_budgets import MemberBudget, MemberCostSummary, NotificationSetting


def test_crossed_thresholds_are_claimed_once_and_coalesced() -> None:
    now = datetime(2026, 7, 28, tzinfo=timezone.utc)
    repo = InMemoryMemberBudgetRepository()
    repo.save_budget("tenant-safe", MemberBudget(member_ref="actor-safe", amount_usd=Decimal("200"), thresholds_pct=(80, 95, 100), enabled=True, budget_id="budget-safe", revision=1, created_by_ref="owner-safe", updated_by_ref="owner-safe", created_at=now, updated_at=now), base_revision=0)
    repo.save_notification_setting("tenant-safe", NotificationSetting(recipient_actor_ref="admin-safe", recipient_email="admin@example.test", sender_display_name="DataForge", subject_template="subject", body_template="body", enabled=True, revision=1, created_by_ref="owner-safe", updated_by_ref="owner-safe", created_at=now, updated_at=now), base_revision=0)

    class _Costs:
        def summarize_month(self, *_args):
            return {"actor-safe": MemberCostSummary(actor_ref="actor-safe", estimated_spend_usd=Decimal("210"), priced_requests=21, total_requests=21)}

    class _Sender:
        operation_ids = []
        def send(self, _message, operation_id): self.operation_ids.append(operation_id); return type("R", (), {"state":"sent", "sent_at":now, "safe_error_category":None})()

    sender = _Sender()
    evaluator = MemberBudgetEvaluator(repository=repo, costs=_Costs(), active_member_refs=lambda *_: {"actor-safe"}, active_admins=lambda *_: {"admin-safe":"admin@example.test"}, sender=sender, automatic_enabled=lambda: True)
    assert evaluator.evaluate_tenant("tenant-safe", now=now).created == 3
    assert sorted((x.threshold_pct, x.delivery_state) for x in repo.list_alerts("tenant-safe")) == [(80, "suppressed"), (95, "suppressed"), (100, "sent")]
    evaluator.evaluate_tenant("tenant-safe", now=now)
    assert len(sender.operation_ids) == 1
