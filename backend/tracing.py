from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any


LOGGER = logging.getLogger("dataforge.trace")
logging.basicConfig(level=logging.WARNING)
LOGGER.setLevel(os.environ.get("LOG_LEVEL", "INFO"))
LOGGER.propagate = False
if not LOGGER.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
    LOGGER.addHandler(handler)


_CURRENT_AGENT_SPAN: ContextVar[Any | None] = ContextVar("dataforge_agent_span", default=None)
_CURRENT_GATEWAY_HEADERS: ContextVar[dict[str, str] | None] = ContextVar(
    "dataforge_gateway_headers",
    default=None,
)
_SAFE_TELEMETRY_NAME = re.compile(r"^[A-Za-z0-9_.:-]+$")
_TRACE_CORRELATION = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)
_DELIVERY_LOCK = threading.RLock()
_DELIVERY_RECORDS: dict[tuple[str, str], dict[str, Any]] = {}


def _foundry_runtime_agent_id() -> str:
    """Return the external Foundry registration ID without trusting arbitrary config."""

    configured = str(os.environ.get("FOUNDRY_AGENT_ID") or "").strip()
    if configured and len(configured) <= 128 and _SAFE_TELEMETRY_NAME.fullmatch(configured):
        return configured
    return "dataforge"


def _delivery_key(workspace_id: str, run_id: str | None) -> tuple[str, str]:
    return (
        hashlib.sha256(str(workspace_id or "").strip().encode("utf-8")).hexdigest(),
        hashlib.sha256(str(run_id or "").strip().encode("utf-8")).hexdigest(),
    )


def _trace_identifier_hash(value: str) -> str:
    return hashlib.sha256(str(value or "").strip().encode("utf-8")).hexdigest()


def _span_correlation_id(span: Any) -> str | None:
    try:
        trace_id = int(span.get_span_context().trace_id)
        value = f"{trace_id:032x}"
        return value if _TRACE_CORRELATION.fullmatch(value) else None
    except Exception:
        return None


def trace_id_from_span(span: Any) -> str | None:
    """Return the current trace ID only when it is safe to expose as a reference."""
    return _span_correlation_id(span)


def gateway_request_headers() -> dict[str, str]:
    """Return only one-way request lineage markers for an APIM call."""
    return dict(_CURRENT_GATEWAY_HEADERS.get() or {})


def foundry_runtime_agent_id() -> str:
    """Return the validated Foundry external-agent ID used for this process."""
    return _foundry_runtime_agent_id()


def record_local_span_emit(workspace_id: str, run_id: str | None, correlation_id: str | None = None) -> None:
    """Record local span emission; Azure SDK exporter callbacks remain unknown by default."""
    correlation = str(correlation_id or "").strip().lower()
    if not _TRACE_CORRELATION.fullmatch(correlation):
        correlation = None
    with _DELIVERY_LOCK:
        previous = _DELIVERY_RECORDS.get(_delivery_key(workspace_id, run_id), {})
        _DELIVERY_RECORDS[_delivery_key(workspace_id, run_id)] = {
            "local_emit_at": datetime.now(timezone.utc),
            "correlation_id": correlation,
            "exporter_state": previous.get("exporter_state") if previous.get("exporter_state") in {"succeeded", "failed"} else "unknown",
            "exporter_callback_at": previous.get("exporter_callback_at"),
        }


def record_exporter_callback(workspace_id: str, run_id: str | None, succeeded: bool) -> None:
    """Accept an exporter callback when an SDK integration explicitly provides one."""
    with _DELIVERY_LOCK:
        key = _delivery_key(workspace_id, run_id)
        previous = dict(_DELIVERY_RECORDS.get(key) or {})
        _DELIVERY_RECORDS[key] = {
            "local_emit_at": previous.get("local_emit_at"),
            "correlation_id": previous.get("correlation_id"),
            "exporter_state": "succeeded" if succeeded else "failed",
            "exporter_callback_at": datetime.now(timezone.utc),
        }


def trace_delivery_record(workspace_id: str, run_id: str | None) -> dict[str, Any] | None:
    with _DELIVERY_LOCK:
        value = _DELIVERY_RECORDS.get(_delivery_key(workspace_id, run_id))
        return dict(value) if value else None


