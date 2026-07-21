from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

import backend.orchestrator as orchestrator
import backend.run_store as run_store
from backend.maf_contracts import MafAgentRecord, MafRuntimeMode
from backend.maf_team_runtime import (
    MafRuntimeEvent,
    MafTeamRunResult,
    RuntimeCollaborationPlan,
    RuntimeMafRunSummary,
)
from backend.run_store import _maf_summary, _normalize_run_detail
from backend.schemas import (
    ChatRequest,
    GuardedFeasibilityReport,
    RoutingDecision,
    RunDetailResponse,
    RunSummary,
)


def _event_names(frames: list[str]) -> list[str]:
    return [frame.splitlines()[0].removeprefix("event: ") for frame in frames]


def _event_payload(frame: str) -> dict[str, Any]:
    return json.loads(frame.splitlines()[1].removeprefix("data: "))


def _decision(*, experts: list[str] | None = None, output_mode: str = "report") -> RoutingDecision:
    return RoutingDecision(
        workspace_id="workspace-1",
        intent="feasibility_analysis",
        experts=experts
        or ["df-corpus-analyst", "df-feasibility-analyst", "df-auditor"],
        output_mode=output_mode,
        needs_clarification=False,
        reason="test route",
    )


def _authoritative_corpus() -> dict[str, Any]:
    content = "Observed retention improved from 70% to 82%."
    return {
        "hits": [
            {
                "id": "workspace-1-row-1",
                "source_file": "evidence.csv",
                "chunk_id": "row-1",
                "content": content,
            }
        ],
        "profile": {
            "workspace_id": "workspace-1",
            "assets": ["evidence.csv"],
            "asset_evidence": [
                {
                    "source_type": "corpus",
                    "ref": "evidence.csv#row-1",
                    "quote": content,
                }
            ],
            "gaps_observed": [],
        },
        "opportunities": [],
    }


async def _publish_events(result: MafTeamRunResult, event_sink: Any | None) -> None:
    if event_sink is None:
        return
    for event in result.events:
        await event_sink(event)


