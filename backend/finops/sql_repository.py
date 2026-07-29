from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Protocol, Sequence

from .models import FinOpsRequestEvent
from .repository import (
    FinOpsEventKeyRepair,
    resolve_event_key_repair,
)


_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "sql" / "finops_schema.sql"


class FinOpsPersistenceError(RuntimeError):
    pass


class _Cursor(Protocol):
    def execute(self, operation: str, *parameters: Any) -> "_Cursor": ...
    def fetchall(self) -> Sequence[Any]: ...
    def fetchone(self) -> Any | None: ...


class _Connection(Protocol):
    autocommit: bool
    def cursor(self) -> _Cursor: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...


ConnectionFactory = Callable[[], _Connection]


class SqlFinOpsRepository:
    def __init__(
        self,
        *,
        connection_factory: ConnectionFactory,
        schema_path: Path | None = None,
    ) -> None:
        if not callable(connection_factory):
            raise TypeError("connection_factory must be callable")
        self._connection_factory = connection_factory
        self._schema_path = schema_path or _SCHEMA_PATH

    def initialize_schema(self) -> None:
        schema = self._schema_path.read_text(encoding="utf-8")
        with self._transaction() as cursor:
            cursor.execute(f"/* finops:schema */\n{schema}")

    def upsert_events(self, events: Iterable[FinOpsRequestEvent]) -> None:
        with self._transaction() as cursor:
            for event in events:
                _upsert_event(cursor, event)

    def repair_event_keys(
        self,
        plans: Iterable[FinOpsEventKeyRepair],
    ) -> int:
        changed = 0
        with self._transaction() as cursor:
            for plan in plans:
                canonical = plan.canonical_event
                rows = cursor.execute(
                    """/* finops:lock-event-key-repair */
                    SELECT tenant_ref, request_ref, event_payload
                    FROM df_finops.request_event WITH (UPDLOCK, HOLDLOCK)
                    WHERE (tenant_ref = ? AND request_ref = ?)
                       OR (tenant_ref = ? AND request_ref = ?)""",
                    plan.legacy_tenant_ref,
                    plan.legacy_request_ref,
                    canonical.tenant_ref,
                    canonical.request_ref,
                ).fetchall()
                existing: dict[tuple[str, str], FinOpsRequestEvent] = {}
                for row in rows:
                    key = (
                        str(_row_value(row, 0)),
                        str(_row_value(row, 1)),
                    )
                    existing[key] = _event_from_payload(_row_value(row, 2))
                legacy_key = (
                    plan.legacy_tenant_ref,
                    plan.legacy_request_ref,
                )
                canonical_key = (
                    canonical.tenant_ref,
                    canonical.request_ref,
                )
                legacy = existing.get(legacy_key)
                recorded = existing.get(canonical_key)
                resolved = resolve_event_key_repair(
                    plan,
                    legacy=legacy,
                    canonical=recorded,
                )
                if recorded != resolved:
                    _upsert_event(cursor, resolved)
                    changed += 1
                if legacy_key != canonical_key and legacy is not None:
                    cursor.execute(
                        """/* finops:delete-legacy-request-event */
                        DELETE FROM df_finops.request_event
                        WHERE tenant_ref = ? AND request_ref = ?""",
                        plan.legacy_tenant_ref,
                        plan.legacy_request_ref,
                    )
                    if recorded == resolved:
                        changed += 1
        return changed

    def list_events(
        self,
        *,
        tenant_ref: str,
        workspace_ids: tuple[str, ...],
        from_value: str,
        to_value: str,
    ) -> list[FinOpsRequestEvent]:
        if not workspace_ids:
            return []
        placeholders = ",".join("?" for _ in workspace_ids)
        with self._transaction() as cursor:
            rows = cursor.execute(
                f"""/* finops:list-request-events */
                SELECT event_payload
                FROM df_finops.request_event
                WHERE tenant_ref = ?
                  AND workspace_id IN ({placeholders})
                  AND occurred_at >= ?
                  AND occurred_at <= ?
                ORDER BY occurred_at ASC, request_ref ASC""",
                tenant_ref,
                *workspace_ids,
                from_value,
                to_value,
            ).fetchall()
        return [_event_from_payload(_row_value(row, 0)) for row in rows]

    def get_event(
        self,
        *,
        tenant_ref: str,
        workspace_ids: tuple[str, ...],
        request_ref: str,
    ) -> FinOpsRequestEvent | None:
        if not workspace_ids:
            return None
        placeholders = ",".join("?" for _ in workspace_ids)
        with self._transaction() as cursor:
            row = cursor.execute(
                f"""/* finops:get-request-event */
                SELECT event_payload
                FROM df_finops.request_event
                WHERE tenant_ref = ?
                  AND request_ref = ?
                  AND workspace_id IN ({placeholders})""",
                tenant_ref,
                request_ref,
                *workspace_ids,
            ).fetchone()
        return _event_from_payload(_row_value(row, 0)) if row is not None else None

    def list_scopes(
        self,
        *,
        from_value: str,
        to_value: str,
    ) -> dict[str, tuple[str, ...]]:
        with self._transaction() as cursor:
            rows = cursor.execute(
                """/* finops:list-scopes */
                SELECT DISTINCT tenant_ref, workspace_id
                FROM df_finops.request_event
                WHERE occurred_at >= ? AND occurred_at <= ?
                ORDER BY tenant_ref, workspace_id""",
                from_value,
                to_value,
            ).fetchall()
        grouped: dict[str, list[str]] = {}
        for row in rows:
            tenant_ref = str(_row_value(row, 0) or "").strip()
            workspace_id = str(_row_value(row, 1) or "").strip()
            if tenant_ref and workspace_id:
                grouped.setdefault(tenant_ref, []).append(workspace_id)
        return {
            tenant_ref: tuple(sorted(set(workspace_ids)))
            for tenant_ref, workspace_ids in grouped.items()
        }

    def purge_expired_request_facts(self, *, retention_days: int = 90) -> None:
        days = max(1, min(int(retention_days), 365))
        with self._transaction() as cursor:
            cursor.execute(
                """/* finops:purge-expired-request-events */
                DELETE FROM df_finops.request_event
                WHERE occurred_at < DATEADD(day, ?, SYSUTCDATETIME())""",
                -days,
            )

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
            raise FinOpsPersistenceError("FinOps SQL operation failed") from exc
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass


