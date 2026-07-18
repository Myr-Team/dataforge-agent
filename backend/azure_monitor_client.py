"""Bounded Azure Monitor delivery confirmation for DataForge traces."""
from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal
from urllib.parse import quote

from pydantic import BaseModel, field_validator, model_validator


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
_CONFIRMED_CACHE: dict[tuple[str, str, str, str, str], tuple[float, "RemoteTraceProof"]] = {}


class TraceDeliveryStatus(BaseModel):
    state: Literal["connected", "partial", "not_configured", "unavailable"]
    local_emit_at: datetime | None = None
    local_exporter_callback_at: datetime | None = None
    last_export_confirmed_at: datetime | None = None
    correlation_id: str | None = None
    transaction_url: str | None = None
    exporter_state: Literal["unknown", "succeeded", "failed"] = "unknown"
    error_type: str | None = None
    error_status: int | None = None


class TraceDeliveryQueryError(RuntimeError):
    def __init__(self, error_type: str, error_status: int | None = None) -> None:
        self.error_type = _safe_error_type(error_type)
        self.error_status = _safe_status(error_status)
        suffix = f" ({self.error_status})" if self.error_status is not None else ""
        super().__init__(f"Azure Monitor delivery query unavailable: {self.error_type}{suffix}")


class _MonitorConfig(BaseModel):
    logs_workspace_id: str
    application_id: str
    resource_id: str


class TraceDeliveryExpectation(BaseModel):
    workspace_hash: str
    run_hash: str
    correlation_hash: str
    resource_id: str
    application_id: str
    correlation_id: str

    @field_validator("workspace_hash", "run_hash", "correlation_hash")
    @classmethod
    def _validate_hash(cls, value: str) -> str:
        if not _HASH.fullmatch(value):
            raise ValueError("trace hash must be sha256")
        return value

    @field_validator("resource_id")
    @classmethod
    def _validate_resource(cls, value: str) -> str:
        if not _RESOURCE_ID.fullmatch(value):
            raise ValueError("resource id is invalid")
        return value

    @field_validator("application_id")
    @classmethod
    def _validate_application(cls, value: str) -> str:
        if not _UUID.fullmatch(value):
            raise ValueError("application id is invalid")
        return value.lower()

    @field_validator("correlation_id")
    @classmethod
    def _validate_correlation(cls, value: str) -> str:
        correlation = _safe_correlation_id(value)
        if not correlation:
            raise ValueError("correlation id is invalid")
        return correlation

    @model_validator(mode="after")
    def _bind_correlation_hash(self) -> "TraceDeliveryExpectation":
        if self.correlation_hash != hash_trace_identifier(self.correlation_id):
            raise ValueError("correlation hash does not match correlation id")
        return self


class RemoteTraceProof(BaseModel):
    observed_at: datetime
    trace_id: str
    workspace_hash: str
    run_hash: str
    correlation_hash: str
    resource_id: str
    application_id: str
    source_table: Literal["requests", "dependencies", "traces"]

    @field_validator("trace_id")
    @classmethod
    def _validate_trace_id(cls, value: str) -> str:
        trace_id = _safe_correlation_id(value)
        if not trace_id:
            raise ValueError("trace id is invalid")
        return trace_id

    @field_validator("workspace_hash", "run_hash", "correlation_hash")
    @classmethod
    def _validate_hash(cls, value: str) -> str:
        if not _HASH.fullmatch(value):
            raise ValueError("trace hash must be sha256")
        return value

    @field_validator("resource_id")
    @classmethod
    def _validate_resource(cls, value: str) -> str:
        if not _RESOURCE_ID.fullmatch(value):
            raise ValueError("resource id is invalid")
        return value

    @field_validator("application_id")
    @classmethod
    def _validate_application(cls, value: str) -> str:
        if not _UUID.fullmatch(value):
            raise ValueError("application id is invalid")
        return value.lower()

    @model_validator(mode="after")
    def _bind_correlation_hash(self) -> "RemoteTraceProof":
        if self.correlation_hash != hash_trace_identifier(self.trace_id):
            raise ValueError("correlation hash does not match trace id")
        return self


