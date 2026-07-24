from __future__ import annotations

from backend.finops.query_cache import CachedFinOpsQueryService
from backend.finops.query import FinOpsQuery


class _Delegate:
    def __init__(self) -> None:
        self.calls = 0

    def overview(self, query: FinOpsQuery) -> dict[str, object]:
        self.calls += 1
        return {
            "scope": {"tenant_ref": query.tenant_ref},
            "freshness": {"generated_at": "2026-07-24T00:00:00Z"},
            "metrics": {"requests": self.calls},
        }

    def bootstrap(self, query: FinOpsQuery) -> dict[str, object]:
        self.calls += 1
        return {
            "scope": {"tenant_ref": query.tenant_ref},
            "freshness": {"generated_at": "2026-07-24T00:00:00Z"},
            "overview": {"metrics": {"requests": self.calls}},
        }

    def events(self, query: FinOpsQuery) -> list[object]:
        return []


class _MemoryCache:
    def __init__(self) -> None:
        self.values: dict[str, dict[str, object]] = {}
        self.keys: list[str] = []

    def get_json(self, key: str) -> tuple[dict[str, object] | None, dict[str, object]]:
        self.keys.append(key)
        value = self.values.get(key)
        return value, {"provider": "redis", "status": "hit" if value else "miss"}

    def set_json(
        self,
        key: str,
        value: dict[str, object],
        *,
        ttl_seconds: int | None = None,
    ) -> dict[str, object]:
        self.values[key] = value
        return {"provider": "redis", "status": "stored", "ttl_seconds": ttl_seconds}


def _query(tenant_ref: str) -> FinOpsQuery:
    return FinOpsQuery(
        tenant_ref=tenant_ref,
        authorized_workspace_ids=("ws-a",),
        from_value="2026-07-01T00:00:00Z",
        to_value="2026-07-24T00:00:00Z",
    )


def test_query_cache_uses_redis_for_results_and_marks_hit_without_leaking_scope() -> None:
    delegate = _Delegate()
    cache = _MemoryCache()
    service = CachedFinOpsQueryService(delegate, cache=cache, ttl_seconds=60)

    first = service.overview(_query("tenant-a"))
    second = service.overview(_query("tenant-a"))
    other_tenant = service.overview(_query("tenant-b"))

    assert first["freshness"]["query_cache"]["status"] == "miss"
    assert second["freshness"]["query_cache"]["status"] == "hit"
    assert second["metrics"]["requests"] == 1
    assert other_tenant["metrics"]["requests"] == 2
    assert cache.keys[0] == cache.keys[1]
    assert cache.keys[0] != cache.keys[2]
    assert "tenant-a" not in cache.keys[0]


def test_query_cache_never_caches_internal_event_objects() -> None:
    delegate = _Delegate()
    service = CachedFinOpsQueryService(delegate, cache=_MemoryCache(), ttl_seconds=60)

    assert service.events(_query("tenant-a")) == []


def test_bootstrap_cache_deduplicates_same_scope_and_isolates_tenants() -> None:
    delegate = _Delegate()
    cache = _MemoryCache()
    service = CachedFinOpsQueryService(delegate, cache=cache, ttl_seconds=60)

    first = service.bootstrap(_query("tenant-a"))
    second = service.bootstrap(_query("tenant-a"))
    other_tenant = service.bootstrap(_query("tenant-b"))

    assert first["freshness"]["query_cache"]["status"] == "miss"
    assert second["freshness"]["query_cache"]["status"] == "hit"
    assert second["overview"]["metrics"]["requests"] == 1
    assert other_tenant["overview"]["metrics"]["requests"] == 2
    assert cache.keys[0] == cache.keys[1]
    assert cache.keys[0] != cache.keys[2]
