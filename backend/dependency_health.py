from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import socket
import time
import urllib.error
import urllib.request
from collections import deque
from typing import Any

from azure.identity import DefaultAzureCredential
try:
    from azure.keyvault.secrets import SecretClient as KeyVaultSecretClient
except ImportError:
    KeyVaultSecretClient = None  # type: ignore[assignment]

try:
    from .blob_store import probe_blob_container
    from .foundry_roi import discover_foundry_roi
    from .search_admin import API_VERSION, search_endpoint, search_index_name
except ImportError:
    from blob_store import probe_blob_container
    from foundry_roi import discover_foundry_roi
    from search_admin import API_VERSION, search_endpoint, search_index_name


_CACHE: dict[str, Any] = {"expires_at": 0.0, "fingerprint": "", "dependencies": {}, "details": {}}
_LAST_OK: dict[str, dict[str, Any]] = {}
_PROBE_HISTORY: deque[dict[str, Any]] = deque(maxlen=int(os.environ.get("DF_HEALTH_HISTORY_SIZE", "240")))
_CACHE_TTL_SECONDS = float(os.environ.get("DF_HEALTH_CACHE_SECONDS", "90"))
# 列 Foundry 全部模型(用户有数百个)偶尔 >5s 会让健康状态闪烁成灰；默认放宽到 8s
_PROBE_TIMEOUT_SECONDS = float(os.environ.get("DF_HEALTH_PROBE_TIMEOUT_SECONDS", "8.0"))
_MCP_PROBE_TIMEOUT_SECONDS = float(os.environ.get("DF_MCP_HEALTH_TIMEOUT_SECONDS", "2.0"))
_FOUNDRY_STALE_OK_SECONDS = float(os.environ.get("DF_FOUNDRY_HEALTH_STALE_OK_SECONDS", "1800"))
_FOUNDRY_RETRY_DELAY_SECONDS = float(os.environ.get("DF_FOUNDRY_HEALTH_RETRY_DELAY_SECONDS", "0.35"))
_FOUNDRY_SAMPLE_BYTES = int(os.environ.get("DF_FOUNDRY_HEALTH_SAMPLE_BYTES", "256"))


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
        "foundry": (_probe_foundry, True),
        "foundry_roi": (_probe_foundry_roi, False),
        "search": (_probe_search, True),
        "mcp": (_probe_mcp, True),
        "speech": (_probe_speech, True),
        "blob": (_probe_blob, True),
        "content_safety": (_probe_content_safety, True),
        "key_vault": (_probe_key_vault, True),
    }
    results: dict[str, dict[str, Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(probes), thread_name_prefix="dataforge-health") as pool:
        futures = {
            name: pool.submit(_timed_probe, name, func, required=required)
            for name, (func, required) in probes.items()
        }
        for name, future in futures.items():
            try:
                results[name] = future.result(timeout=_probe_future_timeout(name))
            except Exception as exc:
                _, required = probes[name]
                detail = {
                    "ok": False,
                    "state": "down",
                    "required": required,
                    "error_type": "timeout",
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                    "latency_ms": int(_probe_future_timeout(name) * 1000),
                }
                if name == "foundry":
                    detail = _stale_ok_if_recent(name, detail)
                _record_probe(name, detail)
                results[name] = detail
    return results


def _probe_future_timeout(name: str) -> float:
    if name == "foundry":
        return (_PROBE_TIMEOUT_SECONDS * 2) + _FOUNDRY_RETRY_DELAY_SECONDS + 1.0
    return _PROBE_TIMEOUT_SECONDS + 0.4


def _timed_probe(name: str, func: Any, *, required: bool) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        detail = func()
    except Exception as exc:
        detail = {"ok": False, "state": "down", **_classify_probe_error(exc)}
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    if not isinstance(detail, dict):
        detail = {"ok": False, "state": "down", "error_type": "invalid_result"}
    detail.setdefault("required", required)
    detail.setdefault("latency_ms", elapsed_ms)
    detail.setdefault("error_type", "none" if detail.get("ok") else "unknown")
    detail.setdefault("observed_at", _now_iso())
    if detail.get("ok") and detail.get("state") == "ok":
        _LAST_OK[name] = {"at": time.time(), "detail": {key: value for key, value in detail.items() if key != "latency_ms"}}
    _record_probe(name, detail)
    return detail


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
    attempts: list[dict[str, Any]] = []
    for attempt in range(2):
        started = time.perf_counter()
        try:
            status, sample_len = _request_status_sample(
                url,
                method="GET",
                headers=headers,
                timeout=_PROBE_TIMEOUT_SECONDS,
                max_bytes=_FOUNDRY_SAMPLE_BYTES,
            )
            return {
                "ok": True,
                "state": "ok",
                "endpoint": _redact_endpoint(endpoint),
                "probe": "models_status_sample",
                "status": status,
                "sample_bytes": sample_len,
                "attempts": attempt + 1,
                "error_type": "none",
            }
        except Exception as exc:
            info = _classify_probe_error(exc)
            info["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
            info["attempt"] = attempt + 1
            attempts.append(info)
            if not info.get("transient") or attempt >= 1:
                break
            time.sleep(_FOUNDRY_RETRY_DELAY_SECONDS)
    last = attempts[-1] if attempts else {"error_type": "unknown", "error": "Foundry probe failed"}
    detail = {
        "ok": False,
        "state": "down",
        "endpoint": _redact_endpoint(endpoint),
        "probe": "models_status_sample",
        "attempts": len(attempts),
        "error_type": last.get("error_type"),
        "status": last.get("status"),
        "error": str(last.get("error") or "")[:500],
        "attempt_details": attempts,
    }
    return _stale_ok_if_recent("foundry", detail)


def _probe_foundry_roi() -> dict[str, Any]:
    status = discover_foundry_roi()
    return {
        "ok": status.state == "connected",
        "state": status.state,
        "configured": status.configured,
        "source": status.source,
        "provider_version": status.provider_version,
        "reason": status.reason,
    }


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


def _stale_ok_if_recent(name: str, detail: dict[str, Any]) -> dict[str, Any]:
    if not _is_transient_error_type(str(detail.get("error_type") or "")):
        return detail
    last = _LAST_OK.get(name) or {}
    last_at = float(last.get("at") or 0)
    age = time.time() - last_at if last_at else None
    if age is None or age > _FOUNDRY_STALE_OK_SECONDS:
        return detail
    degraded = dict(detail)
    degraded["ok"] = True
    degraded["state"] = "degraded"
    degraded["last_ok_age_seconds"] = round(age, 1)
    degraded["degraded_reason"] = "transient_probe_failure_recent_success"
    return degraded


def _is_transient_error_type(value: str) -> bool:
    return value in {"timeout", "rate_limited", "transient_http", "connection_error"}


def _classify_probe_error(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, urllib.error.HTTPError):
        status = int(getattr(exc, "code", 0) or 0)
        if status == 429:
            error_type, transient = "rate_limited", True
        elif status in {408, 409, 425} or 500 <= status <= 599:
            error_type, transient = "transient_http", True
        elif status in {401, 403}:
            error_type, transient = "auth_error", False
        else:
            error_type, transient = "http_error", False
        return {"error_type": error_type, "status": status, "transient": transient, "error": _read_http_error(exc)}
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return {"error_type": "timeout", "transient": True, "error": f"{type(exc).__name__}: {exc}"[:500]}
    if isinstance(exc, urllib.error.URLError):
        reason = str(getattr(exc, "reason", exc))
        lowered = reason.lower()
        error_type = "timeout" if "timed out" in lowered or "timeout" in lowered else "connection_error"
        return {"error_type": error_type, "transient": True, "error": f"{type(exc).__name__}: {reason}"[:500]}
    return {"error_type": "unknown", "transient": False, "error": f"{type(exc).__name__}: {exc}"[:500]}


def _record_probe(name: str, detail: dict[str, Any]) -> None:
    entry = {
        "type": "dataforge_health_probe",
        "dependency": name,
        "ok": bool(detail.get("ok")),
        "state": detail.get("state"),
        "latency_ms": detail.get("latency_ms"),
        "error_type": detail.get("error_type"),
        "status": detail.get("status"),
        "attempts": detail.get("attempts"),
        "observed_at": detail.get("observed_at") or _now_iso(),
    }
    _PROBE_HISTORY.append(entry)
    if os.environ.get("DF_HEALTH_PROBE_LOG", "1") != "0":
        print(json.dumps(entry, ensure_ascii=False, sort_keys=True), flush=True)


def _probe_blob() -> dict[str, Any]:
    return probe_blob_container(timeout=_PROBE_TIMEOUT_SECONDS)


def _probe_content_safety() -> dict[str, Any]:
    try:
        try:
            from . import content_safety
        except ImportError:
            import content_safety
        return content_safety.health()
    except Exception as exc:
        return {"ok": False, "state": "down", "error": f"{type(exc).__name__}: {exc}"[:300]}


def _probe_key_vault() -> dict[str, Any]:
    vault_url = str(os.environ.get("DF_KEY_VAULT_URL") or "").strip()
    if not vault_url:
        return {"ok": True, "state": "unconfigured", "persistence": "session_only"}
    try:
        try:
            from .connector_secret_store import validate_key_vault_url
        except ImportError:
            from connector_secret_store import validate_key_vault_url
        if KeyVaultSecretClient is None:
            raise RuntimeError("azure-keyvault-secrets is unavailable")
        credential = DefaultAzureCredential()
        credential.get_token("https://vault.azure.net/.default")
        endpoint = validate_key_vault_url(vault_url)
        KeyVaultSecretClient(vault_url=endpoint, credential=credential)
        return {"ok": False, "state": "configured_unverified", "persistence": "key_vault", "endpoint": _redact_endpoint(endpoint)}
    except Exception as exc:
        return {"ok": False, "state": "degraded", "persistence": "key_vault", "error_type": type(exc).__name__}


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


def _request_status_sample(
    url: str,
    *,
    method: str,
    headers: dict[str, str],
    timeout: float,
    max_bytes: int,
) -> tuple[int, int]:
    req = urllib.request.Request(url, method=method)
    req.add_header("User-Agent", "DataForge-health/1.0")
    for name, value in headers.items():
        req.add_header(name, value)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        sample = resp.read(max(0, max_bytes))
        return int(resp.status), len(sample or b"")


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
        "FOUNDRY_PROJECT_ENDPOINT",
        "FOUNDRY_AGENT_ID",
        "AZURE_OPENAI_API_KEY",
        "OPENAI_API_KEY",
        "SPEECH_KEY",
        "SPEECH_REGION",
        "DF_REGION",
        "AZURE_STORAGE_CONNECTION_STRING",
        "AZURE_STORAGE_KEY",
        "DF_STORAGE_KEY",
        "STORAGE_ACCOUNT_NAME",
        "DF_STORAGE_ACCOUNT",
        "DF_WORKSPACE_CONTAINER",
        "DF_KEY_VAULT_URL",
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


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
