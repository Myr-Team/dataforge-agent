from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.finops.models import FinOpsRequestEvent
from backend.finops.query import FinOpsQuery
from backend.finops.rollups import aggregate_rollups
from backend.finops.rollup_refresh import _safe_exception_chain, refresh_rollups
from backend.finops.repository import InMemoryFinOpsRepository
from backend.finops.sql_repository import FinOpsPersistenceError
from backend.finops.sql_rollups import SqlFinOpsRollupRepository
from test_finops_sql import RecordingConnection


def _event(
    request_ref: str,
    minute: int,
    *,
    status: str,
    total_tokens: int | None,
    cost: float | None,
    latency_ms: int,
) -> FinOpsRequestEvent:
    return FinOpsRequestEvent.model_validate(
        {
            "request_ref": request_ref,
            "occurred_at": datetime(2026, 7, 24, 2, minute, tzinfo=timezone.utc),
            "call_class": "model",
            "tenant_ref": "tenant-a",
            "workspace_id": "ws-a",
            "agent_id": "coordinator",
            "deployment": "gpt-5-mini",
            "status": status,
            "latency_ms": latency_ms,
            "tokens": {"total": total_tokens},
            "gateway_coverage": "apim_governed",
            "estimated_cost": {
                "amount": cost,
                "currency": "USD",
                "status": "estimated" if cost is not None else "unavailable",
            },
            "evidence_state": "observed" if total_tokens is not None else "partial",
        }
    )


def test_rollups_aggregate_hour_and_day_without_turning_unknowns_into_zero() -> None:
    events = [
        _event("req_aaaaaaaaaaaa", 1, status="succeeded", total_tokens=10, cost=0.001, latency_ms=100),
        _event("req_bbbbbbbbbbbb", 2, status="failed", total_tokens=None, cost=None, latency_ms=500),
    ]

    hourly, daily = aggregate_rollups(events)

    assert len(hourly) == len(daily) == 1
    assert hourly[0].request_count == 2
    assert hourly[0].failure_count == 1
    assert hourly[0].total_tokens == 10
    assert hourly[0].estimated_cost == 0.001
    assert hourly[0].unpriced_count == 1
    assert hourly[0].p50_latency_ms == 100
    assert hourly[0].p95_latency_ms == 500


def test_rollups_preserve_cache_counts_and_avoided_tokens() -> None:
    payload = _event(
        "req_aaaaaaaaaaaa",
        1,
        status="succeeded",
        total_tokens=100,
        cost=0.004,
        latency_ms=100,
    ).model_dump(mode="python")
    payload["cache"] = {"state": "hit", "eligible": True, "avoided_tokens": 60}
    payload["result_cache"] = {
        "state": "hit",
        "eligible": True,
        "reason": "eligible",
        "policy_revision": 1,
    }
    event = FinOpsRequestEvent.model_validate(payload)

    hourly, _daily = aggregate_rollups([event])

    assert hourly[0].cache_hit_count == 1
    assert hourly[0].cache_miss_count == 0
    assert hourly[0].cache_bypassed_count == 0
    assert hourly[0].cache_avoided_tokens == 60


def test_rollups_collapse_identifier_case_variants_before_sql_persistence() -> None:
    first = _event(
        "req_aaaaaaaaaaaa",
        1,
        status="succeeded",
        total_tokens=100,
        cost=0.004,
        latency_ms=100,
    )
    variant_payload = _event(
        "req_bbbbbbbbbbbb",
        2,
        status="succeeded",
        total_tokens=200,
        cost=0.006,
        latency_ms=200,
    ).model_dump(mode="python")
    variant_payload.update(
        {
            "tenant_ref": "TENANT-A",
            "department_id": "UNASSIGNED",
            "workspace_id": "WS-A",
            "agent_id": "COORDINATOR",
            "deployment": "GPT-5-MINI",
        }
    )
    variant = FinOpsRequestEvent.model_validate(variant_payload)

    hourly, daily = aggregate_rollups([first, variant])

    assert len(hourly) == len(daily) == 1
    assert hourly[0].request_count == 2
    assert hourly[0].total_tokens == 300
    assert hourly[0].estimated_cost == 0.01
    assert hourly[0].tenant_ref == "tenant-a"
    assert hourly[0].workspace_id == "ws-a"
    assert hourly[0].agent_id == "coordinator"
    assert hourly[0].model_deployment == "gpt-5-mini"


