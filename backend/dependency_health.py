from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from azure.identity import DefaultAzureCredential

try:
    from .blob_store import probe_blob_container
    from .search_admin import API_VERSION, search_endpoint, search_index_name
except ImportError:
    from blob_store import probe_blob_container
    from search_admin import API_VERSION, search_endpoint, search_index_name


_CACHE: dict[str, Any] = {"expires_at": 0.0, "fingerprint": "", "dependencies": {}, "details": {}}
_CACHE_TTL_SECONDS = float(os.environ.get("DF_HEALTH_CACHE_SECONDS", "45"))
_PROBE_TIMEOUT_SECONDS = float(os.environ.get("DF_HEALTH_PROBE_TIMEOUT_SECONDS", "5.0"))
_MCP_PROBE_TIMEOUT_SECONDS = float(os.environ.get("DF_MCP_HEALTH_TIMEOUT_SECONDS", "2.0"))


def health_dependencies() -> dict[str, bool]:
    status = dependency_status()
    return dict(status["dependencies"])


def health_dependency_details() -> dict[str, dict[str, Any]]:
    status = dependency_status()
    return dict(status["details"])


def dependency_status() -> dict[str, Any]:
    now = time.monotonic()
    fingerprint = _env_fingerprint()
    cached = _CACHE.get("dependencies")
    if cached and _CACHE.get("fingerprint") == fingerprint and now < float(_CACHE.get("expires_at") or 0):
        return {"dependencies": dict(_CACHE["dependencies"]), "details": dict(_CACHE["details"])}

    details = _probe_all()
    dependencies = {name: bool(detail.get("ok")) for name, detail in details.items()}
    _CACHE.update(
        {
            "expires_at": now + _CACHE_TTL_SECONDS,
            "fingerprint": fingerprint,
            "dependencies": dependencies,
            "details": details,
        }
    )
    return {"dependencies": dependencies, "details": details}


