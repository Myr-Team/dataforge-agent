from __future__ import annotations

import asyncio
import json
from collections import defaultdict, deque
from typing import Any

import pytest
from agent_framework import (
    AgentResponse,
    AgentResponseUpdate,
    AgentSession,
    FunctionalWorkflow,
    ResponseStream,
    Workflow,
)

from backend import maf_team_runtime
from backend.maf_contracts import CollaborationPattern
from backend.maf_team_runtime import (
    MafRuntimeEvent,
    MafTeamRequest,
    MafTeamRuntime,
    TransientAgentError,
    select_collaboration_plan,
)


class FakeAgent:
    def __init__(self, agent_id: str, registry: "FakeRegistry") -> None:
        self.id = agent_id
        self.name = agent_id
        self.description = f"Fake {agent_id}"
        self._registry = registry

    @staticmethod
    def _payload_text(messages: Any) -> str:
        if isinstance(messages, str):
            return messages
        if isinstance(messages, list) and messages:
            return str(getattr(messages[-1], "text", "{}") or "{}")
        return "{}"

    async def _execute(self, payload: str) -> dict[str, Any]:
        self._registry.calls.append(self.id)
        self._registry.inputs[self.id].append(json.loads(payload))
        await asyncio.sleep(self._registry.delays[self.id])
        failures = self._registry.failures[self.id]
        failure = failures.popleft() if failures else None
        if failure:
            raise failure
        queued = self._registry.outputs[self.id]
        return queued.popleft() if queued else {"agent": self.id}

    def run(self, messages: Any = None, *, stream: bool = False, **_kwargs: Any) -> Any:
        payload = self._payload_text(messages)
        if not stream:
            async def complete() -> AgentResponse[dict[str, Any]]:
                output = await self._execute(payload)
                return AgentResponse(messages=[], agent_id=self.id, value=output)

            return complete()

        holder: dict[str, dict[str, Any]] = {}

        async def updates():
            holder["output"] = await self._execute(payload)
            yield AgentResponseUpdate(agent_id=self.id)

        def finalize(_updates: Any) -> AgentResponse[dict[str, Any]]:
            return AgentResponse(messages=[], agent_id=self.id, value=holder["output"])

        return ResponseStream(updates(), finalizer=finalize)

    def create_session(self, *, session_id: str | None = None) -> AgentSession:
        return AgentSession(session_id=session_id)

    def get_session(
        self,
        service_session_id: str,
        *,
        session_id: str | None = None,
    ) -> AgentSession:
        return AgentSession(service_session_id=service_session_id, session_id=session_id)


class FakeRegistry:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.inputs: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.delays: dict[str, float] = defaultdict(lambda: 0.005)
        self.failures: dict[str, deque[Exception | None]] = defaultdict(deque)
        self.outputs: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
        self._agents = {
            agent_id: FakeAgent(agent_id, self)
            for agent_id in (
                "df-coordinator",
                "df-corpus-analyst",
                "df-market-researcher",
                "df-feasibility-analyst",
                "df-auditor",
                "df-producer",
            )
        }

    def agent(self, agent_id: str) -> FakeAgent:
        return self._agents[agent_id]

    def ids(self) -> tuple[str, ...]:
        return tuple(self._agents)

    def fail(self, agent_id: str, error: Exception) -> None:
        self.failures[agent_id].append(error)

    def fail_on_call(self, agent_id: str, call_number: int, error: Exception) -> None:
        self.failures[agent_id].extend([None] * (call_number - 1))
        self.failures[agent_id].append(error)

    def audit_verdicts(self, *verdicts: str) -> None:
        self.outputs["df-auditor"].extend({"verdict": verdict} for verdict in verdicts)


@pytest.fixture
def fake_registry() -> FakeRegistry:
    registry = FakeRegistry()
    registry.outputs["df-corpus-analyst"].append({"hits": [{"id": "workspace-1"}]})
    registry.outputs["df-market-researcher"].append({"signals": [{"id": "market-1"}]})
    registry.outputs["df-feasibility-analyst"].append({"verdict": "supported"})
    registry.outputs["df-auditor"].append({"verdict": "pass"})
    return registry


