from __future__ import annotations

from backend.tracing import _telemetry_event_data, agent_trace, trace_event


class _FakeSpan:
    def __init__(self) -> None:
        self.attributes: dict[str, object] = {}
        self.events: list[tuple[str, dict[str, object]]] = []

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value

    def add_event(self, name: str, attributes: dict[str, object] | None = None) -> None:
        self.events.append((name, attributes or {}))

    def is_recording(self) -> bool:
        return True

    def record_exception(self, _exc: BaseException) -> None:
        return None

    def set_status(self, _status: object) -> None:
        return None


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
