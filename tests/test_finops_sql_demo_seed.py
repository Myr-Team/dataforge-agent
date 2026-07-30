from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.finops.sql_demo_seed import SqlDemoSeedRepository


class RecordingCursor:
    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self.rows = rows or []
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, operation: str, *parameters: Any) -> "RecordingCursor":
        self.calls.append((operation, parameters))
        return self

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self.rows)


class RecordingConnection:
    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self.autocommit = True
        self.cursor_value = RecordingCursor(rows)
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


def test_demo_seed_schema_is_additive_and_internal() -> None:
    schema = (
        Path(__file__).resolve().parents[1]
        / "backend"
        / "sql"
        / "finops_schema.sql"
    ).read_text(encoding="utf-8").lower()

    assert "df_finops.demo_seed_event" in schema
    assert "seed_batch" in schema
    assert "drop table" not in schema
    assert "truncate table" not in schema


def test_sql_seed_repository_replaces_one_bounded_batch() -> None:
    connection = RecordingConnection(rows=[("req_existing",)])
    repository = SqlDemoSeedRepository(connection_factory=lambda: connection)

    created, updated = repository.replace_batch(
        tenant_ref="tenant_demo",
        workspace_id="ws-demo",
        batch="operations-v1",
        request_refs=("req_existing", "req_new"),
    )

    operations = "\n".join(operation for operation, _ in connection.cursor_value.calls)
    parameters = str([parameters for _, parameters in connection.cursor_value.calls])
    assert created == 1
    assert updated == 1
    assert "finops:list-demo-seed-batch" in operations
    assert "finops:upsert-demo-seed-event" in operations
    assert "finops:delete-stale-demo-seed-event" in operations
    assert "tenant_demo" in parameters
    assert "ws-demo" in parameters
    assert "operations-v1" in parameters
    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert connection.closed is True
