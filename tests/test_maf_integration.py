from __future__ import annotations

import json
from typing import Any

import pytest

import backend.orchestrator as orchestrator
from backend.maf_contracts import MafAgentRecord, MafRuntimeMode
from backend.maf_team_runtime import (
    MafRuntimeEvent,
    MafTeamRunResult,
    RuntimeCollaborationPlan,
    RuntimeMafRunSummary,
)
from backend.run_store import _maf_summary, _normalize_run_detail
from backend.schemas import ChatRequest, RoutingDecision


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
        "feasibility": {"verdict": "conditional"},
        "audit": {"verdict": "pass"},
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


@pytest.mark.asyncio
async def test_full_mode_binds_registry_maps_route_and_emits_compatible_events(monkeypatch) -> None:
    decision = _decision()
    _patch_common(monkeypatch, decision)
    result = _team_result()
    captured: dict[str, Any] = {}

    def create_registry(*, workspace_id: str):
        captured["workspace_id"] = workspace_id
        return object()

    class FakeRuntime:
        async def run(self, request):
            captured["request"] = request
            return result

    async def answer_frames(_req, _decision, _artifact, conversation_id, state):
        state["text"] = "bounded MAF answer"
        yield orchestrator._frame("answer_delta", {"delta": state["text"]}, conversation_id)

    monkeypatch.setattr(orchestrator, "create_agent_registry", create_registry)
    monkeypatch.setattr(orchestrator, "MafTeamRuntime", lambda _registry: FakeRuntime())
    monkeypatch.setattr(orchestrator, "_stream_answer_frames", answer_frames)

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
    assert "maf_plan" in names
    assert "maf_agent_started" in names
    assert "maf_handoff" in names
    assert "maf_review" in names
    assert "role_change" in names
    assert "audit" in names
    assert names.count("final") == 1


@pytest.mark.asyncio
async def test_runtime_failure_emits_one_fallback_and_runs_legacy_once(monkeypatch) -> None:
    decision = _decision(experts=["df-corpus-analyst"], output_mode="chat")
    decision.intent = "corpus_qa"
    _patch_common(monkeypatch, decision)
    legacy_calls = 0

    class FailingRuntime:
        async def run(self, _request):
            raise RuntimeError("workflow construction failed")

    def legacy_corpus(*_args, **_kwargs):
        nonlocal legacy_calls
        legacy_calls += 1
        return {"hits": [], "profile": {}}

    async def answer_frames(_req, _decision, _artifact, conversation_id, state):
        state["text"] = "legacy answer"
        yield orchestrator._frame("answer_delta", {"delta": state["text"]}, conversation_id)

    monkeypatch.setattr(orchestrator, "create_agent_registry", lambda **_kwargs: object())
    monkeypatch.setattr(orchestrator, "MafTeamRuntime", lambda _registry: FailingRuntime())
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
async def test_required_corpus_failure_cannot_be_strengthened_by_legacy_final(monkeypatch) -> None:
    decision = _decision()
    _patch_common(monkeypatch, decision)
    answer_calls = 0

    class FakeRuntime:
        async def run(self, _request):
            return _team_result(required_corpus_failed=True)

    async def unsafe_answer(*_args, **_kwargs):
        nonlocal answer_calls
        answer_calls += 1
        yield "unused"

    monkeypatch.setattr(orchestrator, "create_agent_registry", lambda **_kwargs: object())
    monkeypatch.setattr(orchestrator, "MafTeamRuntime", lambda _registry: FakeRuntime())
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
    assert summary["duration_ms"] == 30


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