def hash_trace_identifier(value: str) -> str:
    return hashlib.sha256(str(value).strip().encode("utf-8")).hexdigest()


def clear_trace_delivery_cache() -> None:
    with _CACHE_LOCK:
        _CONFIRMED_CACHE.clear()


def build_trace_status(
    *,
    configured: bool,
    local_emit_at: datetime | None,
    remote_proof: RemoteTraceProof | None,
    expected: TraceDeliveryExpectation | None,
    correlation_id: str | None,
    exporter_state: Literal["unknown", "succeeded", "failed"] = "unknown",
    local_exporter_callback_at: datetime | None = None,
    query_error_type: str | None = None,
    query_error_status: int | None = None,
    transaction_url: str | None = None,
) -> TraceDeliveryStatus:
    if remote_proof is not None and not isinstance(remote_proof, RemoteTraceProof):
        raise TypeError("remote_proof must be a RemoteTraceProof")
    if expected is not None and not isinstance(expected, TraceDeliveryExpectation):
        raise TypeError("expected must be a TraceDeliveryExpectation")
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
            error_status=_safe_status(query_error_status),
        )
    if isinstance(remote_proof, RemoteTraceProof) and expected and _proof_matches(remote_proof, expected):
        if remote_proof.observed_at:
            return TraceDeliveryStatus(
                state="connected",
                local_emit_at=local_emit_at,
                local_exporter_callback_at=local_exporter_callback_at,
                last_export_confirmed_at=remote_proof.observed_at,
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
            remote_proof=None,
            expected=None,
            correlation_id=correlation,
            exporter_state=exporter_state,  # type: ignore[arg-type]
        )
    if not run_id or not correlation:
        return build_trace_status(
            configured=True,
            local_emit_at=local_emit_at,
            local_exporter_callback_at=callback_at,
            remote_proof=None,
            expected=None,
            correlation_id=correlation,
            exporter_state=exporter_state,  # type: ignore[arg-type]
        )

    expected = _trace_expectation(workspace_id, run_id, correlation, config)
    cache_key = (
        expected.workspace_hash,
        expected.run_hash,
        expected.correlation_hash,
        expected.resource_id.lower(),
        expected.application_id,
    )
    remote = _cached_confirmation(cache_key, expected)
    try:
        if remote is None:
            remote = query_trace_delivery(workspace_id, run_id, correlation)
            if remote is not None and _proof_matches(remote, expected):
                _cache_confirmation(cache_key, remote, expected)
    except TraceDeliveryQueryError as exc:
        return build_trace_status(
            configured=True,
            local_emit_at=local_emit_at,
            local_exporter_callback_at=callback_at,
            remote_proof=None,
            expected=expected,
            correlation_id=correlation,
            exporter_state=exporter_state,  # type: ignore[arg-type]
            query_error_type=exc.error_type,
            query_error_status=exc.error_status,
        )

    transaction_url = None
    if remote and _proof_matches(remote, expected):
        transaction_url = build_transaction_link(config.resource_id, config.application_id, correlation)
    return build_trace_status(
        configured=True,
        local_emit_at=local_emit_at,
        local_exporter_callback_at=callback_at,
        remote_proof=remote,
        expected=expected,
        correlation_id=correlation,
        exporter_state=exporter_state,  # type: ignore[arg-type]
        transaction_url=transaction_url,
    )