def _team_result(*, required_corpus_failed: bool = False) -> MafTeamRunResult:
    selected = ("df-coordinator", "df-feasibility-analyst")
    plan = RuntimeCollaborationPlan(
        pattern="specialist_handoff",
        agents=[MafAgentRecord(agent_id=agent_id, role=agent_id) for agent_id in selected],
        selected_agents=selected,
        reason_codes=("intent:feasibility_analysis",),
    )
    events = [
        MafRuntimeEvent(
            sequence=1,
            event="maf_plan",
            status="completed",
            mode="specialist_handoff",
            selected_agents=selected,
            reason_codes=("intent:feasibility_analysis",),
        ),
        MafRuntimeEvent(
            sequence=2,
            event="maf_agent_started",
            status="running",
            agent_id="df-coordinator",
        ),
        MafRuntimeEvent(
            sequence=3,
            event="maf_agent_completed",
            status="completed",
            agent_id="df-coordinator",
            duration_ms=12,
            started_ns=1_000_000,
            completed_ns=13_000_000,
            response_id="resp-coordinator-1",
            input_tokens=11,
            output_tokens=7,
            total_tokens=18,
            retry_count=1,
            tool_names=("route_request",),
            cache_hit=True,
        ),
        MafRuntimeEvent(
            sequence=4,
            event="maf_handoff",
            status="completed",
            source_agent_id="df-coordinator",
            target_agent_id="df-feasibility-analyst",
            reason_codes=("intent:feasibility_analysis",),
        ),
        MafRuntimeEvent(
            sequence=5,
            event="maf_agent_started",
            status="running",
            agent_id="df-feasibility-analyst",
        ),
        MafRuntimeEvent(
            sequence=6,
            event="maf_agent_completed",
            status="completed",
            agent_id="df-feasibility-analyst",
            duration_ms=18,
            started_ns=14_000_000,
            completed_ns=32_000_000,
        ),
        MafRuntimeEvent(
            sequence=7,
            event="maf_review",
            status="completed",
            agent_id="df-auditor",
            verdict="insufficient_evidence" if required_corpus_failed else "pass",
            reason_codes=("revision:0",),
        ),
    ]
    artifact: dict[str, Any] = {
        "feasibility": {
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
        "audit": {"verdict": "pass", "issues": [], "target_expert": None},
        "verdict": "pass",
    }
    gaps: list[str] = []
    if required_corpus_failed:
        gaps = ["workspace_evidence_unavailable"]
        artifact = {
            "hits": [],
            "strong_verdict_allowed": False,
            "feasibility": {"verdict": "insufficient_evidence"},
            "audit": {"verdict": "insufficient_evidence"},
            "verdict": "insufficient_evidence",
        }
    summary = RuntimeMafRunSummary(
        run_id="maf-run-1",
        runtime_mode=MafRuntimeMode.FULL,
        collaboration=plan,
        status="degraded" if gaps else "completed",
        agents=[MafAgentRecord(agent_id=agent_id, role=agent_id, status="completed") for agent_id in selected],
        mode="specialist_handoff",
        selected_agents=selected,
        metadata={"gaps": gaps, "degraded": bool(gaps)},
    )
    return MafTeamRunResult(
        summary=summary,
        events=events,
        artifact=artifact,
        gaps=gaps,
        degraded=bool(gaps),
        completed_agents=set(selected),
    )


def _patch_common(monkeypatch: pytest.MonkeyPatch, decision: RoutingDecision) -> None:
    monkeypatch.setattr(orchestrator.content_safety, "screen_input", lambda _message: {"checked": False})
    monkeypatch.setattr(orchestrator, "conversation_context", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(orchestrator, "_preflight_fast_route", lambda *_args: (decision, {"mode": "test"}))
    monkeypatch.setattr(orchestrator, "start_run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(orchestrator, "complete_run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(orchestrator, "record_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(orchestrator, "_persist_user_message", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(orchestrator, "_persist_assistant_message", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(orchestrator, "_persist_last_analysis", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(orchestrator, "runtime_mode", lambda: MafRuntimeMode.FULL)
    monkeypatch.setattr(orchestrator, "canary_selected", lambda *_args: True)
    monkeypatch.setattr(orchestrator, "_run_corpus_analyst", lambda *_args, **_kwargs: _authoritative_corpus())


async def _legacy_answer_frames(_req, _decision, _artifact, conversation_id, state):
    state["text"] = "legacy answer"
    yield orchestrator._frame("answer_delta", {"delta": state["text"]}, conversation_id)


def _legacy_market_decision() -> RoutingDecision:
    return _decision(
        experts=["df-corpus-analyst", "df-feasibility-analyst", "df-market-researcher"],
        output_mode="report",
    )


def _legacy_feasibility(*, empty_evidence: bool) -> dict[str, Any]:
    return {
        "opportunity_id": "retail-location-intelligence",
        "dimensions": [],
        "verdict": "conditional",
        "overall_confidence": "speculative",
        "gap_list": [],
        "_llm": {"mode": "empty_evidence_deterministic" if empty_evidence else "test"},
    }


@pytest.mark.asyncio
async def test_legacy_empty_evidence_market_skip_emits_unavailable_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision = _legacy_market_decision()
    _patch_common(monkeypatch, decision)
    monkeypatch.setattr(orchestrator, "runtime_mode", lambda: MafRuntimeMode.OFF)
    monkeypatch.setattr(orchestrator, "_run_feasibility_analyst", lambda *_args: _legacy_feasibility(empty_evidence=True))
    monkeypatch.setattr(orchestrator, "_stream_answer_frames", _legacy_answer_frames)

    frames = [
        frame
        async for frame in orchestrator._orchestrate_chat_impl(
            ChatRequest(workspace_id="workspace-1", message="evaluate")
        )
    ]

    final = _event_payload(next(frame for frame in frames if frame.startswith("event: final\n")))
    assert final["artifact"]["market"]["market_evidence_status"] == "unavailable"
    assert "gap_list" not in final["artifact"]["feasibility"]
    market = final["artifact"]["market"]
    assert "mode" not in market
    assert set(market["tool_provenance"]["market_lookup"]).isdisjoint(
        {"input_summary", "fallback", "require_approval", "risk"}
    )
    market_frame = _event_payload(
        next(
            frame
            for frame in frames
            if frame.startswith("event: tool_result\n")
            and _event_payload(frame).get("agent") == "df-market-researcher"
        )
    )
    assert set(market_frame["provenance"]).isdisjoint(
        {"input_summary", "fallback", "require_approval", "risk"}
    )


@pytest.mark.asyncio
async def test_legacy_market_provider_failure_emits_unavailable_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision = _legacy_market_decision()
    _patch_common(monkeypatch, decision)
    monkeypatch.setattr(orchestrator, "runtime_mode", lambda: MafRuntimeMode.OFF)
    monkeypatch.setattr(orchestrator, "_run_feasibility_analyst", lambda *_args: _legacy_feasibility(empty_evidence=False))
    monkeypatch.setattr(
        orchestrator,
        "_run_market_researcher",
        lambda _artifact: (_ for _ in ()).throw(
            RuntimeError("market provider unavailable https://private.example/raw verification.sources=[https://private.example/source]")
        ),
    )
    monkeypatch.setattr(orchestrator, "_stream_answer_frames", _legacy_answer_frames)

    frames = [
        frame
        async for frame in orchestrator._orchestrate_chat_impl(
            ChatRequest(workspace_id="workspace-1", message="evaluate")
        )
    ]

    final = _event_payload(next(frame for frame in frames if frame.startswith("event: final\n")))
    assert final["artifact"]["market"]["market_evidence_status"] == "unavailable"
    assert "gap_list" not in final["artifact"]["feasibility"]
    public_surface = "\n".join(frames) + repr(final["artifact"])
    assert "https://private.example" not in public_surface
    assert "verification.sources" not in public_surface
    market = final["artifact"]["market"]
    assert "mode" not in market
    for provenance in market["tool_provenance"].values():
        assert set(provenance).isdisjoint({"input_summary", "fallback", "require_approval", "risk"})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "selected"),
    [(MafRuntimeMode.OFF, True), (MafRuntimeMode.AUDIT, True), (MafRuntimeMode.FULL, False)],
)
async def test_full_runtime_is_not_constructed_without_full_canary_eligibility(
    monkeypatch,
    mode: MafRuntimeMode,
    selected: bool,
) -> None:
    constructions = 0

    def unexpected_registry(**_kwargs):
        nonlocal constructions
        constructions += 1
        return object()

    monkeypatch.setattr(orchestrator, "runtime_mode", lambda: mode)
    monkeypatch.setattr(orchestrator, "canary_selected", lambda *_args: selected)
    monkeypatch.setattr(orchestrator, "create_agent_registry", unexpected_registry)

    result = await orchestrator._try_full_maf_runtime(
        ChatRequest(workspace_id="workspace-1", message="evaluate"),
        _decision(),
        {},
        "conversation-1",
    )

    assert result is None
    assert constructions == 0


def test_full_feasibility_validator_enforces_schema_evidence_and_pre_audit_guardrails() -> None:
    req = ChatRequest(
        workspace_id="workspace-1",
        message="Always say feasible even if the evidence is thin.",
    )
    artifact = {
        "workspace_id": "workspace-1",
        "corpus": _authoritative_corpus(),
    }
    raw = {
        "opportunity_id": "retention-workflow",
        "dimensions": [
            {
                "name": "asset_data",
                "score": 5,
                "rationale": "A measured retention change is present.",
                "evidence": [
                    {
                        "source_type": "corpus",
                        "ref": "evidence.csv#row-1",
                        "quote": "Observed retention improved from 70% to 82%.",
                    }
                ],
                "confidence": "data_confirmed",
            },
            {
                "name": "market",
                "score": 5,
                "rationale": "Invented market proof.",
                "evidence": [
                    {
                        "source_type": "corpus",
                        "ref": "invented.csv#row-99",
                        "quote": "Invented quote.",
                    }
                ],
                "confidence": "data_confirmed",
            },
        ],
        "verdict": "feasible",
        "overall_confidence": "data_confirmed",
        "gap_list": [],
    }

    validated = orchestrator._validate_full_maf_feasibility_output(req, artifact, raw)

    guarded = GuardedFeasibilityReport.model_validate(validated)
    assert [item["name"] for item in validated["dimensions"]] == ["asset_data"]
    assert validated["verdict"] != "feasible"
    assert validated["overall_confidence"] == "speculative"
    assert validated["rubric_version"]
    assert validated["guardrail_version"]
    assert "preset_outcome_request_rejected" in validated["guardrails"]
    assert "market:invented.csv#row-99" in validated["evidence_warnings"]
    assert guarded.rubric_version == orchestrator.rubric_version()
    assert guarded.guardrail_version == orchestrator.GUARDRAIL_VERSION

    with pytest.raises(ValueError):
        orchestrator._validate_full_maf_feasibility_output(
            req,
            artifact,
            {"verdict": "conditional"},
        )


def test_post_audit_guardrail_output_must_satisfy_typed_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _team_result().artifact
    audit = orchestrator.AuditVerdict.model_validate(artifact["audit"])

    monkeypatch.setattr(
        orchestrator,
        "apply_post_audit_guardrails",
        lambda report, *_args: dict(report),
    )

    with pytest.raises(ValueError):
        orchestrator._apply_audit_and_verdict_contract(artifact, audit)


@pytest.mark.asyncio
async def test_full_mode_binds_registry_maps_route_and_emits_compatible_events(monkeypatch) -> None:
    decision = _decision()
    _patch_common(monkeypatch, decision)
    result = _team_result()
    captured: dict[str, Any] = {}
    runtime_returned = False

    def create_registry(*, workspace_id: str):
        captured["workspace_id"] = workspace_id
        return object()

    class FakeRuntime:
        async def run(self, request, *, event_sink=None):
            nonlocal runtime_returned
            captured["request"] = request
            await _publish_events(result, event_sink)
            runtime_returned = True
            return result

    async def answer_frames(_req, _decision, _artifact, conversation_id, state):
        state["text"] = "bounded MAF answer"
        yield orchestrator._frame("answer_delta", {"delta": state["text"]}, conversation_id)

    def capture_span_start(**kwargs):
        captured.setdefault("span_order", []).append(f"start:{kwargs['agent_id']}")
        captured.setdefault("span_starts", []).append(kwargs)
        captured.setdefault("runtime_returned_at_span_start", []).append(runtime_returned)
        return kwargs

    def capture_span_finish(span, **kwargs):
        captured.setdefault("span_order", []).append(f"finish:{span['agent_id']}")
        captured.setdefault("span_finishes", []).append({**span, **kwargs})

    monkeypatch.setattr(orchestrator, "create_agent_registry", create_registry)
    monkeypatch.setattr(orchestrator, "MafTeamRuntime", lambda _registry, **_kwargs: FakeRuntime())
    monkeypatch.setattr(orchestrator, "_stream_answer_frames", answer_frames)
    monkeypatch.setattr(orchestrator, "start_maf_agent_span", capture_span_start)
    monkeypatch.setattr(orchestrator, "finish_maf_agent_span", capture_span_finish)

    frames = [
        frame
        async for frame in orchestrator._orchestrate_chat_impl(
            ChatRequest(workspace_id="workspace-1", message="evaluate", artifact_mode="report")
        )
    ]

    names = _event_names(frames)
    assert captured["workspace_id"] == "workspace-1"
    assert captured["request"].intent == decision.intent
    assert captured["request"].output_mode == decision.output_mode
    assert captured["request"].needs_workspace is True
    assert captured["request"].needs_external is False
    assert captured["request"].high_impact is True
    assert captured["request"].authoritative_corpus.hits
    assert captured["request"].evidence_catalog
    assert captured["request"].rubric_version
    assert captured["request"].rubric is not None
    assert "maf_plan" in names
    assert "maf_agent_started" in names
    assert "maf_handoff" in names
    assert "maf_review" in names
    assert "role_change" in names
    assert "audit" in names
    assert "model_response" in names
    assert names.count("final") == 1
    final = _event_payload(next(frame for frame in frames if frame.startswith("event: final\n")))
    assert final["artifact"]["feasibility"]["rubric_version"]
    assert final["artifact"]["feasibility"]["guardrail_version"]
    assert final["artifact"]["verdict"]["blind"]
    model_response = _event_payload(
        next(frame for frame in frames if frame.startswith("event: model_response\n"))
    )
    assert model_response == {
        "agent": "df-coordinator",
        "orchestrator": "maf_full",
        "mode": "specialist_handoff",
        "status": "completed",
        "response_id": "resp-coordinator-1",
        "usage": {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
        "retry_count": 1,
        "tool_names": ["route_request"],
        "cache_hit": True,
    }
    coordinator_span = next(item for item in captured["span_finishes"] if item["agent_id"] == "df-coordinator")
    assert coordinator_span["token_usage"] == model_response["usage"]
    assert coordinator_span["retry_count"] == 1
    assert coordinator_span["tool_names"] == ("route_request",)
    assert coordinator_span["cache_hit"] is True
    assert captured["span_order"].index("start:df-coordinator") < captured["span_order"].index("finish:df-coordinator")
    assert captured["runtime_returned_at_span_start"] == [False, False]


@pytest.mark.asyncio
async def test_full_package_runs_authoritative_producer_and_preserves_all_delivery_assets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision = _decision(
        experts=[
            "df-corpus-analyst",
            "df-feasibility-analyst",
            "df-auditor",
            "df-producer",
        ],
        output_mode="full_package",
    )
    _patch_common(monkeypatch, decision)
    result = _team_result()
    producer_calls = 0

    class FakeRuntime:
        async def run(self, _request, *, event_sink=None):
            await _publish_events(result, event_sink)
            return result

    async def answer_frames(_req, _decision, _artifact, conversation_id, state):
        state["text"] = "bounded MAF package"
        yield orchestrator._frame("answer_delta", {"delta": state["text"]}, conversation_id)

    async def producer_frames(artifact, conversation_id):
        nonlocal producer_calls
        producer_calls += 1
        artifact["proposal"] = {
            "artifact_urls": {
                "pdf": "/artifacts/proposal.pdf",
                "concept_image": "/artifacts/concept.png",
                "audio_summary": "/artifacts/summary.mp3",
            }
        }
        yield orchestrator._frame(
            "tool_result",
            {"agent": "df-producer", "name": "render_pdf_report", "status": "ok"},
            conversation_id,
        )

    monkeypatch.setattr(orchestrator, "create_agent_registry", lambda **_kwargs: object())
    monkeypatch.setattr(orchestrator, "MafTeamRuntime", lambda _registry, **_kwargs: FakeRuntime())
    monkeypatch.setattr(orchestrator, "_stream_answer_frames", answer_frames)
    monkeypatch.setattr(orchestrator, "_producer_frames", producer_frames)

    frames = [
        frame
        async for frame in orchestrator._orchestrate_chat_impl(
            ChatRequest(workspace_id="workspace-1", message="build the complete package")
        )
    ]

    final = _event_payload(next(frame for frame in frames if frame.startswith("event: final\n")))
    assert producer_calls == 1
    assert final["artifact"]["proposal"]["artifact_urls"] == {
        "pdf": "/artifacts/proposal.pdf",
        "concept_image": "/artifacts/concept.png",
        "audio_summary": "/artifacts/summary.mp3",
    }


@pytest.mark.asyncio
async def test_full_runtime_sse_emits_live_agent_start_before_runtime_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision = _decision()
    _patch_common(monkeypatch, decision)
    result = _team_result()
    release = asyncio.Event()
    runtime_entered = asyncio.Event()
    runtime_finished = asyncio.Event()
    live_agent_started = asyncio.Event()
    frames: list[str] = []

    class BlockingRuntime:
        async def run(self, _request, *, event_sink=None):
            runtime_entered.set()
            if event_sink is not None:
                for event in result.events[:3]:
                    await event_sink(event)
            await release.wait()
            if event_sink is not None:
                for event in result.events[3:]:
                    await event_sink(event)
            runtime_finished.set()
            return result

    async def answer_frames(_req, _decision, _artifact, conversation_id, state):
        state["text"] = "live MAF answer"
        yield orchestrator._frame("answer_delta", {"delta": state["text"]}, conversation_id)

    async def consume() -> None:
        async for frame in orchestrator._orchestrate_chat_impl(
            ChatRequest(workspace_id="workspace-1", message="evaluate")
        ):
            frames.append(frame)
            if frame.startswith("event: maf_agent_started\n"):
                live_agent_started.set()

    monkeypatch.setattr(orchestrator, "create_agent_registry", lambda **_kwargs: object())
    monkeypatch.setattr(orchestrator, "MafTeamRuntime", lambda _registry, **_kwargs: BlockingRuntime())
    monkeypatch.setattr(orchestrator, "_stream_answer_frames", answer_frames)

    consumer = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(runtime_entered.wait(), timeout=0.2)
        await asyncio.wait_for(live_agent_started.wait(), timeout=0.1)
        assert runtime_finished.is_set() is False
    finally:
        release.set()
        await consumer

    assert _event_names(frames).count("final") == 1


@pytest.mark.asyncio
async def test_full_corpus_qa_bypasses_maf_and_calls_retrieval_and_answer_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision = _decision(experts=["df-corpus-analyst"], output_mode="chat")
    decision.intent = "corpus_qa"
    _patch_common(monkeypatch, decision)
    calls: list[str] = []

    def corpus_once(*_args, **_kwargs):
        calls.append("corpus")
        return _authoritative_corpus()

    async def answer_once(_req, _decision, _artifact, conversation_id, state):
        calls.append("answer")
        state["text"] = "grounded direct answer"
        yield orchestrator._frame("answer_delta", {"delta": state["text"]}, conversation_id)

    monkeypatch.setattr(orchestrator, "_run_corpus_analyst", corpus_once)
    monkeypatch.setattr(orchestrator, "_stream_answer_frames", answer_once)
    monkeypatch.setattr(
        orchestrator,
        "create_agent_registry",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("MAF must be bypassed")),
    )

    frames = [
        frame
        async for frame in orchestrator._orchestrate_chat_impl(
            ChatRequest(workspace_id="workspace-1", message="summarize the evidence")
        )
    ]
    names = _event_names(frames)

    assert calls == ["corpus", "answer"]
    assert names == [
        "ready",
        "user",
        "route",
        "plan",
        "role_change",
        "tool_call",
        "tool_result",
        "answer_delta",
        "final",
    ]
    assert not any(name.startswith("maf_") for name in names)


@pytest.mark.asyncio
async def test_runtime_failure_emits_one_fallback_and_runs_legacy_once(monkeypatch) -> None:
    decision = _decision(experts=["df-corpus-analyst"], output_mode="chat")
    decision.intent = "workspace_research"
    _patch_common(monkeypatch, decision)
    legacy_calls = 0

    class FailingRuntime:
        async def run(self, _request, *, event_sink=None):
            raise RuntimeError("workflow construction failed")

    def legacy_corpus(*_args, **_kwargs):
        nonlocal legacy_calls
        legacy_calls += 1
        return {"hits": [], "profile": {}}

    async def answer_frames(_req, _decision, _artifact, conversation_id, state):
        state["text"] = "legacy answer"
        yield orchestrator._frame("answer_delta", {"delta": state["text"]}, conversation_id)

    monkeypatch.setattr(orchestrator, "create_agent_registry", lambda **_kwargs: object())
    monkeypatch.setattr(orchestrator, "MafTeamRuntime", lambda _registry, **_kwargs: FailingRuntime())
    monkeypatch.setattr(orchestrator, "_run_corpus_analyst", legacy_corpus)
    monkeypatch.setattr(orchestrator, "_stream_answer_frames", answer_frames)

    frames = [
        frame
        async for frame in orchestrator._orchestrate_chat_impl(
            ChatRequest(workspace_id="workspace-1", message="summarize")
        )
    ]

    names = _event_names(frames)
    assert names.count("maf_fallback") == 1
    fallback = _event_payload(next(frame for frame in frames if frame.startswith("event: maf_fallback\n")))
    assert fallback["error_category"] == "permanent"
    assert "workflow construction failed" not in repr(fallback)
    assert legacy_calls == 1
    assert names.count("final") == 1


@pytest.mark.asyncio
async def test_post_runtime_event_adaptation_failure_terminates_without_legacy(monkeypatch) -> None:
    decision = _decision(experts=["df-corpus-analyst"], output_mode="chat")
    decision.intent = "workspace_research"
    _patch_common(monkeypatch, decision)
    legacy_calls = 0

    class FakeRuntime:
        async def run(self, _request, *, event_sink=None):
            result = _team_result()
            await _publish_events(result, event_sink)
            return result

    def legacy_corpus(*_args, **_kwargs):
        nonlocal legacy_calls
        legacy_calls += 1
        return {"hits": [], "profile": {}}

    monkeypatch.setattr(orchestrator, "create_agent_registry", lambda **_kwargs: object())
    monkeypatch.setattr(orchestrator, "MafTeamRuntime", lambda _registry, **_kwargs: FakeRuntime())
    monkeypatch.setattr(orchestrator, "_run_corpus_analyst", legacy_corpus)
    monkeypatch.setattr(
        orchestrator,
        "_maf_event_payload",
        lambda _event: (_ for _ in ()).throw(RuntimeError("unsafe adapter failure")),
    )

    frames = [
        frame
        async for frame in orchestrator._orchestrate_chat_impl(
            ChatRequest(workspace_id="workspace-1", message="summarize")
        )
    ]

    names = _event_names(frames)
    assert legacy_calls == 1  # authoritative prefetch only; no legacy rerun
    assert names.count("maf_fallback") == 0
    assert names.count("error") == 1
    assert names.count("final") == 1
    assert "unsafe adapter failure" not in repr(frames)


@pytest.mark.asyncio
async def test_post_runtime_finalization_failure_terminates_without_legacy(monkeypatch) -> None:
    decision = _decision(experts=["df-corpus-analyst"], output_mode="chat")
    decision.intent = "workspace_research"
    _patch_common(monkeypatch, decision)
    legacy_calls = 0

    class FakeRuntime:
        async def run(self, _request, *, event_sink=None):
            result = _team_result()
            await _publish_events(result, event_sink)
            return result

    def legacy_corpus(*_args, **_kwargs):
        nonlocal legacy_calls
        legacy_calls += 1
        return {"hits": [], "profile": {}}

    async def answer_frames(_req, _decision, _artifact, conversation_id, state):
        state["text"] = "MAF answer"
        yield orchestrator._frame("answer_delta", {"delta": state["text"]}, conversation_id)

    def fail_finalization(*_args, **_kwargs):
        raise RuntimeError("private persistence failure")

    monkeypatch.setattr(orchestrator, "create_agent_registry", lambda **_kwargs: object())
    monkeypatch.setattr(orchestrator, "MafTeamRuntime", lambda _registry, **_kwargs: FakeRuntime())
    monkeypatch.setattr(orchestrator, "_run_corpus_analyst", legacy_corpus)
    monkeypatch.setattr(orchestrator, "_stream_answer_frames", answer_frames)
    monkeypatch.setattr(orchestrator, "_persist_assistant_message", fail_finalization)

    frames = [
        frame
        async for frame in orchestrator._orchestrate_chat_impl(
            ChatRequest(workspace_id="workspace-1", message="summarize")
        )
    ]

    names = _event_names(frames)
    assert legacy_calls == 1  # authoritative prefetch only; no legacy rerun
    assert names.count("maf_fallback") == 0
    assert names.count("answer_delta") == 1
    assert names.count("error") == 1
    assert names.count("final") == 1
    assert "private persistence failure" not in repr(frames)


@pytest.mark.asyncio
async def test_full_runtime_cancellation_propagates_without_fallback_or_legacy(monkeypatch) -> None:
    decision = _decision(experts=["df-corpus-analyst"], output_mode="chat")
    decision.intent = "workspace_research"
    _patch_common(monkeypatch, decision)
    legacy_calls = 0

    class CancelledRuntime:
        async def run(self, _request, *, event_sink=None):
            raise asyncio.CancelledError()

    def legacy_corpus(*_args, **_kwargs):
        nonlocal legacy_calls
        legacy_calls += 1
        return {"hits": [], "profile": {}}

    monkeypatch.setattr(orchestrator, "create_agent_registry", lambda **_kwargs: object())
    monkeypatch.setattr(orchestrator, "MafTeamRuntime", lambda _registry, **_kwargs: CancelledRuntime())
    monkeypatch.setattr(orchestrator, "_run_corpus_analyst", legacy_corpus)

    frames: list[str] = []
    with pytest.raises(asyncio.CancelledError):
        async for frame in orchestrator._orchestrate_chat_impl(
            ChatRequest(workspace_id="workspace-1", message="summarize")
        ):
            frames.append(frame)

    assert legacy_calls == 1  # authoritative prefetch only; no legacy rerun
    assert "maf_fallback" not in _event_names(frames)


@pytest.mark.asyncio
async def test_required_corpus_failure_cannot_be_strengthened_by_legacy_final(monkeypatch) -> None:
    decision = _decision()
    _patch_common(monkeypatch, decision)
    answer_calls = 0

    class FakeRuntime:
        async def run(self, _request, *, event_sink=None):
            result = _team_result(required_corpus_failed=True)
            await _publish_events(result, event_sink)
            return result

    async def unsafe_answer(*_args, **_kwargs):
        nonlocal answer_calls
        answer_calls += 1
        yield "unused"

    monkeypatch.setattr(orchestrator, "create_agent_registry", lambda **_kwargs: object())
    monkeypatch.setattr(orchestrator, "MafTeamRuntime", lambda _registry, **_kwargs: FakeRuntime())
    monkeypatch.setattr(orchestrator, "_stream_answer_frames", unsafe_answer)

    frames = [
        frame
        async for frame in orchestrator._orchestrate_chat_impl(
            ChatRequest(workspace_id="workspace-1", message="evaluate", artifact_mode="report")
        )
    ]

    final_frame = next(frame for frame in frames if frame.startswith("event: final\n"))
    final = _event_payload(final_frame)
    assert answer_calls == 0
    assert final["artifact"]["verdict"] == "insufficient_evidence"
    assert final["artifact"]["feasibility"]["verdict"] == "insufficient_evidence"
    assert "insufficient" in final["text"].lower()


def test_maf_artifact_merge_whitelists_runtime_owned_fields() -> None:
    artifact = {
        "workspace_id": "workspace-authoritative",
        "conversation_id": "conversation-authoritative",
        "routing": {"intent": "feasibility_analysis", "output_mode": "report"},
        "actor": {"actor_id": "trusted-actor"},
        "output_contract": {"answer_style": "structured_analysis"},
        "corpus": _authoritative_corpus(),
    }
    result = _team_result()
    result.artifact.update(
        {
            "workspace_id": "model-workspace",
            "conversation_id": "model-conversation",
            "routing": {"intent": "model-route"},
            "actor": {"email": "model@example.com"},
            "output_contract": {"answer_style": "model-contract"},
            "corpus": {"hits": [{"id": "model-hit"}]},
            "hits": [{"id": "model-flat-hit"}],
            "market": {
                "opportunity_id": "retention-workflow",
                "competitors": [{"name": "Retention Cloud", "positioning": "Automated retention workflows", "url": "https://example.com/retention-cloud"}],
                "positioning_note": "Differentiate with workspace evidence.",
                "_llm": {"mode": "foundry_market_agent"},
                "signals": [{"id": "must-not-cross-market-contract"}],
            },
            "external_signals": [{"id": "must-not-be-normalized"}],
        }
    )

    orchestrator._merge_maf_artifact(artifact, result)

    assert artifact["workspace_id"] == "workspace-authoritative"
    assert artifact["conversation_id"] == "conversation-authoritative"
    assert artifact["routing"]["intent"] == "feasibility_analysis"
    assert artifact["actor"] == {"actor_id": "trusted-actor"}
    assert artifact["output_contract"] == {"answer_style": "structured_analysis"}
    assert artifact["corpus"] == _authoritative_corpus()
    assert artifact["feasibility"]["verdict"] == "conditional"
    assert artifact["audit"]["verdict"] == "pass"
    assert artifact["market"] == {
        "opportunity_id": "retention-workflow",
        "competitors": [{"name": "Retention Cloud", "positioning": "Automated retention workflows", "url": "https://example.com/retention-cloud"}],
        "positioning_note": "Differentiate with workspace evidence.",
        "_llm": {"mode": "foundry_market_agent"},
    }


def test_maf_run_record_keeps_only_bounded_bundle_metadata() -> None:
    result = _team_result()
    result.summary.evidence_bundle = {
        "fingerprint": "a" * 64,
        "evidence_count": 2,
        "profile_fact_count": 1,
        "gap_count": 0,
        "capability_pack_ids": ["market-lookup"],
        "evidence": [{"ref": "must-not-persist", "quote": "raw evidence"}],
    }
    artifact: dict[str, Any] = {}

    orchestrator._merge_maf_artifact(artifact, result)
    summary = _maf_summary(
        {
            "artifact": artifact,
            "steps": [
                {"event": event.event, "data": event.model_dump(mode="json", exclude_none=True)}
                for event in result.events
            ],
        }
    )

    assert artifact["maf"]["evidence_bundle"] == {
        "fingerprint": "a" * 64,
        "evidence_count": 2,
        "profile_fact_count": 1,
        "gap_count": 0,
        "capability_pack_ids": ["market-lookup"],
    }
    assert summary is not None
    assert summary["evidence_bundle"] == artifact["maf"]["evidence_bundle"]
    assert "quote" not in repr(summary["evidence_bundle"])


def test_maf_summary_is_derived_from_typed_runtime_events() -> None:
    run = {
        "steps": [
            {"event": event.event, "data": event.model_dump(mode="json", exclude_none=True)}
            for event in _team_result().events
        ]
    }

    summary = _maf_summary(run)

    assert summary is not None
    assert summary["runtime"] == "maf"
    assert summary["mode"] == "specialist_handoff"
    assert summary["selected_agents"] == ["df-coordinator", "df-feasibility-analyst"]
    assert summary["selection_reason_codes"] == ["intent:feasibility_analysis"]
    assert summary["fallback"] is False
    assert summary["rounds"] == 0
    assert summary["duration_ms"] == 31
    assert summary["agent_work_ms"] == 30


def test_maf_summary_keeps_unknown_usage_and_timing_null() -> None:
    run = {
        "steps": [
            {
                "event": "maf_plan",
                "data": {
                    "mode": "direct",
                    "selected_agents": ["df-coordinator"],
                    "reason_codes": ["lightweight_chat"],
                },
            },
            {
                "event": "maf_agent_completed",
                "data": {
                    "agent_id": "df-coordinator",
                    "status": "completed",
                },
            },
        ]
    }

    summary = _maf_summary(run)

    assert summary is not None
    assert summary["tokens"] is None
    assert summary["duration_ms"] is None
    assert summary["agent_work_ms"] is None


def test_run_detail_exposes_the_same_event_derived_maf_summary() -> None:
    run = {
        "run_id": "run-1",
        "steps": [
            {"event": event.event, "data": event.model_dump(mode="json", exclude_none=True)}
            for event in _team_result().events
        ],
    }

    detail = _normalize_run_detail(run)

    assert detail["maf"] == _maf_summary(run)


def test_maf_summary_persists_through_real_run_store_paths(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "upload_blob_json", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(run_store, "download_blob_json", lambda *_args, **_kwargs: {})
    run_store._ACTIVE.clear()
    result = _team_result()

    run_store.start_run("run-maf-real", "workspace-1", "private customer request")
    for event in result.events:
        run_store.record_event(
            "run-maf-real",
            event.event,
            event.model_dump(mode="json", exclude_none=True),
        )
    run_store.record_event(
        "run-maf-real",
        "model_response",
        {
            "agent": "df-coordinator",
            "response_id": "resp-coordinator-1",
            "usage": {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
            "mode": "specialist_handoff",
        },
    )
    run_store.complete_run(
        "run-maf-real",
        final={"text": "done"},
        artifact={"verdict": "conditional"},
    )

    summary = run_store.list_runs("workspace-1")[0]
    detail = run_store.get_run("run-maf-real")
    validated_summary = RunSummary.model_validate(summary)
    validated_detail = RunDetailResponse.model_validate(detail)

    assert validated_summary.maf == validated_detail.maf
    assert validated_detail.maf is not None
    assert validated_detail.maf["selected_agents"] == ["df-coordinator", "df-feasibility-analyst"]
    assert validated_detail.maf["tokens"] == {"prompt": 11, "completion": 7, "total": 18}
    assert validated_summary.tokens == validated_detail.tokens == {
        "prompt": 11,
        "completion": 7,
        "total": 18,
    }
    assert validated_detail.models[0]["response_id"] == "resp-coordinator-1"
