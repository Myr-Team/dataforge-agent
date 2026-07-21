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
_GATEWAY_ID = re.compile(r"^[A-Za-z0-9-]{1,80}$")
_RESOURCE_ID = re.compile(
    r"^/subscriptions/[0-9a-f-]{36}/resourcegroups/[a-z0-9._()-]+/providers/microsoft\.insights/components/[a-z0-9._-]+$",
    re.IGNORECASE,
)
_QUERY_WINDOW = timedelta(minutes=15)
_QUERY_TIMEOUT_SECONDS = 10
_CACHE_TTL_SECONDS = 30.0
_CACHE_LOCK = threading.RLock()
_CONFIRMED_CACHE: dict[tuple[str, str, str, str, str], tuple[float, "RemoteTraceProof"]] = {}
_GATEWAY_METRIC_CACHE: dict[tuple[str, str, str, str], tuple[float, "GatewayMetricEvidence"]] = {}


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


class TraceTelemetryMetrics(BaseModel):
    """Aggregate-only telemetry for one verified DataForge run."""

    state: Literal["connected", "partial", "not_configured", "unavailable"]
    correlation_id: str | None = None
    record_count: int | None = None
    request_count: int | None = None
    dependency_count: int | None = None
    trace_event_count: int | None = None
    error_count: int | None = None
    first_observed_at: datetime | None = None
    last_observed_at: datetime | None = None
    error_type: str | None = None
    error_status: int | None = None


class GatewayMetricEvidence(BaseModel):
    """Workspace-scoped APIM metric proof without exposing raw telemetry."""

    state: Literal["verified", "pending", "unavailable", "not_configured"]
    governed_calls: int | None = None
    total_tokens: int | None = None
    last_observed_at: datetime | None = None
    provenance: Literal["apim_custom_metric", "apim_metric_pending", "apim_metric_query_unavailable", "apim_metric_not_configured"]


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
        raise TraceDeliveryQueryError(type(exc).__name__, _exception_status(exc)) from None

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
            source_table=_logical_source_table(row.get("source_table")),
        )
    except Exception:
        return None
    return proof if _proof_matches(proof, _trace_expectation(workspace_id, run_id, correlation, config)) else None


def get_trace_telemetry_metrics(
    workspace_id: str,
    run_id: str | None = None,
    correlation_id: str | None = None,
) -> TraceTelemetryMetrics:
    """Return only aggregate telemetry after binding it to one trace identity."""
    config = _monitor_config()
    correlation = _safe_correlation_id(correlation_id)
    if not config:
        return TraceTelemetryMetrics(state="not_configured", correlation_id=correlation)
    if not run_id or not correlation:
        return TraceTelemetryMetrics(state="partial", correlation_id=correlation)
    return query_trace_telemetry_metrics(workspace_id, run_id, correlation)


def get_gateway_metric_evidence(
    workspace_id: str,
    gateway_id: str,
    *,
    client_factory: Callable[[], Any] | None = None,
) -> GatewayMetricEvidence:
    """Confirm APIM token-metric evidence for one workspace hash only."""
    config = _monitor_config()
    safe_gateway_id = str(gateway_id or "").strip()
    if not config or not _GATEWAY_ID.fullmatch(safe_gateway_id):
        return GatewayMetricEvidence(
            state="not_configured",
            provenance="apim_metric_not_configured",
        )

    workspace_hash = hash_trace_identifier(workspace_id)
    cache_key = (workspace_hash, safe_gateway_id.lower(), config.application_id, config.resource_id.lower())
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _GATEWAY_METRIC_CACHE.get(cache_key)
        if cached and cached[0] > now:
            return cached[1]

    query = _gateway_metrics_query(workspace_hash, safe_gateway_id)
    try:
        client = client_factory() if client_factory else _managed_identity_logs_client()
        result = client.query_workspace(
            config.logs_workspace_id,
            query,
            timespan=timedelta(hours=24),
            server_timeout=_QUERY_TIMEOUT_SECONDS,
        )
    except Exception:
        evidence = GatewayMetricEvidence(
            state="unavailable",
            provenance="apim_metric_query_unavailable",
        )
        _cache_gateway_metric(cache_key, evidence)
        return evidence

    if _is_partial_result(result):
        evidence = GatewayMetricEvidence(
            state="unavailable",
            provenance="apim_metric_query_unavailable",
        )
        _cache_gateway_metric(cache_key, evidence)
        return evidence

    row = _first_row(result)
    if not row or str(row.get("appId") or "").lower() != config.application_id or str(row.get("_ResourceId") or "").lower() != config.resource_id.lower():
        evidence = GatewayMetricEvidence(state="pending", provenance="apim_metric_pending")
        _cache_gateway_metric(cache_key, evidence)
        return evidence

    calls = _metric_count(row.get("governed_calls"))
    if calls is None or calls <= 0:
        evidence = GatewayMetricEvidence(state="pending", provenance="apim_metric_pending")
        _cache_gateway_metric(cache_key, evidence)
        return evidence
    evidence = GatewayMetricEvidence(
        state="verified",
        governed_calls=calls,
        total_tokens=_metric_count(row.get("total_tokens")),
        last_observed_at=_as_datetime(row.get("last_observed_at")),
        provenance="apim_custom_metric",
    )
    _cache_gateway_metric(cache_key, evidence)
    return evidence


