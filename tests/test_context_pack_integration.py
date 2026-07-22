from __future__ import annotations

import json

import backend.orchestrator as orchestrator
import backend.run_store as run_store
from backend.model_policy import ModelRoute, SelectedTextRoute
from backend.schemas import ChatRequest, RoutingDecision


def _followup_decision() -> RoutingDecision:
    return RoutingDecision(
        workspace_id="ws-a",
        intent="followup_edit",
        experts=[],
        output_mode="chat",
        needs_clarification=False,
        reason="test",
    )


def test_followup_uses_context_pack_and_falls_back_to_legacy_history_when_pack_build_fails(monkeypatch) -> None:
    captured_payloads: list[dict[str, object]] = []

    monkeypatch.setattr(
        orchestrator,
        "workspace_context",
        lambda workspace_id: {
            "workspace_id": workspace_id,
            "name": "Workspace A",
            "doc_count": 2,
            "profile_summary": "Two candidate areas",
            "customer_summary": "Site selection workspace",
        },
    )
    monkeypatch.setattr(
        orchestrator,
        "_last_analysis_for_workspace",
        lambda workspace_id, context=None: {
            "run_id": "run-r4",
            "verdict": "conditional",
            "overall_confidence": "data_confirmed",
            "recommendation": "Pilot north and atrium",
            "gap_list": ["Need real conversion"],
            "citations": [{"marker": "[D1]", "ref": "doc-1", "snippet": "North zone is stable"}],
            "audit": {"issues": ["Keep recommendation bounded to evidence"]},
            "output_contract": {"version": "analysis-v1"},
        },
    )
    monkeypatch.setattr(orchestrator, "_run_answer_composer_first", lambda *args, **kwargs: None)
    monkeypatch.setattr(orchestrator, "_followup_red_team_assessment", lambda *args, **kwargs: None)
    monkeypatch.setattr(orchestrator, "_followup_plan_draft", lambda *args, **kwargs: None)
    monkeypatch.setattr(orchestrator, "_followup_provisional_choice_assessment", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        orchestrator,
        "select_text_route_record",
        lambda *args, **kwargs: SelectedTextRoute(
            route=ModelRoute(
                route_id="followup",
                deployment="gpt-5-mini",
                label="Follow-up",
                capabilities=frozenset({"followup"}),
            ),
            execution_kind="follow_up",
            selection="policy",
            fallback_reason=None,
        ),
    )
    monkeypatch.setattr(orchestrator, "build_context_pack", lambda **_kwargs: (_ for _ in ()).throw(ValueError("bad revision")))

    def _fake_followup(payload):
        captured_payloads.append(payload)
        return {
            "text": "## 综合判断\n可以继续，但先收窄试点。",
            "mode": "followup_assessment",
            "response_id": "resp-fallback",
            "usage": {"total_tokens": 12},
            "assessment": "needs_more_evidence",
            "gaps": ["Need real conversion"],
            "clarify": "",
            "should_clarify": False,
            "is_plan": False,
            "needs_full_analysis": False,
            "route_hint": "direct_answer",
            "answer_type": "brief_answer",
        }

    monkeypatch.setattr(orchestrator, "run_followup_assessment", _fake_followup)

    result = orchestrator._lightweight_reply(
        ChatRequest(workspace_id="ws-a", conversation_id="conv-a", message="Refine the pilot"),
        _followup_decision(),
        history=[
            {"role": "user", "text": "Original request", "time": "2026-07-22T10:00:00Z"},
            {"role": "assistant", "text": "Previous answer", "time": "2026-07-22T10:01:00Z"},
        ],
    )

    assert result["context_pack"]["status"] == "fallback"
    assert result["context_pack"]["fallback_reason"] == "pack_build_failed"
    assert result["text"]
    assert captured_payloads[0]["conversation_history"] == [
        {"role": "user", "text": "Original request", "time": "2026-07-22T10:00:00Z"},
        {"role": "assistant", "text": "Previous answer", "time": "2026-07-22T10:01:00Z"},
    ]
    assert "context_pack" not in captured_payloads[0]


