from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.finops.models import FinOpsRequestEvent, TokenUsage
from backend.finops.anomaly_store import ManagedAnomaly
from backend.finops.sql_anomalies import SqlFinOpsAnomalyRepository
from backend.finops.repository import FinOpsEventKeyRepair
from backend.finops.sql_repository import FinOpsPersistenceError, SqlFinOpsRepository


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
            "routing_policy_revision": 7,
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
    *schema_calls, merge_call = connection.cursor_value.calls
    assert schema_calls
    assert all("finops:schema" in call[0] for call in schema_calls)
    assert "finops:upsert-request-event" in merge_call[0]
    normalized_merge = " ".join(merge_call[0].split())
    assert (
        "ON target.tenant_ref = source.tenant_ref "
        "AND target.request_ref = source.request_ref"
    ) in normalized_merge
    assert "routing_policy_revision = ?" in normalized_merge
    assert (
        "route, routing_policy_revision, execution_kind"
        in normalized_merge
    )
    serialized = str(merge_call[1])
    assert "must-not-persist" not in serialized
    assert "tenant-safe" in serialized
    assert "req_aaaaaaaaaaaa" in serialized


def test_sql_repository_executes_go_delimited_schema_batches_separately(
    tmp_path: Path,
) -> None:
    schema_path = tmp_path / "schema.sql"
    schema_path.write_text(
        "CREATE TABLE df_finops.example (id INT NOT NULL);\n"
        "GO\n"
        "ALTER TABLE df_finops.example ADD label NVARCHAR(32) NULL;\n",
        encoding="utf-8",
    )
    connection = RecordingConnection()
    repository = SqlFinOpsRepository(
        connection_factory=lambda: connection,
        schema_path=schema_path,
    )

    repository.initialize_schema()

    operations = [operation for operation, _ in connection.cursor_value.calls]
    assert len(operations) == 2
    assert "CREATE TABLE df_finops.example" in operations[0]
    assert "ALTER TABLE df_finops.example" in operations[1]
    assert all("\nGO\n" not in operation.upper() for operation in operations)
    assert connection.commits == 1


def test_sql_repository_reads_historical_payload_without_routing_revision_as_null() -> None:
    connection = RecordingConnection()
    payload = _event().model_dump(mode="json")
    payload.pop("routing_policy_revision")
    connection.cursor_value.rows = [
        (json.dumps(payload, separators=(",", ":")),)
    ]
    repository = SqlFinOpsRepository(connection_factory=lambda: connection)

    [event] = repository.list_events(
        tenant_ref="tenant-safe",
        workspace_ids=("ws-a",),
        from_value="2026-07-01T00:00:00Z",
        to_value="2026-08-01T00:00:00Z",
    )

    assert event.routing_policy_revision is None


def test_sql_event_rekey_uses_one_locked_transaction_and_deletes_legacy_key() -> None:
    connection = RecordingConnection()
    repository = SqlFinOpsRepository(connection_factory=lambda: connection)
    canonical = _event().model_copy(
        update={
            "tenant_ref": "tenant-canonical",
            "request_ref": "req_canonicalaaaa",
            "actor_ref": "actor_canonicalaaa",
        }
    )
    legacy = canonical.model_copy(
        update={
            "tenant_ref": "tenant-legacy",
            "request_ref": "req_legacyaaaaaa",
            "actor_ref": "actor_legacyyyyyy",
        }
    )
    connection.cursor_value.rows = [
        (
            legacy.tenant_ref,
            legacy.request_ref,
            json.dumps(legacy.model_dump(mode="json"), separators=(",", ":")),
        )
    ]

    changed = repository.repair_event_keys(
        [
            FinOpsEventKeyRepair(
                legacy_tenant_ref="tenant-legacy",
                legacy_request_ref="req_legacyaaaaaa",
                canonical_event=canonical,
            )
        ]
    )

    operations = [call[0] for call in connection.cursor_value.calls]
    assert changed == 1
    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert "WITH (UPDLOCK, HOLDLOCK)" in operations[0]
    assert "finops:upsert-request-event" in operations[1]
    assert "finops:delete-legacy-request-event" in operations[2]


def test_sql_event_rekey_rolls_back_when_atomic_delete_fails() -> None:
    class FailingCursor(RecordingCursor):
        def execute(self, operation: str, *parameters: object) -> "RecordingCursor":
            result = super().execute(operation, *parameters)
            if "finops:delete-legacy-request-event" in operation:
                raise RuntimeError("database detail must not escape")
            return result

    connection = RecordingConnection()
    connection.cursor_value = FailingCursor()
    repository = SqlFinOpsRepository(connection_factory=lambda: connection)
    canonical = _event()
    legacy = canonical.model_copy(
        update={
            "tenant_ref": "tenant-legacy",
            "request_ref": "req_legacyaaaaaa",
        }
    )
    connection.cursor_value.rows = [
        (
            legacy.tenant_ref,
            legacy.request_ref,
            json.dumps(legacy.model_dump(mode="json"), separators=(",", ":")),
        )
    ]

    with pytest.raises(FinOpsPersistenceError) as error:
        repository.repair_event_keys(
            [
                FinOpsEventKeyRepair(
                    legacy_tenant_ref="tenant-legacy",
                    legacy_request_ref="req_legacyaaaaaa",
                    canonical_event=canonical,
                )
            ]
        )

    assert str(error.value) == "FinOps SQL operation failed"
    assert connection.commits == 0
    assert connection.rollbacks == 1


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
