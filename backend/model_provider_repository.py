from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import Lock, RLock
from typing import Any, ContextManager, Iterator, Protocol, Sequence

from .finops.sql_repository import ConnectionFactory
from .model_providers import ModelProviderRecord, ProviderModel, ProviderPatch


class ModelProviderRepositoryError(RuntimeError):
    code = "model_provider_persistence_failed"

    def __init__(self, code: str | None = None) -> None:
        if code:
            self.code = code
        super().__init__(self.code)


class ModelProviderConflictError(ModelProviderRepositoryError):
    code = "model_provider_revision_conflict"


class ModelProviderNotFoundError(ModelProviderRepositoryError):
    code = "model_provider_not_found"


class ModelProviderRepository(Protocol):
    def mutation_guard(
        self,
        tenant_ref: str,
        provider_id: str,
    ) -> ContextManager[None]: ...

    def list(self, tenant_ref: str) -> list[ModelProviderRecord]: ...

    def get(self, tenant_ref: str, provider_id: str) -> ModelProviderRecord: ...

    def create(self, value: ModelProviderRecord) -> ModelProviderRecord: ...

    def update(
        self,
        tenant_ref: str,
        provider_id: str,
        patch: ProviderPatch,
        *,
        actor_ref: str,
    ) -> ModelProviderRecord: ...


class _KeyedMutationGuards:
    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], tuple[RLock, int]] = {}
        self._registry_lock = Lock()

    @contextmanager
    def hold(self, tenant_ref: str, provider_id: str) -> Iterator[None]:
        key = (tenant_ref, provider_id)
        with self._registry_lock:
            guard, users = self._entries.get(key, (RLock(), 0))
            self._entries[key] = (guard, users + 1)
        guard.acquire()
        try:
            yield
        finally:
            guard.release()
            with self._registry_lock:
                current_guard, current_users = self._entries[key]
                if current_users == 1:
                    del self._entries[key]
                else:
                    self._entries[key] = (current_guard, current_users - 1)


_IN_MEMORY_MUTATION_GUARDS = _KeyedMutationGuards()
_MUTATION_LOCK_TIMEOUT_MS = 30_000


class InMemoryModelProviderRepository:
    def __init__(self) -> None:
        self._values: dict[tuple[str, str], ModelProviderRecord] = {}
        self._lock = RLock()

    def mutation_guard(
        self,
        tenant_ref: str,
        provider_id: str,
    ) -> ContextManager[None]:
        return _IN_MEMORY_MUTATION_GUARDS.hold(tenant_ref, provider_id)

    def list(self, tenant_ref: str) -> list[ModelProviderRecord]:
        with self._lock:
            return [
                value.model_copy(deep=True)
                for (tenant, _), value in sorted(self._values.items())
                if tenant == tenant_ref
            ]

    def get(self, tenant_ref: str, provider_id: str) -> ModelProviderRecord:
        with self._lock:
            value = self._values.get((tenant_ref, provider_id))
            if value is None:
                raise ModelProviderNotFoundError()
            return value.model_copy(deep=True)

    def create(self, value: ModelProviderRecord) -> ModelProviderRecord:
        key = (value.tenant_ref, value.provider_id)
        with self._lock:
            if key in self._values:
                raise ModelProviderConflictError()
            self._values[key] = value.model_copy(deep=True)
            return value.model_copy(deep=True)

    def update(
        self,
        tenant_ref: str,
        provider_id: str,
        patch: ProviderPatch,
        *,
        actor_ref: str,
    ) -> ModelProviderRecord:
        key = (tenant_ref, provider_id)
        with self._lock:
            current = self._values.get(key)
            if current is None:
                raise ModelProviderNotFoundError()
            if current.revision != patch.base_revision:
                raise ModelProviderConflictError()
            changes = patch.model_dump(exclude={"base_revision"}, exclude_none=True)
            updated = ModelProviderRecord.model_validate(
                {
                    **current.__dict__,
                    **changes,
                    "revision": current.revision + 1,
                    "updated_by_ref": actor_ref,
                    "updated_at": datetime.now(timezone.utc),
                }
            )
            self._values[key] = updated
            return updated.model_copy(deep=True)


class _Cursor(Protocol):
    rowcount: int

    def execute(self, operation: str, *parameters: Any) -> "_Cursor": ...

    def fetchall(self) -> Sequence[Any]: ...

    def fetchone(self) -> Any | None: ...