def clear_trace_delivery_records() -> None:
    with _DELIVERY_LOCK:
        _DELIVERY_RECORDS.clear()


def _actor_fingerprint(actor: Any) -> str | None:
    if not isinstance(actor, dict):
        return None
    identity = str(actor.get("email") or actor.get("id") or actor.get("name") or "").strip().lower()
    if not identity:
        return None
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def _telemetry_event_data(event: str, data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {"value_type": type(data).__name__}

    safe_keys = {
        "agent",
        "confidence",
        "conversation_id",
        "count",
        "elapsed_ms",
        "error_type",
        "framework",
        "framework_version",
        "intent",
        "latency_ms",
        "mode",
        "name",
        "needs_clarification",
        "orchestrator",
        "output_mode",
        "pattern",
        "provider",
        "revision",
        "status",
        "target_expert",
        "verdict",
        "workspace_id",
    }
    safe: dict[str, Any] = {key: data[key] for key in safe_keys if key in data and data[key] is not None}

    response_id = str(data.get("response_id") or "").strip()
    if response_id and len(response_id) <= 128 and _SAFE_TELEMETRY_NAME.fullmatch(response_id):
        safe["response_id"] = response_id
    retry_count = data.get("retry_count")
    if isinstance(retry_count, (int, float)) and not isinstance(retry_count, bool):
        safe["retry_count"] = max(0, min(100, int(retry_count)))
    if isinstance(data.get("cache_hit"), bool):
        safe["cache_hit"] = data["cache_hit"]
    if data.get("error_category") in {"transient", "content_policy", "contract_validation", "permanent"}:
        safe["error_category"] = data["error_category"]
    tool_names = data.get("tool_names")
    if isinstance(tool_names, (list, tuple)):
        bounded_names = [
            name
            for item in tool_names[:12]
            if (name := str(item).strip())
            and len(name) <= 80
            and _SAFE_TELEMETRY_NAME.fullmatch(name)
        ]
        if bounded_names:
            safe["tool_names"] = bounded_names

    actor = data.get("actor")
    actor_id = _actor_fingerprint(actor)
    if actor_id:
        safe["actor_id"] = actor_id
    if isinstance(actor, dict) and actor.get("source"):
        safe["actor_source"] = str(actor["source"])

    if event == "user" and "text" in data:
        safe["text_length"] = len(str(data.get("text") or ""))
    if event == "final" and "text" in data:
        safe["text_length"] = len(str(data.get("text") or ""))

    args = data.get("args")
    if isinstance(args, dict):
        safe["argument_keys"] = sorted(str(key) for key in args.keys())

    usage = data.get("usage")
    if isinstance(usage, dict):
        safe["usage"] = {
            key: int(value)
            for key, value in usage.items()
            if key in {"input_tokens", "output_tokens", "total_tokens"} and isinstance(value, (int, float))
        }

    experts = data.get("experts")
    if isinstance(experts, list):
        safe["experts"] = [str(item) for item in experts[:12]]

    issues = data.get("issues")
    if isinstance(issues, list):
        safe["issue_count"] = len(issues)

    artifact = data.get("artifact")
    if isinstance(artifact, dict):
        safe["artifact_keys"] = sorted(str(key) for key in artifact.keys())[:30]

    return safe


def _otel_event_attributes(data: dict[str, Any]) -> dict[str, Any]:
    attributes: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            attributes[key] = json.dumps(value, ensure_ascii=True, sort_keys=True)
        elif isinstance(value, list):
            attributes[key] = tuple(str(item) for item in value)
        elif isinstance(value, (str, bool, int, float)):
            attributes[key] = value
    return attributes


def _set_span_status(span: Any, status_name: str, description: str | None = None) -> None:
    try:
        from opentelemetry.trace import Status, StatusCode

        code = StatusCode.OK if status_name == "OK" else StatusCode.ERROR
        span.set_status(Status(code, description))
    except ImportError:
        span.set_status(status_name)


@contextmanager
def agent_trace(
    *,
    workspace_id: str,
    conversation_id: str | None,
    actor: dict[str, Any] | None,
    tracer: Any | None = None,
):
    if tracer is None:
        try:
            from opentelemetry import trace

            tracer = trace.get_tracer("dataforge.agent")
        except Exception:
            yield None
            return

    with tracer.start_as_current_span("create_agent DataForge") as span:
        span.set_attribute("gen_ai.operation.name", "create_agent")
        span.set_attribute("gen_ai.agent.id", _foundry_runtime_agent_id())
        span.set_attribute("gen_ai.agent.name", "DataForge")
        span.set_attribute("dataforge.workspace.id", workspace_id)
        span.set_attribute("dataforge.workspace.hash", _trace_identifier_hash(workspace_id))
        if conversation_id:
            span.set_attribute("gen_ai.conversation.id", conversation_id)
            span.set_attribute("dataforge.run.hash", _trace_identifier_hash(conversation_id))
        correlation_id = _span_correlation_id(span)
        if correlation_id:
            span.set_attribute("dataforge.correlation.hash", _trace_identifier_hash(correlation_id))
        actor_id = _actor_fingerprint(actor)
        if actor_id:
            span.set_attribute("enduser.id", actor_id)

        gateway_headers = {"x-dataforge-workspace-hash": _trace_identifier_hash(workspace_id)}
        if conversation_id:
            gateway_headers["x-dataforge-run-hash"] = _trace_identifier_hash(conversation_id)
        if correlation_id:
            gateway_headers["x-dataforge-correlation-id"] = correlation_id
        if isinstance(actor, dict):
            identity = str(actor.get("actor_id") or actor.get("email") or actor.get("id") or actor.get("name") or "").strip().lower()
            if identity:
                gateway_headers["x-dataforge-actor-hash"] = _trace_identifier_hash(identity)

        token = _CURRENT_AGENT_SPAN.set(span)
        gateway_token = _CURRENT_GATEWAY_HEADERS.set(gateway_headers)
        try:
            yield span
        except Exception as exc:
            try:
                span.record_exception(exc)
                _set_span_status(span, "ERROR", type(exc).__name__)
            except Exception:
                pass
            raise
        else:
            try:
                _set_span_status(span, "OK")
            except Exception:
                pass
        finally:
            record_local_span_emit(workspace_id, conversation_id, _span_correlation_id(span))
            _CURRENT_GATEWAY_HEADERS.reset(gateway_token)
            _CURRENT_AGENT_SPAN.reset(token)


def start_maf_agent_span(
    *,
    agent_id: str,
    agent_name: str,
    collaboration_mode: str,
    branch_id: str | None,
    workspace_id: str,
    conversation_id: str | None,
    run_id: str | None,
    actor: dict[str, Any] | None,
    handoff_source: str | None = None,
    handoff_target: str | None = None,
    tracer: Any | None = None,
) -> Any | None:
    """Start a detached participant span when its live start event arrives."""
    if tracer is None:
        try:
            from opentelemetry import trace

            tracer = trace.get_tracer("dataforge.maf.agent")
        except Exception:
            return None

    span = tracer.start_span(f"invoke_agent {agent_id}")
    span.set_attribute("gen_ai.operation.name", "invoke_agent")
    span.set_attribute("gen_ai.agent.id", _foundry_runtime_agent_id())
    span.set_attribute("gen_ai.agent.name", agent_name)
    span.set_attribute("dataforge.maf.agent.id", agent_id)
    span.set_attribute("dataforge.maf.collaboration_mode", collaboration_mode)
    span.set_attribute("dataforge.workspace.id", workspace_id)
    span.set_attribute("dataforge.workspace.hash", _trace_identifier_hash(workspace_id))
    span.set_attribute("dataforge.maf.status", "running")
    if branch_id:
        span.set_attribute("dataforge.maf.branch_id", branch_id)
    if conversation_id:
        span.set_attribute("gen_ai.conversation.id", conversation_id)
    if run_id:
        span.set_attribute("dataforge.run.id", run_id)
        span.set_attribute("dataforge.run.hash", _trace_identifier_hash(run_id))
    correlation_id = _span_correlation_id(span)
    if correlation_id:
        span.set_attribute("dataforge.correlation.hash", _trace_identifier_hash(correlation_id))
    if handoff_source:
        span.set_attribute("dataforge.maf.handoff.source", handoff_source)
    if handoff_target:
        span.set_attribute("dataforge.maf.handoff.target", handoff_target)
    actor_id = _actor_fingerprint(actor)
    if actor_id:
        span.set_attribute("enduser.id", actor_id)
    return span


def finish_maf_agent_span(
    span: Any | None,
    *,
    status: str,
    error_category: str | None,
    duration_ms: float | None,
    token_usage: dict[str, Any] | None,
    response_id: str | None = None,
    retry_count: int | None = None,
    tool_names: list[str] | tuple[str, ...] = (),
    cache_hit: bool | None = None,
) -> None:
    """Finish a detached participant span from its matching live terminal event."""
    if span is None:
        return
    safe_status = status if status in {"completed", "failed"} else "failed"
    span.set_attribute("dataforge.maf.status", safe_status)
    safe_error = error_category if error_category in {"transient", "content_policy", "contract_validation", "permanent"} else None
    if safe_error:
        span.set_attribute("dataforge.maf.error_category", safe_error)
    if duration_ms is not None:
        span.set_attribute("dataforge.maf.duration_ms", max(0.0, float(duration_ms)))
    if retry_count is not None:
        span.set_attribute("dataforge.maf.retry_count", max(0, min(100, int(retry_count))))
    safe_response_id = str(response_id or "").strip()
    if safe_response_id and len(safe_response_id) <= 128 and _SAFE_TELEMETRY_NAME.fullmatch(safe_response_id):
        span.set_attribute("gen_ai.response.id", safe_response_id)
    safe_tool_names = tuple(
        name
        for item in tool_names[:12]
        if (name := str(item).strip())
        and len(name) <= 80
        and _SAFE_TELEMETRY_NAME.fullmatch(name)
    )
    if safe_tool_names:
        span.set_attribute("dataforge.maf.tool_names", safe_tool_names)
    if isinstance(cache_hit, bool):
        span.set_attribute("dataforge.maf.cache_hit", cache_hit)
    usage = token_usage if isinstance(token_usage, dict) else {}
    for source, target in (
        ("input_tokens", "gen_ai.usage.input_tokens"),
        ("output_tokens", "gen_ai.usage.output_tokens"),
        ("total_tokens", "gen_ai.usage.total_tokens"),
    ):
        value = usage.get(source)
        if isinstance(value, (int, float)):
            span.set_attribute(target, max(0, int(value)))
    try:
        _set_span_status(span, "ERROR" if safe_status == "failed" else "OK", safe_error)
    finally:
        span.end()


@contextmanager
def maf_agent_trace(
    *,
    agent_id: str,
    agent_name: str,
    collaboration_mode: str,
    branch_id: str | None,
    workspace_id: str,
    conversation_id: str | None,
    run_id: str | None,
    actor: dict[str, Any] | None,
    handoff_source: str | None = None,
    handoff_target: str | None = None,
    duration_ms: float | None = None,
    started_ns: int | None = None,
    completed_ns: int | None = None,
    response_id: str | None = None,
    token_usage: dict[str, Any] | None = None,
    retry_count: int | None = None,
    tool_names: list[str] | tuple[str, ...] = (),
    cache_hit: bool | None = None,
    status: str = "completed",
    error_category: str | None = None,
    tracer: Any | None = None,
):
    """Emit one content-free span for a participant observed in a MAF run."""
    if tracer is None:
        try:
            from opentelemetry import trace

            tracer = trace.get_tracer("dataforge.maf.agent")
        except Exception:
            yield None
            return

    with tracer.start_as_current_span(f"invoke_agent {agent_id}") as span:
        span.set_attribute("gen_ai.operation.name", "invoke_agent")
        span.set_attribute("gen_ai.agent.id", _foundry_runtime_agent_id())
        span.set_attribute("gen_ai.agent.name", agent_name)
        span.set_attribute("dataforge.maf.agent.id", agent_id)
        span.set_attribute("dataforge.maf.collaboration_mode", collaboration_mode)
        span.set_attribute("dataforge.workspace.id", workspace_id)
        span.set_attribute("dataforge.workspace.hash", _trace_identifier_hash(workspace_id))
        if retry_count is not None:
            span.set_attribute("dataforge.maf.retry_count", max(0, min(100, int(retry_count))))
        safe_status = status if status in {"completed", "failed"} else "unknown"
        span.set_attribute("dataforge.maf.status", safe_status)
        safe_error = error_category if error_category in {"transient", "content_policy", "contract_validation", "permanent"} else None
        if safe_error:
            span.set_attribute("dataforge.maf.error_category", safe_error)
        if branch_id:
            span.set_attribute("dataforge.maf.branch_id", branch_id)
        if conversation_id:
            span.set_attribute("gen_ai.conversation.id", conversation_id)
        if run_id:
            span.set_attribute("dataforge.run.id", run_id)
            span.set_attribute("dataforge.run.hash", _trace_identifier_hash(run_id))
        correlation_id = _span_correlation_id(span)
        if correlation_id:
            span.set_attribute("dataforge.correlation.hash", _trace_identifier_hash(correlation_id))
        if handoff_source:
            span.set_attribute("dataforge.maf.handoff.source", handoff_source)
        if handoff_target:
            span.set_attribute("dataforge.maf.handoff.target", handoff_target)
        if duration_ms is not None:
            span.set_attribute("dataforge.maf.duration_ms", max(0.0, float(duration_ms)))
        if started_ns is not None:
            span.set_attribute("dataforge.maf.started_ns", max(0, int(started_ns)))
        if completed_ns is not None:
            span.set_attribute("dataforge.maf.completed_ns", max(0, int(completed_ns)))
        safe_response_id = str(response_id or "").strip()
        if safe_response_id and len(safe_response_id) <= 128 and _SAFE_TELEMETRY_NAME.fullmatch(safe_response_id):
            span.set_attribute("gen_ai.response.id", safe_response_id)
        safe_tool_names = tuple(
            name
            for item in tool_names[:12]
            if (name := str(item).strip())
            and len(name) <= 80
            and _SAFE_TELEMETRY_NAME.fullmatch(name)
        )
        if safe_tool_names:
            span.set_attribute("dataforge.maf.tool_names", safe_tool_names)
        if isinstance(cache_hit, bool):
            span.set_attribute("dataforge.maf.cache_hit", cache_hit)
        usage = token_usage if isinstance(token_usage, dict) else {}
        for source, target in (
            ("input_tokens", "gen_ai.usage.input_tokens"),
            ("output_tokens", "gen_ai.usage.output_tokens"),
            ("total_tokens", "gen_ai.usage.total_tokens"),
        ):
            value = usage.get(source)
            if isinstance(value, (int, float)):
                span.set_attribute(target, max(0, int(value)))
        actor_id = _actor_fingerprint(actor)
        if actor_id:
            span.set_attribute("enduser.id", actor_id)

        token = _CURRENT_AGENT_SPAN.set(span)
        try:
            yield span
        except Exception as exc:
            try:
                span.record_exception(exc)
                _set_span_status(span, "ERROR", type(exc).__name__)
            except Exception:
                pass
            raise
        else:
            try:
                _set_span_status(span, "ERROR" if safe_status == "failed" else "OK", safe_error)
            except Exception:
                pass
        finally:
            _CURRENT_AGENT_SPAN.reset(token)


def configure_monitoring() -> None:
    connection_string = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not connection_string:
        return
    try:
        from microsoft.opentelemetry import use_microsoft_opentelemetry

        os.environ.setdefault("AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING", "true")
        os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "gen_ai_latest_experimental")
        use_microsoft_opentelemetry(
            enable_azure_monitor=True,
            azure_monitor_connection_string=connection_string,
            sampling_ratio=1.0,
            instrumentation_options={
                "fastapi": {"enabled": False},
                "agent-framework": {
                    "enabled": True,
                    "agent_id": _foundry_runtime_agent_id(),
                    "agent_name": "DataForge",
                },
            },
        )
        LOGGER.info("dataforge_foundry_monitoring_configured")
    except Exception as exc:
        LOGGER.warning("dataforge_monitoring_unavailable %s", type(exc).__name__)


def trace_event(event: str, data: Any, conversation_id: str | None = None) -> None:
    safe_data = _telemetry_event_data(event, data)
    payload = {
        "event": event,
        "conversation_id": conversation_id,
        "data": safe_data,
    }
    span = _CURRENT_AGENT_SPAN.get()
    if span is not None and getattr(span, "is_recording", lambda: False)():
        if conversation_id:
            span.set_attribute("gen_ai.conversation.id", conversation_id)
        span.add_event(f"dataforge.{event}", attributes=_otel_event_attributes(safe_data))
    LOGGER.info("dataforge_trace %s", json.dumps(payload, ensure_ascii=False, default=str))
