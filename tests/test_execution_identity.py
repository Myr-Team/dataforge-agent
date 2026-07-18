from __future__ import annotations

import asyncio
import json

import backend.app as app_module
import backend.orchestrator as orchestrator
import backend.run_store as run_store
import backend.workspace_store as workspace_store
from backend.schemas import ChatRequest
from fastapi.testclient import TestClient


def test_execution_context_uses_distinct_run_and_conversation_identifiers(monkeypatch) -> None:
    monkeypatch.setenv("DF_SEPARATE_ANALYSIS_CONVERSATIONS", "1")
    auto = orchestrator.execution_context(
        ChatRequest(
            workspace_id="ws-separation",
            message="Analyze the workspace",
            run_id="run-auto-1",
            origin="workspace_auto_analysis",
            persist_messages=False,
        )
    )

    assert auto.run_id == "run-auto-1"
    assert auto.conversation_id is None
    assert auto.persist_messages is False

    conversation = orchestrator.execution_context(
        ChatRequest(
            workspace_id="ws-separation",
            message="Summarize the conclusion",
            run_id="run-chat-1",
            conversation_id="conversation-1",
            origin="conversation",
        )
    )

    assert conversation.run_id == "run-chat-1"
    assert conversation.conversation_id == "conversation-1"
    assert conversation.persist_messages is True


def test_run_store_persists_execution_origin_without_conversation() -> None:
    run_store._ACTIVE.clear()

    run_store.start_run(
        "run-auto-2",
        "ws-separation",
        "Analyze the workspace",
        conversation_id=None,
        origin="workspace_auto_analysis",
    )

    assert run_store._ACTIVE["run-auto-2"]["run_id"] == "run-auto-2"
    assert run_store._ACTIVE["run-auto-2"]["conversation_id"] is None
    assert run_store._ACTIVE["run-auto-2"]["origin"] == "workspace_auto_analysis"


def test_automatic_execution_does_not_persist_synthetic_messages(monkeypatch) -> None:
    monkeypatch.setenv("DF_SEPARATE_ANALYSIS_CONVERSATIONS", "1")
    persisted: list[str] = []
    monkeypatch.setattr(orchestrator.content_safety, "screen_input", lambda _message: {"checked": True, "allowed": False})
    monkeypatch.setattr(orchestrator.content_safety, "refusal_message", lambda _screen: "Blocked")
    monkeypatch.setattr(orchestrator, "_persist_user_message", lambda *_args: persisted.append("user"))
    monkeypatch.setattr(orchestrator, "_persist_assistant_message", lambda *_args: persisted.append("assistant"))

    async def collect() -> list[str]:
        return [
            frame
            async for frame in orchestrator.orchestrate_chat(
                ChatRequest(
                    workspace_id="ws-separation",
                    message="Analyze the workspace",
                    run_id="run-auto-3",
                    origin="workspace_auto_analysis",
                    persist_messages=False,
                )
            )
        ]

    frames = asyncio.run(collect())
    ready = next(json.loads(frame.split("data: ", 1)[1]) for frame in frames if frame.startswith("event: ready"))

    assert ready["run_id"] == "run-auto-3"
    assert ready["origin"] == "workspace_auto_analysis"
    assert ready["conversation_id"] is None
    assert persisted == []


def test_auto_analyze_endpoint_uses_run_identity_without_message_audit(monkeypatch) -> None:
    audit_actions: list[str] = []

    async def stream(request: ChatRequest):
        assert request.origin == "workspace_auto_analysis"
        assert request.persist_messages is False
        assert request.run_id
        yield f'event: ready\ndata: {{"run_id":"{request.run_id}","conversation_id":null,"origin":"workspace_auto_analysis"}}\n\n'
        yield f'event: final\ndata: {{"run_id":"{request.run_id}","conversation_id":null,"artifact":{{"run_id":"{request.run_id}","origin":"workspace_auto_analysis"}},"text":"Done"}}\n\n'

    monkeypatch.setattr(app_module, "_require_workspace_action", lambda *_args: None)
    monkeypatch.setattr(app_module, "_audit_required", lambda _request, _workspace, action, *_args: audit_actions.append(action))
    monkeypatch.setattr(app_module, "orchestrate_chat", stream)

    response = TestClient(app_module.app).post("/api/workspaces/ws-separation/auto-analyze", json={})

    assert response.status_code == 200
    payload = response.json()
    assert payload["run_id"]
    assert payload["conversation_id"] is None
    assert audit_actions == ["analysis.run"]


def test_latest_analysis_keeps_canonical_run_id_without_conversation() -> None:
    analysis = workspace_store._last_analysis_from_final(
        {
            "run_id": "run-auto-4",
            "conversation_id": None,
            "text": "Completed",
            "artifact": {
                "run_id": "run-auto-4",
                "origin": "workspace_auto_analysis",
                "conversation_id": None,
                "feasibility": {"verdict": "conditional", "dimensions": [{"name": "evidence", "score": 3}]},
            },
        }
    )

    assert analysis["run_id"] == "run-auto-4"
    assert "conversation_id" not in analysis


def test_persisted_human_message_links_its_distinct_run(monkeypatch) -> None:
    calls: list[tuple] = []
    monkeypatch.setattr(orchestrator, "append_message", lambda *args, **kwargs: calls.append(("message", args, kwargs)))
    monkeypatch.setattr(orchestrator, "link_run", lambda *args, **kwargs: calls.append(("link", args, kwargs)))

    orchestrator._persist_user_message(
        "conversation-3",
        "ws-separation",
        "A real question",
        run_id="run-chat-3",
    )

    assert calls[0][0] == "message"
    assert calls[1] == ("link", ("conversation-3",), {"workspace_id": "ws-separation", "run_id": "run-chat-3"})


def test_data_send_analysis_is_classified_as_automatic_analysis() -> None:
    request = ChatRequest(
        workspace_id="ws-separation",
        message="Analyze the selected asset",
        origin="data_send_analysis",
        persist_messages=False,
        ui_context={"mode": "data_workbench_analysis"},
    )

    assert orchestrator._is_auto_analyze_request(request) is True


def test_analysis_conversation_separation_is_explicitly_feature_gated(monkeypatch) -> None:
    monkeypatch.delenv("DF_SEPARATE_ANALYSIS_CONVERSATIONS", raising=False)
    assert orchestrator.separation_enabled() is False

    monkeypatch.setenv("DF_SEPARATE_ANALYSIS_CONVERSATIONS", "1")
    assert orchestrator.separation_enabled() is True