def test_sql_rollup_repository_replaces_only_requested_tenant_window() -> None:
    connection = RecordingConnection()
    repository = SqlFinOpsRollupRepository(connection_factory=lambda: connection)
    hourly, daily = aggregate_rollups(
        [_event("req_aaaaaaaaaaaa", 1, status="succeeded", total_tokens=10, cost=0.001, latency_ms=100)]
    )

    repository.replace(
        tenant_ref="tenant-a",
        from_value="2026-07-24T02:00:00Z",
        to_value="2026-07-24T03:00:00Z",
        hourly=hourly,
        daily=daily,
    )

    operations = [operation for operation, _ in connection.cursor_value.calls]
    assert any("finops:delete-hour-rollups" in operation for operation in operations)
    assert any("finops:insert-hour-rollup" in operation for operation in operations)
    assert any("finops:delete-day-rollups" in operation for operation in operations)
    assert any("finops:insert-day-rollup" in operation for operation in operations)


def test_sql_rollup_repository_accepts_casefolded_tenant_scope() -> None:
    connection = RecordingConnection()
    repository = SqlFinOpsRollupRepository(connection_factory=lambda: connection)
    hourly, daily = aggregate_rollups(
        [_event("req_aaaaaaaaaaaa", 1, status="succeeded", total_tokens=10, cost=0.001, latency_ms=100)]
    )

    repository.replace(
        tenant_ref="TENANT-A",
        from_value="2026-07-24T02:00:00Z",
        to_value="2026-07-24T03:00:00Z",
        hourly=hourly,
        daily=daily,
    )

    assert connection.commits == 1


def test_sql_rollup_read_is_tenant_workspace_and_window_scoped() -> None:
    connection = RecordingConnection()
    connection.cursor_value.rows = [
        (
            "2026-07-24",
            "tenant-a",
            "commerce",
            "ws-a",
            "coordinator",
            "gpt-5-mini",
            2,
            1,
            20,
            0.01,
            100,
            200,
            2,
            0,
        )
    ]
    repository = SqlFinOpsRollupRepository(connection_factory=lambda: connection)
    query = FinOpsQuery(
        tenant_ref="tenant-a",
        authorized_workspace_ids=("ws-a",),
        workspace_id="ws-a",
        department_id="commerce",
        agent_id="coordinator",
        model="gpt-5-mini",
        from_value="2026-07-01T00:00:00Z",
        to_value="2026-08-01T00:00:00Z",
    )

    [rollup] = repository.read(query, "day")

    operation, parameters = connection.cursor_value.calls[0]
    assert "tenant_ref = ?" in operation
    assert "workspace_id IN (?)" in operation
    assert "bucket_at >=" in operation and "bucket_at <" in operation
    assert parameters[0] == "tenant-a"
    assert "ws-a" in parameters
    assert rollup.bucket_kind == "day"
    assert rollup.workspace_id == "ws-a"
    assert rollup.estimated_cost == 0.01


def test_rollup_refresh_processes_scopes_without_returning_scope_identifiers() -> None:
    events = InMemoryFinOpsRepository()
    events.upsert_events(
        [_event("req_aaaaaaaaaaaa", 1, status="succeeded", total_tokens=10, cost=0.001, latency_ms=100)]
    )

    class RollupSink:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def replace(self, **kwargs: object) -> None:
            self.calls.append(kwargs)

    sink = RollupSink()
    result = refresh_rollups(
        event_repository=events,
        rollup_repository=sink,
        scopes={"tenant-a": ("ws-a",)},
        from_value="2026-07-24T02:00:00Z",
        to_value="2026-07-24T03:00:00Z",
    )

    assert result == {"scope_count": 1, "event_count": 1, "hourly_rows": 1, "daily_rows": 1}
    assert len(sink.calls) == 1
    assert "tenant-a" not in str(result)