def concurrent_request() -> MafTeamRequest:
    return MafTeamRequest(
        intent="feasibility_analysis",
        output_mode="report",
        needs_workspace=True,
        needs_external=True,
        high_impact=True,
        payload={"workspace_id": "workspace-1", "query": "evaluate"},
    )


def review_request() -> MafTeamRequest:
    return MafTeamRequest(
        intent="feasibility_analysis",
        output_mode="report",
        needs_workspace=True,
        needs_external=False,
        high_impact=True,
        payload={"workspace_id": "workspace-1", "query": "material decision"},
    )


def verdict_values(value: Any) -> list[str]:
    if isinstance(value, dict):
        found = [str(item) for key, item in value.items() if key == "verdict" or key.endswith("_verdict")]
        for item in value.values():
            found.extend(verdict_values(item))
        return found
    if isinstance(value, list):
        found = []
        for item in value:
            found.extend(verdict_values(item))
        return found
    return []


def test_simple_question_selects_direct_without_specialists():
    plan = select_collaboration_plan(
        intent="qa",
        output_mode="chat",
        needs_workspace=True,
        needs_external=False,
        high_impact=False,
    )

    assert plan.pattern is CollaborationPattern.DIRECT
    assert plan.selected_agents == ("df-coordinator",)


def test_selector_uses_only_normalized_semantic_fields():
    first = select_collaboration_plan(
        intent="feasibility_analysis",
        output_mode="report",
        needs_workspace=True,
        needs_external=True,
        high_impact=False,
    )
    second = select_collaboration_plan(
        intent="feasibility_analysis",
        output_mode="report",
        needs_workspace=True,
        needs_external=True,
        high_impact=False,
    )

    assert first == second
    assert first.pattern is CollaborationPattern.CONCURRENT_RESEARCH


def test_high_impact_concurrent_plan_carries_bounded_review():
    plan = select_collaboration_plan(
        intent="feasibility_analysis",
        output_mode="report",
        needs_workspace=True,
        needs_external=True,
        high_impact=True,
    )

    assert plan.pattern is CollaborationPattern.CONCURRENT_RESEARCH
    assert plan.max_revisions == 2


@pytest.mark.asyncio
async def test_direct_path_invokes_only_registry_coordinator(fake_registry: FakeRegistry):
    request = MafTeamRequest(
        intent="qa",
        output_mode="chat",
        needs_workspace=True,
        needs_external=False,
        high_impact=False,
        payload={"query": "summarize"},
    )

    runtime = MafTeamRuntime(fake_registry)
    result = await runtime.run(request)

    assert isinstance(runtime.last_workflow, FunctionalWorkflow)
    assert fake_registry.calls == ["df-coordinator"]
    assert result.summary.mode == "direct"


@pytest.mark.asyncio
async def test_unknown_agent_telemetry_is_omitted(fake_registry: FakeRegistry):
    request = MafTeamRequest(
        intent="qa",
        output_mode="chat",
        needs_workspace=True,
        needs_external=False,
        high_impact=False,
        payload={"query": "summarize"},
    )

    result = await MafTeamRuntime(fake_registry).run(request)

    completed = next(event for event in result.events if event.event == "maf_agent_completed")
    payload = completed.model_dump(mode="json", exclude_none=True)
    for key in (
        "response_id",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "retry_count",
        "tool_names",
        "cache_hit",
    ):
        assert key not in payload


def test_typed_agent_telemetry_rejects_unsafe_identifiers() -> None:
    with pytest.raises(ValueError):
        MafRuntimeEvent(
            sequence=1,
            event="maf_agent_completed",
            status="completed",
            agent_id="df-coordinator",
            response_id="person@example.com",
        )
    with pytest.raises(ValueError):
        MafRuntimeEvent(
            sequence=1,
            event="maf_agent_completed",
            status="completed",
            agent_id="df-coordinator",
            tool_names=("search_pack_context", "AccountKey=secret"),
        )


