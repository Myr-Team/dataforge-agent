from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event

import pytest

from backend.model_provider_repository import (
    InMemoryModelProviderRepository,
    ModelProviderConflictError,
    ModelProviderNotFoundError,
    ModelProviderRepositoryError,
    SqlModelProviderRepository,
)
from backend.model_providers import ModelProviderRecord, ProviderPatch


def _record(*, tenant_ref: str = "tenant-a") -> ModelProviderRecord:
    now = datetime(2026, 7, 28, tzinfo=timezone.utc)
    return ModelProviderRecord(
        provider_id="provider_01",
        tenant_ref=tenant_ref,
        provider_type="deepseek",
        display_name="DeepSeek",
        base_url="https://api.deepseek.com",
        secret_ref="kv:provider-secret-reference",
        connection_state="testing",
        governance_state="pending",
        available_models=[],
        revision=1,
        created_by_ref="actor-a",
        updated_by_ref="actor-a",
        created_at=now,
        updated_at=now,
    )


def test_repository_isolates_identical_provider_ids_by_tenant() -> None:
    repository = InMemoryModelProviderRepository()
    repository.create(_record(tenant_ref="tenant-a"))
    repository.create(_record(tenant_ref="tenant-b"))

    assert [item.tenant_ref for item in repository.list("tenant-a")] == ["tenant-a"]
    assert [item.tenant_ref for item in repository.list("tenant-b")] == ["tenant-b"]

    with pytest.raises(ModelProviderNotFoundError):
        repository.get("tenant-c", "provider_01")


def test_repository_rejects_stale_provider_revision_without_mutation() -> None:
    repository = InMemoryModelProviderRepository()
    repository.create(_record())

    saved = repository.update(
        "tenant-a",
        "provider_01",
        ProviderPatch(base_revision=1, display_name="DeepSeek primary"),
        actor_ref="actor-b",
    )
    assert saved.revision == 2
    assert saved.display_name == "DeepSeek primary"

    with pytest.raises(ModelProviderConflictError):
        repository.update(
            "tenant-a",
            "provider_01",
            ProviderPatch(base_revision=1, display_name="stale overwrite"),
            actor_ref="actor-c",
        )

    current = repository.get("tenant-a", "provider_01")
    assert current.revision == 2
    assert current.display_name == "DeepSeek primary"


def test_repository_never_accepts_api_key_material() -> None:
    repository = InMemoryModelProviderRepository()

    with pytest.raises(TypeError):
        repository.create(_record(), api_key="forbidden-key-material")  # type: ignore[call-arg]


def test_in_memory_mutation_guard_is_keyed_across_repository_instances() -> None:
    first_repository = InMemoryModelProviderRepository()
    second_repository = InMemoryModelProviderRepository()
    attempted = Event()
    order: list[str] = []

    def competing_writer() -> None:
        attempted.set()
        with second_repository.mutation_guard("tenant-a", "provider_01"):
            order.append("competing")

    with ThreadPoolExecutor(max_workers=1) as pool:
        with first_repository.mutation_guard("tenant-a", "provider_01"):
            future = pool.submit(competing_writer)
            assert attempted.wait(timeout=2)
            order.append("holding")
        future.result(timeout=2)

    assert order == ["holding", "competing"]


def test_repository_persists_and_updates_provider_region() -> None:
    repository = InMemoryModelProviderRepository()
    repository.create(_record().model_copy(update={"region": "ap-southeast-1"}))

    saved = repository.update(
        "tenant-a",
        "provider_01",
        ProviderPatch(base_revision=1, region="us-west-2"),
        actor_ref="actor-b",
    )

    assert saved.region == "us-west-2"
    assert repository.get("tenant-a", "provider_01").region == "us-west-2"


