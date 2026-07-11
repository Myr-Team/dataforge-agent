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
                from opentelemetry.trace.status import Status, StatusCode

                span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            except Exception:
                pass
            raise
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
