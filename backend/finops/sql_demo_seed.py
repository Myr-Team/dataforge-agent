from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from .demo_seed_repository import _key, _request_refs
from .sql_repository import FinOpsPersistenceError


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
            for request_ref in normalized:
                cursor.execute(
                    """/* finops:upsert-demo-seed-event */
                    MERGE df_finops.demo_seed_event WITH (HOLDLOCK) AS target
                    USING (
                        SELECT ? AS tenant_ref, ? AS workspace_id,
                               ? AS seed_batch, ? AS request_ref
                    ) AS source
                    ON target.tenant_ref = source.tenant_ref
                       AND target.workspace_id = source.workspace_id
                       AND target.seed_batch = source.seed_batch
                       AND target.request_ref = source.request_ref
                    WHEN MATCHED THEN
                        UPDATE SET updated_at = ?
                    WHEN NOT MATCHED THEN
                        INSERT (
                            tenant_ref, workspace_id, seed_batch,
                            request_ref, updated_at
                        )
                        VALUES (
                            source.tenant_ref, source.workspace_id,
                            source.seed_batch, source.request_ref, ?
                        );""",
                    tenant_ref,
                    workspace_id,
                    batch,
                    request_ref,
                    updated_at,
                    updated_at,
                )
            if normalized:
                placeholders = ", ".join("?" for _ in normalized)
                cursor.execute(
                    f"""/* finops:delete-stale-demo-seed-event */
                    DELETE FROM df_finops.demo_seed_event
                    WHERE tenant_ref = ? AND workspace_id = ? AND seed_batch = ?
                      AND request_ref NOT IN ({placeholders})""",
                    tenant_ref,
                    workspace_id,
                    batch,
                    *normalized,
                )
            else:
                cursor.execute(
                    """/* finops:delete-stale-demo-seed-event */
                    DELETE FROM df_finops.demo_seed_event
                    WHERE tenant_ref = ? AND workspace_id = ? AND seed_batch = ?""",
                    tenant_ref,
                    workspace_id,
                    batch,
                )
            connection.commit()
            current = set(normalized)
            return len(current - existing), len(current & existing)
        except Exception as exc:
            connection.rollback()
            raise FinOpsPersistenceError("demo seed persistence is unavailable") from exc
        finally:
            connection.close()
