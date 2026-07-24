from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Iterator

from .anomaly_store import ManagedAnomaly
from .sql_repository import ConnectionFactory, FinOpsPersistenceError


class SqlFinOpsAnomalyRepository:
    def __init__(self, *, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def get(self, tenant_ref: str, anomaly_id: str) -> ManagedAnomaly | None:
        with self._transaction() as cursor:
            row = cursor.execute(
                """/* finops:get-anomaly */
                SELECT details_payload
                FROM df_finops.anomaly
                WHERE tenant_ref = ? AND anomaly_id = ?""",
                tenant_ref,
                anomaly_id,
            ).fetchone()
        return _decode(row) if row is not None else None

    def save(self, value: ManagedAnomaly) -> ManagedAnomaly:
        payload = json.dumps(
            value.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        workspace_id = value.workspace_ids[0] if len(value.workspace_ids) == 1 else None
        with self._transaction() as cursor:
            cursor.execute(
                """/* finops:save-anomaly */
                MERGE df_finops.anomaly WITH (HOLDLOCK) AS target
                USING (SELECT ? AS tenant_ref, ? AS anomaly_id) AS source
                ON target.tenant_ref = source.tenant_ref
                   AND target.anomaly_id = source.anomaly_id
                WHEN MATCHED THEN UPDATE SET
                    policy_id = ?, workspace_id = ?, severity = ?, anomaly_status = ?,
                    observed_value = ?, threshold_value = ?, sample_count = ?,
                    updated_at = ?, details_payload = ?
                WHEN NOT MATCHED THEN INSERT (
                    tenant_ref, anomaly_id, policy_id, workspace_id, severity,
                    anomaly_status, observed_value, threshold_value, sample_count,
                    detected_at, updated_at, details_payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
                value.tenant_ref,
                value.anomaly_id,
                value.policy_type,
                workspace_id,
                value.severity,
                value.status,
                value.observed_value,
                value.threshold_value,
                value.sample_count,
                value.updated_at,
                payload,
                value.tenant_ref,
                value.anomaly_id,
                value.policy_type,
                workspace_id,
                value.severity,
                value.status,
                value.observed_value,
                value.threshold_value,
                value.sample_count,
                value.first_detected_at,
                value.updated_at,
                payload,
            )
        return value.model_copy(deep=True)

    def list(self, tenant_ref: str) -> list[ManagedAnomaly]:
        with self._transaction() as cursor:
            rows = cursor.execute(
                """/* finops:list-anomalies */
                SELECT details_payload
                FROM df_finops.anomaly
                WHERE tenant_ref = ?
                ORDER BY updated_at DESC, anomaly_id ASC""",
                tenant_ref,
            ).fetchall()
        return [_decode(row) for row in rows]

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
            raise FinOpsPersistenceError("FinOps anomaly SQL operation failed") from exc
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass


def _decode(row: Any) -> ManagedAnomaly:
    return ManagedAnomaly.model_validate(json.loads(str(row[0])))