@pytest.mark.asyncio
async def test_completed_agent_event_carries_only_bounded_safe_telemetry(fake_registry: FakeRegistry):
    secret_prompt = "private customer prompt"
    secret_email = "person@example.com"
    fake_registry.outputs["df-coordinator"].append(
        {
            "answer": "bounded result",
            "_llm": {
                "response_id": "resp-safe-1",
                "usage": {"input_tokens": 21, "output_tokens": 8, "total_tokens": 29},
                "retry_count": 2,
                "tool_names": [
                    "search_pack_context",
                    "render_pdf_report",
                    secret_email,
                    *[f"tool_{index}" for index in range(20)],
                ],
                "cache": {"hit": True},
                "prompt": secret_prompt,
                "evidence": "private evidence row",
                "credentials": "AccountKey=secret",
                "email": secret_email,
            },
        }
    )
    request = MafTeamRequest(
        intent="qa",
        output_mode="chat",
        needs_workspace=True,
        needs_external=False,
        high_impact=False,
        payload={"query": secret_prompt},
    )

    result = await MafTeamRuntime(fake_registry).run(request)

    completed = next(
        event
        for event in result.events
        if event.event == "maf_agent_completed" and event.agent_id == "df-coordinator"
    )
    assert completed.response_id == "resp-safe-1"
    assert completed.input_tokens == 21
    assert completed.output_tokens == 8
    assert completed.total_tokens == 29
    assert completed.retry_count == 2
    assert completed.tool_names[:2] == ("search_pack_context", "render_pdf_report")
    assert len(completed.tool_names) == 12
    assert completed.cache_hit is True
    assert completed.started_ns is not None
    assert completed.completed_ns is not None
    assert completed.completed_ns >= completed.started_ns
    assert completed.duration_ms == pytest.approx(
        (completed.completed_ns - completed.started_ns) / 1_000_000
    )
    serialized = repr(completed)
    for secret in (secret_prompt, secret_email, "private evidence row", "AccountKey=secret"):
        assert secret not in serialized


@pytest.mark.asyncio
async def test_concurrent_branch_completion_carries_safe_telemetry(fake_registry: FakeRegistry):
    fake_registry.outputs["df-corpus-analyst"].clear()
    fake_registry.outputs["df-corpus-analyst"].append(
        {
            "hits": [{"id": "workspace-1"}],
            "_llm": {
                "response_id": "resp-corpus-1",
                "usage": {"input_tokens": 13, "output_tokens": 5, "total_tokens": 18},
                "tool_names": ["search_pack_context"],
                "cache_hit": False,
            },
        }
    )

    result = await MafTeamRuntime(fake_registry).run(concurrent_request())

    completed = next(
        event
        for event in result.events
        if event.event == "maf_agent_completed" and event.agent_id == "df-corpus-analyst"
    )
    assert completed.response_id == "resp-corpus-1"
    assert completed.total_tokens == 18
    assert completed.tool_names == ("search_pack_context",)
    assert completed.cache_hit is False
    assert completed.started_ns is not None
    assert completed.completed_ns is not None
    assert completed.completed_ns >= completed.started_ns


@pytest.mark.asyncio
async def test_internal_and_external_research_run_concurrently(fake_registry: FakeRegistry):
    fake_registry.delays["df-corpus-analyst"] = 0.03
    fake_registry.delays["df-market-researcher"] = 0.03

    runtime = MafTeamRuntime(fake_registry)
    result = await runtime.run(concurrent_request())

    assert isinstance(runtime.last_workflow, FunctionalWorkflow)
    assert isinstance(runtime.last_pattern_workflow, Workflow)
    assert result.summary.mode == "concurrent_research"
    assert result.branch_overlap_ms > 10
    assert result.completed_agents == {
        "df-corpus-analyst",
        "df-market-researcher",
        "df-feasibility-analyst",
        "df-auditor",
    }


