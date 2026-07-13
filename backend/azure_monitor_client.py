"""Bounded Azure Monitor delivery confirmation for DataForge traces."""
from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from urllib.parse import quote

from pydantic import BaseModel


_HASH = re.compile(r"^[0-9a-f]{64}$")
_UUID = re.compile(r"^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$", re.IGNORECASE)
_CORRELATION = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)
_RESOURCE_ID = re.compile(
    r"^/subscriptions/[0-9a-f-]{36}/resourcegroups/[a-z0-9._()-]+/providers/microsoft\.insights/components/[a-z0-9._-]+$",
    re.IGNORECASE,
)
_QUERY_WINDOW = timedelta(minutes=15)
_QUERY_TIMEOUT_SECONDS = 10
_CACHE_TTL_SECONDS = 30.0
_CACHE_LOCK = threading.RLock()
_CONFIRMED_CACHE: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}


class TraceDeliveryStatus(BaseModel):
    state: Literal["connected", "partial", "not_configured", "unavailable"]
    local_emit_at: datetime | None = None
    local_exporter_callback_at: datetime | None = None
    last_export_confirmed_at: datetime | None = None
    correlation_id: str | None = None
    transaction_url: str | None = None
    exporter_state: Literal["unknown", "succeeded", "failed"] = "unknown"
    error_type: str | None = None


class TraceDeliveryQueryError(RuntimeError):
    def __init__(self, error_type: str) -> None:
        self.error_type = _safe_error_type(error_type)
        super().__init__(f"Azure Monitor delivery query unavailable: {self.error_type}")


class _MonitorConfig(BaseModel):
    logs_workspace_id: str
    application_id: str
    resource_id: str


def hash_trace_identifier(value: str) -> str:
    return hashlib.sha256(str(value).strip().encode("utf-8")).hexdigest()


def clear_trace_delivery_cache() -> None:
    with _CACHE_LOCK:
        _CONFIRMED_CACHE.clear()


def build_trace_status(
    *,
    configured: bool,
    local_emit_at: datetime | None,
    remote_trace: dict[str, Any] | None,
    correlation_id: str | None,
    exporter_state: Literal["unknown", "succeeded", "failed"] = "unknown",
    local_exporter_callback_at: datetime | None = None,
    query_error_type: str | None = None,
    transaction_url: str | None = None,
) -> TraceDeliveryStatus:
    safe_correlation = _safe_correlation_id(correlation_id)
    if not configured:
        return TraceDeliveryStatus(
            state="not_configured",
            local_emit_at=local_emit_at,
            local_exporter_callback_at=local_exporter_callback_at,
            correlation_id=safe_correlation,
            exporter_state=exporter_state,
        )
    if query_error_type:
        return TraceDeliveryStatus(
            state="unavailable",
            local_emit_at=local_emit_at,
            local_exporter_callback_at=local_exporter_callback_at,
            correlation_id=safe_correlation,
            exporter_state=exporter_state,
            error_type=_safe_error_type(query_error_type),
        )
    if remote_trace:
        observed_at = _as_datetime(remote_trace.get("observed_at"))
        if observed_at:
            return TraceDeliveryStatus(
                state="connected",
                local_emit_at=local_emit_at,
                local_exporter_callback_at=local_exporter_callback_at,
                last_export_confirmed_at=observed_at,
                correlation_id=safe_correlation,
                transaction_url=transaction_url,
                exporter_state=exporter_state,
            )
    return TraceDeliveryStatus(
        state="partial",
        local_emit_at=local_emit_at,
        local_exporter_callback_at=local_exporter_callback_at,
        correlation_id=safe_correlation,
        exporter_state=exporter_state,
    )


