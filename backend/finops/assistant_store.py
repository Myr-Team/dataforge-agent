from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Callable, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class AssistantConversationExpired(RuntimeError):
    """Raised when writing to a conversation whose retention window elapsed."""


class AssistantScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_ref: str = Field(min_length=1, max_length=128)
    actor_ref: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(min_length=1, max_length=160)


class AssistantConversation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    conversation_ref: str
    title: str
    created_at: datetime
    updated_at: datetime
    expires_at: datetime


class AssistantMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=1600)
    metric_context_payload: dict[str, object] | None = None
    created_at: datetime | None = None


class InMemoryAssistantConversationStore:
    def __init__(self, *, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._lock = RLock()
        self._conversations: dict[
            tuple[str, str, str, str], AssistantConversation
        ] = {}
        self._messages: dict[
            tuple[str, str, str, str], list[AssistantMessage]
        ] = {}

    @staticmethod
    def _key(scope: AssistantScope, conversation_ref: str) -> tuple[str, str, str, str]:
        return (
            scope.tenant_ref,
            scope.actor_ref,
            scope.workspace_id,
            conversation_ref,
        )

    def create(
        self,
        scope: AssistantScope,
        *,
        title: str,
        retention_days: int = 30,
    ) -> AssistantConversation:
        now = self._now()
        conversation = AssistantConversation(
            conversation_ref=f"foc_{uuid4().hex}",
            title=" ".join(str(title or "").split())[:120] or "新会话",
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(days=max(1, min(retention_days, 30))),
        )
        key = self._key(scope, conversation.conversation_ref)
        with self._lock:
            self._conversations[key] = conversation
            self._messages[key] = []
        return conversation

    def list_conversations(
        self, scope: AssistantScope
    ) -> tuple[AssistantConversation, ...]:
        prefix = (scope.tenant_ref, scope.actor_ref, scope.workspace_id)
        now = self._now()
        with self._lock:
            rows = [
                value
                for key, value in self._conversations.items()
                if key[:3] == prefix and value.expires_at > now
            ]
        return tuple(sorted(rows, key=lambda item: item.updated_at, reverse=True))

    def get_messages(
        self,
        scope: AssistantScope,
        conversation_ref: str,
    ) -> tuple[AssistantMessage, ...]:
        key = self._key(scope, conversation_ref)
        now = self._now()
        with self._lock:
            conversation = self._conversations.get(key)
            if conversation is None or conversation.expires_at <= now:
                return ()
            return tuple(self._messages.get(key, ()))

    def append(
        self,
        scope: AssistantScope,
        conversation_ref: str,
        message: AssistantMessage,
    ) -> None:
        key = self._key(scope, conversation_ref)
        now = self._now()
        with self._lock:
            conversation = self._conversations.get(key)
            if conversation is None:
                raise KeyError(conversation_ref)
            if conversation.expires_at <= now:
                raise AssistantConversationExpired(conversation_ref)
            stored = message.model_copy(
                update={"created_at": message.created_at or now}
            )
            self._messages.setdefault(key, []).append(stored)
            self._conversations[key] = conversation.model_copy(
                update={
                    "updated_at": now,
                    "expires_at": now + timedelta(days=30),
                }
            )

    def clear(self, scope: AssistantScope, conversation_ref: str) -> None:
        key = self._key(scope, conversation_ref)
        with self._lock:
            if key not in self._conversations:
                raise KeyError(conversation_ref)
            del self._conversations[key]
            self._messages.pop(key, None)

    def purge_expired(self, now: datetime | None = None) -> int:
        cutoff = now or self._now()
        with self._lock:
            expired = [
                key
                for key, value in self._conversations.items()
                if value.expires_at <= cutoff
            ]
            for key in expired:
                del self._conversations[key]
                self._messages.pop(key, None)
        return len(expired)


__all__ = [
    "AssistantConversation",
    "AssistantConversationExpired",
    "AssistantMessage",
    "AssistantScope",
    "InMemoryAssistantConversationStore",
]