def _upsert_event(cursor: _Cursor, event: FinOpsRequestEvent) -> None:
    payload = json.dumps(
        event.model_dump(mode="json", exclude_none=False),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    cursor.execute(
        """/* finops:upsert-request-event */
        MERGE df_finops.request_event WITH (HOLDLOCK) AS target
        USING (SELECT ? AS tenant_ref, ? AS request_ref) AS source
        ON target.tenant_ref = source.tenant_ref
           AND target.request_ref = source.request_ref
        WHEN MATCHED THEN UPDATE SET
            occurred_at = ?, call_class = ?, department_id = ?, workspace_id = ?,
            actor_ref = ?, run_id = ?, agent_id = ?, model_deployment = ?,
            route = ?, execution_kind = ?, request_status = ?, error_category = ?,
            latency_ms = ?, total_tokens = ?, cost_amount = ?,
            price_card_revision = ?, gateway_coverage = ?, evidence_state = ?,
            correlation_ref = ?, event_payload = ?, updated_at = SYSUTCDATETIME()
        WHEN NOT MATCHED THEN INSERT (
            tenant_ref, request_ref, occurred_at, call_class, department_id,
            workspace_id, actor_ref, run_id, agent_id, model_deployment, route,
            execution_kind, request_status, error_category, latency_ms, total_tokens,
            cost_amount, price_card_revision, gateway_coverage, evidence_state,
            correlation_ref, event_payload
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        );""",
        event.tenant_ref,
        event.request_ref,
        event.occurred_at,
        event.call_class,
        event.department_id,
        event.workspace_id,
        event.actor_ref,
        event.run_id,
        event.agent_id,
        event.deployment or event.model,
        event.route,
        event.execution_kind,
        event.status,
        event.error_category,
        event.latency_ms,
        event.tokens.total,
        event.estimated_cost.amount,
        event.estimated_cost.price_card_revision,
        event.gateway_coverage,
        event.evidence_state,
        event.correlation_ref,
        payload,
        event.tenant_ref,
        event.request_ref,
        event.occurred_at,
        event.call_class,
        event.department_id,
        event.workspace_id,
        event.actor_ref,
        event.run_id,
        event.agent_id,
        event.deployment or event.model,
        event.route,
        event.execution_kind,
        event.status,
        event.error_category,
        event.latency_ms,
        event.tokens.total,
        event.estimated_cost.amount,
        event.estimated_cost.price_card_revision,
        event.gateway_coverage,
        event.evidence_state,
        event.correlation_ref,
        payload,
    )


def _row_value(row: Any, index: int) -> Any:
    if isinstance(row, (tuple, list)):
        return row[index]
    try:
        return row[index]
    except (KeyError, TypeError):
        return getattr(row, "event_payload")


def _event_from_payload(value: Any) -> FinOpsRequestEvent:
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    payload = json.loads(str(value))
    return FinOpsRequestEvent.model_validate(payload)