def get_trace_delivery_status(
    workspace_id: str,
    run_id: str | None = None,
    correlation_id: str | None = None,
) -> TraceDeliveryStatus:
    """Return a truthful delivery state without exposing query input or output."""
    config = _monitor_config()
    local = _local_delivery_record(workspace_id, run_id)
    local_emit_at = _as_datetime(local.get("local_emit_at")) if local else None
    callback_at = _as_datetime(local.get("exporter_callback_at")) if local else None
    exporter_state = str((local or {}).get("exporter_state") or "unknown")
    if exporter_state not in {"unknown", "succeeded", "failed"}:
        exporter_state = "unknown"
    correlation = _safe_correlation_id(correlation_id) or _safe_correlation_id((local or {}).get("correlation_id"))
    if not config:
        return build_trace_status(
            configured=False,
            local_emit_at=local_emit_at,
            local_exporter_callback_at=callback_at,
            remote_trace=None,
            correlation_id=correlation,
            exporter_state=exporter_state,  # type: ignore[arg-type]
        )
    if not run_id or not correlation:
        return build_trace_status(
            configured=True,
            local_emit_at=local_emit_at,
            local_exporter_callback_at=callback_at,
            remote_trace=None,
            correlation_id=correlation,
            exporter_state=exporter_state,  # type: ignore[arg-type]
        )

    cache_key = (hash_trace_identifier(workspace_id), hash_trace_identifier(run_id), hash_trace_identifier(correlation))
    remote = _cached_confirmation(cache_key)
    try:
        if remote is None:
            remote = query_trace_delivery(workspace_id, run_id, correlation)
            if remote is not None:
                _cache_confirmation(cache_key, remote)
    except TraceDeliveryQueryError as exc:
        return build_trace_status(
            configured=True,
            local_emit_at=local_emit_at,
            local_exporter_callback_at=callback_at,
            remote_trace=None,
            correlation_id=correlation,
            exporter_state=exporter_state,  # type: ignore[arg-type]
            query_error_type=exc.error_type,
        )

    transaction_url = None
    if remote:
        transaction_url = build_transaction_link(config.resource_id, config.application_id, correlation)
    return build_trace_status(
        configured=True,
        local_emit_at=local_emit_at,
        local_exporter_callback_at=callback_at,
        remote_trace=remote,
        correlation_id=correlation,
        exporter_state=exporter_state,  # type: ignore[arg-type]
        transaction_url=transaction_url,
    )


