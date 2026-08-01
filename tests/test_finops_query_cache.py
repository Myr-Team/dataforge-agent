from __future__ import annotations

from datetime import datetime, timezone

from backend.finops.query import FinOpsQuery
from backend.finops.query_cache import CachedFinOpsQueryService, _cache_key


class _Delegate:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def overview(self, query: FinOpsQuery) -> dict[str, object]:
        self.calls.append("overview")
        return _payload(len(self.calls))

    def bootstrap(self, query: FinOpsQuery) -> dict[str, object]:
        self.calls.append("bootstrap")
        return _payload(len(self.calls))

    def events(self, query: FinOpsQuery) -> list[object]:
        return []

    def unit_economics_trend(
        self,
        query: FinOpsQuery,
        bucket: str,
    ) -> dict[str, object]:
        self.calls.append(f"unit_economics_trend:{bucket}")
        return {
            "freshness": {"generated_at": "2026-07-31T00:00:00Z"},
            "items": [{"bucket_at": bucket, "calls": len(self.calls)}],
            "count": 1,
        }


class _MemoryCache:
    def __init__(
        self,
        value: dict[str, object] | None = None,
        *,
        available: bool = True,
        lock_busy: bool = False,
    ) -> None:
        self.default_value = value
        self.values: dict[str, dict[str, object]] = {}
        self.revisions: dict[str, int] = {}
        self.keys: list[str] = []
        self.available = available
        self.lock_busy = lock_busy
        self.lock_tokens: dict[str, str] = {}
        self.released: list[tuple[str, str]] = []

    def get_json(
        self,
        key: str,
    ) -> tuple[dict[str, object] | None, dict[str, object]]:
        self.keys.append(key)
        if not self.available:
            return None, {"provider": "redis", "status": "unavailable"}
        value = self.values.get(key, self.default_value)
        return value, {
            "provider": "redis",
            "status": "hit" if value is not None else "miss",
        }

    def set_json(
        self,
        key: str,
        value: dict[str, object],
        *,
        ttl_seconds: int | None = None,
    ) -> dict[str, object]:
        if not self.available:
            return {"provider": "redis", "status": "unavailable"}
        self.values[key] = value
        self.default_value = None
        return {
            "provider": "redis",
            "status": "stored",
            "ttl_seconds": ttl_seconds,
        }

    def get_int(self, key: str) -> tuple[int | None, dict[str, object]]:
        if not self.available:
            return None, {"provider": "redis", "status": "unavailable"}
        return self.revisions.get(key), {"provider": "redis", "status": "hit"}

    def increment(
        self,
        key: str,
        *,
        ttl_seconds: int = 2_592_000,
    ) -> tuple[int | None, dict[str, object]]:
        if not self.available:
            return None, {"provider": "redis", "status": "unavailable"}
        self.revisions[key] = self.revisions.get(key, 0) + 1
        return self.revisions[key], {"provider": "redis", "status": "incremented"}

    def acquire_lock(
        self,
        key: str,
        token: str,
        *,
        ttl_seconds: int = 30,
    ) -> tuple[bool, dict[str, object]]:
        if not self.available:
            return False, {"provider": "redis", "status": "unavailable"}
        if self.lock_busy or key in self.lock_tokens:
            return False, {"provider": "redis", "status": "busy"}
        self.lock_tokens[key] = token
        return True, {"provider": "redis", "status": "acquired"}

    def release_lock(
        self,
        key: str,
        token: str,
    ) -> tuple[bool, dict[str, object]]:
        self.released.append((key, token))
        if self.lock_tokens.get(key) != token:
            return False, {"provider": "redis", "status": "not_owner"}
        del self.lock_tokens[key]
        return True, {"provider": "redis", "status": "released"}


class _Clock:
    def __init__(self, value: str) -> None:
        self.value = datetime.fromisoformat(value.replace("Z", "+00:00"))

    def __call__(self) -> datetime:
        return self.value


def _payload(requests: int) -> dict[str, object]:
    return {
        "scope": {"workspace_ids": ["ws-a"]},
        "freshness": {"generated_at": "2026-07-31T00:00:00Z"},
        "metrics": {"requests": requests},
    }


def _envelope(
    requests: int = 60,
) -> dict[str, object]:
    return {
        "payload": _payload(requests),
        "_cache": {
            "generated_at": "2026-07-31T00:00:00Z",
            "fresh_until": "2026-07-31T00:05:00Z",
            "stale_until": "2026-07-31T00:30:00Z",
        },
    }


def _query(
    tenant_ref: str = "tenant-a",
    *,
    permission_scope: str = "a2f90f16ea22c03b",
) -> FinOpsQuery:
    return FinOpsQuery(
        tenant_ref=tenant_ref,
        authorized_workspace_ids=("ws-a",),
        permission_scope=permission_scope,
        from_value="2026-07-01T00:00:00Z",
        to_value="2026-07-31T00:00:00Z",
    )


def _service(
    delegate: _Delegate,
    cache: _MemoryCache,
    now: str,
) -> CachedFinOpsQueryService:
    return CachedFinOpsQueryService(delegate, cache=cache, clock=_Clock(now))


