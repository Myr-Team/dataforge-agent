from __future__ import annotations

import asyncio
import json
from collections import defaultdict, deque
from typing import Any

import pytest
from agent_framework import FunctionalWorkflow, Workflow

from backend.maf_contracts import CollaborationPattern
from backend.maf_team_runtime import (
    MafTeamRequest,
    MafTeamRuntime,
    TransientAgentError,
    select_collaboration_plan,
)


class FakeAgent:
    def __init__(self, agent_id: str, registry: "FakeRegistry") -> None:
        self.id = agent_id
        self.name = agent_id
        self._registry = registry

    async def run(self, payload: str) -> Any:
        self._registry.calls.append(self.id)
        self._registry.inputs[self.id].append(json.loads(payload))
        await asyncio.sleep(self._registry.delays[self.id])
        failure = self._registry.failures.get(self.id)
        if failure is not None:
            raise failure
        queued = self._registry.outputs[self.id]
        return queued.popleft() if queued else {"agent": self.id}


class FakeRegistry:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.inputs: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.delays: dict[str, float] = defaultdict(lambda: 0.005)
        self.failures: dict[str, Exception] = {}
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
        self.failures[agent_id] = error

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
async def test_optional_market_failure_degrades_without_losing_corpus(fake_registry: FakeRegistry):
    fake_registry.fail("df-market-researcher", TransientAgentError("timeout"))

    result = await MafTeamRuntime(fake_registry).run(concurrent_request())

    assert result.degraded is True
    assert result.artifact["hits"]
    assert "external_signal_unavailable" in result.gaps
    assert result.artifact["strong_verdict_allowed"] is True


@pytest.mark.asyncio
async def test_required_corpus_failure_prevents_stronger_verdict(fake_registry: FakeRegistry):
    fake_registry.fail("df-corpus-analyst", RuntimeError("corpus unavailable"))

    result = await MafTeamRuntime(fake_registry).run(concurrent_request())

    assert result.degraded is True
    assert "workspace_evidence_unavailable" in result.gaps
    assert result.artifact["strong_verdict_allowed"] is False
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
async def test_review_loop_stops_at_two_revisions(fake_registry: FakeRegistry):
    fake_registry.outputs["df-auditor"].clear()
    fake_registry.audit_verdicts("revise", "revise", "revise")

    result = await MafTeamRuntime(fake_registry, max_revisions=2).run(review_request())

    assert result.summary.rounds == 2
    assert fake_registry.calls.count("df-feasibility-analyst") == 3
    assert fake_registry.calls.count("df-auditor") == 3
    assert result.events[-1].event == "maf_review"
