from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from threading import Barrier, Thread
from typing import Any

import pytest

from backend.finops.member_budget_evaluator import MemberBudgetEvaluator
from backend.finops.member_budget_repository import InMemoryMemberBudgetRepository
from backend.finops.member_budgets import BudgetAlert, MemberBudget, MemberCostSummary, NotificationSetting


UTC = timezone.utc
NOW = datetime(2026, 7, 28, 8, tzinfo=UTC)


def _budget(
    *,
    member_ref: str = "actor-safe",
    amount: str = "200",
    revision: int = 1,
    enabled: bool = True,
) -> MemberBudget:
    return MemberBudget(
        member_ref=member_ref,
        amount_usd=Decimal(amount),
        thresholds_pct=(80, 95, 100),
        enabled=enabled,
        budget_id=f"budget-{member_ref}",
        revision=revision,
        created_by_ref="owner-safe",
        updated_by_ref="owner-safe",
        created_at=NOW,
        updated_at=NOW,
    )


def _notification(*, revision: int = 1, enabled: bool = True) -> NotificationSetting:
    return NotificationSetting(
        recipient_actor_ref="admin-safe",
        recipient_email="admin@example.test",
        sender_display_name="DataForge",
        subject_template="{{member_name}} {{threshold_percent}}% / {{period_label}}",
        body_template=(
            "{{budget_amount}}|{{estimated_spend}}|{{usage_percent}}|"
            "{{pricing_coverage}}|{{portal_url}}"
        ),
        enabled=enabled,
        revision=revision,
        created_by_ref="owner-safe",
        updated_by_ref="owner-safe",
        created_at=NOW,
        updated_at=NOW,
    )


class _Costs:
    def __init__(self, summaries: dict[str, MemberCostSummary]) -> None:
        self.summaries = summaries
        self.calls: list[tuple[Any, ...]] = []

    def summarize_month(self, *args: Any) -> dict[str, MemberCostSummary]:
        self.calls.append(args)
        return self.summaries


class _Sender:
    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.messages: list[Any] = []
        self.operation_ids: list[str] = []

    def send(self, message: Any, operation_id: str) -> Any:
        self.messages.append(message)
        self.operation_ids.append(operation_id)
        if len(self.operation_ids) <= self.failures:
            raise RuntimeError("provider detail must not escape")
        return type(
            "Result",
            (),
            {"state": "sent", "sent_at": NOW, "safe_error_category": None},
        )()


def _scenario(
    *,
    spend: str | None = "190",
    priced: int = 19,
    total: int = 20,
    sender: _Sender | None = None,
    active_members: Any = None,
    active_admins: Any = None,
    member_names: Any = None,
    automatic_enabled: Any = None,
    portal_url: str = "https://dataforge.example.test/operations/member-budgets",
) -> tuple[InMemoryMemberBudgetRepository, _Costs, _Sender, MemberBudgetEvaluator]:
    repository = InMemoryMemberBudgetRepository()
    repository.save_budget("tenant-safe", _budget(), base_revision=0)
    repository.save_notification_setting("tenant-safe", _notification(), base_revision=0)
    summaries = {
        "actor-safe": MemberCostSummary(
            actor_ref="actor-safe",
            estimated_spend_usd=Decimal(spend) if spend is not None else None,
            priced_requests=priced,
            total_requests=total,
        )
    }
    costs = _Costs(summaries)
    email_sender = sender or _Sender()
    evaluator = MemberBudgetEvaluator(
        repository=repository,
        costs=costs,
        active_member_refs=active_members or (lambda *_: {"actor-safe"}),
        active_admins=active_admins or (
            lambda *_: {"admin-safe": "admin@example.test"}
        ),
        member_names=member_names or (lambda *_: {"actor-safe": "Lin Finance"}),
        sender=email_sender,
        automatic_enabled=automatic_enabled or (lambda: True),
        portal_url=portal_url,
    )
    return repository, costs, email_sender, evaluator


