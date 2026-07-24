from __future__ import annotations

from contextlib import contextmanager
from threading import RLock
from typing import Any, Callable, Iterator, Protocol, Sequence

from .evidence import FinOpsEvidenceAlias
from .sql_repository import FinOpsPersistenceError


class EvidenceAliasRepository(Protocol):
    def get_or_create(self, value: FinOpsEvidenceAlias) -> FinOpsEvidenceAlias: ...
    def get(
        self,
        *,
        tenant_ref: str,
        workspace_id: str,
        object_kind: str,
        object_ref: str,
    ) -> FinOpsEvidenceAlias | None: ...


class InMemoryEvidenceAliasRepository:
    def __init__(self) -> None:
        self._lock = RLock()
        self._items: dict[tuple[str, str, str, str], FinOpsEvidenceAlias] = {}

    @staticmethod
    def _key(
        tenant_ref: str,
        workspace_id: str,
        object_kind: str,
        object_ref: str,
    ) -> tuple[str, str, str, str]:
        return (tenant_ref, workspace_id, object_kind, object_ref)

    def get_or_create(self, value: FinOpsEvidenceAlias) -> FinOpsEvidenceAlias:
        key = self._key(
            value.tenant_ref,
            value.workspace_id,
            value.object_kind,
            value.object_ref,
        )
        with self._lock:
            existing = self._items.get(key)
            if existing is None:
                existing = value.model_copy(deep=True)
                self._items[key] = existing
        return existing.model_copy(deep=True)

    def get(
        self,
        *,
        tenant_ref: str,
        workspace_id: str,
        object_kind: str,
        object_ref: str,
    ) -> FinOpsEvidenceAlias | None:
        with self._lock:
            value = self._items.get(
                self._key(tenant_ref, workspace_id, object_kind, object_ref)
            )
        return value.model_copy(deep=True) if value else None


class _Cursor(Protocol):
    def execute(self, operation: str, *parameters: Any) -> "_Cursor": ...
    def fetchone(self) -> Any | None: ...


class _Connection(Protocol):
    autocommit: bool
    def cursor(self) -> _Cursor: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...


ConnectionFactory = Callable[[], _Connection]


class SqlEvidenceAliasRepository:
    def __init__(self, *, connection_factory: ConnectionFactory) -> None:
        if not callable(connection_factory):
            raise TypeError("connection_factory must be callable")
        self._connection_factory = connection_factory

    def get_or_create(self, value: FinOpsEvidenceAlias) -> FinOpsEvidenceAlias:
        with self._transaction() as cursor:
            existing = cursor.execute(
                """/* finops:get-or-create-evidence-alias:read */
                SELECT tenant_ref, workspace_id, object_kind, object_ref,
                       operation_code, workspace_name_snapshot, display_name,
                       occurred_at, created_at
                FROM df_finops.evidence_alias WITH (UPDLOCK, HOLDLOCK)
                WHERE tenant_ref = ? AND workspace_id = ?
                  AND object_kind = ? AND object_ref = ?""",
                value.tenant_ref,
                value.workspace_id,
                value.object_kind,
                value.object_ref,
            ).fetchone()
            if existing is not None:
                return _alias_from_row(existing)
            cursor.execute(
                """/* finops:get-or-create-evidence-alias:insert */
                INSERT INTO df_finops.evidence_alias (
                    tenant_ref, workspace_id, object_kind, object_ref,
                    operation_code, workspace_name_snapshot, display_name,
                    occurred_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                value.tenant_ref,
                value.workspace_id,
                value.object_kind,
                value.object_ref,
                value.operation_code,
                value.workspace_name_snapshot,
                value.display_name,
                value.occurred_at,
                value.created_at,
            )
        return value.model_copy(deep=True)

    def get(
        self,
        *,
        tenant_ref: str,
        workspace_id: str,
        object_kind: str,
        object_ref: str,
    ) -> FinOpsEvidenceAlias | None:
        with self._transaction() as cursor:
            row = cursor.execute(
                """/* finops:get-evidence-alias */
                SELECT tenant_ref, workspace_id, object_kind, object_ref,
                       operation_code, workspace_name_snapshot, display_name,
                       occurred_at, created_at
                FROM df_finops.evidence_alias
                WHERE tenant_ref = ? AND workspace_id = ?
                  AND object_kind = ? AND object_ref = ?""",
                tenant_ref,
                workspace_id,
                object_kind,
                object_ref,
            ).fetchone()
        return _alias_from_row(row) if row is not None else None

    @contextmanager
    def _transaction(self) -> Iterator[_Cursor]:
        connection: _Connection | None = None
        try:
            connection = self._connection_factory()
            connection.autocommit = False
            cursor = connection.cursor()
            yield cursor
            connection.commit()
        except Exception as exc:
            if connection is not None:
                try:
                    connection.rollback()
                except Exception:
                    pass
            if isinstance(exc, FinOpsPersistenceError):
                raise
            raise FinOpsPersistenceError("FinOps evidence alias operation failed") from exc
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass


def _alias_from_row(row: Sequence[Any] | Any) -> FinOpsEvidenceAlias:
    def value(index: int, name: str) -> Any:
        if isinstance(row, (tuple, list)):
            return row[index]
        try:
            return row[index]
        except (KeyError, TypeError):
            return getattr(row, name)

    return FinOpsEvidenceAlias.model_validate(
        {
            "tenant_ref": value(0, "tenant_ref"),
            "workspace_id": value(1, "workspace_id"),
            "object_kind": value(2, "object_kind"),
            "object_ref": value(3, "object_ref"),
            "operation_code": value(4, "operation_code"),
            "workspace_name_snapshot": value(5, "workspace_name_snapshot"),
            "display_name": value(6, "display_name"),
            "occurred_at": value(7, "occurred_at"),
            "created_at": value(8, "created_at"),
        }
    )
