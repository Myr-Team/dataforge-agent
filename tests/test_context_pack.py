from __future__ import annotations

import json

import pytest

import backend.conversation_store as conversation_store
from backend.schemas import ChatRequest


def _configure_local_store(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(conversation_store, "CONVERSATION_DIR", tmp_path / "conversations")
    monkeypatch.setattr(conversation_store, "download_blob_json", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(conversation_store, "upload_blob_json", lambda *_args, **_kwargs: None)


def test_context_pack_is_scoped_bounded_and_invalidated_by_evidence_revision() -> None:
    from backend.context_pack import build_context_pack

    request = ChatRequest(
        workspace_id="ws-a",
        conversation_id="conv-a",
        message="Compare the pilot options",
    )
    facts = [
        {
            "fact_id": f"fact-{index}",
            "scope": "ws-a:conv-a",
            "kind": "verified_constraint",
            "text": f"Constraint {index}",
        }
        for index in range(10)
    ]

    pack = build_context_pack(
        request=request,
        profile={"revision": "data-r2", "profile_summary": "Two candidate areas"},
        analysis={"revision": "run-r4", "verdict": "conditional", "evidence_refs": ["doc-1", "doc-2"]},
        facts=facts,
    )
    revised = build_context_pack(
        request=request,
        profile={"revision": "data-r2", "profile_summary": "Two candidate areas"},
        analysis={"revision": "run-r5", "verdict": "conditional", "evidence_refs": ["doc-1", "doc-2"]},
        facts=facts,
    )

    assert pack.scope == {"workspace_id": "ws-a", "conversation_id": "conv-a"}
    assert len(pack.durable_facts) <= 6
    assert pack.fingerprint != ""
    assert "Compare the pilot options" not in pack.serialized_for_telemetry
    assert pack.fingerprint != revised.fingerprint


def test_context_pack_excludes_cross_scope_and_disallowed_facts() -> None:
    from backend.context_pack import build_context_pack

    pack = build_context_pack(
        request=ChatRequest(workspace_id="ws-a", conversation_id="conv-a", message="What changed?"),
        profile={"revision": "data-r2"},
        analysis={"revision": "run-r4", "evidence_refs": ["doc-1"]},
        facts=[
            {
                "fact_id": "fact-1",
                "scope": "ws-a:conv-a",
                "kind": "selected_metric",
                "text": "North zone dwell time is the anchor metric",
            },
            {
                "fact_id": "fact-2",
                "scope": "ws-a:conv-b",
                "kind": "verified_constraint",
                "text": "Other conversation fact",
            },
            {
                "fact_id": "fact-3",
                "scope": "ws-b:conv-a",
                "kind": "accepted_scope",
                "text": "Other workspace fact",
            },
            {
                "fact_id": "fact-4",
                "scope": "ws-a:conv-a",
                "kind": "freeform_note",
                "text": "Should not be admitted",
            },
        ],
    )

    assert pack.durable_facts == ("North zone dwell time is the anchor metric",)
    telemetry = json.loads(pack.serialized_for_telemetry)
    assert telemetry["durable_fact_ids"] == ["fact-1"]
    assert telemetry["durable_fact_kinds"] == ["selected_metric"]


def test_context_pack_fingerprint_ignores_raw_fact_text() -> None:
    from backend.context_pack import build_context_pack

    request = ChatRequest(workspace_id="ws-a", conversation_id="conv-a", message="Refine it")
    first = build_context_pack(
        request=request,
        profile={"revision": "data-r2"},
        analysis={"revision": "run-r4", "evidence_refs": ["doc-1"]},
        facts=[
            {
                "fact_id": "fact-1",
                "scope": "ws-a:conv-a",
                "kind": "verified_constraint",
                "text": "Budget is capped at 50k",
            }
        ],
    )
    second = build_context_pack(
        request=request,
        profile={"revision": "data-r2"},
        analysis={"revision": "run-r4", "evidence_refs": ["doc-1"]},
        facts=[
            {
                "fact_id": "fact-1",
                "scope": "ws-a:conv-a",
                "kind": "verified_constraint",
                "text": "The budget changed wording but not identity",
            }
        ],
    )

    assert first.fingerprint == second.fingerprint


def test_record_durable_fact_is_allowlisted_and_scoped(tmp_path, monkeypatch) -> None:
    _configure_local_store(tmp_path, monkeypatch)
    conversation_store.append_message("conv-a", workspace_id="ws-a", role="user", text="Seed")

    stored = conversation_store.record_durable_fact(
        "conv-a",
        workspace_id="ws-a",
        fact={
            "kind": "verified_constraint",
            "text": "Budget is capped at 50k",
            "fact_id": "fact-1",
            "source_run_id": "run-1",
        },
    )
    conversation_store.record_durable_fact(
        "conv-b",
        workspace_id="ws-a",
        fact={"kind": "selected_metric", "text": "Ignore other conversation", "fact_id": "fact-2"},
    )

    assert stored["scope"] == "ws-a:conv-a"
    assert stored["fact_id"] == "fact-1"
    assert conversation_store.conversation_durable_facts("conv-a", workspace_id="ws-a") == [stored]
    assert conversation_store.conversation_durable_facts("conv-a", workspace_id="ws-b") == []

    with pytest.raises(ValueError, match="durable fact kind"):
        conversation_store.record_durable_fact(
            "conv-a",
            workspace_id="ws-a",
            fact={"kind": "freeform_note", "text": "Not allowed"},
        )
