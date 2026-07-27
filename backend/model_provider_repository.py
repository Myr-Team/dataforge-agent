from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Iterator, Protocol, Sequence

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


class InMemoryModelProviderRepository:
    def __init__(self) -> None:
        self._values: dict[tuple[str, str], ModelProviderRecord] = {}
        self._lock = RLock()

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

    def list(self, tenant_ref: str) -> list[ModelProviderRecord]:
        with self._transaction() as cursor:
            rows = cursor.execute(
                """/* providers:list */
                SELECT provider_id, tenant_ref, provider_type, display_name,
                    base_url, secret_ref, connection_state, governance_state,
                    available_models_json, last_tested_at, last_success_at,
                    safe_error_category, revision, created_by_ref,
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
                    base_url, secret_ref, connection_state, governance_state,
                    available_models_json, last_tested_at, last_success_at,
                    safe_error_category, revision, created_by_ref,
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
                    base_url, secret_ref, connection_state, governance_state,
                    available_models_json, last_tested_at, last_success_at,
                    safe_error_category, revision, created_by_ref,
                    updated_by_ref, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    display_name = ?, base_url = ?, connection_state = ?,
                    governance_state = ?, available_models_json = ?,
                    last_tested_at = ?, last_success_at = ?,
                    safe_error_category = ?, revision = ?,
                    updated_by_ref = ?, updated_at = ?
                WHERE tenant_ref = ? AND provider_id = ? AND revision = ?""",
                updated.display_name,
                updated.base_url,
                updated.connection_state,
                updated.governance_state,
                _models_json(updated.available_models),
                updated.last_tested_at,
                updated.last_success_at,
                updated.safe_error_category,
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


def _record_parameters(value: ModelProviderRecord) -> tuple[Any, ...]:
    return (
        value.tenant_ref,
        value.provider_id,
        value.provider_type,
        value.display_name,
        value.base_url,
        value.secret_ref,
        value.connection_state,
        value.governance_state,
        _models_json(value.available_models),
        value.last_tested_at,
        value.last_success_at,
        value.safe_error_category,
        value.revision,
        value.created_by_ref,
        value.updated_by_ref,
        value.created_at,
        value.updated_at,
    )


def _record_from_row(row: Any) -> ModelProviderRecord:
    values = list(row)
    raw_models = json.loads(str(values[8] or "[]"))
    return ModelProviderRecord(
        provider_id=str(values[0]),
        tenant_ref=str(values[1]),
        provider_type=str(values[2]),
        display_name=str(values[3]),
        base_url=str(values[4]),
        secret_ref=str(values[5]),
        connection_state=str(values[6]),
        governance_state=str(values[7]),
        available_models=raw_models,
        last_tested_at=values[9],
        last_success_at=values[10],
        safe_error_category=str(values[11]) if values[11] is not None else None,
        revision=int(values[12]),
        created_by_ref=str(values[13]),
        updated_by_ref=str(values[14]),
        created_at=values[15],
        updated_at=values[16],
    )


__all__ = [
    "InMemoryModelProviderRepository",
    "ModelProviderConflictError",
    "ModelProviderNotFoundError",
    "ModelProviderRepository",
    "ModelProviderRepositoryError",
    "SqlModelProviderRepository",
]
