from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Iterator

from .planning import BudgetRecord
from .saved_views import SavedView
from .sql_repository import ConnectionFactory, FinOpsPersistenceError


class SqlFinOpsPlanningRepository:
    """Tenant-scoped persistence for budgets and saved views."""

    def __init__(self, *, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def save_budget(self, tenant_ref: str, value: BudgetRecord) -> BudgetRecord:
        with self._transaction() as cursor:
            cursor.execute(
                """/* finops:save-budget */
                MERGE df_finops.budget WITH (HOLDLOCK) AS target
                USING (SELECT ? AS tenant_ref, ? AS budget_id) AS source
                ON target.tenant_ref = source.tenant_ref AND target.budget_id = source.budget_id
                WHEN MATCHED THEN UPDATE SET name = ?, scope_type = ?, scope_id = ?,
                    period_start = ?, period_end = ?, amount = ?, warning_pct = ?,
                    critical_pct = ?, version = ?, updated_at = ?, updated_by = ?
                WHEN NOT MATCHED THEN INSERT (
                    tenant_ref, budget_id, name, scope_type, scope_id, period_start,
                    period_end, amount, currency, warning_pct, critical_pct, version,
                    updated_at, updated_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
                tenant_ref, value.budget_id,
                value.name, value.scope_type, value.scope_id, value.period_start,
                value.period_end, value.amount, value.warning_pct, value.critical_pct,
                value.version, value.updated_at, value.created_by,
                tenant_ref, value.budget_id, value.name, value.scope_type, value.scope_id,
                value.period_start, value.period_end, value.amount, value.currency,
                value.warning_pct, value.critical_pct, value.version, value.updated_at,
                value.created_by,
            )
        return value.model_copy(deep=True)

    def get_budget(self, tenant_ref: str, budget_id: str) -> BudgetRecord | None:
        return next(
            (item for item in self.list_budgets(tenant_ref) if item.budget_id == budget_id),
            None,
        )

    def list_budgets(self, tenant_ref: str) -> list[BudgetRecord]:
        with self._transaction() as cursor:
            rows = cursor.execute(
                """/* finops:list-budgets */
                SELECT budget_id, name, scope_type, scope_id, period_start, period_end,
                    amount, currency, warning_pct, critical_pct, version, updated_at, updated_by
                FROM df_finops.budget WHERE tenant_ref = ? ORDER BY period_start, name""",
                tenant_ref,
            ).fetchall()
        return [
            BudgetRecord(
                budget_id=str(_value(row, 0)),
                name=str(_value(row, 1)),
                scope_type=str(_value(row, 2)),
                scope_id=_optional(_value(row, 3)),
                period_start=_iso(_value(row, 4)),
                period_end=_iso(_value(row, 5)),
                amount=float(_value(row, 6)),
                currency=str(_value(row, 7)),
                warning_pct=float(_value(row, 8)),
                critical_pct=float(_value(row, 9)),
                version=int(_value(row, 10)),
                updated_at=_iso(_value(row, 11)),
                created_by=str(_value(row, 12)),
            )
            for row in rows
        ]

    def save(self, tenant_ref: str, value: SavedView) -> SavedView:
        payload = json.dumps(value.filters, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        with self._transaction() as cursor:
            cursor.execute(
                """/* finops:save-view */
                INSERT INTO df_finops.saved_view (
                    tenant_ref, view_id, name, audience, portal_tab, filter_payload,
                    version, created_by, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                tenant_ref, value.view_id, value.name, value.audience, value.tab,
                payload, value.version, value.created_by, value.updated_at,
            )
        return value.model_copy(deep=True)

    def list(self, tenant_ref: str) -> list[SavedView]:
        with self._transaction() as cursor:
            rows = cursor.execute(
                """/* finops:list-views */
                SELECT view_id, name, audience, portal_tab, filter_payload, version,
                    created_by, updated_at
                FROM df_finops.saved_view WHERE tenant_ref = ? ORDER BY updated_at DESC""",
                tenant_ref,
            ).fetchall()
        return [
            SavedView(
                view_id=str(_value(row, 0)),
                name=str(_value(row, 1)),
                audience=str(_value(row, 2)),
                tab=str(_value(row, 3)),
                filters=json.loads(str(_value(row, 4))),
                version=int(_value(row, 5)),
                created_by=str(_value(row, 6)),
                updated_at=_iso(_value(row, 7)),
            )
            for row in rows
        ]

    def delete(self, tenant_ref: str, view_id: str) -> bool:
        with self._transaction() as cursor:
            cursor.execute(
                "DELETE FROM df_finops.saved_view WHERE tenant_ref = ? AND view_id = ?",
                tenant_ref,
                view_id,
            )
            return bool(cursor.rowcount)

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
                connection.rollback()
            raise FinOpsPersistenceError("FinOps planning persistence failed") from exc
        finally:
            if connection is not None:
                connection.close()


def _value(row: Any, index: int) -> Any:
    return row[index]


def _optional(value: Any) -> str | None:
    return None if value is None else str(value)


def _iso(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat().replace("+00:00", "Z")
    return str(value)