@pytest.mark.asyncio
async def test_isolated_branch_builders_receive_registry_agents(
    fake_registry: FakeRegistry,
    monkeypatch,
):
    actual_builder = maf_team_runtime.SequentialBuilder
    captured: list[Any] = []

    class RecordingBuilder:
        def __init__(self, *, participants, **kwargs):
            captured.extend(participants)
            self._builder = actual_builder(participants=participants, **kwargs)

        def build(self):
            return self._builder.build()

    monkeypatch.setattr(maf_team_runtime, "SequentialBuilder", RecordingBuilder)

    await MafTeamRuntime(fake_registry).run(concurrent_request())

    assert captured == [
        fake_registry.agent("df-corpus-analyst"),
        fake_registry.agent("df-market-researcher"),
    ]


@pytest.mark.asyncio
async def test_runtime_events_are_typed_and_strictly_ordered(fake_registry: FakeRegistry):
    result = await MafTeamRuntime(fake_registry).run(concurrent_request())

    assert [event.sequence for event in result.events] == list(range(1, len(result.events) + 1))
    assert result.events[0].event == "maf_plan"
    for agent_id in result.completed_agents:
        started = next(
            event.sequence
            for event in result.events
            if event.event == "maf_agent_started" and event.agent_id == agent_id
        )
        completed = next(
            event.sequence
            for event in result.events
            if event.event == "maf_agent_completed" and event.agent_id == agent_id
        )
        assert started < completed


@pytest.mark.asyncio
async def test_plan_event_contains_truthful_rendering_metadata(fake_registry: FakeRegistry):
    result = await MafTeamRuntime(fake_registry).run(concurrent_request())

    plan = result.events[0]
    assert plan.event == "maf_plan"
    assert plan.mode == "concurrent_research"
    assert plan.selected_agents == result.summary.selected_agents
    assert plan.skipped_agents == result.summary.skipped_agents
    assert plan.max_revisions == 2


@pytest.mark.asyncio
async def test_optional_market_failure_degrades_without_losing_corpus(fake_registry: FakeRegistry):
    fake_registry.fail("df-market-researcher", TransientAgentError("timeout"))

    result = await MafTeamRuntime(fake_registry).run(concurrent_request())

    assert result.degraded is True
    assert result.artifact["hits"]
    assert "external_signal_unavailable" in result.gaps
    assert result.artifact["strong_verdict_allowed"] is True


@pytest.mark.asyncio
async def test_immediate_market_failure_does_not_cancel_slow_corpus(fake_registry: FakeRegistry):
    fake_registry.delays["df-market-researcher"] = 0
    fake_registry.delays["df-corpus-analyst"] = 0.05
    fake_registry.fail("df-market-researcher", TransientAgentError("market unavailable"))

    result = await MafTeamRuntime(fake_registry).run(concurrent_request())

    assert result.artifact["hits"] == [{"id": "workspace-1"}]
    assert result.degraded is True
    assert "external_signal_unavailable" in result.gaps
    assert result.branch_overlap_ms > 0
    assert fake_registry.calls.count("df-corpus-analyst") == 1
    assert fake_registry.calls.count("df-market-researcher") == 1

    market_failed = next(
        event.sequence
        for event in result.events
        if event.event == "maf_agent_completed"
        and event.agent_id == "df-market-researcher"
        and event.status == "failed"
    )
    corpus_completed = next(
        event.sequence
        for event in result.events
        if event.event == "maf_agent_completed"
        and event.agent_id == "df-corpus-analyst"
        and event.status == "completed"
    )
    assert market_failed < corpus_completed