def test_partial_priced_usage_crosses_once_and_renders_all_approved_values() -> None:
    repository, _costs, sender, evaluator = _scenario()

    summary = evaluator.evaluate_tenant(
        "tenant-safe", now=NOW, workspace_ids=("ws-a",)
    )

    assert summary.created == 2
    assert summary.sent == 1
    assert sorted(
        (alert.threshold_pct, alert.delivery_state)
        for alert in repository.list_alerts("tenant-safe")
    ) == [(80, "suppressed"), (95, "sent")]
    assert sender.messages[0].subject == "Lin Finance 95% / 2026-07"
    assert sender.messages[0].plain_text == (
        "200|190|95|95.0|"
        "https://dataforge.example.test/operations/member-budgets"
    )
    assert sender.messages[0].recipient == "admin@example.test"

    evaluator.evaluate_tenant("tenant-safe", now=NOW, workspace_ids=("ws-a",))
    assert len(sender.operation_ids) == 1


def test_direct_jump_coalesces_to_only_highest_delivery() -> None:
    repository, _costs, sender, evaluator = _scenario(
        spend="210", priced=21, total=21
    )

    summary = evaluator.evaluate_tenant("tenant-safe", now=NOW)

    assert summary.created == 3
    assert summary.sent == 1
    assert sorted(
        (alert.threshold_pct, alert.delivery_state)
        for alert in repository.list_alerts("tenant-safe")
    ) == [(80, "suppressed"), (95, "suppressed"), (100, "sent")]
    assert len(sender.operation_ids) == 1


@pytest.mark.parametrize(
    ("spend", "priced", "total"),
    ((None, 0, 1), ("0", 0, 0), ("190", 0, 1)),
)
def test_unavailable_or_unpriced_usage_never_claims(
    spend: str | None, priced: int, total: int
) -> None:
    repository, _costs, sender, evaluator = _scenario(
        spend=spend, priced=priced, total=total
    )

    summary = evaluator.evaluate_tenant("tenant-safe", now=NOW)

    assert summary.created == 0
    assert repository.list_alerts("tenant-safe") == ()
    assert sender.operation_ids == []


def test_retry_backoff_is_due_only_never_retries_in_same_evaluation_and_stops_at_three() -> None:
    repository, _costs, sender, evaluator = _scenario(sender=_Sender(failures=99))

    first = evaluator.evaluate_tenant("tenant-safe", now=NOW)
    alert = next(
        row for row in repository.list_alerts("tenant-safe")
        if row.threshold_pct == 95
    )
    assert first.failed == 1
    assert alert.attempt_count == 1
    assert alert.next_attempt_at == NOW + timedelta(minutes=5)
    assert len(sender.operation_ids) == 1

    evaluator.evaluate_tenant("tenant-safe", now=NOW)
    assert len(sender.operation_ids) == 1
    evaluator.evaluate_tenant("tenant-safe", now=NOW + timedelta(minutes=4, seconds=59))
    assert len(sender.operation_ids) == 1

    evaluator.evaluate_tenant("tenant-safe", now=NOW + timedelta(minutes=5))
    alert = next(
        row for row in repository.list_alerts("tenant-safe")
        if row.threshold_pct == 95
    )
    assert alert.attempt_count == 2
    assert alert.next_attempt_at == NOW + timedelta(minutes=15)
    assert len(sender.operation_ids) == 2

    evaluator.evaluate_tenant("tenant-safe", now=NOW + timedelta(minutes=15))
    alert = next(
        row for row in repository.list_alerts("tenant-safe")
        if row.threshold_pct == 95
    )
    assert alert.attempt_count == 3
    assert alert.next_attempt_at is None
    assert len(sender.operation_ids) == 3

    evaluator.evaluate_tenant("tenant-safe", now=NOW + timedelta(days=1))
    assert len(sender.operation_ids) == 3
    assert len(set(sender.operation_ids)) == 1