def query_trace_delivery(
    workspace_id: str,
    run_id: str,
    correlation_id: str,
    *,
    client_factory: Callable[[], Any] | None = None,
) -> RemoteTraceProof | None:
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
        client = client_factory() if client_factory else _managed_identity_logs_client()
        result = client.query_workspace(
            config.logs_workspace_id,
            query,
            timespan=_QUERY_WINDOW,
            server_timeout=_QUERY_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        raise TraceDeliveryQueryError(type(exc).__name__) from None

    if _is_partial_result(result):
        raise TraceDeliveryQueryError("LogsQueryPartialResult", _partial_result_status(result))

    row = _first_row(result)
    if not row:
        return None
    try:
        proof = RemoteTraceProof(
            observed_at=row.get("timestamp"),
            trace_id=str(row.get("operation_Id") or ""),
            workspace_hash=workspace_hash,
            run_hash=str(row.get("run_hash") or ""),
            correlation_hash=str(row.get("correlation_hash") or ""),
            application_id=str(row.get("appId") or ""),
            resource_id=str(row.get("_ResourceId") or ""),
            source_table=str(row.get("source_table") or "").lower(),
        )
    except Exception:
        return None
    return proof if _proof_matches(proof, _trace_expectation(workspace_id, run_id, correlation, config)) else None


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
            "union isfuzzy=true withsource=source_table requests, dependencies, traces",
            f'| where tostring(customDimensions["dataforge.workspace.hash"]) == "{workspace_hash}"',
            f'| where tostring(customDimensions["dataforge.run.hash"]) == "{run_hash}"',
            f'| where tostring(customDimensions["dataforge.correlation.hash"]) == "{correlation_hash}"',
            "| project timestamp, operation_Id, appId, _ResourceId, source_table,",
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


def _cached_confirmation(key: tuple[str, str, str, str, str], expected: TraceDeliveryExpectation) -> RemoteTraceProof | None:
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CONFIRMED_CACHE.get(key)
        if not cached:
            return None
        expires_at, proof = cached
        if expires_at <= now or not _proof_matches(proof, expected):
            _CONFIRMED_CACHE.pop(key, None)
            return None
        return proof


def _cache_confirmation(key: tuple[str, str, str, str, str], proof: RemoteTraceProof, expected: TraceDeliveryExpectation) -> None:
    # Only a parsed remote confirmation reaches this function; misses and failures never cache.
    if not _proof_matches(proof, expected):
        return
    with _CACHE_LOCK:
        _CONFIRMED_CACHE[key] = (time.monotonic() + _CACHE_TTL_SECONDS, proof)


def _managed_identity_logs_client() -> Any:
    from azure.identity import ManagedIdentityCredential
    from azure.monitor.query import LogsQueryClient

    client_id = str(os.environ.get("AZURE_CLIENT_ID") or "").strip()
    credential = ManagedIdentityCredential(client_id=client_id) if client_id else ManagedIdentityCredential()
    return LogsQueryClient(credential)


def _partial_result_status(result: Any) -> int | None:
    partial_error = getattr(result, "partial_error", None)
    if partial_error is None:
        return None
    if isinstance(partial_error, dict):
        return _safe_status(partial_error.get("status") or partial_error.get("status_code"))
    return _safe_status(getattr(partial_error, "status", None) or getattr(partial_error, "status_code", None))


def _is_partial_result(result: Any) -> bool:
    return type(result).__name__ == "LogsQueryPartialResult" or getattr(result, "partial_error", None) is not None


def _trace_expectation(workspace_id: str, run_id: str, correlation_id: str, config: _MonitorConfig) -> TraceDeliveryExpectation:
    return TraceDeliveryExpectation(
        workspace_hash=hash_trace_identifier(workspace_id),
        run_hash=hash_trace_identifier(run_id),
        correlation_hash=hash_trace_identifier(correlation_id),
        resource_id=config.resource_id,
        application_id=config.application_id,
        correlation_id=correlation_id,
    )


def _proof_matches(proof: RemoteTraceProof, expected: TraceDeliveryExpectation) -> bool:
    return (
        proof.workspace_hash == expected.workspace_hash
        and proof.run_hash == expected.run_hash
        and proof.correlation_hash == expected.correlation_hash
        and proof.resource_id.lower() == expected.resource_id.lower()
        and proof.application_id == expected.application_id
        and proof.trace_id == expected.correlation_id
    )


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


def _safe_status(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        status = int(value)
    except (TypeError, ValueError):
        return None
    return status if 100 <= status <= 599 else None


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
