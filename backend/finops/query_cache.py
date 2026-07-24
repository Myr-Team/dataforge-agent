from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Callable, Protocol

from .query import FinOpsQuery


class JsonCache(Protocol):
    def get_json(self, key: str) -> tuple[dict[str, Any] | None, dict[str, Any]]: ...
    def set_json(
        self,
        key: str,
        value: dict[str, Any],
        *,
        ttl_seconds: int | None = None,
    ) -> dict[str, Any]: ...


class CachedFinOpsQueryService:
    """Redis-only result cache around the tenant-scoped FinOps query service."""

    def __init__(self, delegate: Any, *, cache: JsonCache, ttl_seconds: int = 60) -> None:
        self._delegate = delegate
        self._cache = cache
        self._ttl_seconds = max(1, min(int(ttl_seconds), 300))

    def filters(self, query: FinOpsQuery) -> dict[str, Any]:
        return self._cached("filters", query, lambda: self._delegate.filters(query))

    def overview(self, query: FinOpsQuery) -> dict[str, Any]:
        return self._cached("overview", query, lambda: self._delegate.overview(query))

    def bootstrap(self, query: FinOpsQuery) -> dict[str, Any]:
        return self._cached("bootstrap", query, lambda: self._delegate.bootstrap(query))

    def breakdowns(self, query: FinOpsQuery, group_by: str) -> dict[str, Any]:
        return self._cached(
            "breakdowns",
            query,
            lambda: self._delegate.breakdowns(query, group_by),
            extras={"group_by": group_by},
        )

    def agents(self, query: FinOpsQuery) -> dict[str, Any]:
        return self._cached("agents", query, lambda: self._delegate.agents(query))

    def trends(self, query: FinOpsQuery, bucket: str) -> dict[str, Any]:
        return self._cached(
            "trends",
            query,
            lambda: self._delegate.trends(query, bucket),
            extras={"bucket": bucket},
        )

    def requests(self, query: FinOpsQuery) -> dict[str, Any]:
        return self._cached("requests", query, lambda: self._delegate.requests(query))

    def request_detail(self, query: FinOpsQuery, request_ref: str) -> dict[str, Any] | None:
        return self._cached(
            "request_detail",
            query,
            lambda: self._delegate.request_detail(query, request_ref),
            extras={"request_ref": request_ref},
        )

    def events(self, query: FinOpsQuery) -> list[Any]:
        # Internal Pydantic objects are never serialized into the query cache.
        return self._delegate.events(query)

    def _cached(
        self,
        operation: str,
        query: FinOpsQuery,
        compute: Callable[[], dict[str, Any] | None],
        *,
        extras: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        key = _cache_key(operation, query, extras or {})
        cached, metadata = self._cache.get_json(key)
        if isinstance(cached, dict):
            return _with_cache_status(cached, metadata, default="hit")
        value = compute()
        if value is None:
            return None
        clean = deepcopy(value)
        self._cache.set_json(key, clean, ttl_seconds=self._ttl_seconds)
        return _with_cache_status(clean, metadata, default="miss")


def _cache_key(operation: str, query: FinOpsQuery, extras: dict[str, Any]) -> str:
    payload = {
        "operation": operation,
        "query": query.model_dump(mode="json"),
        "extras": extras,
    }
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return f"finops:query:v1:{digest}"


def _with_cache_status(
    payload: dict[str, Any],
    metadata: dict[str, Any],
    *,
    default: str,
) -> dict[str, Any]:
    result = deepcopy(payload)
    freshness = result.get("freshness")
    if not isinstance(freshness, dict):
        freshness = {}
        result["freshness"] = freshness
    raw_status = str(metadata.get("status") or default).strip().lower()
    status = raw_status if raw_status in {"hit", "miss", "unconfigured", "unavailable"} else default
    freshness["query_cache"] = {"provider": "redis", "status": status}
    return result
