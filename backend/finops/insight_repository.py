from __future__ import annotations

import base64
import json
from contextlib import contextmanager
from threading import RLock
from typing import Any, Callable, Iterator, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .insights import AgentKind, FinOpsInsight
from .sql_repository import FinOpsPersistenceError


class InsightPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[FinOpsInsight]
    next_cursor: str | None = None
    count: int = Field(ge=0)


class InMemoryInsightRepository:
    def __init__(self) -> None:
        self._lock = RLock()
        self._by_id: dict[str, FinOpsInsight] = {}
        self._by_fingerprint: dict[tuple[str, str, str], str] = {}

    def save(self, insight: FinOpsInsight) -> FinOpsInsight:
        with self._lock:
            by_id = self._by_id.get(insight.insight_id)
            if by_id is not None and by_id.tenant_ref != insight.tenant_ref:
                raise ValueError("insight id belongs to another tenant")
            key = (
                insight.tenant_ref,
                insight.agent_kind,
                insight.trigger_fingerprint,
            )
            existing_id = self._by_fingerprint.get(key)
            if existing_id is not None:
                return self._by_id[existing_id].model_copy(deep=True)
            stored = insight.model_copy(deep=True)
            self._by_id[insight.insight_id] = stored
            self._by_fingerprint[key] = insight.insight_id
            return stored.model_copy(deep=True)

    def replace(self, insight: FinOpsInsight) -> FinOpsInsight:
        with self._lock:
            existing = self._by_id.get(insight.insight_id)
            if existing is not None and existing.tenant_ref != insight.tenant_ref:
                raise ValueError("insight id belongs to another tenant")
            stored = insight.model_copy(deep=True)
            self._by_id[insight.insight_id] = stored
            self._by_fingerprint[
                (
                    insight.tenant_ref,
                    insight.agent_kind,
                    insight.trigger_fingerprint,
                )
            ] = insight.insight_id
            return stored.model_copy(deep=True)

    def get_by_fingerprint(
        self,
        *,
        tenant_ref: str,
        agent_kind: AgentKind,
        trigger_fingerprint: str,
    ) -> FinOpsInsight | None:
        with self._lock:
            insight_id = self._by_fingerprint.get(
                (tenant_ref, agent_kind, trigger_fingerprint)
            )
            value = self._by_id.get(insight_id or "")
        return value.model_copy(deep=True) if value else None

    def list(
        self,
        *,
        tenant_ref: str,
        authorized_workspace_ids: tuple[str, ...],
        agent_kind: AgentKind | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> InsightPage:
        allowed = set(authorized_workspace_ids)
        offset = _decode_cursor(cursor)
        bounded_limit = max(1, min(int(limit), 100))
        with self._lock:
            rows = [
                value.model_copy(deep=True)
                for value in self._by_id.values()
                if value.tenant_ref == tenant_ref
                and (agent_kind is None or value.agent_kind == agent_kind)
                and set(value.workspace_ids).issubset(allowed)
            ]
        rows.sort(
            key=lambda item: (item.generated_at, item.insight_id),
            reverse=True,
        )
        page = rows[offset : offset + bounded_limit]
        next_offset = offset + len(page)
        return InsightPage(
            items=page,
            count=len(page),
            next_cursor=_encode_cursor(next_offset) if next_offset < len(rows) else None,
        )


class _Cursor(Protocol):
    def execute(self, operation: str, *parameters: Any) -> "_Cursor": ...
    def fetchone(self) -> Any | None: ...
    def fetchall(self) -> list[Any]: ...


class _Connection(Protocol):
    autocommit: bool

    def cursor(self) -> _Cursor: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...


class SqlInsightRepository:
    def __init__(self, *, connection_factory: Callable[[], _Connection]) -> None:
        self._connection_factory = connection_factory

    def save(self, insight: FinOpsInsight) -> FinOpsInsight:
        payload = insight.model_dump_json(by_alias=True)
        scope_hash = _scope_hash(insight.workspace_ids)
        with self._transaction() as cursor:
            existing = cursor.execute(
                """/* finops:insight-save-read */
                SELECT insight_payload
                FROM df_finops.insight WITH (UPDLOCK, HOLDLOCK)
                WHERE tenant_ref = ? AND agent_kind = ?
                  AND trigger_fingerprint = ?""",
                insight.tenant_ref,
                insight.agent_kind,
                insight.trigger_fingerprint,
            ).fetchone()
            if existing is not None:
                return _from_payload(existing[0])
            collision = cursor.execute(
                """/* finops:insight-id-collision */
                SELECT tenant_ref FROM df_finops.insight
                WHERE insight_id = ?""",
                insight.insight_id,
            ).fetchone()
            if collision is not None and str(collision[0]) != insight.tenant_ref:
                raise ValueError("insight id belongs to another tenant")
            cursor.execute(
                """/* finops:insight-save-insert */
                INSERT INTO df_finops.insight (
                    insight_id, tenant_ref, agent_kind, workspace_scope_hash,
                    trigger_type, trigger_ref, trigger_fingerprint, insight_status,
                    generated_at, expires_at, insight_payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                insight.insight_id,
                insight.tenant_ref,
                insight.agent_kind,
                scope_hash,
                insight.trigger_type,
                insight.trigger_ref,
                insight.trigger_fingerprint,
                insight.status,
                insight.generated_at,
                insight.expires_at,
                payload,
            )
        return insight.model_copy(deep=True)

    def list(
        self,
        *,
        tenant_ref: str,
        authorized_workspace_ids: tuple[str, ...],
        agent_kind: AgentKind | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> InsightPage:
        offset = _decode_cursor(cursor)
        bounded_limit = max(1, min(int(limit), 100))
        with self._transaction() as db_cursor:
            rows = db_cursor.execute(
                """/* finops:insight-list */
                SELECT insight_payload
                FROM df_finops.insight
                WHERE tenant_ref = ? AND (? IS NULL OR agent_kind = ?)
                ORDER BY generated_at DESC, insight_id DESC""",
                tenant_ref,
                agent_kind,
                agent_kind,
            ).fetchall()
        allowed = set(authorized_workspace_ids)
        scoped = [
            _from_payload(row[0])
            for row in rows
            if set(_from_payload(row[0]).workspace_ids).issubset(allowed)
        ]
        page = scoped[offset : offset + bounded_limit]
        next_offset = offset + len(page)
        return InsightPage(
            items=page,
            count=len(page),
            next_cursor=(
                _encode_cursor(next_offset) if next_offset < len(scoped) else None
            ),
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
            if isinstance(exc, (FinOpsPersistenceError, ValueError)):
                raise
            raise FinOpsPersistenceError("FinOps insight operation failed") from exc
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass


def _scope_hash(workspace_ids: list[str]) -> str:
    import hashlib

    return hashlib.sha256(
        "\x1f".join(sorted(workspace_ids)).encode("utf-8")
    ).hexdigest()


def _from_payload(value: Any) -> FinOpsInsight:
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str):
        value = json.loads(value)
    return FinOpsInsight.model_validate(value)


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode("ascii")).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = int(base64.urlsafe_b64decode(padded).decode("ascii"))
    except (ValueError, UnicodeDecodeError):
        raise ValueError("invalid insight cursor") from None
    if value < 0:
        raise ValueError("invalid insight cursor")
    return value
