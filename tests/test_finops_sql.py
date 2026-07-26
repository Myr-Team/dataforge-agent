from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from backend.finops.models import FinOpsRequestEvent, TokenUsage
from backend.finops.anomaly_store import ManagedAnomaly
from backend.finops.sql_anomalies import SqlFinOpsAnomalyRepository
from backend.finops.sql_repository import SqlFinOpsRepository


class RecordingCursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.rows: list[tuple[object, ...]] = []

    def execute(self, operation: str, *parameters: object) -> "RecordingCursor":
        self.calls.append((operation, parameters))
        return self

    def fetchall(self) -> list[tuple[object, ...]]:
        return list(self.rows)

    def fetchone(self) -> tuple[object, ...] | None:
        return self.rows[0] if self.rows else None


class RecordingConnection:
    def __init__(self) -> None:
        self.autocommit = False
        self.cursor_value = RecordingCursor()
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self) -> RecordingCursor:
        return self.cursor_value

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


def _event() -> FinOpsRequestEvent:
    return FinOpsRequestEvent.model_validate(
        {
            "request_ref": "req_aaaaaaaaaaaa",
            "occurred_at": datetime(2026, 7, 24, 2, 0, tzinfo=timezone.utc),
            "call_class": "model",
            "tenant_ref": "tenant-safe",
            "workspace_id": "ws-a",
            "status": "succeeded",
            "tokens": TokenUsage(input=10, output=2, total=12),
            "gateway_coverage": "app_observed",
            "estimated_cost": {
                "amount": 0.001,
                "currency": "USD",
                "status": "estimated",
                "price_card_revision": "price-1",
            },
            "evidence_state": "observed",
            "usage_source": "provider",
            "internal_correlation_key": "must-not-persist",
        }
    )


def test_finops_schema_is_additive_and_contains_ledger_rollup_and_governance_tables() -> None:
    schema = (
        Path(__file__).resolve().parents[1]
        / "backend"
        / "sql"
        / "finops_schema.sql"
    ).read_text(encoding="utf-8")
    lowered = schema.lower()

    for table in (
        "request_event",
        "request_rollup_hour",
        "request_rollup_day",
        "department",
        "workspace_department",
        "price_card_revision",
        "price_card_item",
        "official_price_mapping",
        "assistant_conversation",
        "assistant_message",
        "anomaly",
        "policy",
        "governance_action",
        "action_transition",
        "evidence_alias",
        "insight",
    ):
        assert f"df_finops.{table}" in lowered
    assert "drop table" not in lowered
    assert "truncate table" not in lowered


def test_sql_repository_initializes_schema_and_upserts_only_public_event_payload() -> None:
    connection = RecordingConnection()
    repository = SqlFinOpsRepository(connection_factory=lambda: connection)

    repository.initialize_schema()
    repository.upsert_events([_event()])

    assert connection.commits == 2
    schema_call, merge_call = connection.cursor_value.calls
    assert "finops:schema" in schema_call[0]
    assert "finops:upsert-request-event" in merge_call[0]
    serialized = str(merge_call[1])
    assert "must-not-persist" not in serialized
    assert "tenant-safe" in serialized
    assert "req_aaaaaaaaaaaa" in serialized


def test_sql_anomaly_repository_upserts_lifecycle_state_without_raw_evidence() -> None:
    connection = RecordingConnection()
    repository = SqlFinOpsAnomalyRepository(connection_factory=lambda: connection)
    anomaly = ManagedAnomaly(
        anomaly_id="anomaly_error_ws-a",
        tenant_ref="tenant-safe",
        policy_type="error_rate",
        severity="warning",
        status="acknowledged",
        observed_value=8,
        threshold_value=5,
        sample_count=25,
        workspace_ids=["ws-a"],
        recommendation="Inspect categorized failures.",
        first_detected_at="2026-07-24T02:00:00Z",
        updated_at="2026-07-24T02:05:00Z",
        acknowledged_by="actor-safe",
        acknowledged_at="2026-07-24T02:04:00Z",
    )

    repository.save(anomaly)

    operation, parameters = connection.cursor_value.calls[0]
    assert "finops:save-anomaly" in operation
    serialized = str(parameters)
    assert "actor-safe" in serialized
    assert "prompt" not in serialized
    assert "response_body" not in serialized


def test_sql_repository_lists_opaque_tenant_workspace_scopes_for_backfill() -> None:
    connection = RecordingConnection()
    connection.cursor_value.rows = [
        ("tenant-safe", "ws-a"),
        ("tenant-safe", "ws-b"),
        ("tenant-other", "ws-c"),
    ]
    repository = SqlFinOpsRepository(connection_factory=lambda: connection)

    scopes = repository.list_scopes(
        from_value="2026-07-24T01:55:00Z",
        to_value="2026-07-24T02:05:00Z",
    )

    assert scopes == {
        "tenant-safe": ("ws-a", "ws-b"),
        "tenant-other": ("ws-c",),
    }
    assert "finops:list-scopes" in connection.cursor_value.calls[0][0]
