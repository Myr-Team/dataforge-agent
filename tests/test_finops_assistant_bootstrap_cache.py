from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.finops.assistant_bootstrap import AssistantBootstrapCache
from backend.finops.assistant_store import (
    AssistantBootstrap,
    AssistantConversation,
    AssistantScope,
)


class _Backend:
    def __init__(self) -> None:
        self.values: dict[str, dict] = {}
        self.set_calls = 0
        self.deleted: list[str] = []

    def get_json(self, key: str):
        return self.values.get(key), {"status": "hit" if key in self.values else "miss"}

    def set_json(self, key: str, value: dict, *, ttl_seconds: int):
        self.set_calls += 1
        self.values[key] = value
        return {"status": "stored"}

    def delete(self, key: str):
        self.deleted.append(key)
        self.values.pop(key, None)
        return {"status": "deleted"}


def _scope(actor: str = "actor-a") -> AssistantScope:
    return AssistantScope(
        tenant_ref="tenant-a",
        actor_ref=actor,
        workspace_id="ws-a",
    )


def _value() -> AssistantBootstrap:
    now = datetime(2026, 8, 9, tzinfo=timezone.utc)
    return AssistantBootstrap(
        conversation=AssistantConversation(
            conversation_ref="foc_latest",
            title="成本分析",
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(days=30),
        ),
        loaded_at=now,
    )


def test_bootstrap_cache_is_scoped_and_reuses_snapshot() -> None:
    backend = _Backend()
    cache = AssistantBootstrapCache(backend=backend)
    loads = 0

    def loader() -> AssistantBootstrap:
        nonlocal loads
        loads += 1
        return _value()

    first = cache.load(_scope(), loader)
    second = cache.load(_scope(), loader)
    other_actor = cache.load(_scope("actor-b"), loader)

    assert first.cache_status == "miss"
    assert second.cache_status == "hit"
    assert other_actor.cache_key != first.cache_key
    assert loads == 2


def test_bootstrap_cache_invalidation_deletes_only_exact_scope_key() -> None:
    backend = _Backend()
    cache = AssistantBootstrapCache(backend=backend)
    snapshot = cache.load(_scope(), _value)

    cache.invalidate(_scope())

    assert backend.deleted == [snapshot.cache_key]
