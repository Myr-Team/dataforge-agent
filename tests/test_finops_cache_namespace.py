from __future__ import annotations

import backend.cache_store as cache_store
from backend.finops.cache_namespace import FinOpsCacheNamespace


class _AtomicCache:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.values: dict[str, int] = {}
        self.read_keys: list[str] = []
        self.incremented_keys: list[str] = []

    def get_int(self, key: str):
        self.read_keys.append(key)
        if not self.available:
            return None, {"provider": "redis", "status": "unavailable"}
        return self.values.get(key), {"provider": "redis", "status": "hit"}

    def increment(self, key: str, *, ttl_seconds: int = 2_592_000):
        self.incremented_keys.append(key)
        if not self.available:
            return None, {"provider": "redis", "status": "unavailable"}
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key], {"provider": "redis", "status": "incremented"}


def test_namespace_revision_changes_only_selected_domain() -> None:
    cache = _AtomicCache()
    namespace = FinOpsCacheNamespace(cache)
    before = namespace.current("tenant-a", ("ws-a",), ("roi", "overview"))

    namespace.bump("tenant-a", "ws-a", ("roi",))

    assert namespace.current("tenant-a", ("ws-a",), ("roi", "overview")) != before
    assert namespace.current("tenant-a", ("ws-a",), ("risk",)) == "risk:ws-a:0"
    assert len(cache.incremented_keys) == 1


def test_namespace_is_stable_and_hashes_redis_key_material() -> None:
    cache = _AtomicCache()
    namespace = FinOpsCacheNamespace(cache)

    first = namespace.current("tenant-a", ("ws-b", "ws-a"), ("risk", "roi"))
    second = namespace.current("tenant-a", ("ws-a", "ws-b"), ("roi", "risk"))

    assert first == second
    assert cache.read_keys
    assert all("tenant-a" not in key and "ws-a" not in key for key in cache.read_keys)


def test_unavailable_namespace_uses_zero_without_losing_scope_isolation() -> None:
    namespace = FinOpsCacheNamespace(_AtomicCache(available=False))

    tenant_a = namespace.current("tenant-a", ("ws-a",), ("roi",))
    tenant_b = namespace.current("tenant-b", ("ws-a",), ("roi",))
    workspace_b = namespace.current("tenant-a", ("ws-b",), ("roi",))

    assert tenant_a == tenant_b == "roi:ws-a:0"
    assert workspace_b == "roi:ws-b:0"


def test_namespace_rejects_unknown_domain() -> None:
    namespace = FinOpsCacheNamespace(_AtomicCache())

    try:
        namespace.current("tenant-a", ("ws-a",), ("secrets",))
    except ValueError as exc:
        assert str(exc) == "unsupported FinOps cache domain"
    else:
        raise AssertionError("unknown domain must be rejected")


def test_release_lock_uses_compare_and_delete_script(monkeypatch) -> None:
    class _Redis:
        def __init__(self) -> None:
            self.calls: list[tuple[str, int, str, str]] = []

        def eval(self, script: str, keys: int, key: str, token: str) -> int:
            self.calls.append((script, keys, key, token))
            return 1

    redis = _Redis()
    monkeypatch.setattr(
        cache_store,
        "_client",
        lambda: (redis, {"provider": "redis", "status": "configured"}),
    )

    released, metadata = cache_store.release_lock("lock-key", "owner-token")

    assert released is True
    assert metadata["status"] == "released"
    script, keys, key, token = redis.calls[0]
    assert keys == 1
    assert key == "lock-key"
    assert token == "owner-token"
    assert "redis.call('get', KEYS[1]) == ARGV[1]" in script
    assert "redis.call('del', KEYS[1])" in script
