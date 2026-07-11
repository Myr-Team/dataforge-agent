from __future__ import annotations

from opentelemetry.trace import StatusCode

import backend.tracing as tracing
from backend.tracing import _telemetry_event_data, agent_trace, trace_event


class _FakeSpan:
    def __init__(self) -> None:
        self.attributes: dict[str, object] = {}
        self.events: list[tuple[str, dict[str, object]]] = []
        self.statuses: list[object] = []

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value

    def add_event(self, name: str, attributes: dict[str, object] | None = None) -> None:
        self.events.append((name, attributes or {}))

    def is_recording(self) -> bool:
        return True

    def record_exception(self, _exc: BaseException) -> None:
        return None

    def set_status(self, status: object) -> None:
        self.statuses.append(status)


class _SpanContext:
    def __init__(self, span: _FakeSpan) -> None:
        self.span = span

    def __enter__(self) -> _FakeSpan:
        return self.span

    def __exit__(self, *_args: object) -> None:
        return None


class _FakeTracer:
    def __init__(self) -> None:
        self.span = _FakeSpan()

    def start_as_current_span(self, _name: str) -> _SpanContext:
        return _SpanContext(self.span)


def test_telemetry_event_data_redacts_user_and_tool_content() -> None:
    user = _telemetry_event_data(
        "user",
        {
            "text": "private business question",
            "actor": {"name": "Example User", "email": "person@example.com", "source": "easy_auth"},
        },
    )
    tool = _telemetry_event_data(
        "tool_call",
        {
            "agent": "df-corpus-analyst",
            "name": "search_pack_context",
            "args": {"query": "private query", "connection_string": "secret"},
        },
    )

    assert user["text_length"] == len("private business question")
    assert user["actor_source"] == "easy_auth"
    assert user["actor_id"]
    assert "private business question" not in repr(user)
    assert "person@example.com" not in repr(user)
    assert tool["agent"] == "df-corpus-analyst"
    assert tool["name"] == "search_pack_context"
    assert tool["argument_keys"] == ["connection_string", "query"]
    assert "private query" not in repr(tool)
    assert "secret" not in repr(tool)


def test_model_response_telemetry_keeps_only_bounded_safe_metadata() -> None:
    safe = _telemetry_event_data(
        "model_response",
        {
            "agent": "df-corpus-analyst",
            "response_id": "resp-safe-1",
            "usage": {"input_tokens": 12, "output_tokens": 4, "total_tokens": 16},
            "retry_count": 1,
            "tool_names": ["search_pack_context", "person@example.com"],
            "cache_hit": False,
            "error_category": "permanent",
            "prompt": "private prompt",
            "evidence": "private evidence",
            "credentials": "AccountKey=secret",
            "email": "person@example.com",
        },
    )

    assert safe["retry_count"] == 1
    assert safe["tool_names"] == ["search_pack_context"]
    assert safe["cache_hit"] is False
    assert safe["error_category"] == "permanent"
    combined = repr(safe)
    for secret in ("private prompt", "private evidence", "AccountKey=secret", "person@example.com"):
        assert secret not in combined


def test_agent_trace_emits_foundry_agent_identity_without_raw_actor_email() -> None:
    tracer = _FakeTracer()

    with agent_trace(
        workspace_id="workspace-1",
        conversation_id=None,
        actor={"email": "person@example.com", "source": "easy_auth"},
        tracer=tracer,
    ):
        trace_event(
            "ready",
            {"workspace_id": "workspace-1", "actor": {"email": "person@example.com"}},
            "conversation-1",
        )

    attributes = tracer.span.attributes
    assert attributes["gen_ai.operation.name"] == "create_agent"
    assert attributes["gen_ai.agent.id"] == "dataforge"
    assert attributes["gen_ai.agent.name"] == "DataForge"
    assert attributes["dataforge.workspace.id"] == "workspace-1"
    assert attributes["gen_ai.conversation.id"] == "conversation-1"
    assert attributes["enduser.id"]
    assert "person@example.com" not in repr(attributes)
    assert tracer.span.events[0][0] == "dataforge.ready"
    assert tracer.span.statuses
    assert tracer.span.statuses[-1].status_code is StatusCode.OK


def test_maf_agent_trace_has_redacted_collaboration_attributes() -> None:
    tracer = _FakeTracer()
    raw_message = "private customer prompt"
    actor_email = "person@example.com"

    with tracing.maf_agent_trace(
        agent_id="df-corpus-analyst",
        agent_name="Workspace evidence analyst",
        collaboration_mode="concurrent_research",
        branch_id="workspace",
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        run_id="run-1",
        actor={"email": actor_email, "source": "easy_auth"},
        handoff_source="df-coordinator",
        handoff_target="df-corpus-analyst",
        duration_ms=42,
        started_ns=1_000_000,
        completed_ns=43_000_000,
        token_usage={"input_tokens": 12, "output_tokens": 8, "total_tokens": 20},
        retry_count=1,
        tool_names=["search_pack_context"],
        cache_hit=True,
        status="completed",
        error_category=None,
        tracer=tracer,
    ):
        trace_event("maf_agent_completed", {"agent": "df-corpus-analyst", "message": raw_message})

    attributes = tracer.span.attributes
    assert attributes["gen_ai.agent.id"] == "df-corpus-analyst"
    assert attributes["gen_ai.agent.name"] == "Workspace evidence analyst"
    assert attributes["dataforge.maf.collaboration_mode"] == "concurrent_research"
    assert attributes["dataforge.maf.branch_id"] == "workspace"
    assert attributes["dataforge.maf.handoff.source"] == "df-coordinator"
    assert attributes["dataforge.maf.handoff.target"] == "df-corpus-analyst"
    assert attributes["dataforge.maf.status"] == "completed"
    assert attributes["dataforge.maf.cache_hit"] is True
    assert attributes["dataforge.maf.started_ns"] == 1_000_000
    assert attributes["dataforge.maf.completed_ns"] == 43_000_000
    assert attributes["enduser.id"]
    assert tracer.span.statuses[-1].status_code is StatusCode.OK
    combined = repr((attributes, tracer.span.events))
    assert raw_message not in combined
    assert actor_email not in combined
