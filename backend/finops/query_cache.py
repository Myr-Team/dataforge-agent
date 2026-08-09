from __future__ import annotations

import hashlib
import json
import secrets
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal, Protocol

from .cache_namespace import FinOpsCacheNamespace
from .query import FinOpsQuery


FRESH_SECONDS = 300
STALE_SECONDS = 1800
LOCK_SECONDS = 30
WAIT_TIMEOUT_SECONDS = 2.0
WAIT_POLL_SECONDS = 0.02
QUERY_CACHE_SCHEMA_VERSION = "v4"


class FinOpsCacheBusy(RuntimeError):
    """Retryable contention when another request owns the recompute lock."""

    def __init__(self, *_args: object) -> None:
        super().__init__("FinOps query cache is temporarily busy")


class JsonCache(Protocol):
    def get_json(self, key: str) -> tuple[dict[str, Any] | None, dict[str, Any]]: ...

    def set_json(
        self,
        key: str,
        value: dict[str, Any],
        *,
        ttl_seconds: int | None = None,
    ) -> dict[str, Any]: ...

    def get_int(self, key: str) -> tuple[int | None, dict[str, Any]]: ...

    def increment(
        self,
        key: str,
        *,
        ttl_seconds: int = STALE_SECONDS,
    ) -> tuple[int | None, dict[str, Any]]: ...

    def acquire_lock(
        self,
        key: str,
        token: str,
        *,
        ttl_seconds: int = LOCK_SECONDS,
    ) -> tuple[bool, dict[str, Any]]: ...

    def release_lock(self, key: str, token: str) -> tuple[bool, dict[str, Any]]: ...