def _probe_all() -> dict[str, dict[str, Any]]:
    probes = {
        "foundry": _probe_foundry,
        "search": _probe_search,
        "mcp": _probe_mcp,
        "speech": _probe_speech,
        "blob": _probe_blob,
    }
    results: dict[str, dict[str, Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(probes), thread_name_prefix="dataforge-health") as pool:
        futures = {name: pool.submit(func) for name, func in probes.items()}
        for name, future in futures.items():
            try:
                results[name] = future.result(timeout=_PROBE_TIMEOUT_SECONDS + 0.4)
            except Exception as exc:
                results[name] = {"ok": False, "state": "down", "error": f"{type(exc).__name__}: {exc}"[:500]}
    return results


def _probe_foundry() -> dict[str, Any]:
    endpoint = (
        os.environ.get("OPENAI_ENDPOINT")
        or os.environ.get("AZURE_OPENAI_ENDPOINT")
        or os.environ.get("AZURE_OPENAI_API_BASE")
    )
    if not endpoint:
        return {"ok": False, "state": "unconfigured", "error": "OPENAI_ENDPOINT is not configured"}
    url = endpoint.rstrip("/") + "/openai/models?api-version=" + os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21")
    headers = _azure_ai_headers()
    if not headers:
        return {"ok": False, "state": "unconfigured", "error": "no Azure OpenAI credential configured"}
    try:
        payload = _request_json(url, method="GET", headers=headers, timeout=_PROBE_TIMEOUT_SECONDS)
        models = payload.get("data") if isinstance(payload, dict) else None
        count = len(models) if isinstance(models, list) else None
        return {"ok": True, "state": "ok", "endpoint": _redact_endpoint(endpoint), "model_count": count}
    except Exception as exc:
        return {"ok": False, "state": "down", "endpoint": _redact_endpoint(endpoint), "error": f"{type(exc).__name__}: {exc}"[:500]}


def _probe_speech() -> dict[str, Any]:
    region = os.environ.get("SPEECH_REGION") or os.environ.get("DF_REGION")
    key = os.environ.get("SPEECH_KEY")
    if key and region:
        url = f"https://{region}.api.cognitive.microsoft.com/sts/v1.0/issueToken"
        try:
            req = urllib.request.Request(url, data=b"", method="POST")
            req.add_header("Ocp-Apim-Subscription-Key", key)
            req.add_header("Content-Length", "0")
            with urllib.request.urlopen(req, timeout=_PROBE_TIMEOUT_SECONDS) as resp:
                token = resp.read(64)
                ok = 200 <= int(resp.status) < 300 and bool(token)
            return {"ok": ok, "state": "ok" if ok else "down", "region": region, "probe": "token_endpoint"}
        except Exception as exc:
            return {"ok": False, "state": "down", "region": region, "probe": "token_endpoint", "error": f"{type(exc).__name__}: {exc}"[:500]}
    if not region:
        return {"ok": False, "state": "unconfigured", "error": "SPEECH_REGION or DF_REGION is not configured"}
    if not _has_azure_identity_config():
        return {"ok": False, "state": "unconfigured", "region": region, "error": "no speech credential configured"}
    try:
        DefaultAzureCredential().get_token("https://cognitiveservices.azure.com/.default")
        return {"ok": True, "state": "ok", "region": region, "probe": "aad_token"}
    except Exception as exc:
        return {"ok": False, "state": "down", "region": region, "probe": "aad_token", "error": f"{type(exc).__name__}: {exc}"[:500]}


def _probe_blob() -> dict[str, Any]:
    return probe_blob_container(timeout=_PROBE_TIMEOUT_SECONDS)


def _probe_search() -> dict[str, Any]:
    endpoint = search_endpoint()
    index = search_index_name()
    if not endpoint:
        return {"ok": False, "state": "unconfigured", "error": "SEARCH_ENDPOINT or DF_SEARCH_SERVICE is not configured"}
    headers = _search_headers()
    if not headers:
        return {"ok": False, "state": "unconfigured", "endpoint": _redact_endpoint(endpoint), "index": index, "error": "no AI Search credential configured"}
    url = f"{endpoint.rstrip('/')}/indexes/{index}?api-version={API_VERSION}"
    try:
        payload = _request_json(url, method="GET", headers=headers, timeout=_PROBE_TIMEOUT_SECONDS)
        fields = payload.get("fields") if isinstance(payload, dict) else None
        return {
            "ok": True,
            "state": "ok",
            "endpoint": _redact_endpoint(endpoint),
            "index": index,
            "field_count": len(fields) if isinstance(fields, list) else None,
        }
    except urllib.error.HTTPError as exc:
        state = "missing_index" if exc.code == 404 else "down"
        return {"ok": False, "state": state, "endpoint": _redact_endpoint(endpoint), "index": index, "status": exc.code, "error": _read_http_error(exc)}
    except Exception as exc:
        return {"ok": False, "state": "down", "endpoint": _redact_endpoint(endpoint), "index": index, "error": f"{type(exc).__name__}: {exc}"[:500]}


def _probe_mcp() -> dict[str, Any]:
    base = os.environ.get("MCP_MARKET_URL", "https://ca-dataforge-mcp.thankfultree-c0fc8321.eastus2.azurecontainerapps.io")
    url = base.rstrip("/")
    if url.endswith("/mcp"):
        url = url.removesuffix("/mcp")
    errors: list[str] = []
    for suffix in ("/health", "/"):
        try:
            req = urllib.request.Request(url + suffix, method="GET")
            with urllib.request.urlopen(req, timeout=_MCP_PROBE_TIMEOUT_SECONDS) as resp:
                reachable = 200 <= int(resp.status) < 500
                return {"ok": reachable, "state": "ok" if reachable else "down", "endpoint": url, "status": int(resp.status)}
        except Exception as exc:
            errors.append(f"{suffix}: {type(exc).__name__}")
    return {"ok": False, "state": "down", "endpoint": url, "error": "; ".join(errors)[:500]}


def _azure_ai_headers() -> dict[str, str] | None:
    key = os.environ.get("AZURE_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if key:
        return {"api-key": key}
    if not _has_azure_identity_config():
        return None
    try:
        token = DefaultAzureCredential().get_token("https://cognitiveservices.azure.com/.default").token
        return {"Authorization": f"Bearer {token}"}
    except Exception:
        return None


def _search_headers() -> dict[str, str] | None:
    key = os.environ.get("SEARCH_KEY") or os.environ.get("AZURE_SEARCH_API_KEY")
    if key:
        return {"api-key": key, "Content-Type": "application/json"}
    if not _has_azure_identity_config():
        return None
    try:
        token = DefaultAzureCredential().get_token("https://search.azure.com/.default").token
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    except Exception:
        return None


def _request_json(url: str, *, method: str, headers: dict[str, str], timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(url, method=method)
    for name, value in headers.items():
        req.add_header(name, value)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def _has_azure_identity_config() -> bool:
    return bool(
        os.environ.get("AZURE_CLIENT_ID")
        and os.environ.get("AZURE_CLIENT_SECRET")
        and os.environ.get("AZURE_TENANT_ID")
    )


def _env_fingerprint() -> str:
    keys = [
        "SEARCH_ENDPOINT",
        "SEARCH_INDEX_NAME",
        "SEARCH_KEY",
        "AZURE_SEARCH_API_KEY",
        "DF_SEARCH_SERVICE",
        "OPENAI_ENDPOINT",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_API_BASE",
        "AZURE_OPENAI_API_KEY",
        "OPENAI_API_KEY",
        "SPEECH_KEY",
        "SPEECH_REGION",
        "DF_REGION",
        "AZURE_STORAGE_CONNECTION_STRING",
        "AZURE_STORAGE_KEY",
        "DF_STORAGE_KEY",
        "DF_STORAGE_ACCOUNT",
        "DF_WORKSPACE_CONTAINER",
        "MCP_MARKET_URL",
        "AZURE_CLIENT_ID",
        "AZURE_CLIENT_SECRET",
        "AZURE_TENANT_ID",
    ]
    material = "\n".join(f"{key}={os.environ.get(key, '')}" for key in keys)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _read_http_error(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read().decode("utf-8", errors="replace")[:500]
    except Exception:
        return f"HTTP {exc.code}"


def _redact_endpoint(endpoint: str) -> str:
    return endpoint.rstrip("/")
