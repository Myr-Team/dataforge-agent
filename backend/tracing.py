from __future__ import annotations

import hashlib
import json
import logging
import os
from contextlib import contextmanager
from contextvars import ContextVar
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
        "response_id",
        "revision",
        "status",
        "target_expert",
        "verdict",
        "workspace_id",
    }
    safe: dict[str, Any] = {key: data[key] for key in safe_keys if key in data and data[key] is not None}

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
        span.set_attribute("gen_ai.agent.id", "dataforge")
        span.set_attribute("gen_ai.agent.name", "DataForge")
        span.set_attribute("dataforge.workspace.id", workspace_id)
        if conversation_id:
            span.set_attribute("gen_ai.conversation.id", conversation_id)
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
                _set_span_status(span, "OK")
            except Exception:
                pass
        finally:
            _CURRENT_AGENT_SPAN.reset(token)


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
    token_usage: dict[str, Any] | None = None,
    retry_count: int = 0,
    tool_names: list[str] | tuple[str, ...] = (),
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
        span.set_attribute("gen_ai.agent.id", agent_id)
        span.set_attribute("gen_ai.agent.name", agent_name)
        span.set_attribute("dataforge.maf.collaboration_mode", collaboration_mode)
        span.set_attribute("dataforge.workspace.id", workspace_id)
        span.set_attribute("dataforge.maf.retry_count", max(0, int(retry_count)))
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
        if handoff_source:
            span.set_attribute("dataforge.maf.handoff.source", handoff_source)
        if handoff_target:
            span.set_attribute("dataforge.maf.handoff.target", handoff_target)
        if duration_ms is not None:
            span.set_attribute("dataforge.maf.duration_ms", max(0.0, float(duration_ms)))
        if tool_names:
            span.set_attribute("dataforge.maf.tool_names", tuple(str(name) for name in tool_names))
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
        from azure.monitor.opentelemetry import configure_azure_monitor

        configure_azure_monitor(connection_string=connection_string, logger_name="dataforge.trace")
        LOGGER.info("dataforge_monitoring_configured")
    except Exception as exc:
        LOGGER.warning("dataforge_monitoring_fallback %s", exc)


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
