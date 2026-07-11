from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
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
from backend.schemas import ChatRequest, RoutingDecision, RunDetailResponse, RunSummary


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

    @contextmanager
    def capture_span(**kwargs):
        captured.setdefault("spans", []).append(kwargs)
        yield None

    monkeypatch.setattr(orchestrator, "create_agent_registry", create_registry)
    monkeypatch.setattr(orchestrator, "MafTeamRuntime", lambda _registry: FakeRuntime())
    monkeypatch.setattr(orchestrator, "_stream_answer_frames", answer_frames)
    monkeypatch.setattr(orchestrator, "maf_agent_trace", capture_span)

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
    assert "model_response" in names
    assert names.count("final") == 1
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
    coordinator_span = next(item for item in captured["spans"] if item["agent_id"] == "df-coordinator")
    assert coordinator_span["token_usage"] == model_response["usage"]
    assert coordinator_span["retry_count"] == 1
    assert coordinator_span["tool_names"] == ("route_request",)
    assert coordinator_span["cache_hit"] is True
    assert coordinator_span["started_ns"] == 1_000_000
    assert coordinator_span["completed_ns"] == 13_000_000


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
async def test_post_runtime_event_adaptation_failure_terminates_without_legacy(monkeypatch) -> None:
    decision = _decision(experts=["df-corpus-analyst"], output_mode="chat")
    decision.intent = "corpus_qa"
    _patch_common(monkeypatch, decision)
    legacy_calls = 0

    class FakeRuntime:
        async def run(self, _request):
            return _team_result()

    def legacy_corpus(*_args, **_kwargs):
        nonlocal legacy_calls
        legacy_calls += 1
        return {"hits": [], "profile": {}}

    monkeypatch.setattr(orchestrator, "create_agent_registry", lambda **_kwargs: object())
    monkeypatch.setattr(orchestrator, "MafTeamRuntime", lambda _registry: FakeRuntime())
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
    assert legacy_calls == 0
    assert names.count("maf_fallback") == 0
    assert names.count("error") == 1
    assert names.count("final") == 1
    assert "unsafe adapter failure" not in repr(frames)


@pytest.mark.asyncio
async def test_post_runtime_finalization_failure_terminates_without_legacy(monkeypatch) -> None:
    decision = _decision(experts=["df-corpus-analyst"], output_mode="chat")
    decision.intent = "corpus_qa"
    _patch_common(monkeypatch, decision)
    legacy_calls = 0

    class FakeRuntime:
        async def run(self, _request):
            return _team_result()

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
    monkeypatch.setattr(orchestrator, "MafTeamRuntime", lambda _registry: FakeRuntime())
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
    assert legacy_calls == 0
    assert names.count("maf_fallback") == 0
    assert names.count("answer_delta") == 1
    assert names.count("error") == 1
    assert names.count("final") == 1
    assert "private persistence failure" not in repr(frames)


@pytest.mark.asyncio
async def test_full_runtime_cancellation_propagates_without_fallback_or_legacy(monkeypatch) -> None:
    decision = _decision(experts=["df-corpus-analyst"], output_mode="chat")
    decision.intent = "corpus_qa"
    _patch_common(monkeypatch, decision)
    legacy_calls = 0

    class CancelledRuntime:
        async def run(self, _request):
            raise asyncio.CancelledError()

    def legacy_corpus(*_args, **_kwargs):
        nonlocal legacy_calls
        legacy_calls += 1
        return {"hits": [], "profile": {}}

    monkeypatch.setattr(orchestrator, "create_agent_registry", lambda **_kwargs: object())
    monkeypatch.setattr(orchestrator, "MafTeamRuntime", lambda _registry: CancelledRuntime())
    monkeypatch.setattr(orchestrator, "_run_corpus_analyst", legacy_corpus)

    frames: list[str] = []
    with pytest.raises(asyncio.CancelledError):
        async for frame in orchestrator._orchestrate_chat_impl(
            ChatRequest(workspace_id="workspace-1", message="summarize")
        ):
            frames.append(frame)

    assert legacy_calls == 0
    assert "maf_fallback" not in _event_names(frames)


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