@pytest.mark.asyncio
async def test_branch_local_cancellation_is_immediate_and_cleans_up_blocked_sibling(
    fake_registry: FakeRegistry,
    monkeypatch,
):
    fake_registry.fail("df-market-researcher", asyncio.CancelledError())
    fake_registry.delays["df-market-researcher"] = 0.01
    corpus_started = asyncio.Event()
    corpus_cancelled = asyncio.Event()
    never_release = asyncio.Event()
    branch_tasks: list[asyncio.Task[Any]] = []
    observer_tasks: list[asyncio.Task[Any]] = []
    actual_branch = MafTeamRuntime._run_isolated_branch
    actual_observer = MafTeamRuntime._observe_concurrent_branches

    async def blocking_corpus(_payload: str) -> dict[str, Any]:
        corpus_started.set()
        try:
            await never_release.wait()
        finally:
            corpus_cancelled.set()
        return {"hits": []}

    async def tracking_branch(self, *args, **kwargs):
        task = asyncio.current_task()
        assert task is not None
        branch_tasks.append(task)
        return await actual_branch(self, *args, **kwargs)

    async def tracking_observer(self, *args, **kwargs):
        task = asyncio.current_task()
        assert task is not None
        observer_tasks.append(task)
        return await actual_observer(self, *args, **kwargs)

    monkeypatch.setattr(MafTeamRuntime, "_run_isolated_branch", tracking_branch)
    monkeypatch.setattr(MafTeamRuntime, "_observe_concurrent_branches", tracking_observer)
    monkeypatch.setattr(fake_registry.agent("df-corpus-analyst"), "_execute", blocking_corpus)

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(
            MafTeamRuntime(fake_registry).run(concurrent_request()),
            timeout=0.15,
        )

    assert corpus_started.is_set()
    assert corpus_cancelled.is_set()
    assert len(branch_tasks) == 2
    assert len(observer_tasks) == 1
    assert all(task.done() for task in [*branch_tasks, *observer_tasks])


@pytest.mark.asyncio
async def test_native_branch_failures_emit_in_arrival_order(fake_registry: FakeRegistry):
    fake_registry.delays["df-market-researcher"] = 0
    fake_registry.delays["df-corpus-analyst"] = 0.04
    fake_registry.fail("df-market-researcher", TransientAgentError("market unavailable"))
    fake_registry.fail("df-corpus-analyst", RuntimeError("corpus unavailable"))

    result = await MafTeamRuntime(fake_registry).run(concurrent_request())

    failures = [
        event.agent_id
        for event in result.events
        if event.event == "maf_agent_completed" and event.status == "failed"
    ]
    assert failures[:2] == ["df-market-researcher", "df-corpus-analyst"]
    assert failures.count("df-market-researcher") == 1
    assert failures.count("df-corpus-analyst") == 1


@pytest.mark.asyncio
async def test_required_corpus_failure_prevents_stronger_verdict(fake_registry: FakeRegistry):
    fake_registry.outputs["df-feasibility-analyst"].clear()
    fake_registry.outputs["df-feasibility-analyst"].append(
        {"verdict": "supported", "detail": {"feasibility_verdict": "strong_go"}}
    )
    fake_registry.outputs["df-auditor"].clear()
    fake_registry.outputs["df-auditor"].append(
        {"verdict": "pass", "checks": [{"audit_verdict": "approved"}]}
    )
    fake_registry.fail("df-corpus-analyst", RuntimeError("corpus unavailable"))

    result = await MafTeamRuntime(fake_registry).run(concurrent_request())

    assert result.degraded is True
    assert "workspace_evidence_unavailable" in result.gaps
    assert result.artifact["strong_verdict_allowed"] is False
    assert result.artifact["verdict"] == "insufficient_evidence"
    assert set(verdict_values(result.artifact)) == {"insufficient_evidence"}


@pytest.mark.asyncio
async def test_high_impact_concurrent_review_honors_revise(fake_registry: FakeRegistry):
    fake_registry.outputs["df-feasibility-analyst"].append(
        {"verdict": "supported", "version": "revised"}
    )
    fake_registry.outputs["df-auditor"].clear()
    fake_registry.audit_verdicts("revise", "pass")

    result = await MafTeamRuntime(fake_registry).run(concurrent_request())

    assert result.summary.rounds == 1
    assert fake_registry.calls.count("df-feasibility-analyst") == 2
    assert fake_registry.calls.count("df-auditor") == 2
    assert result.artifact["feasibility"]["version"] == "revised"
    assert result.artifact["verdict"] == "pass"


