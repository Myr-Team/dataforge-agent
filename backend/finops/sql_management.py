from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from .management import Department, FinOpsPolicy, PriceCardItem, PriceCardRevision
from .sql_repository import ConnectionFactory, FinOpsPersistenceError


class SqlFinOpsManagementRepository:
    def __init__(self, *, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def get_department(self, tenant_ref: str, department_id: str) -> Department | None:
        with self._transaction() as cursor:
            row = cursor.execute(
                """/* finops:get-department */
                SELECT department_id, display_name, cost_center, status, version, updated_at, updated_by
                FROM df_finops.department
                WHERE tenant_ref = ? AND department_id = ?""",
                tenant_ref,
                department_id,
            ).fetchone()
        return _department(row) if row is not None else None

    def save_department(self, tenant_ref: str, value: Department) -> Department:
        with self._transaction() as cursor:
            cursor.execute(
                """/* finops:save-department */
                MERGE df_finops.department WITH (HOLDLOCK) AS target
                USING (SELECT ? AS tenant_ref, ? AS department_id) AS source
                ON target.tenant_ref = source.tenant_ref AND target.department_id = source.department_id
                WHEN MATCHED THEN UPDATE SET display_name = ?, cost_center = ?, status = ?,
                    version = ?, updated_at = ?, updated_by = ?
                WHEN NOT MATCHED THEN INSERT (
                    tenant_ref, department_id, display_name, cost_center, status, version, updated_at, updated_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);""",
                tenant_ref,
                value.department_id,
                value.display_name,
                value.cost_center,
                value.status,
                value.version,
                value.updated_at,
                value.updated_by,
                tenant_ref,
                value.department_id,
                value.display_name,
                value.cost_center,
                value.status,
                value.version,
                value.updated_at,
                value.updated_by,
            )
        return value.model_copy(deep=True)

    def list_departments(self, tenant_ref: str) -> list[Department]:
        with self._transaction() as cursor:
            rows = cursor.execute(
                """/* finops:list-departments */
                SELECT department_id, display_name, cost_center, status, version, updated_at, updated_by
                FROM df_finops.department WHERE tenant_ref = ? ORDER BY display_name""",
                tenant_ref,
            ).fetchall()
        return [_department(row) for row in rows]

    def get_workspace_department(self, tenant_ref: str, workspace_id: str) -> str | None:
        with self._transaction() as cursor:
            row = cursor.execute(
                """/* finops:get-workspace-department */
                SELECT department_id FROM df_finops.workspace_department
                WHERE tenant_ref = ? AND workspace_id = ?""",
                tenant_ref,
                workspace_id,
            ).fetchone()
        return str(_value(row, 0)) if row is not None and _value(row, 0) is not None else None

    def save_workspace_department(self, tenant_ref: str, workspace_id: str, department_id: str | None) -> None:
        with self._transaction() as cursor:
            cursor.execute(
                """/* finops:save-workspace-department */
                MERGE df_finops.workspace_department WITH (HOLDLOCK) AS target
                USING (SELECT ? AS tenant_ref, ? AS workspace_id) AS source
                ON target.tenant_ref = source.tenant_ref AND target.workspace_id = source.workspace_id
                WHEN MATCHED THEN UPDATE SET department_id = ?, version = target.version + 1, updated_at = SYSUTCDATETIME()
                WHEN NOT MATCHED THEN INSERT (tenant_ref, workspace_id, department_id, version)
                VALUES (?, ?, ?, 1);""",
                tenant_ref,
                workspace_id,
                department_id,
                tenant_ref,
                workspace_id,
                department_id,
            )

    def get_price_card(self, tenant_ref: str, revision_id: str) -> PriceCardRevision | None:
        rows = self.list_price_cards(tenant_ref)
        return next((row for row in rows if row.revision_id == revision_id), None)

    def save_price_card(self, tenant_ref: str, value: PriceCardRevision) -> PriceCardRevision:
        with self._transaction() as cursor:
            cursor.execute(
                """/* finops:save-price-card */
                MERGE df_finops.price_card_revision WITH (HOLDLOCK) AS target
                USING (SELECT ? AS tenant_ref, ? AS revision_id) AS source
                ON target.tenant_ref = source.tenant_ref AND target.revision_id = source.revision_id
                WHEN MATCHED THEN UPDATE SET revision_status = ?, reviewed_by = ?, reviewed_at = ?, activated_at = ?
                WHEN NOT MATCHED THEN INSERT (
                    tenant_ref, revision_id, revision_status, currency, created_by, reviewed_by,
                    created_at, reviewed_at, activated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);""",
                tenant_ref,
                value.revision_id,
                value.status,
                value.reviewed_by,
                value.reviewed_at,
                value.activated_at,
                tenant_ref,
                value.revision_id,
                value.status,
                value.currency,
                value.created_by,
                value.reviewed_by,
                value.created_at,
                value.reviewed_at,
                value.activated_at,
            )
            cursor.execute(
                """/* finops:replace-price-card-items */
                DELETE FROM df_finops.price_card_item
                WHERE tenant_ref = ? AND revision_id = ?""",
                tenant_ref,
                value.revision_id,
            )
            for item in value.items:
                cursor.execute(
                    """/* finops:insert-price-card-item */
                    INSERT INTO df_finops.price_card_item (
                        tenant_ref, revision_id, model_deployment, input_per_million,
                        output_per_million, cached_input_per_million, reasoning_per_million
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    tenant_ref,
                    value.revision_id,
                    item.deployment,
                    item.input_per_million,
                    item.output_per_million,
                    item.cached_input_per_million,
                    item.reasoning_per_million,
                )
        return value.model_copy(deep=True)

    def list_price_cards(self, tenant_ref: str) -> list[PriceCardRevision]:
        with self._transaction() as cursor:
            revisions = cursor.execute(
                """/* finops:list-price-card-revisions */
                SELECT revision_id, revision_status, currency, created_by, reviewed_by,
                    created_at, reviewed_at, activated_at
                FROM df_finops.price_card_revision
                WHERE tenant_ref = ? ORDER BY created_at DESC""",
                tenant_ref,
            ).fetchall()
            items = cursor.execute(
                """/* finops:list-price-card-items */
                SELECT revision_id, model_deployment, input_per_million, output_per_million,
                    cached_input_per_million, reasoning_per_million
                FROM df_finops.price_card_item WHERE tenant_ref = ?""",
                tenant_ref,
            ).fetchall()
        by_revision: dict[str, list[PriceCardItem]] = {}
        for row in items:
            by_revision.setdefault(str(_value(row, 0)), []).append(
                PriceCardItem(
                    deployment=str(_value(row, 1)),
                    input_per_million=_optional_float(_value(row, 2)),
                    output_per_million=_optional_float(_value(row, 3)),
                    cached_input_per_million=_optional_float(_value(row, 4)),
                    reasoning_per_million=_optional_float(_value(row, 5)),
                )
            )
        return [
            PriceCardRevision(
                revision_id=str(_value(row, 0)),
                status=str(_value(row, 1)),
                currency=str(_value(row, 2)),
                created_by=str(_value(row, 3)),
                reviewed_by=_optional_text(_value(row, 4)),
                created_at=_iso(_value(row, 5)),
                reviewed_at=_optional_iso(_value(row, 6)),
                activated_at=_optional_iso(_value(row, 7)),
                items=by_revision.get(str(_value(row, 0)), []),
            )
            for row in revisions
        ]

    def get_policy(self, tenant_ref: str, policy_id: str) -> FinOpsPolicy | None:
        with self._transaction() as cursor:
            row = cursor.execute(
                """/* finops:get-policy */
                SELECT policy_id, policy_type, policy_status, version, policy_payload, updated_at, created_by
                FROM df_finops.policy WHERE tenant_ref = ? AND policy_id = ?""",
                tenant_ref,
                policy_id,
            ).fetchone()
        return _policy(row) if row is not None else None

    def save_policy(self, tenant_ref: str, value: FinOpsPolicy) -> FinOpsPolicy:
        payload = json.dumps(value.configuration, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        with self._transaction() as cursor:
            cursor.execute(
                """/* finops:save-policy */
                MERGE df_finops.policy WITH (HOLDLOCK) AS target
                USING (SELECT ? AS tenant_ref, ? AS policy_id) AS source
                ON target.tenant_ref = source.tenant_ref AND target.policy_id = source.policy_id
                WHEN MATCHED THEN UPDATE SET policy_status = ?, version = ?, policy_payload = ?,
                    created_by = ?, updated_at = ?
                WHEN NOT MATCHED THEN INSERT (
                    tenant_ref, policy_id, policy_type, policy_status, version,
                    policy_payload, created_by, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);""",
                tenant_ref,
                value.policy_id,
                value.status,
                value.version,
                payload,
                value.updated_by,
                value.updated_at,
                tenant_ref,
                value.policy_id,
                value.policy_type,
                value.status,
                value.version,
                payload,
                value.updated_by,
                value.updated_at,
            )
        return value.model_copy(deep=True)

    def list_policies(self, tenant_ref: str) -> list[FinOpsPolicy]:
        with self._transaction() as cursor:
            rows = cursor.execute(
                """/* finops:list-policies */
                SELECT policy_id, policy_type, policy_status, version, policy_payload, updated_at, created_by
                FROM df_finops.policy WHERE tenant_ref = ? ORDER BY updated_at DESC""",
                tenant_ref,
            ).fetchall()
        return [_policy(row) for row in rows]

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
            raise FinOpsPersistenceError("FinOps management SQL operation failed") from exc
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass


def _department(row: Any) -> Department:
    return Department(
        department_id=str(_value(row, 0)),
        display_name=str(_value(row, 1)),
        cost_center=_optional_text(_value(row, 2)),
        status=str(_value(row, 3)),
        version=int(_value(row, 4)),
        updated_at=_iso(_value(row, 5)),
        updated_by=str(_value(row, 6)),
    )


def _policy(row: Any) -> FinOpsPolicy:
    return FinOpsPolicy(
        policy_id=str(_value(row, 0)),
        policy_type=str(_value(row, 1)),
        status=str(_value(row, 2)),
        version=int(_value(row, 3)),
        configuration=json.loads(str(_value(row, 4))),
        updated_at=_iso(_value(row, 5)),
        updated_by=str(_value(row, 6)),
    )


def _value(row: Any, index: int) -> Any:
    return row[index]


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _optional_float(value: Any) -> float | None:
    return float(value) if value is not None else None


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        parsed = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


def _optional_iso(value: Any) -> str | None:
    return _iso(value) if value is not None else None
