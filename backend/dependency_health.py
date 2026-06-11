from __future__ import annotations

import os
import threading
import time
import urllib.request
from typing import Any

try:
    from .blob_store import blob_configured
except ImportError:
    from blob_store import blob_configured


_CACHE: dict[str, Any] = {"expires_at": 0.0, "value": {}, "refreshing": False}
_LOCK = threading.RLock()


def health_dependencies() -> dict[str, bool]:
    now = time.monotonic()
    with _LOCK:
        value = dict(_CACHE.get("value") or {})
        if value and now < float(_CACHE.get("expires_at") or 0):
            return value
        if not value:
            value = _configured_dependencies(mcp=False)
            _CACHE["value"] = value
            _CACHE["expires_at"] = now + 5
        if not _CACHE.get("refreshing"):
            _CACHE["refreshing"] = True
            threading.Thread(target=_refresh_dependencies, name="dataforge-health-refresh", daemon=True).start()
        return value


def _refresh_dependencies() -> None:
    try:
        deps = _configured_dependencies(mcp=_mcp_reachable())
        with _LOCK:
            _CACHE["value"] = deps
            _CACHE["expires_at"] = time.monotonic() + 30
    finally:
        with _LOCK:
            _CACHE["refreshing"] = False


def _configured_dependencies(*, mcp: bool) -> dict[str, bool]:
    return {
        "foundry": _foundry_configured(),
        "mcp": mcp,
        "speech": _speech_configured(),
        "blob": _blob_configured(),
    }


def _foundry_configured() -> bool:
    if not os.environ.get("FOUNDRY_PROJECT_ENDPOINT"):
        return False
    if os.environ.get("AZURE_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY"):
        return True
    return bool(
        os.environ.get("AZURE_CLIENT_ID")
        and os.environ.get("AZURE_CLIENT_SECRET")
        and os.environ.get("AZURE_TENANT_ID")
    )


def _speech_configured() -> bool:
    if os.environ.get("SPEECH_KEY"):
        return True
    if not (os.environ.get("SPEECH_REGION") or os.environ.get("DF_REGION")):
        return False
    return bool(
        os.environ.get("AZURE_CLIENT_ID")
        and os.environ.get("AZURE_CLIENT_SECRET")
        and os.environ.get("AZURE_TENANT_ID")
    )


def _blob_configured() -> bool:
    if not blob_configured():
        return False
    if os.environ.get("AZURE_STORAGE_CONNECTION_STRING") or os.environ.get("AZURE_STORAGE_KEY") or os.environ.get("DF_STORAGE_KEY"):
        return True
    return bool(
        os.environ.get("DF_STORAGE_ACCOUNT")
        and os.environ.get("AZURE_CLIENT_ID")
        and os.environ.get("AZURE_CLIENT_SECRET")
        and os.environ.get("AZURE_TENANT_ID")
    )


def _mcp_reachable() -> bool:
    base = os.environ.get("MCP_MARKET_URL", "https://ca-dataforge-mcp.thankfultree-c0fc8321.eastus2.azurecontainerapps.io")
    url = base.rstrip("/")
    if url.endswith("/mcp"):
        url = url.removesuffix("/mcp")
    for suffix in ("/health", "/"):
        try:
            req = urllib.request.Request(url + suffix, method="GET")
            with urllib.request.urlopen(req, timeout=0.35) as resp:
                return 200 <= int(resp.status) < 500
        except Exception:
            continue
    return False
