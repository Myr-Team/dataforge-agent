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
    AuthoritativeCorpus,
    FeasibilityRubric,
    MafRuntimeEvent,
    MafTeamRequest,
    MafTeamRuntime,
    TransientAgentError,
    classify_agent_error,
    classify_workflow_error,
    select_collaboration_plan,
)
from backend.schemas import FeasibilityReport, MarketComparison


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
                response_kwargs = self._registry.response_kwargs[self.id]
                return AgentResponse(
                    messages=[],
                    agent_id=self.id,
                    value=output,
                    **(response_kwargs.popleft() if response_kwargs else {}),
                )

            return complete()

        holder: dict[str, Any] = {}

        async def updates():
            holder["output"] = await self._execute(payload)
            response_kwargs = self._registry.response_kwargs[self.id]
            holder["response_kwargs"] = response_kwargs.popleft() if response_kwargs else {}
            yield AgentResponseUpdate(agent_id=self.id)

        def finalize(_updates: Any) -> AgentResponse[dict[str, Any]]:
            return AgentResponse(
                messages=[],
                agent_id=self.id,
                value=holder["output"],
                **holder["response_kwargs"],
            )

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
        self.response_kwargs: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
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
    registry.outputs["df-market-researcher"].append(
        {
            "opportunity_id": "retention-workflow",
            "competitors": [
                {
                    "name": "Retention Cloud",
                    "positioning": "Automated retention workflows",
                    "url": "https://example.com/retention-cloud",
                }
            ],
            "positioning_note": "Differentiate with workspace-confirmed evidence.",
            "_llm": {"mode": "foundry_market_agent"},
        }
    )
    registry.outputs["df-feasibility-analyst"].append({"verdict": "supported"})
    registry.outputs["df-auditor"].append({"verdict": "pass"})
    return registry


def authoritative_context() -> dict[str, Any]:
    hit = {
        "id": "workspace-1-row-1",
        "source_file": "evidence.csv",
        "chunk_id": "row-1",
        "content": "Observed retention improved from 70% to 82%.",
    }
    return {
        "authoritative_corpus": {
            "hits": [hit],
            "profile": {
                "asset_evidence": [
                    {
                        "source_type": "corpus",
                        "ref": "evidence.csv#row-1",
                        "quote": hit["content"],
                    }
                ]
            },
            "opportunities": [],
        },
        "evidence_catalog": [
            {
                "source_type": "corpus",
                "ref": "evidence.csv#row-1",
                "quote": hit["content"],
            }
        ],
    }


def feasibility_rubric() -> dict[str, Any]:
    return {
        "rubric_version": "test-rubric-v1",
        "score_scale": {"min": 0, "max": 5, "precision": 1},
        "dimensions": [
            {
                "name": "asset_data",
                "display_name": "Asset data",
                "weight": 1.0,
                "criteria": {"0": "No evidence", "5": "Strong evidence"},
            }
        ],
        "verdict_thresholds": {"conditional": {"weighted_min": 2.0}},
        "confidence_policy": {"speculative": "Use when evidence is thin."},
        "calibration_gate": {"min_spearman": 0.8},
    }


def test_maf_request_types_authoritative_corpus_evidence_and_rubric() -> None:
    context = authoritative_context()

    request = MafTeamRequest(
        intent="feasibility_analysis",
        output_mode="report",
        needs_workspace=True,
        needs_external=False,
        high_impact=True,
        payload={"workspace_id": "workspace-1", "query": "evaluate"},
        rubric=feasibility_rubric(),
        rubric_version="test-rubric-v1",
        **context,
    )

    assert isinstance(request.authoritative_corpus, AuthoritativeCorpus)
    assert request.authoritative_corpus.hits[0].content.startswith("Observed retention")
    assert request.evidence_catalog[0].ref == "evidence.csv#row-1"
    assert isinstance(request.rubric, FeasibilityRubric)
    assert request.rubric.rubric_version == request.rubric_version


def concurrent_request() -> MafTeamRequest:
    return MafTeamRequest(
        intent="feasibility_analysis",
        output_mode="report",
        needs_workspace=True,
        needs_external=True,
        high_impact=True,
        payload={"workspace_id": "workspace-1", "query": "evaluate"},
        **authoritative_context(),
    )