def test_rollup_refresh_invokes_budget_hook_only_after_success_and_isolates_failure() -> None:
    events = InMemoryFinOpsRepository()
    events.upsert_events(
        [_event("req_aaaaaaaaaaaa", 1, status="succeeded", total_tokens=10, cost=0.001, latency_ms=100)]
    )

    class RollupSink:
        def replace(self, **_kwargs: object) -> None:
            calls.append("rollup")

    class Evaluator:
        def evaluate_tenant(self, tenant_ref: str, **_kwargs: object) -> None:
            calls.append(f"budget:{tenant_ref}")
            raise RuntimeError("email delivery is isolated")

    calls: list[str] = []
    result = refresh_rollups(
        event_repository=events,
        rollup_repository=RollupSink(),
        scopes={"tenant-a": ("ws-a",)},
        from_value="2026-07-24T02:00:00Z",
        to_value="2026-07-24T03:00:00Z",
        budget_evaluator=Evaluator(),
    )

    assert calls == ["rollup", "budget:tenant-a"]
    assert result["scope_count"] == 1


def test_rollup_refresh_retries_a_transient_persistence_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    events = InMemoryFinOpsRepository()
    events.upsert_events(
        [_event("req_aaaaaaaaaaaa", 1, status="succeeded", total_tokens=10, cost=0.001, latency_ms=100)]
    )

    class FlakyRollupSink:
        def __init__(self) -> None:
            self.attempts = 0

        def replace(self, **_kwargs: object) -> None:
            self.attempts += 1
            if self.attempts == 1:
                raise FinOpsPersistenceError("transient write contention")

    delays: list[float] = []
    monkeypatch.setattr("backend.finops.rollup_refresh.time.sleep", delays.append)
    sink = FlakyRollupSink()

    result = refresh_rollups(
        event_repository=events,
        rollup_repository=sink,
        scopes={"tenant-a": ("ws-a",)},
        from_value="2026-07-24T02:00:00Z",
        to_value="2026-07-24T03:00:00Z",
    )

    assert result["scope_count"] == 1
    assert sink.attempts == 2
    assert delays == [0.5]


def test_rollup_refresh_keeps_persistence_retries_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    events = InMemoryFinOpsRepository()
    events.upsert_events(
        [_event("req_aaaaaaaaaaaa", 1, status="succeeded", total_tokens=10, cost=0.001, latency_ms=100)]
    )

    class FailedRollupSink:
        def __init__(self) -> None:
            self.attempts = 0

        def replace(self, **_kwargs: object) -> None:
            self.attempts += 1
            raise FinOpsPersistenceError("persistent write failure")

    delays: list[float] = []
    monkeypatch.setattr("backend.finops.rollup_refresh.time.sleep", delays.append)
    sink = FailedRollupSink()

    with pytest.raises(FinOpsPersistenceError):
        refresh_rollups(
            event_repository=events,
            rollup_repository=sink,
            scopes={"tenant-a": ("ws-a",)},
            from_value="2026-07-24T02:00:00Z",
            to_value="2026-07-24T03:00:00Z",
        )

    assert sink.attempts == 3
    assert delays == [0.5, 1.5]


def test_rollup_failure_diagnostics_expose_only_categories_and_sqlstate() -> None:
    database_error = RuntimeError(
        "23000",
        "Violation of PRIMARY KEY constraint 'PK_finops_request_rollup_hour'; "
        "credential-like detail must stay private",
    )
    wrapped = FinOpsPersistenceError("safe persistence category")
    wrapped.__cause__ = database_error

    diagnostics = _safe_exception_chain(wrapped)

    assert diagnostics == [
        {"category": "FinOpsPersistenceError"},
        {
            "category": "RuntimeError",
            "sqlstate": "23000",
            "constraint": "PK_finops_request_rollup_hour",
        },
    ]
    assert "credential-like" not in str(diagnostics)