def query_trace_telemetry_metrics(
    workspace_id: str,
    run_id: str,
    correlation_id: str,
    *,
    client_factory: Callable[[], Any] | None = None,
) -> TraceTelemetryMetrics:
    """Query bounded metric totals without exposing telemetry rows to callers."""
    config = _monitor_config()
    correlation = _safe_correlation_id(correlation_id)
    if not config:
        return TraceTelemetryMetrics(state="not_configured", correlation_id=correlation)
    if not correlation:
        return TraceTelemetryMetrics(state="partial", correlation_id=None)

    workspace_hash = hash_trace_identifier(workspace_id)
    run_hash = hash_trace_identifier(run_id)
    correlation_hash = hash_trace_identifier(correlation)
    query = _metrics_query(workspace_hash, run_hash, correlation_hash)
    try:
        client = client_factory() if client_factory else _managed_identity_logs_client()
        result = client.query_workspace(
            config.logs_workspace_id,
            query,
            timespan=_QUERY_WINDOW,
            server_timeout=_QUERY_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        return TraceTelemetryMetrics(
            state="unavailable",
            correlation_id=correlation,
            error_type=_safe_error_type(type(exc).__name__),
            error_status=_exception_status(exc),
        )

    if _is_partial_result(result):
        return TraceTelemetryMetrics(
            state="unavailable",
            correlation_id=correlation,
            error_type="LogsQueryPartialResult",
            error_status=_partial_result_status(result),
        )

    row = _first_row(result)
    if not row:
        return TraceTelemetryMetrics(state="partial", correlation_id=correlation)
    if str(row.get("appId") or "").lower() != config.application_id or str(row.get("_ResourceId") or "").lower() != config.resource_id.lower():
        return TraceTelemetryMetrics(state="partial", correlation_id=correlation)

    record_count = _metric_count(row.get("record_count"))
    if record_count is None or record_count <= 0:
        return TraceTelemetryMetrics(state="partial", correlation_id=correlation)
    return TraceTelemetryMetrics(
        state="connected",
        correlation_id=correlation,
        record_count=record_count,
        request_count=_metric_count(row.get("request_count")),
        dependency_count=_metric_count(row.get("dependency_count")),
        trace_event_count=_metric_count(row.get("trace_event_count")),
        error_count=_metric_count(row.get("error_count")),
        first_observed_at=_as_datetime(row.get("first_observed_at")),
        last_observed_at=_as_datetime(row.get("last_observed_at")),
    )


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
            "union isfuzzy=true withsource=source_table AppRequests, AppDependencies, AppTraces",
            '| extend telemetry_properties=column_ifexists("Properties", dynamic({}))',
            f'| where tostring(telemetry_properties["dataforge.workspace.hash"]) == "{workspace_hash}"',
            f'| where tostring(telemetry_properties["dataforge.run.hash"]) == "{run_hash}"',
            f'| where tostring(telemetry_properties["dataforge.correlation.hash"]) == "{correlation_hash}"',
            "| project timestamp=TimeGenerated, operation_Id=OperationId, appId=ResourceGUID, _ResourceId, source_table,",
            '    run_hash=tostring(telemetry_properties["dataforge.run.hash"]),',
            '    correlation_hash=tostring(telemetry_properties["dataforge.correlation.hash"])',
            "| take 1",
        )
    )


