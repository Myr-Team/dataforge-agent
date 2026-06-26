from __future__ import annotations

import json
import os
import time
from typing import Any


DEFAULT_TTL_SECONDS = int(os.environ.get("DF_REDIS_CACHE_TTL_SECONDS", "3600"))


def get_json(key: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    client, meta = _client()
    if client is None:
        return None, meta
    started = time.monotonic()
    try:
        raw = client.get(key)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if not raw:
            return None, meta | {"status": "miss", "elapsed_ms": elapsed_ms}
        data = json.loads(raw)
        if isinstance(data, dict):
            return data, meta | {"status": "hit", "elapsed_ms": elapsed_ms}
        return None, meta | {"status": "miss", "elapsed_ms": elapsed_ms, "error": "cached value is not an object"}
    except Exception as exc:
        return None, meta | {"status": "unavailable", "elapsed_ms": int((time.monotonic() - started) * 1000), "error": _err(exc)}


def set_json(key: str, value: dict[str, Any], *, ttl_seconds: int | None = None) -> dict[str, Any]:
    client, meta = _client()
    if client is None:
        return meta
    started = time.monotonic()
    try:
        client.setex(key, int(ttl_seconds or DEFAULT_TTL_SECONDS), json.dumps(value, ensure_ascii=False))
        return meta | {"status": "stored", "elapsed_ms": int((time.monotonic() - started) * 1000)}
    except Exception as exc:
        return meta | {"status": "unavailable", "elapsed_ms": int((time.monotonic() - started) * 1000), "error": _err(exc)}


def delete_matching(pattern: str, *, scan_count: int = 500) -> dict[str, Any]:
    client, meta = _client()
    if client is None:
        return meta | {"pattern": pattern, "deleted": 0}
    started = time.monotonic()
    deleted = 0
    scanned = 0
    try:
        cursor: int | str = 0
        while True:
            cursor, keys = client.scan(cursor=cursor, match=pattern, count=scan_count)
            keys = list(keys or [])
            scanned += len(keys)
            if keys:
                deleted += int(client.delete(*keys) or 0)
            if str(cursor) == "0":
                break
        return meta | {
            "status": "deleted",
            "pattern": pattern,
            "scanned": scanned,
            "deleted": deleted,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
    except Exception as exc:
        return meta | {
            "status": "unavailable",
            "pattern": pattern,
            "scanned": scanned,
            "deleted": deleted,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "error": _err(exc),
        }


def probe() -> dict[str, Any]:
    client, meta = _client()
    if client is None:
        return meta
    started = time.monotonic()
    try:
        client.ping()
        return meta | {"status": "ok", "elapsed_ms": int((time.monotonic() - started) * 1000)}
    except Exception as exc:
        return meta | {"status": "unavailable", "elapsed_ms": int((time.monotonic() - started) * 1000), "error": _err(exc)}


def _client() -> tuple[Any | None, dict[str, Any]]:
    url = os.environ.get("DF_REDIS_URL") or os.environ.get("REDIS_URL") or os.environ.get("AZURE_REDIS_URL")
    if not url:
        return None, {"provider": "redis", "status": "unconfigured"}
    try:
        import redis
    except Exception as exc:
        return None, {"provider": "redis", "status": "unavailable", "error": f"redis-py unavailable: {_err(exc)}"}
    try:
        client = redis.Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=float(os.environ.get("DF_REDIS_CONNECT_TIMEOUT_SECONDS", "3.0")),
            socket_timeout=float(os.environ.get("DF_REDIS_SOCKET_TIMEOUT_SECONDS", "3.0")),
        )
        return client, {"provider": "redis", "status": "configured"}
    except Exception as exc:
        return None, {"provider": "redis", "status": "unavailable", "error": _err(exc)}


def _err(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"[:400]