def test_expired_lease_is_acquired_by_exactly_one_worker() -> None:
    repository = InMemoryMemberBudgetRepository()
    stale = _alert(
        delivery_state="sending",
        attempt_count=1,
        lease_token="old-token",
        lease_expires_at=NOW - timedelta(seconds=1),
    )
    assert repository.claim_alert(stale)
    barrier = Barrier(3)
    acquired: list[BudgetAlert | None] = []

    def worker(token: str) -> None:
        barrier.wait()
        acquired.append(
            repository.acquire_due_alert(
                "tenant-safe",
                now=NOW,
                lease_token=token,
                lease_expires_at=NOW + timedelta(minutes=15),
            )
        )

    threads = [Thread(target=worker, args=(f"worker-{index}",)) for index in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    winners = [row for row in acquired if row is not None]
    assert len(winners) == 1
    assert winners[0].attempt_count == 2
    assert winners[0].lease_token in {"worker-0", "worker-1"}


def test_final_transition_requires_current_lease_token() -> None:
    repository = InMemoryMemberBudgetRepository()
    assert repository.claim_alert(_alert())
    acquired = repository.acquire_due_alert(
        "tenant-safe",
        now=NOW,
        lease_token="worker-a",
        lease_expires_at=NOW + timedelta(minutes=15),
    )
    assert acquired is not None
    sent = acquired.model_copy(
        update={
            "delivery_state": "sent",
            "lease_token": None,
            "lease_expires_at": None,
            "sent_at": NOW,
        }
    )

    assert repository.finalize_alert(
        "tenant-safe", acquired.alert_id, lease_token="worker-b", value=sent
    ) is False
    assert repository.finalize_alert(
        "tenant-safe", acquired.alert_id, lease_token="worker-a", value=sent
    ) is True


def test_alert_delivery_lease_is_never_returned_by_public_model_dump() -> None:
    value = _alert(
        delivery_state="sending",
        lease_token="private-worker-token",
        lease_expires_at=NOW + timedelta(minutes=15),
    )

    public = value.model_dump(mode="json")

    assert "lease_token" not in public
    assert "lease_expires_at" not in public
    assert "next_attempt_at" not in public
    assert "private-worker-token" not in str(public)


def test_send_revalidates_active_member_and_suppresses_invalidated_claim() -> None:
    calls = 0

    def active_members(*_args: Any) -> set[str]:
        nonlocal calls
        calls += 1
        return {"actor-safe"} if calls == 1 else set()

    repository, _costs, sender, evaluator = _scenario(active_members=active_members)

    summary = evaluator.evaluate_tenant("tenant-safe", now=NOW)

    assert summary.sent == 0
    assert sender.operation_ids == []
    alert = next(
        row for row in repository.list_alerts("tenant-safe")
        if row.threshold_pct == 95
    )
    assert alert.delivery_state == "suppressed"
    assert alert.attempt_count == 1


@pytest.mark.parametrize("invalidated", ("recipient", "notification", "gate"))
def test_send_revalidates_recipient_notification_revision_and_automatic_gate(
    invalidated: str,
) -> None:
    admin_calls = 0
    gate_calls = 0

    def admins(*_args: Any) -> dict[str, str]:
        nonlocal admin_calls
        admin_calls += 1
        if invalidated == "recipient" and admin_calls > 1:
            return {"admin-safe": "changed@example.test"}
        return {"admin-safe": "admin@example.test"}

    def gate() -> bool:
        nonlocal gate_calls
        gate_calls += 1
        return not (invalidated == "gate" and gate_calls > 1)

    repository, _costs, sender, evaluator = _scenario(
        active_admins=admins, automatic_enabled=gate
    )
    original_acquire = repository.acquire_due_alert

    def acquire_and_invalidate(*args: Any, **kwargs: Any) -> BudgetAlert | None:
        alert = original_acquire(*args, **kwargs)
        if alert is not None and invalidated == "notification":
            current = repository.get_notification_setting("tenant-safe")
            assert current is not None
            repository.save_notification_setting(
                "tenant-safe",
                current.model_copy(update={"revision": 2, "updated_at": NOW}),
                base_revision=1,
            )
        return alert

    repository.acquire_due_alert = acquire_and_invalidate  # type: ignore[method-assign]

    evaluator.evaluate_tenant("tenant-safe", now=NOW)

    assert sender.operation_ids == []
    alert = next(
        row for row in repository.list_alerts("tenant-safe")
        if row.threshold_pct == 95
    )
    assert alert.delivery_state == "suppressed"


def test_send_revalidates_budget_revision_notification_recipient_and_downward_spend() -> None:
    repository, costs, sender, evaluator = _scenario()
    original_acquire = repository.acquire_due_alert
    changed = False

    def acquire_and_change(*args: Any, **kwargs: Any) -> BudgetAlert | None:
        nonlocal changed
        alert = original_acquire(*args, **kwargs)
        if alert is not None and not changed:
            changed = True
            current = repository.get_budget("tenant-safe", "budget-actor-safe")
            assert current is not None
            repository.save_budget(
                "tenant-safe",
                current.model_copy(
                    update={
                        "revision": 2,
                        "amount_usd": Decimal("500"),
                        "updated_at": NOW,
                    }
                ),
                base_revision=1,
            )
            costs.summaries["actor-safe"] = MemberCostSummary(
                actor_ref="actor-safe",
                estimated_spend_usd=Decimal("10"),
                priced_requests=1,
                total_requests=1,
            )
        return alert

    repository.acquire_due_alert = acquire_and_change  # type: ignore[method-assign]
    evaluator.evaluate_tenant("tenant-safe", now=NOW)

    assert sender.operation_ids == []
    alert = next(
        row for row in repository.list_alerts("tenant-safe")
        if row.threshold_pct == 95
    )
    assert alert.delivery_state == "suppressed"


def test_prior_month_due_alert_is_suppressed_without_send() -> None:
    repository, _costs, sender, evaluator = _scenario(spend="0", priced=1, total=1)
    assert repository.claim_alert(_alert(period_key="2026-06"))

    evaluator.evaluate_tenant("tenant-safe", now=NOW)

    assert sender.operation_ids == []
    assert repository.list_alerts("tenant-safe")[0].delivery_state == "suppressed"


def test_due_alert_after_more_than_101_sent_rows_is_not_starved() -> None:
    repository, _costs, sender, evaluator = _scenario(spend="190", priced=1, total=1)
    for index in range(110):
        assert repository.claim_alert(
            _alert(
                alert_id=f"sent-{index}",
                budget_id=f"history-{index}",
                threshold_pct=80,
                delivery_state="sent",
                sent_at=NOW,
                triggered_at=NOW - timedelta(days=2),
            )
        )
    assert repository.claim_alert(
        _alert(
            alert_id="due-alert",
            threshold_pct=95,
            delivery_state="failed",
            attempt_count=1,
            next_attempt_at=NOW,
        )
    )

    evaluator.evaluate_tenant("tenant-safe", now=NOW)

    due = next(
        row for row in repository.list_alerts(
            "tenant-safe", budget_id="budget-actor-safe"
        )
        if row.alert_id == "due-alert"
    )
    assert due.delivery_state == "sent"
    assert sender.operation_ids


def test_invalid_portal_url_is_rejected_before_any_send() -> None:
    with pytest.raises(ValueError, match="portal URL"):
        _scenario(portal_url="https://user:secret@example.test/#fragment")


def test_final_cas_loss_is_not_reported_as_sent() -> None:
    repository, _costs, sender, evaluator = _scenario()
    original_finalize = repository.finalize_alert

    def lose_sent_cas(
        tenant_ref: str, alert_id: str, *, lease_token: str, value: BudgetAlert
    ) -> bool:
        if value.delivery_state == "sent":
            return False
        return original_finalize(
            tenant_ref, alert_id, lease_token=lease_token, value=value
        )

    repository.finalize_alert = lose_sent_cas  # type: ignore[method-assign]

    summary = evaluator.evaluate_tenant("tenant-safe", now=NOW)

    assert len(sender.operation_ids) == 1
    assert summary.sent == 0


def _alert(**updates: Any) -> BudgetAlert:
    base = BudgetAlert(
        alert_id="alert-safe",
        tenant_ref="tenant-safe",
        budget_id="budget-actor-safe",
        actor_ref="actor-safe",
        period_key="2026-07",
        threshold_pct=95,
        budget_amount_usd=Decimal("200"),
        estimated_spend_usd=Decimal("190"),
        pricing_coverage_pct=95,
        budget_revision=1,
        notification_revision=1,
        delivery_state="pending",
        triggered_at=NOW,
        updated_at=NOW,
    )
    return base.model_copy(update=updates)
