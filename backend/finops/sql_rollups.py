from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from .rollups import FinOpsRollup
from .sql_repository import ConnectionFactory, FinOpsPersistenceError


class SqlFinOpsRollupRepository:
    def __init__(self, *, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def replace(
        self,
        *,
        tenant_ref: str,
        from_value: str,
        to_value: str,
        hourly: list[FinOpsRollup],
        daily: list[FinOpsRollup],
    ) -> None:
        for item in [*hourly, *daily]:
            if item.tenant_ref != tenant_ref:
                raise ValueError("rollup tenant scope mismatch")
        with self._transaction() as cursor:
            cursor.execute(
                """/* finops:delete-hour-rollups */
                DELETE FROM df_finops.request_rollup_hour
                WHERE tenant_ref = ? AND bucket_at >= ? AND bucket_at < ?""",
                tenant_ref,
                from_value,
                to_value,
            )
            for item in hourly:
                self._insert(cursor, "hour", item)
            cursor.execute(
                """/* finops:delete-day-rollups */
                DELETE FROM df_finops.request_rollup_day
                WHERE tenant_ref = ?
                  AND bucket_at >= CAST(? AS date)
                  AND bucket_at <= CAST(? AS date)""",
                tenant_ref,
                from_value,
                to_value,
            )
            for item in daily:
                self._insert(cursor, "day", item)

    @staticmethod
    def _insert(cursor: Any, kind: str, item: FinOpsRollup) -> None:
        if kind == "hour":
            operation = """/* finops:insert-hour-rollup */
                INSERT INTO df_finops.request_rollup_hour (
                    tenant_ref, bucket_at, department_id, workspace_id, agent_id,
                    model_deployment, request_count, failure_count, total_tokens,
                    estimated_cost, p50_latency_ms, p95_latency_ms,
                    apim_governed_count, unpriced_count, refreshed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, SYSUTCDATETIME())"""
        else:
            operation = """/* finops:insert-day-rollup */
                INSERT INTO df_finops.request_rollup_day (
                    tenant_ref, bucket_at, department_id, workspace_id, agent_id,
                    model_deployment, request_count, failure_count, total_tokens,
                    estimated_cost, p50_latency_ms, p95_latency_ms,
                    apim_governed_count, unpriced_count, refreshed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, SYSUTCDATETIME())"""
        cursor.execute(
            operation,
            item.tenant_ref,
            item.bucket_at,
            item.department_id,
            item.workspace_id,
            item.agent_id,
            item.model_deployment,
            item.request_count,
            item.failure_count,
            item.total_tokens,
            item.estimated_cost,
            item.p50_latency_ms,
            item.p95_latency_ms,
            item.apim_governed_count,
            item.unpriced_count,
        )

    @contextmanager
    def _transaction(self) -> Iterator[Any]:
        connection = None
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
            raise FinOpsPersistenceError("FinOps rollup SQL operation failed") from exc
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass
