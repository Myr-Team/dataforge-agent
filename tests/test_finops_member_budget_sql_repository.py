from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from backend.finops.member_budget_repository import MemberBudgetConflictError
from backend.finops.member_budgets import BudgetAlert, MemberBudget, NotificationSetting
from backend.finops.sql_member_budgets import SqlMemberBudgetRepository
from backend.finops.sql_member_budgets import _is_unique_violation
from backend.finops.sql_repository import FinOpsPersistenceError


class _RecordingCursor:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self._response: Any = None

    def execute(self, operation: str, *parameters: Any) -> "_RecordingCursor":
        self.calls.append((operation, parameters))
        self._response = self.responses.pop(0) if self.responses else None
        if isinstance(self._response, BaseException):
            raise self._response
        return self

    def fetchone(self) -> Any:
        return self._response

    def fetchall(self) -> list[Any]:
        return list(self._response or [])


class _RecordingConnection:
    def __init__(self, responses: list[Any]) -> None:
        self.autocommit = True
        self.cursor_value = _RecordingCursor(responses)
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self) -> _RecordingCursor:
        return self.cursor_value

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def _repository(*connections: _RecordingConnection) -> SqlMemberBudgetRepository:
    pending = list(connections)
    return SqlMemberBudgetRepository(connection_factory=lambda: pending.pop(0))


def _budget(*, revision: int = 1, enabled: bool = True) -> MemberBudget:
    now = datetime(2026, 7, 29, tzinfo=timezone.utc)
    return MemberBudget(
        member_ref="actor_safe",
        amount_usd=Decimal("200"),
        thresholds_pct=(80, 95, 100),
        enabled=enabled,
        budget_id="budget_safe",
        revision=revision,
        created_by_ref="admin_safe",
        updated_by_ref="admin_safe",
        created_at=now,
        updated_at=now,
    )


def _budget_row(*, revision: int = 1, enabled: int = 1) -> tuple[Any, ...]:
    value = _budget(revision=revision, enabled=bool(enabled))
    return (
        value.budget_id, value.member_ref, value.period_type, value.amount_usd,
        b"[80,95,100]", enabled, value.revision, value.created_by_ref,
        value.updated_by_ref, value.created_at, value.updated_at,
    )


def _notification(*, revision: int = 1) -> NotificationSetting:
    now = datetime(2026, 7, 29, tzinfo=timezone.utc)
    return NotificationSetting(
        recipient_actor_ref="admin_safe",
        recipient_email="admin@example.test",
        sender_display_name="DataForge",
        subject_template="Budget alert",
        body_template="Spend reached {threshold}",
        enabled=True,
        revision=revision,
        created_by_ref="admin_safe",
        updated_by_ref="admin_safe",
        created_at=now,
        updated_at=now,
    )


def _alert(*, alert_id: str = "alert_safe", threshold: int = 80) -> BudgetAlert:
    now = datetime(2026, 7, 29, tzinfo=timezone.utc)
    return BudgetAlert(
        alert_id=alert_id,
        tenant_ref="tenant_safe",
        budget_id="budget_safe",
        actor_ref="actor_safe",
        period_key="2026-07",
        threshold_pct=threshold,
        budget_amount_usd=Decimal("200"),
        estimated_spend_usd=Decimal("190"),
        pricing_coverage_pct=95,
        budget_revision=1,
        notification_revision=1,
        delivery_state="pending",
        triggered_at=now,
        updated_at=now,
    )


def test_sql_repository_places_tenant_first_and_decodes_threshold_json() -> None:
    get_connection = _RecordingConnection([_budget_row()])
    list_connection = _RecordingConnection([[_budget_row(enabled=0)]])
    repository = _repository(get_connection, list_connection)

    assert repository.get_budget("tenant_safe", "budget_safe").thresholds_pct == (80, 95, 100)
    assert repository.list_budgets("tenant_safe", include_disabled=True)[0].enabled is False
    assert get_connection.cursor_value.calls[0][1] == ("tenant_safe", "budget_safe")
    assert list_connection.cursor_value.calls[0][1] == ("tenant_safe",)
    assert get_connection.committed and get_connection.closed
    assert list_connection.committed and list_connection.closed


