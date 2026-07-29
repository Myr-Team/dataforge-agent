from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.finops.assistant_store import (
    AssistantConversationExpired,
    AssistantMessage,
    AssistantScope,
    InMemoryAssistantConversationStore,
)


def _scope(**overrides: str) -> AssistantScope:
    payload = {
        "tenant_ref": "tenant-a",
        "actor_ref": "actor-a",
        "workspace_id": "ws-a",
    }
    payload.update(overrides)
    return AssistantScope.model_validate(payload)


def test_assistant_history_is_scoped_by_tenant_actor_and_workspace() -> None:
    store = InMemoryAssistantConversationStore()
    conversation = store.create(_scope(), title="成本为什么上升")
    store.append(
        _scope(),
        conversation.conversation_ref,
        AssistantMessage(role="user", content="成本为什么上升"),
    )

    assert len(store.get_messages(_scope(), conversation.conversation_ref)) == 1
    assert store.get_messages(
        _scope(actor_ref="actor-b"),
        conversation.conversation_ref,
    ) == ()
    assert store.get_messages(
        _scope(workspace_id="ws-b"),
        conversation.conversation_ref,
    ) == ()


def test_assistant_history_preserves_order_and_defaults_to_30_day_expiry() -> None:
    now = datetime(2026, 7, 26, tzinfo=timezone.utc)
    store = InMemoryAssistantConversationStore(now=lambda: now)
    conversation = store.create(_scope(), title="P95")
    store.append(
        _scope(),
        conversation.conversation_ref,
        AssistantMessage(role="user", content="第一条"),
    )
    store.append(
        _scope(),
        conversation.conversation_ref,
        AssistantMessage(role="assistant", content="第二条"),
    )

    messages = store.get_messages(_scope(), conversation.conversation_ref)

    assert [item.content for item in messages] == ["第一条", "第二条"]
    assert conversation.expires_at == now + timedelta(days=30)


def test_assistant_clear_and_expiry_are_scope_safe() -> None:
    now = datetime(2026, 7, 26, tzinfo=timezone.utc)
    store = InMemoryAssistantConversationStore(now=lambda: now)
    conversation = store.create(_scope(), title="Token")
    store.append(
        _scope(),
        conversation.conversation_ref,
        AssistantMessage(role="user", content="Token"),
    )

    with pytest.raises(KeyError):
        store.clear(_scope(actor_ref="actor-b"), conversation.conversation_ref)
    store.clear(_scope(), conversation.conversation_ref)
    assert store.get_messages(_scope(), conversation.conversation_ref) == ()

    old = store.create(_scope(), title="旧会话", retention_days=1)
    assert store.purge_expired(now + timedelta(days=2)) == 1
    assert store.get_messages(_scope(), old.conversation_ref) == ()


def test_expired_conversation_is_not_readable_or_listed_before_purge() -> None:
    clock = [datetime(2026, 7, 26, tzinfo=timezone.utc)]
    store = InMemoryAssistantConversationStore(now=lambda: clock[0])
    conversation = store.create(_scope(), title="即将过期", retention_days=1)

    # Advance past retention but before any scheduled purge runs.
    clock[0] = clock[0] + timedelta(days=2)

    assert store.get_messages(_scope(), conversation.conversation_ref) == ()
    assert store.list_conversations(_scope()) == ()


def test_append_to_expired_conversation_raises_instead_of_reviving() -> None:
    clock = [datetime(2026, 7, 26, tzinfo=timezone.utc)]
    store = InMemoryAssistantConversationStore(now=lambda: clock[0])
    conversation = store.create(_scope(), title="即将过期", retention_days=1)
    clock[0] = clock[0] + timedelta(days=2)

    with pytest.raises(AssistantConversationExpired):
        store.append(
            _scope(),
            conversation.conversation_ref,
            AssistantMessage(role="user", content="过期后追加"),
        )
    # The expired conversation is not silently revived.
    assert store.list_conversations(_scope()) == ()