class CachedFinOpsQueryService:
    """Fresh/stale Redis cache around an already-authorized query service."""

    def __init__(
        self,
        delegate: Any,
        *,
        cache: JsonCache,
        namespace: FinOpsCacheNamespace | None = None,
        clock: Callable[[], datetime] | None = None,
        wait_timeout_seconds: float = WAIT_TIMEOUT_SECONDS,
        wait_poll_seconds: float = WAIT_POLL_SECONDS,
        monotonic: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self._delegate = delegate
        self._cache = cache
        self._namespace = namespace or FinOpsCacheNamespace(cache)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._wait_timeout_seconds = max(
            0.001,
            min(float(wait_timeout_seconds), float(LOCK_SECONDS)),
        )
        self._wait_poll_seconds = max(
            0.001,
            min(float(wait_poll_seconds), self._wait_timeout_seconds),
        )
        self._monotonic = monotonic or time.monotonic
        self._sleep = sleeper or time.sleep

    def filters(
        self,
        query: FinOpsQuery,
        *,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        return self._cached(
            "filters",
            query,
            lambda: self._delegate.filters(query),
            force_refresh=force_refresh,
        )

    def overview(
        self,
        query: FinOpsQuery,
        *,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        return self._cached(
            "overview",
            query,
            lambda: self._delegate.overview(query),
            force_refresh=force_refresh,
        )

    def bootstrap(
        self,
        query: FinOpsQuery,
        *,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        return self._cached(
            "bootstrap",
            query,
            lambda: self._delegate.bootstrap(query),
            force_refresh=force_refresh,
        )

    def breakdowns(
        self,
        query: FinOpsQuery,
        group_by: str,
        *,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        return self._cached(
            "breakdowns",
            query,
            lambda: self._delegate.breakdowns(query, group_by),
            extras={"group_by": group_by},
            force_refresh=force_refresh,
        )

    def agents(
        self,
        query: FinOpsQuery,
        *,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        return self._cached(
            "agents",
            query,
            lambda: self._delegate.agents(query),
            force_refresh=force_refresh,
        )

    def trends(
        self,
        query: FinOpsQuery,
        bucket: str,
        *,
        metric: str = "tokens",
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        return self._cached(
            "trends",
            query,
            lambda: self._delegate.trends(query, bucket, metric=metric),
            extras={"bucket": bucket, "metric": metric},
            force_refresh=force_refresh,
        )

    def requests(
        self,
        query: FinOpsQuery,
        *,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        return self._cached(
            "requests",
            query,
            lambda: self._delegate.requests(query),
            force_refresh=force_refresh,
        )

    def request_detail(
        self,
        query: FinOpsQuery,
        request_ref: str,
        *,
        force_refresh: bool = False,
    ) -> dict[str, Any] | None:
        return self._cached(
            "request_detail",
            query,
            lambda: self._delegate.request_detail(query, request_ref),
            extras={"request_ref": request_ref},
            force_refresh=force_refresh,
        )

    def events(self, query: FinOpsQuery) -> list[Any]:
        # Internal Pydantic objects are never serialized into the query cache.
        return self._delegate.events(query)

    def unit_economics_trend(
        self,
        query: FinOpsQuery,
        bucket: str = "day",
        *,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        return self._cached(
            "unit_economics_trend",
            query,
            lambda: self._delegate.unit_economics_trend(query, bucket),
            extras={"bucket": bucket},
            force_refresh=force_refresh,
        ) or {"items": [], "count": 0}

    def compose(
        self,
        operation: Literal["roi_decision", "risk_decision"],
        query: FinOpsQuery,
        compute: Callable[[], dict[str, Any]],
        *,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        if operation not in {"roi_decision", "risk_decision"}:
            raise ValueError("unsupported FinOps decision cache operation")
        return self._cached(
            operation,
            query,
            compute,
            force_refresh=force_refresh,
        )

    def _cached(
        self,
        operation: str,
        query: FinOpsQuery,
        compute: Callable[[], dict[str, Any] | None],
        *,
        extras: dict[str, Any] | None = None,
        force_refresh: bool = False,
    ) -> dict[str, Any] | None:
        domains = _operation_domains(operation)
        workspace_ids = (
            (query.workspace_id,)
            if query.workspace_id
            else query.authorized_workspace_ids
        )
        namespace_revision = self._namespace.current(
            query.tenant_ref,
            workspace_ids,
            domains,
        )
        key = _cache_key(operation, query, extras or {}, namespace_revision)
        cached, metadata = self._cache.get_json(key)
        status = str(metadata.get("status") or "").strip().lower()
        if status in {"unavailable", "unconfigured"}:
            return _compute_without_cache(compute, status="unavailable")

        now = _utc(self._clock())
        cached_payload, state = _read_envelope(cached, now)
        if not force_refresh:
            if state == "fresh":
                return _with_cache_status(cached_payload, "hit_fresh", cached)
            if state == "stale":
                return _with_cache_status(cached_payload, "hit_stale", cached)

        lock_key = f"{key}:refresh-lock"
        token = secrets.token_urlsafe(24)
        acquired, lock_meta = self._cache.acquire_lock(
            lock_key,
            token,
            ttl_seconds=LOCK_SECONDS,
        )
        lock_status = str(lock_meta.get("status") or "").strip().lower()
        if lock_status in {"unavailable", "unconfigured"}:
            return _compute_without_cache(compute, status="unavailable")
        if not acquired:
            if state in {"fresh", "stale"}:
                return _with_cache_status(
                    cached_payload,
                    (
                        "revalidating"
                        if force_refresh
                        else f"hit_{state}"
                    ),
                    cached,
                )
            return self._wait_for_owner(key, compute)
        try:
            return self._compute_and_store(
                key,
                compute,
                now=now,
                response_status=("revalidated" if force_refresh else "miss"),
            )
        finally:
            self._cache.release_lock(lock_key, token)

    def _wait_for_owner(
        self,
        key: str,
        compute: Callable[[], dict[str, Any] | None],
    ) -> dict[str, Any] | None:
        deadline = self._monotonic() + self._wait_timeout_seconds
        while True:
            cached, metadata = self._cache.get_json(key)
            status = str(metadata.get("status") or "").strip().lower()
            if status in {"unavailable", "unconfigured"}:
                return _compute_without_cache(compute, status="unavailable")
            payload, state = _read_envelope(cached, _utc(self._clock()))
            if state in {"fresh", "stale"}:
                return _with_cache_status(payload, f"hit_{state}", cached)
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise FinOpsCacheBusy()
            self._sleep(min(self._wait_poll_seconds, remaining))

    def _compute_and_store(
        self,
        key: str,
        compute: Callable[[], dict[str, Any] | None],
        *,
        now: datetime,
        response_status: str,
    ) -> dict[str, Any] | None:
        value = compute()
        if value is None:
            return None
        envelope = _envelope(value, now)
        stored = self._cache.set_json(
            key,
            envelope,
            ttl_seconds=STALE_SECONDS,
        )
        stored_status = str(stored.get("status") or "").strip().lower()
        status = (
            "unavailable"
            if stored_status in {"unavailable", "unconfigured"}
            else response_status
        )
        return _with_cache_status(value, status, envelope)


def _operation_domains(operation: str) -> tuple[str, ...]:
    if operation == "roi_decision":
        return ("roi",)
    if operation == "risk_decision":
        return ("risk",)
    if operation in {"requests", "request_detail"}:
        return ("requests",)
    if operation in {"unit_economics_trend"}:
        return ("cost",)
    if operation == "filters":
        return ("settings",)
    return ("overview",)


def _cache_key(
    operation: str,
    query: FinOpsQuery,
    extras: dict[str, Any],
    namespace_revision: str,
) -> str:
    payload = {
        "operation": operation,
        "query": query.model_dump(mode="json"),
        "extras": extras,
        "namespace_revision": namespace_revision,
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return f"finops:query:{QUERY_CACHE_SCHEMA_VERSION}:{digest}"


def _envelope(payload: dict[str, Any], now: datetime) -> dict[str, Any]:
    generated_at = _iso(now)
    return {
        "payload": deepcopy(payload),
        "_cache": {
            "generated_at": generated_at,
            "fresh_until": _iso(now + timedelta(seconds=FRESH_SECONDS)),
            "stale_until": _iso(now + timedelta(seconds=STALE_SECONDS)),
        },
    }


def _read_envelope(
    cached: dict[str, Any] | None,
    now: datetime,
) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(cached, dict):
        return None, "miss"
    payload = cached.get("payload")
    metadata = cached.get("_cache")
    if not isinstance(payload, dict) or not isinstance(metadata, dict):
        return None, "miss"
    try:
        fresh_until = _parse_time(metadata.get("fresh_until"))
        stale_until = _parse_time(metadata.get("stale_until"))
    except (TypeError, ValueError):
        return None, "miss"
    if now <= fresh_until:
        return payload, "fresh"
    if now <= stale_until:
        return payload, "stale"
    return None, "expired"


def _compute_without_cache(
    compute: Callable[[], dict[str, Any] | None],
    *,
    status: str,
) -> dict[str, Any] | None:
    value = compute()
    if value is None:
        return None
    return _with_cache_status(value, status, None)


def _with_cache_status(
    payload: dict[str, Any] | None,
    status: str,
    envelope: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if payload is None:
        return None
    result = deepcopy(payload)
    freshness = result.get("freshness")
    if not isinstance(freshness, dict):
        freshness = {}
        result["freshness"] = freshness
    query_cache = {"provider": "redis", "status": status}
    cache_metadata = envelope.get("_cache") if isinstance(envelope, dict) else None
    if isinstance(cache_metadata, dict):
        query_cache.update(
            {
                key: cache_metadata[key]
                for key in ("generated_at", "fresh_until", "stale_until")
                if key in cache_metadata
            }
        )
    freshness["query_cache"] = query_cache
    return result


def _parse_time(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return _utc(parsed)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")