def _metrics_query(workspace_hash: str, run_hash: str, correlation_hash: str) -> str:
    for value in (workspace_hash, run_hash, correlation_hash):
        if not _HASH.fullmatch(value):
            raise ValueError("trace hash must be sha256")
    return "\n".join(
        (
            "union isfuzzy=true withsource=source_table AppRequests, AppDependencies, AppTraces",
            '| extend telemetry_properties=column_ifexists("Properties", dynamic({}))',
            f'| where tostring(telemetry_properties["dataforge.workspace.hash"]) == "{workspace_hash}"',
            f'| where tostring(telemetry_properties["dataforge.run.hash"]) == "{run_hash}"',
            f'| where tostring(telemetry_properties["dataforge.correlation.hash"]) == "{correlation_hash}"',
            "| summarize record_count=count(),",
            '    request_count=countif(source_table =~ "AppRequests"),',
            '    dependency_count=countif(source_table =~ "AppDependencies"),',
            '    trace_event_count=countif(source_table =~ "AppTraces"),',
            '    error_count=countif(tolower(tostring(column_ifexists("Success", ""))) == "false"),',
            "    first_observed_at=min(TimeGenerated),",
            "    last_observed_at=max(TimeGenerated)",
            "    by appId=ResourceGUID, _ResourceId",
            "| take 2",
        )
    )


def _gateway_metrics_query(workspace_hash: str, gateway_id: str) -> str:
    if not _HASH.fullmatch(workspace_hash):
        raise ValueError("workspace hash must be sha256")
    if not _GATEWAY_ID.fullmatch(gateway_id):
        raise ValueError("gateway id is invalid")
    return "\n".join(
        (
            "AppMetrics",
            '| where Name == "Total Tokens"',
            '| extend metric_properties=column_ifexists("Properties", dynamic({}))',
            f'| where tostring(metric_properties["Workspace Hash"]) == "{workspace_hash}"',
            f'| where tostring(metric_properties["Service ID"]) == "{gateway_id}"',
            "| summarize governed_calls=sum(tolong(ItemCount)),",
            "    total_tokens=sum(tolong(Sum)),",
            "    last_observed_at=max(TimeGenerated)",
            "    by appId=ResourceGUID, _ResourceId",
            "| take 2",
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
    try:
        values = list(row)
    except TypeError:
        return None
    if len(values) != len(names):
        return None
    return dict(zip(names, values, strict=True))


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


def _cache_gateway_metric(key: tuple[str, str, str, str], evidence: GatewayMetricEvidence) -> None:
    with _CACHE_LOCK:
        _GATEWAY_METRIC_CACHE[key] = (time.monotonic() + _CACHE_TTL_SECONDS, evidence)


def _managed_identity_logs_client() -> Any:
    from azure.identity import ManagedIdentityCredential
    from azure.monitor.query import LogsQueryClient

    client_id = monitor_identity_client_id()
    credential = ManagedIdentityCredential(client_id=client_id) if client_id else ManagedIdentityCredential()
    return LogsQueryClient(credential)


def monitor_identity_client_id() -> str | None:
    """Use an explicitly configured Monitor identity, otherwise the app system identity.

    ``AZURE_CLIENT_ID`` belongs to the Foundry/OpenAI workload configuration and
    can refer to an identity that is not attached to this Container App.
    """
    value = str(os.environ.get("DF_AZURE_MONITOR_CLIENT_ID") or "").strip()
    return value or None


def _partial_result_status(result: Any) -> int | None:
    partial_error = getattr(result, "partial_error", None)
    if partial_error is None:
        return None
    if isinstance(partial_error, dict):
        return _safe_status(partial_error.get("status") or partial_error.get("status_code"))
    return _safe_status(getattr(partial_error, "status", None) or getattr(partial_error, "status_code", None))


def _is_partial_result(result: Any) -> bool:
    return type(result).__name__ == "LogsQueryPartialResult" or getattr(result, "partial_error", None) is not None


def _exception_status(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    return _safe_status(
        getattr(exc, "status_code", None)
        or getattr(exc, "status", None)
        or getattr(response, "status_code", None)
        or getattr(response, "status", None)
    )


def _metric_count(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _logical_source_table(value: Any) -> str:
    return {
        "apprequests": "requests",
        "appdependencies": "dependencies",
        "apptraces": "traces",
    }.get(str(value or "").lower(), str(value or "").lower())


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
