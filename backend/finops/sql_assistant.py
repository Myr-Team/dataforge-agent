from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import uuid4

from .assistant_store import (
    AssistantBootstrap,
    AssistantConversation,
    AssistantMessage,
    AssistantScope,
)


class SqlAssistantConversationStore:
    def __init__(
        self,
        *,
        connection_factory: Callable[[], Any],
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._connection_factory = connection_factory
        self._now = now or (lambda: datetime.now(timezone.utc))

    def create(
        self,
        scope: AssistantScope,
        *,
        title: str,
        retention_days: int = 30,
    ) -> AssistantConversation:
        now = self._now()
        value = AssistantConversation(
            conversation_ref=f"foc_{uuid4().hex}",
            title=" ".join(str(title or "").split())[:120] or "新会话",
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(days=max(1, min(retention_days, 30))),
        )
        self._execute(
            """/* finops:create-assistant-conversation */
            INSERT INTO df_finops.assistant_conversation (
                tenant_ref, actor_ref, workspace_id, conversation_ref, title,
                created_at, updated_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            scope.tenant_ref,
            scope.actor_ref,
            scope.workspace_id,
            value.conversation_ref,
            value.title,
            value.created_at,
            value.updated_at,
            value.expires_at,
        )
        return value

    def append(
        self,
        scope: AssistantScope,
        conversation_ref: str,
        message: AssistantMessage,
    ) -> None:
        self._execute(
            """/* finops:append-assistant-message */
            INSERT INTO df_finops.assistant_message (
                tenant_ref, actor_ref, workspace_id, conversation_ref,
                role, content, metric_context_payload, created_at
            )
            SELECT ?, ?, ?, ?, ?, ?, ?, ?
            WHERE EXISTS (
                SELECT 1 FROM df_finops.assistant_conversation
                WHERE tenant_ref = ? AND actor_ref = ? AND workspace_id = ?
                  AND conversation_ref = ?
                  AND expires_at > SYSUTCDATETIME()
            )""",
            scope.tenant_ref,
            scope.actor_ref,
            scope.workspace_id,
            conversation_ref,
            message.role,
            message.content,
            json.dumps(message.metric_context_payload, ensure_ascii=False)
            if message.metric_context_payload is not None
            else None,
            message.created_at or self._now(),
            scope.tenant_ref,
            scope.actor_ref,
            scope.workspace_id,
            conversation_ref,
        )

    def list_conversations(
        self,
        scope: AssistantScope,
    ) -> tuple[AssistantConversation, ...]:
        rows = self._query(
            """/* finops:list-assistant-conversations */
            SELECT conversation_ref, title, created_at, updated_at, expires_at
            FROM df_finops.assistant_conversation
            WHERE tenant_ref = ? AND actor_ref = ? AND workspace_id = ?
              AND expires_at > SYSUTCDATETIME()
            ORDER BY updated_at DESC""",
            scope.tenant_ref,
            scope.actor_ref,
            scope.workspace_id,
        )
        return tuple(
            AssistantConversation(
                conversation_ref=row[0],
                title=row[1],
                created_at=row[2],
                updated_at=row[3],
                expires_at=row[4],
            )
            for row in rows
        )

    def get_messages(
        self,
        scope: AssistantScope,
        conversation_ref: str,
    ) -> tuple[AssistantMessage, ...]:
        rows = self._query(
            """/* finops:list-assistant-messages */
            SELECT m.role, m.content, m.metric_context_payload, m.created_at
            FROM df_finops.assistant_message AS m
            WHERE m.tenant_ref = ? AND m.actor_ref = ? AND m.workspace_id = ?
              AND m.conversation_ref = ?
              AND EXISTS (
                SELECT 1 FROM df_finops.assistant_conversation AS c
                WHERE c.tenant_ref = m.tenant_ref AND c.actor_ref = m.actor_ref
                  AND c.workspace_id = m.workspace_id
                  AND c.conversation_ref = m.conversation_ref
                  AND c.expires_at > SYSUTCDATETIME()
              )
            ORDER BY m.message_id""",
            scope.tenant_ref,
            scope.actor_ref,
            scope.workspace_id,
            conversation_ref,
        )
        return tuple(
            AssistantMessage(
                role=row[0],
                content=row[1],
                metric_context_payload=(
                    json.loads(row[2]) if row[2] else None
                ),
                created_at=row[3],
            )
            for row in rows
        )

    def bootstrap(
        self,
        scope: AssistantScope,
        *,
        message_limit: int = 40,
    ) -> AssistantBootstrap:
        limit = max(1, min(int(message_limit), 40))
        rows = self._query(
            """/* finops:bootstrap-assistant-history */
            WITH latest AS (
                SELECT TOP (1)
                    conversation_ref, title, created_at, updated_at, expires_at
                FROM df_finops.assistant_conversation
                WHERE tenant_ref = ? AND actor_ref = ? AND workspace_id = ?
                  AND expires_at > SYSUTCDATETIME()
                ORDER BY updated_at DESC, conversation_ref DESC
            )
            SELECT c.conversation_ref, c.title, c.created_at, c.updated_at,
                c.expires_at, m.role, m.content, m.metric_context_payload,
                m.created_at, m.message_id
            FROM latest AS c
            OUTER APPLY (
                SELECT TOP (?) role, content, metric_context_payload,
                    created_at, message_id
                FROM df_finops.assistant_message
                WHERE tenant_ref = ? AND actor_ref = ? AND workspace_id = ?
                  AND conversation_ref = c.conversation_ref
                ORDER BY message_id DESC
            ) AS m
            ORDER BY m.message_id""",
            scope.tenant_ref,
            scope.actor_ref,
            scope.workspace_id,
            limit,
            scope.tenant_ref,
            scope.actor_ref,
            scope.workspace_id,
        )
        loaded_at = self._now()
        if not rows:
            return AssistantBootstrap(loaded_at=loaded_at)
        first = rows[0]
        conversation = AssistantConversation(
            conversation_ref=first[0],
            title=first[1],
            created_at=first[2],
            updated_at=first[3],
            expires_at=first[4],
        )
        messages = tuple(
            AssistantMessage(
                role=row[5],
                content=row[6],
                metric_context_payload=json.loads(row[7]) if row[7] else None,
                created_at=row[8],
            )
            for row in rows
            if row[5] is not None
        )
        return AssistantBootstrap(
            conversation=conversation,
            messages=messages,
            loaded_at=loaded_at,
        )

    def clear(
        self,
        scope: AssistantScope,
        conversation_ref: str,
    ) -> None:
        affected = self._execute(
            """/* finops:clear-assistant-conversation */
            DELETE FROM df_finops.assistant_conversation
            WHERE tenant_ref = ? AND actor_ref = ? AND workspace_id = ?
              AND conversation_ref = ?""",
            scope.tenant_ref,
            scope.actor_ref,
            scope.workspace_id,
            conversation_ref,
        )
        if affected == 0:
            raise KeyError(conversation_ref)

    def purge_expired(self, now: datetime | None = None) -> int:
        return self._execute(
            """/* finops:purge-assistant-conversations */
            DELETE FROM df_finops.assistant_conversation
            WHERE expires_at <= ?""",
            now or self._now(),
        )

    def _execute(self, operation: str, *parameters: object) -> int:
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute(operation, *parameters)
            affected = int(getattr(cursor, "rowcount", 0) or 0)
            connection.commit()
            return affected
        except Exception:
            try:
                connection.rollback()
            except Exception:
                pass
            raise
        finally:
            connection.close()

    def _query(
        self,
        operation: str,
        *parameters: object,
    ) -> list[Any]:
        connection = self._connection_factory()
        try:
            return list(
                connection.cursor().execute(
                    operation, *parameters
                ).fetchall()
            )
        finally:
            connection.close()


__all__ = ["SqlAssistantConversationStore"]