def review_request() -> MafTeamRequest:
    return MafTeamRequest(
        intent="feasibility_analysis",
        output_mode="report",
        needs_workspace=True,
        needs_external=False,
        high_impact=True,
        payload={"workspace_id": "workspace-1", "query": "material decision"},
        **authoritative_context(),
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
    assert plan.required_branches == ("workspace",)


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
    assert plan.required_branches == ("workspace",)


def test_provider_errors_are_classified_from_bounded_runtime_attributes() -> None:
    class ProviderError(RuntimeError):
        def __init__(self, *, status_code: int | None = None, code: str | None = None):
            super().__init__(code or "provider error")
            self.status_code = status_code
            self.code = code

    class WorkflowError:
        error_type = "ServiceRequestError"
        message = "connection timeout"

    class ConnectError(RuntimeError):
        pass

    class ReadTimeout(RuntimeError):
        pass

    class DnsFailure(RuntimeError):
        pass

    assert classify_agent_error(ProviderError(status_code=429)) == "transient"
    assert classify_agent_error(ProviderError(code="ResponsibleAIPolicyViolation")) == "content_policy"
    assert classify_agent_error(ConnectError("socket failed")) == "transient"
    assert classify_agent_error(ReadTimeout("operation exceeded deadline")) == "transient"
    assert classify_agent_error(DnsFailure("getaddrinfo failed")) == "transient"
    assert classify_workflow_error(WorkflowError()) == "transient"


def test_error_diagnostic_exposes_only_bounded_provider_codes() -> None:
    class WorkflowError:
        error_type = "ChatClientException"
        message = (
            "service failed: Error code: 401 - "
            "{'error': {'code': 'PermissionDenied', 'message': 'credential-value'}}"
        )
        traceback = "raw prompt and credential-value must never be emitted"

    diagnostic = maf_team_runtime._safe_error_diagnostic(WorkflowError())

    assert diagnostic == {
        "error_type": "ChatClientException",
        "status_code": 401,
        "provider_code": "PermissionDenied",
    }
    assert "credential-value" not in repr(diagnostic)
    assert "raw prompt" not in repr(diagnostic)


@pytest.mark.asyncio
async def test_direct_path_invokes_only_registry_coordinator(fake_registry: FakeRegistry):
    request = MafTeamRequest(
        intent="qa",
        output_mode="chat",
        needs_workspace=True,
        needs_external=False,
        high_impact=False,
        payload={"query": "summarize"},
        **authoritative_context(),
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
        **authoritative_context(),
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


def test_typed_agent_telemetry_rejects_unsafe_identifiers_and_verdicts() -> None:
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
    with pytest.raises(ValueError):
        MafRuntimeEvent(
            sequence=1,
            event="maf_review",
            status="completed",
            agent_id="df-auditor",
            verdict="approved",
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
    fake_registry.response_kwargs["df-coordinator"].append(
        {
            "response_id": "resp-safe-1",
            "usage_details": {
                "input_token_count": 21,
                "output_token_count": 8,
                "total_token_count": 29,
            },
            "additional_properties": {
                "retry_count": 2,
                "tool_names": [
                    "search_pack_context",
                    "render_pdf_report",
                    secret_email,
                    *[f"tool_{index}" for index in range(20)],
                ],
                "cache_hit": True,
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
        **authoritative_context(),
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
async def test_model_dictionary_telemetry_is_never_trusted(fake_registry: FakeRegistry):
    fake_registry.outputs["df-coordinator"].append(
        {
            "answer": "bounded result",
            "_llm": {
                "response_id": "model-invented-id",
                "usage": {"input_tokens": 999, "output_tokens": 999, "total_tokens": 1998},
                "retry_count": 9,
                "tool_names": ["model_invented_tool"],
                "cache_hit": True,
            },
        }
    )
    request = MafTeamRequest(
        intent="qa",
        output_mode="chat",
        needs_workspace=True,
        needs_external=False,
        high_impact=False,
        payload={"query": "summarize"},
        **authoritative_context(),
    )

    result = await MafTeamRuntime(fake_registry).run(request)

    completed = next(event for event in result.events if event.event == "maf_agent_completed")
    assert completed.response_id is None
    assert completed.input_tokens is None
    assert completed.output_tokens is None
    assert completed.total_tokens is None
    assert completed.retry_count is None
    assert completed.tool_names is None
    assert completed.cache_hit is None


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
    fake_registry.response_kwargs["df-corpus-analyst"].append(
        {
            "response_id": "resp-corpus-1",
            "usage_details": {
                "input_token_count": 13,
                "output_token_count": 5,
                "total_token_count": 18,
            },
            "additional_properties": {
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
async def test_live_event_sink_observes_agent_start_before_execution_finishes(
    fake_registry: FakeRegistry,
    monkeypatch: pytest.MonkeyPatch,
):
    release = asyncio.Event()
    agent_entered = asyncio.Event()
    observed: list[MafRuntimeEvent] = []

    async def blocking_coordinator(_payload: str) -> dict[str, Any]:
        agent_entered.set()
        await release.wait()
        return {"answer": "done"}

    monkeypatch.setattr(fake_registry.agent("df-coordinator"), "_execute", blocking_coordinator)
    request = MafTeamRequest(
        intent="qa",
        output_mode="chat",
        needs_workspace=True,
        needs_external=False,
        high_impact=False,
        payload={"query": "summarize"},
        **authoritative_context(),
    )

    run_task = asyncio.create_task(
        MafTeamRuntime(fake_registry).run(request, event_sink=observed.append)
    )
    try:
        await asyncio.wait_for(agent_entered.wait(), timeout=0.2)
        await asyncio.sleep(0)
        assert any(event.event == "maf_agent_started" for event in observed)
        assert not run_task.done()
    finally:
        release.set()

    result = await run_task
    assert observed == result.events


@pytest.mark.asyncio
async def test_concurrent_event_sink_exposes_both_branch_starts_before_first_completion(
    fake_registry: FakeRegistry,
):
    fake_registry.delays["df-corpus-analyst"] = 0.03
    fake_registry.delays["df-market-researcher"] = 0.03
    observed: list[MafRuntimeEvent] = []

    await MafTeamRuntime(fake_registry).run(
        concurrent_request(),
        event_sink=observed.append,
    )

    branch_starts = [
        index
        for index, event in enumerate(observed)
        if event.event == "maf_agent_started"
        and event.agent_id in {"df-corpus-analyst", "df-market-researcher"}
    ]
    first_completion = next(
        index
        for index, event in enumerate(observed)
        if event.event == "maf_agent_completed"
        and event.agent_id in {"df-corpus-analyst", "df-market-researcher"}
    )
    assert len(branch_starts) == 2
    assert max(branch_starts) < first_completion


@pytest.mark.asyncio
async def test_feasibility_contract_validation_retries_once_then_accepts_typed_output(
    fake_registry: FakeRegistry,
):
    fake_registry.outputs["df-feasibility-analyst"].clear()
    fake_registry.outputs["df-feasibility-analyst"].extend(
        [
            {"verdict": "not-a-feasibility-report"},
            {
                "opportunity_id": "retention-workflow",
                "dimensions": [
                    {
                        "name": "asset_data",
                        "score": 3,
                        "rationale": "The workspace contains a measured retention change.",
                        "evidence": [
                            {
                                "source_type": "corpus",
                                "ref": "evidence.csv#row-1",
                                "quote": "Observed retention improved from 70% to 82%.",
                            }
                        ],
                        "confidence": "data_confirmed",
                    }
                ],
                "verdict": "conditional",
                "overall_confidence": "data_confirmed",
                "gap_list": ["Validate the result in a controlled pilot."],
            },
        ]
    )

    def validate(output: dict[str, Any]) -> dict[str, Any]:
        return FeasibilityReport.model_validate(output).model_dump()

    result = await MafTeamRuntime(
        fake_registry,
        feasibility_validator=validate,
    ).run(review_request())

    assert fake_registry.calls.count("df-feasibility-analyst") == 2
    assert "contract_correction" in fake_registry.inputs["df-feasibility-analyst"][1]
    completed = next(
        event
        for event in result.events
        if event.event == "maf_agent_completed" and event.agent_id == "df-feasibility-analyst"
    )
    assert completed.retry_count == 1
    assert result.artifact["feasibility"]["opportunity_id"] == "retention-workflow"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("output_mode", "high_impact"),
    [("chat", False), ("report", True)],
)
async def test_workspace_required_direct_and_bounded_paths_fail_closed_without_valid_corpus(
    fake_registry: FakeRegistry,
    output_mode: str,
    high_impact: bool,
):
    request = MafTeamRequest(
        intent="feasibility_analysis" if high_impact else "qa",
        output_mode=output_mode,
        needs_workspace=True,
        needs_external=False,
        high_impact=high_impact,
        payload={"workspace_id": "workspace-1", "query": "evaluate"},
        authoritative_corpus={"hits": [], "profile": {"asset_evidence": []}},
        evidence_catalog=[],
    )

    result = await MafTeamRuntime(fake_registry).run(request)

    assert result.degraded is True
    assert result.artifact["verdict"] == "insufficient_evidence"
    assert "workspace_evidence_unavailable" in result.gaps
    assert fake_registry.calls == []


@pytest.mark.asyncio
async def test_successful_corpus_agent_call_is_not_valid_evidence_by_itself(
    fake_registry: FakeRegistry,
):
    request = concurrent_request().model_copy(
        update={
            "authoritative_corpus": AuthoritativeCorpus(
                hits=[],
                profile={"asset_evidence": []},
            ),
            "evidence_catalog": [],
        }
    )

    result = await MafTeamRuntime(fake_registry).run(request)

    assert result.artifact["verdict"] == "insufficient_evidence"
    assert "workspace_evidence_unavailable" in result.gaps
    assert fake_registry.calls == []


@pytest.mark.asyncio
async def test_workspace_evidence_must_trace_to_an_authoritative_hit(
    fake_registry: FakeRegistry,
):
    context = authoritative_context()
    context["evidence_catalog"][0]["ref"] = "unrelated.csv#row-99"
    request = MafTeamRequest(
        intent="qa",
        output_mode="chat",
        needs_workspace=True,
        needs_external=False,
        high_impact=False,
        payload={"workspace_id": "workspace-1", "query": "summarize"},
        **context,
    )

    result = await MafTeamRuntime(fake_registry).run(request)

    assert result.artifact["verdict"] == "insufficient_evidence"
    assert "workspace_evidence_unavailable" in result.gaps
    assert fake_registry.calls == []


@pytest.mark.asyncio
async def test_synthetic_unknown_hit_reference_is_not_authoritative(
    fake_registry: FakeRegistry,
):
    request = MafTeamRequest(
        intent="qa",
        output_mode="chat",
        needs_workspace=True,
        needs_external=False,
        high_impact=False,
        payload={"workspace_id": "workspace-1", "query": "summarize"},
        authoritative_corpus={"hits": [{"content": "Unidentified content."}]},
        evidence_catalog=[
            {
                "source_type": "corpus",
                "ref": "unknown#chunk",
                "quote": "Unidentified content.",
            }
        ],
    )

    result = await MafTeamRuntime(fake_registry).run(request)

    assert result.artifact["verdict"] == "insufficient_evidence"
    assert fake_registry.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_file", "chunk_id", "ref"),
    [
        ("unknown", "row-1", "unknown#row-1"),
        ("untitled.csv", "row-1", "untitled.csv#row-1"),
        ("n/a", "row-1", "n/a#row-1"),
        ("evidence.csv", "chunk", "evidence.csv#chunk"),
        ("evidence.csv", "unknown", "evidence.csv#unknown"),
        ("unknown", "chunk", "unknown#chunk"),
    ],
)
async def test_placeholder_corpus_identities_fail_closed_even_with_matching_quote(
    fake_registry: FakeRegistry,
    source_file: str,
    chunk_id: str,
    ref: str,
):
    request = MafTeamRequest(
        intent="qa",
        output_mode="chat",
        needs_workspace=True,
        needs_external=False,
        high_impact=False,
        payload={"workspace_id": "workspace-1", "query": "summarize"},
        authoritative_corpus={
            "hits": [
                {
                    "id": "apparently-valid-id",
                    "source_file": source_file,
                    "chunk_id": chunk_id,
                    "content": "Matching but untraceable content.",
                }
            ]
        },
        evidence_catalog=[
            {
                "source_type": "corpus",
                "ref": ref,
                "quote": "Matching but untraceable content.",
            }
        ],
    )

    result = await MafTeamRuntime(fake_registry).run(request)

    assert result.artifact["verdict"] == "insufficient_evidence"
    assert "workspace_evidence_unavailable" in result.gaps
    assert fake_registry.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "direct_id",
    [
        "unknown#chunk",
        "unknown:chunk",
        "n/a#0",
        "untitled#row-1",
    ],
)
async def test_placeholder_direct_corpus_ids_fail_closed_even_with_matching_quote(
    fake_registry: FakeRegistry,
    direct_id: str,
):
    request = MafTeamRequest(
        intent="qa",
        output_mode="chat",
        needs_workspace=True,
        needs_external=False,
        high_impact=False,
        payload={"workspace_id": "workspace-1", "query": "summarize"},
        authoritative_corpus={
            "hits": [
                {
                    "id": direct_id,
                    "content": "Matching but placeholder-identified content.",
                }
            ]
        },
        evidence_catalog=[
            {
                "source_type": "corpus",
                "ref": direct_id,
                "quote": "Matching but placeholder-identified content.",
            }
        ],
    )

    result = await MafTeamRuntime(fake_registry).run(request)

    assert result.artifact["verdict"] == "insufficient_evidence"
    assert "workspace_evidence_unavailable" in result.gaps
    assert fake_registry.calls == []


@pytest.mark.asyncio
async def test_market_agent_output_uses_market_comparison_contract_and_reaches_feasibility(
    fake_registry: FakeRegistry,
):
    result = await MafTeamRuntime(fake_registry).run(concurrent_request())

    market = MarketComparison.model_validate(result.artifact["market"])
    assert market.competitors[0].name == "Retention Cloud"
    assert market.positioning_note == "Differentiate with workspace-confirmed evidence."
    assert result.artifact["market"]["_llm"] == {"mode": "foundry_market_agent"}
    assert fake_registry.inputs["df-feasibility-analyst"][0]["market"] == result.artifact["market"]


@pytest.mark.asyncio
async def test_signals_only_market_output_is_rejected_as_contract_invalid(
    fake_registry: FakeRegistry,
):
    fake_registry.outputs["df-market-researcher"].clear()
    fake_registry.outputs["df-market-researcher"].append(
        {"signals": [{"id": "unsupported-shape"}]}
    )

    result = await MafTeamRuntime(fake_registry).run(concurrent_request())

    market_branch = next(item for item in result.branch_results if item.branch_id == "external")
    assert market_branch.status == "failed"
    assert market_branch.error_category == "contract_validation"
    assert "external_signal_unavailable" in result.gaps
    assert "market" not in result.artifact


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_market",
    [
        {
            "opportunity_id": "retention-workflow",
            "competitors": [],
            "positioning_note": "Differentiate with evidence.",
        },
        {
            "opportunity_id": "retention-workflow",
            "competitors": [{"name": " ", "positioning": "Automated retention", "url": "https://example.com"}],
            "positioning_note": "Differentiate with evidence.",
        },
        {
            "opportunity_id": "retention-workflow",
            "competitors": [{"name": "Retention Cloud", "positioning": " ", "url": "https://example.com"}],
            "positioning_note": "Differentiate with evidence.",
        },
        {
            "opportunity_id": "retention-workflow",
            "competitors": [{"name": "Retention Cloud", "positioning": "Automated retention", "url": " "}],
            "positioning_note": " ",
        },
    ],
)
async def test_invalid_market_comparison_degrades_optional_branch(
    fake_registry: FakeRegistry,
    invalid_market: dict[str, Any],
):
    fake_registry.outputs["df-market-researcher"].clear()
    fake_registry.outputs["df-market-researcher"].append(invalid_market)

    result = await MafTeamRuntime(fake_registry).run(concurrent_request())

    market_branch = next(item for item in result.branch_results if item.branch_id == "external")
    assert market_branch.status == "failed"
    assert market_branch.error_category == "contract_validation"
    assert "external_signal_unavailable" in result.gaps
    assert "market" not in result.artifact


@pytest.mark.asyncio
async def test_malformed_authoritative_input_normalizes_to_empty_and_fails_closed(
    fake_registry: FakeRegistry,
):
    request = MafTeamRequest(
        intent="qa",
        output_mode="chat",
        needs_workspace=True,
        needs_external=False,
        high_impact=False,
        payload={"workspace_id": "workspace-1", "query": "summarize"},
        authoritative_corpus={"hits": "not-a-list", "profile": None},
        evidence_catalog=[{"source_type": "corpus", "ref": None, "quote": 123}],
    )

    result = await MafTeamRuntime(fake_registry).run(request)

    assert request.authoritative_corpus.hits == []
    assert request.evidence_catalog == []
    assert result.artifact["verdict"] == "insufficient_evidence"
    assert fake_registry.calls == []


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

    assert result.artifact["hits"] == authoritative_context()["authoritative_corpus"]["hits"]
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
        **authoritative_context(),
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
    assert result.artifact["feasibility"]["analysis"] == "preserve me"
    assert result.artifact["verdict"] == "insufficient_evidence"
    assert set(verdict_values(result.artifact)) == {"insufficient_evidence"}
    assert fake_registry.calls.count("df-auditor") == 1