def test_followup_uses_context_pack_projection_when_available(monkeypatch) -> None:
    captured_payloads: list[dict[str, object]] = []

    monkeypatch.setattr(
        orchestrator,
        "workspace_context",
        lambda workspace_id: {
            "workspace_id": workspace_id,
            "name": "Workspace A",
            "doc_count": 2,
            "profile_summary": "Two candidate areas",
            "customer_summary": "Site selection workspace",
        },
    )
    monkeypatch.setattr(
        orchestrator,
        "_last_analysis_for_workspace",
        lambda workspace_id, context=None: {
            "run_id": "run-r4",
            "verdict": "conditional",
            "overall_confidence": "data_confirmed",
            "recommendation": "Pilot north and atrium",
            "gap_list": ["Need real conversion"],
            "citations": [{"marker": "[D1]", "ref": "doc-1", "snippet": "North zone is stable"}],
            "audit": {"issues": ["Keep recommendation bounded to evidence"]},
            "output_contract": {"version": "analysis-v1"},
        },
    )
    monkeypatch.setattr(orchestrator, "_run_answer_composer_first", lambda *args, **kwargs: None)
    monkeypatch.setattr(orchestrator, "_followup_red_team_assessment", lambda *args, **kwargs: None)
    monkeypatch.setattr(orchestrator, "_followup_plan_draft", lambda *args, **kwargs: None)
    monkeypatch.setattr(orchestrator, "_followup_provisional_choice_assessment", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        orchestrator,
        "select_text_route_record",
        lambda *args, **kwargs: SelectedTextRoute(
            route=ModelRoute(
                route_id="followup",
                deployment="gpt-5-mini",
                label="Follow-up",
                capabilities=frozenset({"followup"}),
            ),
            execution_kind="follow_up",
            selection="policy",
            fallback_reason=None,
        ),
    )
    monkeypatch.setattr(
        orchestrator,
        "conversation_durable_facts",
        lambda conversation_id, workspace_id=None: [
            {
                "fact_id": "fact-1",
                "scope": "ws-a:conv-a",
                "kind": "verified_constraint",
                "text": "Budget is capped at 50k",
            }
        ],
    )

    def _fake_followup(payload):
        captured_payloads.append(payload)
        return {
            "text": "## 综合判断\n可以继续，但先收窄试点。",
            "mode": "followup_assessment",
            "response_id": "resp-ready",
            "usage": {"total_tokens": 12},
            "assessment": "needs_more_evidence",
            "gaps": ["Need real conversion"],
            "clarify": "",
            "should_clarify": False,
            "is_plan": False,
            "needs_full_analysis": False,
            "route_hint": "direct_answer",
            "answer_type": "brief_answer",
        }

    monkeypatch.setattr(orchestrator, "run_followup_assessment", _fake_followup)

    result = orchestrator._lightweight_reply(
        ChatRequest(workspace_id="ws-a", conversation_id="conv-a", message="Refine the pilot"),
        _followup_decision(),
        history=[
            {"role": "user", "text": "Original request", "time": "2026-07-22T10:00:00Z"},
            {"role": "assistant", "text": "Previous answer", "time": "2026-07-22T10:01:00Z"},
        ],
    )

    assert result["context_pack"]["status"] == "ready"
    assert result["context_pack"]["fingerprint"]
    assert captured_payloads[0]["conversation_history"] == []
    assert captured_payloads[0]["context_pack"]["scope"] == {
        "workspace_id": "ws-a",
        "conversation_id": "conv-a",
    }
    assert captured_payloads[0]["context_pack"]["durable_facts"][0]["text"] == "Budget is capped at 50k"


def test_run_store_persists_safe_context_pack_metadata(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "upload_blob_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_store, "download_blob_json", lambda *args, **kwargs: {})
    run_store._ACTIVE.clear()

    run_store.start_run("run-context-pack", "ws-a", "Refine the pilot")
    run_store.record_context_pack(
        "run-context-pack",
        {
            "status": "ready",
            "version": "context-pack-v1",
            "scope": {"workspace_id": "ws-a", "conversation_id": "conv-a"},
            "fingerprint": "fp-123",
            "durable_fact_ids": ["fact-1"],
            "durable_fact_kinds": ["verified_constraint"],
            "fact_count": 1,
            "workspace_fact_count": 2,
            "audit_constraint_count": 1,
            "debug_text": "Budget is capped at 50k",
        },
    )
    run_store.complete_run("run-context-pack", final={"text": "done"}, artifact={})

    persisted = run_store.get_run("run-context-pack")
    assert persisted["context_pack"] == {
        "status": "ready",
        "version": "context-pack-v1",
        "scope": {"workspace_id": "ws-a", "conversation_id": "conv-a"},
        "fingerprint": "fp-123",
        "durable_fact_ids": ["fact-1"],
        "durable_fact_kinds": ["verified_constraint"],
        "fact_count": 1,
        "workspace_fact_count": 2,
        "audit_constraint_count": 1,
    }
    context_steps = [item for item in persisted["steps"] if item.get("event") == "context_pack"]
    assert len(context_steps) == 1
    assert json.dumps(context_steps[0], ensure_ascii=False)