class _SqlCursor:
    def __init__(self, row: tuple[object, ...]) -> None:
        self.operations: list[tuple[str, tuple[object, ...]]] = []
        self.row = row
        self.rowcount = 1

    def execute(self, operation: str, *parameters: object) -> "_SqlCursor":
        self.operations.append((operation, parameters))
        return self

    def fetchall(self) -> list[tuple[object, ...]]:
        return [self.row]

    def fetchone(self) -> tuple[object, ...]:
        return self.row


class _SqlConnection:
    autocommit = False

    def __init__(self, cursor: _SqlCursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _SqlCursor:
        return self._cursor

    def commit(self) -> None:
        pass

    def close(self) -> None:
        pass


class _GuardCursor:
    def __init__(self, acquire_result: int = 0) -> None:
        self.acquire_result = acquire_result
        self.operations: list[tuple[str, tuple[object, ...]]] = []
        self.rowcount = 1

    def execute(self, operation: str, *parameters: object) -> "_GuardCursor":
        self.operations.append((operation, parameters))
        return self

    def fetchone(self) -> tuple[int]:
        return (self.acquire_result,)


class _GuardConnection:
    autocommit = False

    def __init__(self, cursor: _GuardCursor) -> None:
        self._cursor = cursor
        self.closed = False

    def cursor(self) -> _GuardCursor:
        return self._cursor

    def close(self) -> None:
        self.closed = True


def test_sql_mutation_guard_holds_session_application_lock_until_exit() -> None:
    cursor = _GuardCursor()
    connection = _GuardConnection(cursor)
    repository = SqlModelProviderRepository(connection_factory=lambda: connection)

    with repository.mutation_guard("tenant-a", "provider_01"):
        assert not connection.closed
        assert connection.autocommit is True
        assert len(cursor.operations) == 1
        assert "sp_getapplock" in cursor.operations[0][0]
        assert "@LockOwner = N'Session'" in cursor.operations[0][0]

    assert connection.closed
    assert len(cursor.operations) == 2
    assert "sp_releaseapplock" in cursor.operations[1][0]
    assert "@LockOwner = N'Session'" in cursor.operations[1][0]
    assert cursor.operations[0][1][0] == cursor.operations[1][1][0]


def test_sql_mutation_guard_rejects_failed_application_lock() -> None:
    cursor = _GuardCursor(acquire_result=-1)
    connection = _GuardConnection(cursor)
    repository = SqlModelProviderRepository(connection_factory=lambda: connection)

    with pytest.raises(
        ModelProviderRepositoryError,
        match="model_provider_mutation_guard_unavailable",
    ):
        with repository.mutation_guard("tenant-a", "provider_01"):
            raise AssertionError("unreachable")

    assert connection.closed


def test_sql_repository_round_trips_and_updates_provider_region() -> None:
    value = _record().model_copy(update={"region": "ap-southeast-1"})
    row = (
        value.provider_id,
        value.tenant_ref,
        value.provider_type,
        value.display_name,
        value.base_url,
        value.region,
        value.secret_ref,
        value.connection_state,
        value.governance_state,
        "[]",
        value.last_tested_at,
        value.last_success_at,
        value.safe_error_category,
        value.revision,
        value.created_by_ref,
        value.updated_by_ref,
        value.created_at,
        value.updated_at,
    )
    cursor = _SqlCursor(row)
    repository = SqlModelProviderRepository(
        connection_factory=lambda: _SqlConnection(cursor)
    )

    repository.create(value)
    loaded = repository.get("tenant-a", "provider_01")
    repository.update(
        "tenant-a",
        "provider_01",
        ProviderPatch(base_revision=1, region="us-west-2"),
        actor_ref="actor-b",
    )

    assert loaded.region == "ap-southeast-1"
    create_operation, create_parameters = cursor.operations[0]
    assert "region" in create_operation
    assert create_parameters[5] == "ap-southeast-1"
    update_operation, update_parameters = cursor.operations[-1]
    assert "region = ?" in update_operation
    assert update_parameters[2] == "us-west-2"
