from __future__ import annotations

import backend.conversation_store as conversations
from backend.schemas import ConversationDetailResponse


def _configure_local_store(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(conversations, "CONVERSATION_DIR", tmp_path / "conversations")
    monkeypatch.setattr(conversations, "download_blob_json", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(conversations, "upload_blob_json", lambda *_args, **_kwargs: None)


def test_human_conversation_tracks_its_linked_run(tmp_path, monkeypatch) -> None:
    _configure_local_store(tmp_path, monkeypatch)

    conversations.append_message(
        "conversation-human",
        workspace_id="ws-separation",
        role="user",
        text="Please summarize the analysis",
    )
    conversations.link_run("conversation-human", workspace_id="ws-separation", run_id="run-chat-2")

    assert conversations.get_conversation("conversation-human")["linked_run_ids"] == ["run-chat-2"]


def test_default_conversation_list_hides_only_explicit_system_activity(tmp_path, monkeypatch) -> None:
    _configure_local_store(tmp_path, monkeypatch)
    conversations.append_message("conversation-human", workspace_id="ws-separation", role="user", text="A real question")
    conversations.append_message("conversation-system", workspace_id="ws-separation", role="system", text="Legacy auto analysis")
    system = conversations.get_conversation("conversation-system")
    system["visibility"] = "system_activity"
    conversations._persist_conversation(system)

    assert [item["conversation_id"] for item in conversations.list_conversations("ws-separation")] == ["conversation-human"]
    assert {item["conversation_id"] for item in conversations.list_conversations("ws-separation", include_system=True)} == {
        "conversation-human",
        "conversation-system",
    }


def test_conversation_detail_exposes_linked_runs_without_exposing_system_activity() -> None:
    response = ConversationDetailResponse.model_validate(
        {
            "conversation_id": "conversation-human",
            "workspace_id": "ws-separation",
            "origin": "conversation",
            "linked_run_ids": ["run-chat-4"],
            "messages": [],
        }
    )

    assert response.origin == "conversation"
    assert response.linked_run_ids == ["run-chat-4"]