@pytest.mark.asyncio
async def test_high_impact_concurrent_review_stops_after_two_revisions(fake_registry: FakeRegistry):
    fake_registry.outputs["df-feasibility-analyst"].extend(
        [
            {"verdict": "supported", "version": "revision-1"},
            {"verdict": "supported", "version": "revision-2"},
        ]
    )
    fake_registry.outputs["df-auditor"].clear()
    fake_registry.audit_verdicts("revise", "revise", "revise")

    result = await MafTeamRuntime(fake_registry).run(concurrent_request())

    assert result.summary.rounds == 2
    assert fake_registry.calls.count("df-feasibility-analyst") == 3
    assert fake_registry.calls.count("df-auditor") == 3
    assert result.artifact["verdict"] == "insufficient_evidence"


@pytest.mark.asyncio
async def test_specialist_handoff_records_real_ownership_transfer(fake_registry: FakeRegistry):
    request = MafTeamRequest(
        intent="corpus_qa",
        output_mode="report",
        needs_workspace=True,
        needs_external=False,
        high_impact=False,
        payload={"query": "grounded details"},
    )

    result = await MafTeamRuntime(fake_registry).run(request)

    assert fake_registry.calls == ["df-coordinator", "df-corpus-analyst"]
    handoff = next(event for event in result.events if event.event == "maf_handoff")
    assert handoff.source_agent_id == "df-coordinator"
    assert handoff.target_agent_id == "df-corpus-analyst"


@pytest.mark.asyncio
async def test_handoff_reason_uses_finite_safe_intent_code(fake_registry: FakeRegistry):
    unsafe_intent = "customer@example.com/custom-secret-intent"
    request = MafTeamRequest(
        intent=unsafe_intent,
        output_mode="report",
        needs_workspace=False,
        needs_external=False,
        high_impact=False,
        payload={"query": "bounded work"},
    )

    result = await MafTeamRuntime(fake_registry).run(request)

    handoffs = [event for event in result.events if event.event == "maf_handoff"]
    assert handoffs
    assert all(event.reason_codes == ("intent:other",) for event in handoffs)
    assert unsafe_intent not in repr(result.events)


@pytest.mark.asyncio
async def test_review_loop_stops_at_two_revisions(fake_registry: FakeRegistry):
    fake_registry.outputs["df-auditor"].clear()
    fake_registry.audit_verdicts("revise", "revise", "revise")

    result = await MafTeamRuntime(fake_registry, max_revisions=2).run(review_request())

    assert result.summary.rounds == 2
    assert fake_registry.calls.count("df-feasibility-analyst") == 3
    assert fake_registry.calls.count("df-auditor") == 3
    assert result.events[-1].event == "maf_review"


@pytest.mark.asyncio
async def test_initial_analyst_failure_cannot_be_passed_by_auditor(fake_registry: FakeRegistry):
    fake_registry.fail("df-feasibility-analyst", RuntimeError("analyst unavailable"))
    fake_registry.outputs["df-auditor"].clear()
    fake_registry.audit_verdicts("pass")

    result = await MafTeamRuntime(fake_registry).run(review_request())

    assert result.degraded is True
    assert result.artifact["verdict"] == "insufficient_evidence"
    assert set(verdict_values(result.artifact)) == {"insufficient_evidence"}
    assert fake_registry.calls.count("df-auditor") == 0


@pytest.mark.asyncio
async def test_revision_failure_preserves_last_valid_artifact_and_forces_insufficient(
    fake_registry: FakeRegistry,
):
    fake_registry.outputs["df-feasibility-analyst"].clear()
    fake_registry.outputs["df-feasibility-analyst"].append(
        {"verdict": "supported", "analysis": "preserve me"}
    )
    fake_registry.fail_on_call(
        "df-feasibility-analyst",
        2,
        RuntimeError("revision unavailable"),
    )
    fake_registry.outputs["df-auditor"].clear()
    fake_registry.audit_verdicts("revise", "pass")

    result = await MafTeamRuntime(fake_registry).run(review_request())

    assert result.degraded is True
    assert result.artifact["analysis"] == "preserve me"
    assert result.artifact["verdict"] == "insufficient_evidence"
    assert set(verdict_values(result.artifact)) == {"insufficient_evidence"}
    assert fake_registry.calls.count("df-auditor") == 1
