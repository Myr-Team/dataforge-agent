from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from .demo_seed_repository import _key, _request_refs
from .models import FinOpsRequestEvent
from .sql_repository import FinOpsPersistenceError, _upsert_event


class _Cursor(Protocol):
    def execute(self, operation: str, *parameters: Any) -> "_Cursor": ...

    def fetchall(self) -> list[Any]: ...


class _Connection(Protocol):
    autocommit: bool

    def cursor(self) -> _Cursor: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


class SqlDemoSeedRepository:
    """Durable ownership ledger for bounded demo request facts."""

    def __init__(self, *, connection_factory: Callable[[], _Connection]) -> None:
        self._connection_factory = connection_factory

    def list_request_refs(
        self,
        *,
        tenant_ref: str,
        workspace_id: str,
        batch: str,
    ) -> tuple[str, ...]:
        tenant_ref, workspace_id, batch = _key(tenant_ref, workspace_id, batch)
        connection = self._connection_factory()
        try:
            rows = connection.cursor().execute(
                """/* finops:list-demo-seed-batch */
                SELECT request_ref
                FROM df_finops.demo_seed_event
                WHERE tenant_ref = ? AND workspace_id = ? AND seed_batch = ?
                ORDER BY request_ref""",
                tenant_ref,
                workspace_id,
                batch,
            ).fetchall()
            return tuple(str(row[0]) for row in rows)
        except Exception as exc:
            raise FinOpsPersistenceError("demo seed persistence is unavailable") from exc
        finally:
            connection.close()

    def replace_batch(
        self,
        *,
        tenant_ref: str,
        workspace_id: str,
        batch: str,
        request_refs: tuple[str, ...],
    ) -> tuple[int, int]:
        tenant_ref, workspace_id, batch = _key(tenant_ref, workspace_id, batch)
        normalized = _request_refs(request_refs)
        connection = self._connection_factory()
        connection.autocommit = False
        cursor = connection.cursor()
        try:
            existing_rows = cursor.execute(
                """/* finops:list-demo-seed-batch */
                SELECT request_ref
                FROM df_finops.demo_seed_event WITH (UPDLOCK, HOLDLOCK)
                WHERE tenant_ref = ? AND workspace_id = ? AND seed_batch = ?""",
                tenant_ref,
                workspace_id,
                batch,
            ).fetchall()
            existing = {str(row[0]) for row in existing_rows}
            updated_at = datetime.now(timezone.utc)
            cursor.execute(
                """/* finops:delete-stale-demo-seed-event */
                DELETE FROM df_finops.demo_seed_event
                WHERE tenant_ref = ? AND workspace_id = ? AND seed_batch = ?""",
                tenant_ref,
                workspace_id,
                batch,
            )
            for request_ref in normalized:
                cursor.execute(
                    """/* finops:upsert-demo-seed-event */
                    INSERT INTO df_finops.demo_seed_event (
                        tenant_ref, workspace_id, seed_batch,
                        request_ref, updated_at
                    ) VALUES (?, ?, ?, ?, ?);""",
                    tenant_ref,
                    workspace_id,
                    batch,
                    request_ref,
                    updated_at,
                )
            connection.commit()
            current = set(normalized)
            return len(current - existing), len(current & existing)
        except Exception as exc:
            connection.rollback()
            raise FinOpsPersistenceError("demo seed persistence is unavailable") from exc
        finally:
            connection.close()

    def replace_batch_events(
        self,
        *,
        tenant_ref: str,
        workspace_id: str,
        batch: str,
        events: tuple[FinOpsRequestEvent, ...],
        event_repository: Any,
    ) -> tuple[int, int]:
        del event_repository
        tenant_ref, workspace_id, batch = _key(
            tenant_ref, workspace_id, batch
        )
        normalized = _request_refs(
            tuple(event.request_ref for event in events)
        )
        if any(
            event.tenant_ref != tenant_ref
            or event.workspace_id != workspace_id
            for event in events
        ):
            raise ValueError("demo seed event scope mismatch")
        connection = self._connection_factory()
        connection.autocommit = False
        cursor = connection.cursor()
        try:
            existing_rows = cursor.execute(
                """/* finops:list-demo-seed-workspace */
                SELECT seed_batch, request_ref
                FROM df_finops.demo_seed_event WITH (UPDLOCK, HOLDLOCK)
                WHERE tenant_ref = ? AND workspace_id = ?""",
                tenant_ref,
                workspace_id,
            ).fetchall()
            previous = {str(row[1]) for row in existing_rows}
            current = set(normalized)

            for event in events:
                _upsert_event(cursor, event)

            stale = tuple(sorted(previous - current))
            for stale_batch in _chunks(stale):
                placeholders = ", ".join("?" for _ in stale_batch)
                cursor.execute(
                    f"""/* finops:delete-stale-demo-request-event */
                    DELETE target
                    FROM df_finops.request_event AS target
                    WHERE target.tenant_ref = ?
                      AND target.workspace_id = ?
                      AND target.request_ref IN ({placeholders})
                      AND EXISTS (
                          SELECT 1
                          FROM df_finops.demo_seed_event AS owned
                          WHERE owned.tenant_ref = target.tenant_ref
                            AND owned.workspace_id = target.workspace_id
                            AND owned.request_ref = target.request_ref
                      )""",
                    tenant_ref,
                    workspace_id,
                    *stale_batch,
                )

            updated_at = datetime.now(timezone.utc)
            cursor.execute(
                """/* finops:delete-retired-demo-seed-ownership */
                DELETE FROM df_finops.demo_seed_event
                WHERE tenant_ref = ? AND workspace_id = ?""",
                tenant_ref,
                workspace_id,
            )
            for request_ref in normalized:
                cursor.execute(
                    """/* finops:upsert-demo-seed-event */
                    INSERT INTO df_finops.demo_seed_event (
                        tenant_ref, workspace_id, seed_batch,
                        request_ref, updated_at
                    ) VALUES (?, ?, ?, ?, ?);""",
                    tenant_ref,
                    workspace_id,
                    batch,
                    request_ref,
                    updated_at,
                )
            connection.commit()
            return len(current - previous), len(current & previous)
        except Exception as exc:
            connection.rollback()
            if isinstance(exc, ValueError):
                raise
            raise FinOpsPersistenceError(
                "demo seed persistence is unavailable"
            ) from exc
        finally:
            connection.close()


def _chunks(
    values: tuple[str, ...],
    size: int = 1_000,
) -> tuple[tuple[str, ...], ...]:
    return tuple(
        values[index : index + size]
        for index in range(0, len(values), size)
    )