class SqlModelProviderRepository:
    def __init__(self, *, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    @contextmanager
    def mutation_guard(
        self,
        tenant_ref: str,
        provider_id: str,
    ) -> Iterator[None]:
        resource = _mutation_lock_resource(tenant_ref, provider_id)
        connection = None
        cursor = None
        try:
            connection = self._connection_factory()
            connection.autocommit = True
            cursor = connection.cursor()
            row = cursor.execute(
                """/* providers:mutation-guard-acquire */
                SET NOCOUNT ON;
                DECLARE @lock_result int;
                EXEC @lock_result = sys.sp_getapplock
                    @Resource = ?,
                    @LockMode = N'Exclusive',
                    @LockOwner = N'Session',
                    @LockTimeout = ?,
                    @DbPrincipal = N'public';
                SELECT @lock_result;""",
                resource,
                _MUTATION_LOCK_TIMEOUT_MS,
            ).fetchone()
            if row is None or int(_row_value(row, 0)) < 0:
                raise ModelProviderRepositoryError(
                    "model_provider_mutation_guard_unavailable"
                )
        except ModelProviderRepositoryError:
            _close_quietly(connection)
            raise
        except Exception:
            _close_quietly(connection)
            raise ModelProviderRepositoryError(
                "model_provider_mutation_guard_unavailable"
            ) from None

        try:
            yield
        finally:
            if cursor is not None:
                try:
                    cursor.execute(
                        """/* providers:mutation-guard-release */
                        EXEC sys.sp_releaseapplock
                            @Resource = ?,
                            @LockOwner = N'Session',
                            @DbPrincipal = N'public';""",
                        resource,
                    )
                except Exception:
                    pass
            _close_quietly(connection)

    def list(self, tenant_ref: str) -> list[ModelProviderRecord]:
        with self._transaction() as cursor:
            rows = cursor.execute(
                """/* providers:list */
                SELECT provider_id, tenant_ref, provider_type, display_name,
                    base_url, region, secret_ref, connection_state, governance_state,
                    available_models_json, last_tested_at, last_success_at,
                    safe_error_category, connection_stage, stage_durations_json,
                    revision, created_by_ref,
                    updated_by_ref, created_at, updated_at
                FROM df_finops.model_provider
                WHERE tenant_ref = ?
                ORDER BY display_name, provider_id""",
                tenant_ref,
            ).fetchall()
        return [_record_from_row(row) for row in rows]

    def get(self, tenant_ref: str, provider_id: str) -> ModelProviderRecord:
        with self._transaction() as cursor:
            row = cursor.execute(
                """/* providers:get */
                SELECT provider_id, tenant_ref, provider_type, display_name,
                    base_url, region, secret_ref, connection_state, governance_state,
                    available_models_json, last_tested_at, last_success_at,
                    safe_error_category, connection_stage, stage_durations_json,
                    revision, created_by_ref,
                    updated_by_ref, created_at, updated_at
                FROM df_finops.model_provider
                WHERE tenant_ref = ? AND provider_id = ?""",
                tenant_ref,
                provider_id,
            ).fetchone()
        if row is None:
            raise ModelProviderNotFoundError()
        return _record_from_row(row)

    def create(self, value: ModelProviderRecord) -> ModelProviderRecord:
        with self._transaction() as cursor:
            cursor.execute(
                """/* providers:create */
                INSERT INTO df_finops.model_provider (
                    tenant_ref, provider_id, provider_type, display_name,
                    base_url, region, secret_ref, connection_state, governance_state,
                    available_models_json, last_tested_at, last_success_at,
                    safe_error_category, connection_stage, stage_durations_json,
                    revision, created_by_ref, updated_by_ref, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                *_record_parameters(value),
            )
        return value.model_copy(deep=True)

    def update(
        self,
        tenant_ref: str,
        provider_id: str,
        patch: ProviderPatch,
        *,
        actor_ref: str,
    ) -> ModelProviderRecord:
        current = self.get(tenant_ref, provider_id)
        if current.revision != patch.base_revision:
            raise ModelProviderConflictError()
        changes = patch.model_dump(exclude={"base_revision"}, exclude_none=True)
        updated = ModelProviderRecord.model_validate(
            {
                **current.__dict__,
                **changes,
                "revision": current.revision + 1,
                "updated_by_ref": actor_ref,
                "updated_at": datetime.now(timezone.utc),
            }
        )
        with self._transaction() as cursor:
            cursor.execute(
                """/* providers:update */
                UPDATE df_finops.model_provider SET
                    display_name = ?, base_url = ?, region = ?, connection_state = ?,
                    governance_state = ?, available_models_json = ?,
                    last_tested_at = ?, last_success_at = ?,
                    safe_error_category = ?, connection_stage = ?,
                    stage_durations_json = ?, revision = ?,
                    updated_by_ref = ?, updated_at = ?
                WHERE tenant_ref = ? AND provider_id = ? AND revision = ?""",
                updated.display_name,
                updated.base_url,
                updated.region,
                updated.connection_state,
                updated.governance_state,
                _models_json(updated.available_models),
                updated.last_tested_at,
                updated.last_success_at,
                updated.safe_error_category,
                updated.connection_stage,
                json.dumps(updated.stage_durations_ms, separators=(",", ":")),
                updated.revision,
                updated.updated_by_ref,
                updated.updated_at,
                tenant_ref,
                provider_id,
                patch.base_revision,
            )
            if cursor.rowcount != 1:
                raise ModelProviderConflictError()
        return updated

    @contextmanager
    def _transaction(self) -> Iterator[_Cursor]:
        connection = None
        try:
            connection = self._connection_factory()
            connection.autocommit = False
            cursor = connection.cursor()
            yield cursor
            connection.commit()
        except (ModelProviderConflictError, ModelProviderNotFoundError):
            if connection is not None:
                try:
                    connection.rollback()
                except Exception:
                    pass
            raise
        except Exception:
            if connection is not None:
                try:
                    connection.rollback()
                except Exception:
                    pass
            raise ModelProviderRepositoryError() from None
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass


def _models_json(value: list[ProviderModel]) -> str:
    return json.dumps(
        [item.model_dump(mode="json") for item in value],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _mutation_lock_resource(tenant_ref: str, provider_id: str) -> str:
    digest = hashlib.sha256(
        f"{tenant_ref}\0{provider_id}".encode("utf-8")
    ).hexdigest()
    return f"df:model-provider:{digest}"


def _row_value(row: Any, index: int) -> Any:
    if isinstance(row, (tuple, list)):
        return row[index]
    try:
        return row[index]
    except (KeyError, TypeError):
        return getattr(row, "lock_result")


def _close_quietly(connection: Any | None) -> None:
    if connection is None:
        return
    try:
        connection.close()
    except Exception:
        pass


def _record_parameters(value: ModelProviderRecord) -> tuple[Any, ...]:
    return (
        value.tenant_ref,
        value.provider_id,
        value.provider_type,
        value.display_name,
        value.base_url,
        value.region,
        value.secret_ref,
        value.connection_state,
        value.governance_state,
        _models_json(value.available_models),
        value.last_tested_at,
        value.last_success_at,
        value.safe_error_category,
        value.connection_stage,
        json.dumps(value.stage_durations_ms, separators=(",", ":")),
        value.revision,
        value.created_by_ref,
        value.updated_by_ref,
        value.created_at,
        value.updated_at,
    )


def _record_from_row(row: Any) -> ModelProviderRecord:
    values = list(row)
    raw_models = json.loads(str(values[9] or "[]"))
    return ModelProviderRecord(
        provider_id=str(values[0]),
        tenant_ref=str(values[1]),
        provider_type=str(values[2]),
        display_name=str(values[3]),
        base_url=str(values[4]),
        region=str(values[5]) if values[5] is not None else None,
        secret_ref=str(values[6]),
        connection_state=str(values[7]),
        governance_state=str(values[8]),
        available_models=raw_models,
        last_tested_at=values[10],
        last_success_at=values[11],
        safe_error_category=str(values[12]) if values[12] is not None else None,
        connection_stage=str(values[13]) if values[13] is not None else None,
        stage_durations_ms=json.loads(str(values[14] or "{}")),
        revision=int(values[15]),
        created_by_ref=str(values[16]),
        updated_by_ref=str(values[17]),
        created_at=values[18],
        updated_at=values[19],
    )


__all__ = [
    "InMemoryModelProviderRepository",
    "ModelProviderConflictError",
    "ModelProviderNotFoundError",
    "ModelProviderRepository",
    "ModelProviderRepositoryError",
    "SqlModelProviderRepository",
]
