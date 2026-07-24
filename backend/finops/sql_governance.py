from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from .governance import ActionTransition, GovernanceAction
from .sql_repository import ConnectionFactory, FinOpsPersistenceError


class SqlFinOpsActionRepository:
    def __init__(self, *, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def save(self, action: GovernanceAction) -> GovernanceAction:
        payload = json.dumps(action.payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        result = (
            json.dumps(action.result, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            if action.result is not None
            else None
        )
        with self._transaction() as cursor:
            cursor.execute(
                """/* finops:save-governance-action */
                MERGE df_finops.governance_action WITH (HOLDLOCK) AS target
                USING (SELECT ? AS tenant_ref, ? AS action_id) AS source
                ON target.tenant_ref = source.tenant_ref AND target.action_id = source.action_id
                WHEN MATCHED THEN UPDATE SET action_status = ?, approved_by = ?, action_payload = ?,
                    result_payload = ?, version = ?, updated_at = ?
                WHEN NOT MATCHED THEN INSERT (
                    tenant_ref, action_id, action_type, action_status, workspace_id,
                    base_version, proposed_by, approved_by, action_payload, result_payload,
                    version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
                action.tenant_ref,
                action.action_id,
                action.status,
                action.approved_by,
                payload,
                result,
                action.version,
                action.updated_at,
                action.tenant_ref,
                action.action_id,
                action.action_type,
                action.status,
                action.payload.get("workspace_id"),
                action.payload.get("base_version"),
                action.proposed_by,
                action.approved_by,
                payload,
                result,
                action.version,
                action.created_at,
                action.updated_at,
            )
            cursor.execute(
                """/* finops:replace-action-transitions */
                DELETE FROM df_finops.action_transition
                WHERE tenant_ref = ? AND action_id = ?""",
                action.tenant_ref,
                action.action_id,
            )
            for transition in action.transitions:
                cursor.execute(
                    """/* finops:insert-action-transition */
                    INSERT INTO df_finops.action_transition (
                        tenant_ref, action_id, from_status, to_status, actor_ref, reason, occurred_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    action.tenant_ref,
                    action.action_id,
                    transition.from_status,
                    transition.to_status,
                    transition.actor_ref,
                    transition.reason,
                    transition.occurred_at,
                )
        return action.model_copy(deep=True)

    def get(self, tenant_ref: str, action_id: str) -> GovernanceAction | None:
        with self._transaction() as cursor:
            row = cursor.execute(
                """/* finops:get-governance-action */
                SELECT action_id, action_type, action_status, proposed_by, approved_by,
                    action_payload, result_payload, version, created_at, updated_at
                FROM df_finops.governance_action
                WHERE tenant_ref = ? AND action_id = ?""",
                tenant_ref,
                action_id,
            ).fetchone()
            if row is None:
                return None
            transitions = self._transitions(cursor, tenant_ref, action_id)
        return _action(tenant_ref, row, transitions)

    def list(self, tenant_ref: str) -> list[GovernanceAction]:
        with self._transaction() as cursor:
            rows = cursor.execute(
                """/* finops:list-governance-actions */
                SELECT action_id, action_type, action_status, proposed_by, approved_by,
                    action_payload, result_payload, version, created_at, updated_at
                FROM df_finops.governance_action
                WHERE tenant_ref = ? ORDER BY created_at DESC""",
                tenant_ref,
            ).fetchall()
            actions = [
                _action(
                    tenant_ref,
                    row,
                    self._transitions(cursor, tenant_ref, str(row[0])),
                )
                for row in rows
            ]
        return actions

    @staticmethod
    def _transitions(cursor: Any, tenant_ref: str, action_id: str) -> list[ActionTransition]:
        rows = cursor.execute(
            """/* finops:list-action-transitions */
            SELECT from_status, to_status, actor_ref, reason, occurred_at
            FROM df_finops.action_transition
            WHERE tenant_ref = ? AND action_id = ?
            ORDER BY transition_id ASC""",
            tenant_ref,
            action_id,
        ).fetchall()
        return [
            ActionTransition(
                from_status=row[0],
                to_status=str(row[1]),
                actor_ref=str(row[2]),
                reason=str(row[3]) if row[3] is not None else None,
                occurred_at=_iso(row[4]),
            )
            for row in rows
        ]

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
            raise FinOpsPersistenceError("FinOps governance SQL operation failed") from exc
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass


def _action(tenant_ref: str, row: Any, transitions: list[ActionTransition]) -> GovernanceAction:
    return GovernanceAction(
        action_id=str(row[0]),
        tenant_ref=tenant_ref,
        action_type=str(row[1]),
        status=str(row[2]),
        proposed_by=str(row[3]),
        approved_by=str(row[4]) if row[4] is not None else None,
        payload=json.loads(str(row[5])),
        result=json.loads(str(row[6])) if row[6] is not None else None,
        version=int(row[7]),
        created_at=_iso(row[8]),
        updated_at=_iso(row[9]),
        transitions=transitions,
    )


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        parsed = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)
