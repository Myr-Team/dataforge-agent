from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Literal

from .query import FinOpsQuery
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
            if item.tenant_ref.strip().casefold() != tenant_ref.strip().casefold():
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

    def read(
        self,
        query: FinOpsQuery,
        bucket_kind: Literal["hour", "day"],
    ) -> list[FinOpsRollup]:
        if not query.authorized_workspace_ids:
            raise ValueError("rollup read requires an authorized workspace")
        if query.actor_ref:
            raise ValueError("actor-scoped rollup reads are unavailable")
        table = (
            "df_finops.request_rollup_hour"
            if bucket_kind == "hour"
            else "df_finops.request_rollup_day"
        )
        placeholders = ", ".join("?" for _ in query.authorized_workspace_ids)
        clauses = [
            "tenant_ref = ?",
            f"workspace_id IN ({placeholders})",
            "bucket_at >= CAST(? AS datetime2)",
            "bucket_at < CAST(? AS datetime2)",
        ]
        parameters: list[Any] = [
            query.tenant_ref,
            *query.authorized_workspace_ids,
            query.from_value,
            query.to_value,
        ]
        for column, value in (
            ("department_id", query.department_id),
            ("agent_id", query.agent_id),
            ("model_deployment", query.model),
        ):
            if value:
                clauses.append(f"{column} = ?")
                parameters.append(value)
        operation = f"""/* finops:read-{bucket_kind}-rollups */
            SELECT bucket_at, tenant_ref, department_id, workspace_id, agent_id,
                   model_deployment, request_count, failure_count, total_tokens,
                   estimated_cost, p50_latency_ms, p95_latency_ms,
                   apim_governed_count, unpriced_count
            FROM {table}
            WHERE {' AND '.join(clauses)}
            ORDER BY bucket_at, department_id, workspace_id, agent_id, model_deployment"""
        connection = None
        try:
            connection = self._connection_factory()
            cursor = connection.cursor()
            cursor.execute(operation, *parameters)
            return [self._row(bucket_kind, item) for item in cursor.fetchall()]
        except ValueError:
            raise
        except Exception as exc:
            raise FinOpsPersistenceError("FinOps rollup SQL operation failed") from exc
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass

    @staticmethod
    def _row(bucket_kind: Literal["hour", "day"], row: Any) -> FinOpsRollup:
        values = tuple(row)
        bucket_at = str(values[0])
        if bucket_kind == "day":
            bucket_at = bucket_at[:10]
        elif bucket_at.endswith("+00:00"):
            bucket_at = bucket_at[:-6] + "Z"
        return FinOpsRollup(
            bucket_kind=bucket_kind,
            bucket_at=bucket_at,
            tenant_ref=str(values[1]),
            department_id=str(values[2]),
            workspace_id=str(values[3]),
            agent_id=str(values[4]),
            model_deployment=str(values[5]),
            request_count=int(values[6]),
            failure_count=int(values[7]),
            total_tokens=int(values[8]) if values[8] is not None else None,
            estimated_cost=float(values[9]) if values[9] is not None else None,
            p50_latency_ms=int(values[10]) if values[10] is not None else None,
            p95_latency_ms=int(values[11]) if values[11] is not None else None,
            apim_governed_count=int(values[12]),
            unpriced_count=int(values[13]),
        )

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