def test_sql_repository_upserts_and_disables_with_ordered_tenant_parameters() -> None:
    save_connection = _RecordingConnection([None, None, None])
    get_connection = _RecordingConnection([_budget_row()])
    disable_connection = _RecordingConnection([(1,), None])
    repository = _repository(save_connection, get_connection, disable_connection)

    repository.save_budget("tenant_safe", _budget(), base_revision=0)
    disabled = repository.disable_budget(
        "tenant_safe", "budget_safe", base_revision=1, updated_by_ref="admin_safe"
    )

    save_calls = save_connection.cursor_value.calls
    assert save_calls[0][1] == ("tenant_safe", "budget_safe")
    assert save_calls[1][1] == ("tenant_safe", "actor_safe", "budget_safe")
    assert len(save_calls[2][1]) == 22
    assert save_calls[2][1][:2] == ("tenant_safe", "budget_safe")
    assert save_calls[2][1][10:12] == ("tenant_safe", "budget_safe")
    assert disabled.enabled is False
    assert disable_connection.cursor_value.calls[0][1] == ("tenant_safe", "budget_safe")
    assert len(disable_connection.cursor_value.calls[1][1]) == 22
    assert disable_connection.cursor_value.calls[1][1][:2] == ("tenant_safe", "budget_safe")


def test_sql_repository_saves_one_revisioned_notification_setting_with_ordered_parameters() -> None:
    connection = _RecordingConnection([None, None])
    value = _notification()
    repository = _repository(connection)

    assert repository.save_notification_setting("tenant_safe", value, base_revision=0) == value
    calls = connection.cursor_value.calls
    assert calls[0][1] == ("tenant_safe",)
    assert len(calls[1][1]) == 22
    assert calls[1][1][0] == "tenant_safe"
    assert calls[1][1][10] == "tenant_safe"
    assert connection.committed and not connection.rolled_back and connection.closed


def test_sql_repository_turns_racing_notification_save_into_typed_conflict() -> None:
    connection = _RecordingConnection([None, RuntimeError("[23000] 2601 duplicate")])
    repository = _repository(connection)

    with pytest.raises(MemberBudgetConflictError, match="notification setting revision"):
        repository.save_notification_setting("tenant_safe", _notification(), base_revision=0)
    assert connection.rolled_back and connection.closed


def test_sql_repository_decodes_alert_nulls_and_uses_tenant_scoped_alert_query() -> None:
    now = datetime(2026, 7, 29, tzinfo=timezone.utc)
    connection = _RecordingConnection([[
        ("alert_safe", "budget_safe", "actor_safe", "2026-07", 80, Decimal("200"),
         Decimal("190"), None, 1, 1, "pending", None, 0, now, None, now)
    ]])
    repository = _repository(connection)

    value = repository.list_alerts("tenant_safe")[0]
    assert value.pricing_coverage_pct is None
    assert value.safe_error_category is None
    assert value.sent_at is None
    assert connection.cursor_value.calls[0][1] == ("tenant_safe",)


def test_sql_repository_returns_false_only_for_existing_threshold_claim() -> None:
    insert_connection = _RecordingConnection([RuntimeError("[23000] 2627 duplicate")])
    lookup_connection = _RecordingConnection([("different_alert",)])
    repository = _repository(insert_connection, lookup_connection)

    assert repository.claim_alert(_alert()) is False
    assert len(insert_connection.cursor_value.calls[0][1]) == 17
    assert insert_connection.cursor_value.calls[0][1][:6] == (
        "tenant_safe", "alert_safe", "budget_safe", "actor_safe", "2026-07", 80
    )
    assert lookup_connection.cursor_value.calls[0][1] == (
        "tenant_safe", "budget_safe", "2026-07", 80
    )


def test_sql_repository_rejects_alert_id_collision_for_a_different_threshold() -> None:
    insert_connection = _RecordingConnection([RuntimeError("[23000] 2627 duplicate")])
    lookup_connection = _RecordingConnection([None])
    repository = _repository(insert_connection, lookup_connection)

    with pytest.raises(MemberBudgetConflictError, match="alert id"):
        repository.claim_alert(_alert(threshold=95))
    assert insert_connection.rolled_back and insert_connection.closed
    assert lookup_connection.committed and lookup_connection.closed


@pytest.mark.parametrize(
    ("responses", "value", "base_revision"),
    [
        ([(2,)], _budget(revision=3), 1),
        ([None, None, RuntimeError("[23000] 2601 duplicate")], _budget(), 0),
    ],
)
def test_sql_repository_turns_stale_or_racing_budget_writes_into_typed_conflicts(
    responses: list[Any], value: MemberBudget, base_revision: int
) -> None:
    connection = _RecordingConnection(responses)
    repository = _repository(connection)

    with pytest.raises(MemberBudgetConflictError):
        repository.save_budget("tenant_safe", value, base_revision=base_revision)
    assert connection.rolled_back and connection.closed


def test_sql_repository_stops_before_merge_when_another_active_member_budget_exists() -> None:
    connection = _RecordingConnection([(1,), ("other_budget",)])
    repository = _repository(connection)

    with pytest.raises(MemberBudgetConflictError, match="active member budget"):
        repository.save_budget("tenant_safe", _budget(revision=2), base_revision=1)
    assert len(connection.cursor_value.calls) == 2
    assert "save-member-budget" not in connection.cursor_value.calls[-1][0]
    assert not connection.committed
    assert connection.rolled_back and connection.closed


@pytest.mark.parametrize(
    "driver_error",
    (
        RuntimeError("23000", "[Microsoft][ODBC Driver 18 for SQL Server][SQL Server] (2601)"),
        RuntimeError(("23000", "[SQL Server]Violation of PRIMARY KEY constraint (2627)")),
    ),
)
def test_unique_violation_recognizes_only_sql_server_native_codes(driver_error: RuntimeError) -> None:
    wrapper = RuntimeError("database operation failed")
    wrapper.__cause__ = driver_error
    assert _is_unique_violation(wrapper) is True


@pytest.mark.parametrize("message", ("duplicate transport packet", "unique network failure"))
def test_sql_repository_does_not_treat_generic_duplicate_words_as_conflicts(message: str) -> None:
    connection = _RecordingConnection([None, None, RuntimeError(message)])
    repository = _repository(connection)

    with pytest.raises(FinOpsPersistenceError) as captured:
        repository.save_budget("tenant_safe", _budget(), base_revision=0)
    assert not isinstance(captured.value, MemberBudgetConflictError)
    assert str(captured.value) == "Member budget SQL operation failed"
    assert connection.rolled_back and connection.closed


def test_sql_repository_wraps_infrastructure_errors_without_leaking_details() -> None:
    connection = _RecordingConnection([RuntimeError("permission denied tenant-secret")])
    repository = _repository(connection)

    with pytest.raises(FinOpsPersistenceError) as captured:
        repository.get_budget("tenant_safe", "budget_safe")
    assert str(captured.value) == "Member budget SQL operation failed"
    assert "tenant-secret" not in repr(captured.value)
    assert connection.rolled_back and connection.closed


def test_sql_member_costs_bound_both_queries_to_authorized_workspaces_and_preserve_zero_vs_absent() -> None:
    connection = _RecordingConnection([[
        ("actor-partial", Decimal("190"), 19, 20),
        ("actor-unpriced", None, 0, 1),
        ("actor-zero", Decimal("0"), 1, 1),
    ], [
        ("actor-partial", "gpt-5.6-terra"),
        ("actor-zero", "gpt-5.6-terra"),
    ]])
    repository = _repository(connection)

    values = repository.summarize_member_costs(
        tenant_ref="tenant-safe",
        from_value="2026-07-01T00:00:00Z",
        to_value="2026-08-01T00:00:00Z",
        workspace_ids=("ws-a", "ws-a", "ws-b"),
    )

    assert values["actor-partial"].estimated_spend_usd == Decimal("190")
    assert values["actor-partial"].pricing_coverage_pct == 95
    assert values["actor-partial"].data_status == "partial"
    assert values["actor-unpriced"].estimated_spend_usd is None
    assert values["actor-unpriced"].data_status == "unavailable"
    assert values["actor-zero"].estimated_spend_usd == Decimal("0")
    assert values["actor-zero"].data_status == "complete"
    calls = connection.cursor_value.calls
    assert len(calls) == 2
    assert all("workspace_id IN (?, ?)" in operation for operation, _parameters in calls)
    assert calls[0][1] == ("tenant-safe", "ws-a", "ws-b", "2026-07-01T00:00:00Z", "2026-08-01T00:00:00Z")
    assert calls[1][1] == calls[0][1]


def test_sql_member_costs_empty_authorized_scope_returns_empty_without_opening_connection() -> None:
    repository = SqlMemberBudgetRepository(
        connection_factory=lambda: (_ for _ in ()).throw(AssertionError("must not query all tenant facts"))
    )

    assert repository.summarize_member_costs(
        tenant_ref="tenant-safe",
        from_value="2026-07-01T00:00:00Z",
        to_value="2026-08-01T00:00:00Z",
        workspace_ids=(),
    ) == {}