def test_fresh_value_is_returned_without_recompute() -> None:
    delegate = _Delegate()
    service = _service(delegate, _MemoryCache(_envelope()), "2026-07-31T00:04:00Z")

    result = service.bootstrap(_query())

    assert result["freshness"]["query_cache"]["status"] == "hit_fresh"
    assert result["metrics"]["requests"] == 60
    assert delegate.calls == []


def test_stale_value_is_returned_without_recompute() -> None:
    delegate = _Delegate()
    service = _service(delegate, _MemoryCache(_envelope()), "2026-07-31T00:06:00Z")

    result = service.bootstrap(_query())

    assert result["freshness"]["query_cache"]["status"] == "hit_stale"
    assert delegate.calls == []


def test_expired_value_is_recomputed_and_stored_for_thirty_minutes() -> None:
    delegate = _Delegate()
    cache = _MemoryCache(_envelope())
    service = _service(delegate, cache, "2026-07-31T00:31:00Z")

    result = service.bootstrap(_query())

    assert result["freshness"]["query_cache"]["status"] == "miss"
    assert delegate.calls == ["bootstrap"]
    stored = next(iter(cache.values.values()))
    assert stored["_cache"] == {
        "generated_at": "2026-07-31T00:31:00Z",
        "fresh_until": "2026-07-31T00:36:00Z",
        "stale_until": "2026-07-31T01:01:00Z",
    }


def test_force_refresh_recomputes_replaces_stale_and_releases_owned_lock() -> None:
    delegate = _Delegate()
    cache = _MemoryCache(_envelope())
    service = _service(delegate, cache, "2026-07-31T00:06:00Z")

    result = service.bootstrap(_query(), force_refresh=True)

    assert result["freshness"]["query_cache"]["status"] == "revalidated"
    assert delegate.calls == ["bootstrap"]
    assert result["metrics"]["requests"] == 1
    assert len(cache.released) == 1
    assert cache.released[0][1]


def test_busy_force_refresh_returns_stale_as_revalidating() -> None:
    delegate = _Delegate()
    cache = _MemoryCache(_envelope(), lock_busy=True)
    service = _service(delegate, cache, "2026-07-31T00:06:00Z")

    result = service.bootstrap(_query(), force_refresh=True)

    assert result["freshness"]["query_cache"]["status"] == "revalidating"
    assert result["metrics"]["requests"] == 60
    assert delegate.calls == []


def test_redis_unavailable_falls_back_to_authorized_delegate() -> None:
    delegate = _Delegate()
    service = _service(delegate, _MemoryCache(available=False), "2026-07-31T00:06:00Z")

    result = service.overview(_query())

    assert result["freshness"]["query_cache"]["status"] == "unavailable"
    assert delegate.calls == ["overview"]


def test_complete_decision_payload_is_cached_as_one_object() -> None:
    delegate = _Delegate()
    cache = _MemoryCache()
    service = _service(delegate, cache, "2026-07-31T00:00:00Z")
    computes: list[str] = []

    first = service.compose(
        "roi_decision",
        _query(),
        lambda: computes.append("roi") or {"decision": {"status": "observed"}},
    )
    second = service.compose(
        "roi_decision",
        _query(),
        lambda: computes.append("unexpected") or {},
    )

    assert first["freshness"]["query_cache"]["status"] == "miss"
    assert second["freshness"]["query_cache"]["status"] == "hit_fresh"
    assert second["decision"] == {"status": "observed"}
    assert computes == ["roi"]
    stored = next(iter(cache.values.values()))
    assert stored["payload"]["decision"] == {"status": "observed"}


def test_cache_key_changes_with_tenant_and_permission_scope_without_leaking_them() -> None:
    namespace = "overview:ws-a:0"
    owner = _query(permission_scope="owner-scope-a1b2")
    member = _query(permission_scope="member-scope-c3d4")
    other_tenant = _query("tenant-b", permission_scope="owner-scope-a1b2")

    owner_key = _cache_key("overview", owner, {}, namespace)
    member_key = _cache_key("overview", member, {}, namespace)
    tenant_key = _cache_key("overview", other_tenant, {}, namespace)

    assert owner_key != member_key
    assert owner_key != tenant_key
    assert "tenant-a" not in owner_key
    assert "owner-scope" not in owner_key


def test_query_cache_never_caches_internal_event_objects() -> None:
    delegate = _Delegate()
    service = _service(delegate, _MemoryCache(), "2026-07-31T00:00:00Z")

    assert service.events(_query()) == []


def test_unit_economics_cache_isolated_by_bucket() -> None:
    delegate = _Delegate()
    cache = _MemoryCache()
    service = _service(delegate, cache, "2026-07-31T00:00:00Z")

    first = service.unit_economics_trend(_query(), "day")
    second = service.unit_economics_trend(_query(), "day")
    hourly = service.unit_economics_trend(_query(), "hour")

    assert first["items"][0]["calls"] == 1
    assert second["items"][0]["calls"] == 1
    assert hourly["items"][0]["calls"] == 2
    assert cache.keys[0] == cache.keys[1]
    assert cache.keys[0] != cache.keys[2]
