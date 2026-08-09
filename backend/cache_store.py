from __future__ import annotations

import json
import os
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit


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


def delete(key: str) -> dict[str, Any]:
    client, meta = _client()
    if client is None:
        return meta | {"deleted": 0}
    started = time.monotonic()
    try:
        deleted = int(client.delete(key) or 0)
        return meta | {
            "status": "deleted",
            "deleted": deleted,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
    except Exception as exc:
        return meta | {
            "status": "unavailable",
            "deleted": 0,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "error": _err(exc),
        }


def get_int(key: str) -> tuple[int | None, dict[str, Any]]:
    client, meta = _client()
    if client is None:
        return None, meta
    started = time.monotonic()
    try:
        raw = client.get(key)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if raw is None:
            return None, meta | {"status": "miss", "elapsed_ms": elapsed_ms}
        return int(raw), meta | {"status": "hit", "elapsed_ms": elapsed_ms}
    except Exception as exc:
        return None, meta | {
            "status": "unavailable",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "error": _err(exc),
        }


def increment(
    key: str,
    *,
    ttl_seconds: int = 86400 * 30,
) -> tuple[int | None, dict[str, Any]]:
    client, meta = _client()
    if client is None:
        return None, meta
    started = time.monotonic()
    try:
        value = client.eval(
            "local value = redis.call('incr', KEYS[1]); "
            "redis.call('expire', KEYS[1], ARGV[1]); return value",
            1,
            key,
            max(1, int(ttl_seconds)),
        )
        return int(value), meta | {
            "status": "incremented",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
    except Exception as exc:
        return None, meta | {
            "status": "unavailable",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "error": _err(exc),
        }


def acquire_lock(
    key: str,
    token: str,
    *,
    ttl_seconds: int = 30,
) -> tuple[bool, dict[str, Any]]:
    client, meta = _client()
    if client is None:
        return False, meta
    started = time.monotonic()
    try:
        acquired = bool(
            client.set(
                key,
                token,
                nx=True,
                ex=max(1, int(ttl_seconds)),
            )
        )
        return acquired, meta | {
            "status": "acquired" if acquired else "busy",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
    except Exception as exc:
        return False, meta | {
            "status": "unavailable",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "error": _err(exc),
        }


def release_lock(key: str, token: str) -> tuple[bool, dict[str, Any]]:
    client, meta = _client()
    if client is None:
        return False, meta
    started = time.monotonic()
    script = (
        "if redis.call('get', KEYS[1]) == ARGV[1] then "
        "return redis.call('del', KEYS[1]) else return 0 end"
    )
    try:
        released = bool(client.eval(script, 1, key, token))
        return released, meta | {
            "status": "released" if released else "not_owner",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
    except Exception as exc:
        return False, meta | {
            "status": "unavailable",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "error": _err(exc),
        }


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
    url = _redis_connection_url()
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


def _redis_connection_url() -> str | None:
    url = os.environ.get("DF_REDIS_URL") or os.environ.get("REDIS_URL") or os.environ.get("AZURE_REDIS_URL")
    override = str(os.environ.get("DF_REDIS_HOST_OVERRIDE") or "").strip()
    if not url or not override:
        return url
    if "://" in override or any(character in override for character in "/?#@"):
        return url
    try:
        parsed = urlsplit(url)
        auth, separator, _host_port = parsed.netloc.rpartition("@")
        override_host_port = override
        if ":" not in override and parsed.port is not None:
            override_host_port = f"{override}:{parsed.port}"
        netloc = f"{auth}{separator}{override_host_port}" if separator else override_host_port
        return urlunsplit(parsed._replace(netloc=netloc))
    except (TypeError, ValueError):
        return url


def _err(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"[:400]