def query_trace_delivery(
    workspace_id: str,
    run_id: str,
    correlation_id: str,
    *,
    client: Any | None = None,
) -> dict[str, Any] | None:
    """Prove one matching trace is queryable from the configured Log workspace."""
    config = _monitor_config()
    correlation = _safe_correlation_id(correlation_id)
    if not config or not correlation:
        return None

    workspace_hash = hash_trace_identifier(workspace_id)
    run_hash = hash_trace_identifier(run_id)
    correlation_hash = hash_trace_identifier(correlation)
    query = _delivery_query(workspace_hash, run_hash, correlation_hash)
    try:
        if client is None:
            from azure.identity import DefaultAzureCredential
            from azure.monitor.query import LogsQueryClient

            client = LogsQueryClient(DefaultAzureCredential())
        result = client.query_workspace(
            config.logs_workspace_id,
            query,
            timespan=_QUERY_WINDOW,
            server_timeout=_QUERY_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        raise TraceDeliveryQueryError(type(exc).__name__) from None

    row = _first_row(result)
    if not row:
        return None
    if str(row.get("appId") or "").lower() != config.application_id.lower():
        return None
    if str(row.get("_ResourceId") or "").lower() != config.resource_id.lower():
        return None
    if str(row.get("run_hash") or "") != run_hash or str(row.get("correlation_hash") or "") != correlation_hash:
        return None
    observed_correlation = _safe_correlation_id(row.get("operation_Id"))
    if observed_correlation != correlation:
        return None
    observed_at = _as_datetime(row.get("timestamp"))
    if not observed_at:
        return None
    return {
        "observed_at": observed_at,
        "correlation_id": observed_correlation,
        "application_id": config.application_id,
        "resource_id": config.resource_id,
    }


def build_transaction_link(resource_id: str, application_id: str, correlation_id: str) -> str | None:
    """Build a portal link only from canonical, verified Azure identifiers."""
    if not _RESOURCE_ID.fullmatch(str(resource_id or "")):
        return None
    if not _UUID.fullmatch(str(application_id or "")):
        return None
    correlation = _safe_correlation_id(correlation_id)
    if not correlation:
        return None
    return (
        "https://portal.azure.com/#blade/AppInsightsExtension/TransactionSearchBlade/"
        f"ComponentId/{quote(resource_id, safe='')}/AppId/{application_id.lower()}/TraceId/{correlation}"
    )


def _monitor_config() -> _MonitorConfig | None:
    if not os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"):
        return None
    logs_workspace_id = str(os.environ.get("DF_AZURE_MONITOR_LOGS_WORKSPACE_ID") or "").strip()
    application_id = str(os.environ.get("DF_APP_INSIGHTS_APPLICATION_ID") or "").strip()
    resource_id = str(os.environ.get("DF_APP_INSIGHTS_RESOURCE_ID") or "").strip()
    if not _UUID.fullmatch(logs_workspace_id) or not _UUID.fullmatch(application_id) or not _RESOURCE_ID.fullmatch(resource_id):
        return None
    return _MonitorConfig(logs_workspace_id=logs_workspace_id, application_id=application_id, resource_id=resource_id)


def _delivery_query(workspace_hash: str, run_hash: str, correlation_hash: str) -> str:
    for value in (workspace_hash, run_hash, correlation_hash):
        if not _HASH.fullmatch(value):
            raise ValueError("trace hash must be sha256")
    return "\n".join(
        (
            "traces",
            f'| where tostring(customDimensions["dataforge.workspace.hash"]) == "{workspace_hash}"',
            f'| where tostring(customDimensions["dataforge.run.hash"]) == "{run_hash}"',
            f'| where tostring(customDimensions["dataforge.correlation.hash"]) == "{correlation_hash}"',
            "| project timestamp, operation_Id, appId, _ResourceId,",
            '    run_hash=tostring(customDimensions["dataforge.run.hash"]),',
            '    correlation_hash=tostring(customDimensions["dataforge.correlation.hash"])',
            "| take 1",
        )
    )


def _first_row(result: Any) -> dict[str, Any] | None:
    tables = getattr(result, "tables", None)
    if not isinstance(tables, list) or not tables:
        return None
    table = tables[0]
    rows = getattr(table, "rows", None)
    columns = getattr(table, "columns", None)
    if not isinstance(rows, list) or not rows or not isinstance(columns, list):
        return None
    names = [str(getattr(column, "name", column)) for column in columns]
    row = rows[0]
    if not isinstance(row, (list, tuple)) or len(row) != len(names):
        return None
    return dict(zip(names, row, strict=True))


def _cached_confirmation(key: tuple[str, str, str]) -> dict[str, Any] | None:
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CONFIRMED_CACHE.get(key)
        if not cached:
            return None
        expires_at, value = cached
        if expires_at <= now:
            _CONFIRMED_CACHE.pop(key, None)
            return None
        return dict(value)


def _cache_confirmation(key: tuple[str, str, str], remote: dict[str, Any]) -> None:
    # Only a parsed remote confirmation reaches this function; misses and failures never cache.
    if not _as_datetime(remote.get("observed_at")):
        return
    with _CACHE_LOCK:
        _CONFIRMED_CACHE[key] = (time.monotonic() + _CACHE_TTL_SECONDS, dict(remote))


def _local_delivery_record(workspace_id: str, run_id: str | None) -> dict[str, Any] | None:
    try:
        from .tracing import trace_delivery_record

        return trace_delivery_record(workspace_id, run_id)
    except Exception:
        return None


def _safe_correlation_id(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    return text if _CORRELATION.fullmatch(text) else None


def _safe_error_type(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]", "", str(value or ""))[:80]
    return text or "AzureMonitorQueryError"


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None
