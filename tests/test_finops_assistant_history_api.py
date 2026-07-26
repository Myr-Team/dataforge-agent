from __future__ import annotations

from fastapi.testclient import TestClient

import backend.finops.router as finops_router
from backend.app import app
from backend.finops.assistant_store import InMemoryAssistantConversationStore
from auth_fixtures import trusted_headers


def test_operations_ai_conversation_persists_and_clears(monkeypatch) -> None:
    store = InMemoryAssistantConversationStore()
    monkeypatch.setenv("DF_WEB_PROXY_SECRET", "test-proxy-secret")
    monkeypatch.setenv("DF_FINOPS_HMAC_SECRET", "finops-test-secret")
    monkeypatch.setenv("DF_FINOPS_READ_ENABLED", "1")
    monkeypatch.setenv("DF_FINOPS_SQL_ENABLED", "0")
    monkeypatch.setattr(
        finops_router,
        "get_finops_assistant_store",
        lambda: store,
        raising=False,
    )
    monkeypatch.setattr(
        finops_router,
        "_authorized_workspace_roles",
        lambda _actor: {"ws-a": "owner"},
    )
    monkeypatch.setattr(finops_router, "_tenant_ref", lambda _actor: "tenantref-a")
    monkeypatch.setattr(finops_router, "_actor_ref", lambda _actor: "actorref-a")
    client = TestClient(app)
    headers = trusted_headers(actor_id="owner-a", tenant_id="tenant-a")

    created = client.post(
        "/api/finops/assistant/conversations",
        headers=headers,
        json={"workspace_id": "ws-a", "title": "成本分析"},
    )
    assert created.status_code == 201
    conversation_ref = created.json()["conversation"]["conversation_ref"]

    listed = client.get(
        "/api/finops/assistant/conversations?workspace_id=ws-a",
        headers=headers,
    )
    assert listed.json()["items"][0]["conversation_ref"] == conversation_ref

    cleared = client.delete(
        f"/api/finops/assistant/conversations/{conversation_ref}?workspace_id=ws-a",
        headers=headers,
    )
    assert cleared.status_code == 204
