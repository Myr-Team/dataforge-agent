from __future__ import annotations

import re

from fastapi.testclient import TestClient

from auth_fixtures import trusted_headers
from backend.app import app


def test_auth_session_exposes_only_safe_trusted_identity(monkeypatch) -> None:
    monkeypatch.setenv("DF_WEB_PROXY_SECRET", "test-proxy-secret")

    response = TestClient(app).get(
        "/api/auth/session",
        headers=trusted_headers(
            actor_id="entra-object-id-must-not-leak",
            tenant_id="entra-tenant-id-must-not-leak",
            email="owner@contoso.com",
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["authenticated"] is True
    assert payload["name"] == "owner"
    assert payload["email"] == "owner@contoso.com"
    assert payload["identity_provider"] == "microsoft_entra"
    assert payload["identity_source"] == "trusted_proxy"
    assert re.fullmatch(r"tenant_[A-Za-z0-9_-]{32,}", payload["tenant_ref"])
    assert re.fullmatch(r"actor_[A-Za-z0-9_-]{32,}", payload["actor_ref"])
    assert re.fullmatch(r"session_[A-Za-z0-9_-]{32,}", payload["session_ref"])
    serialized = response.text.lower()
    assert "object-id" not in serialized
    assert "tenant-id" not in serialized
    assert "claims" not in serialized
    assert "groups" not in serialized


def test_auth_session_reuses_a_signed_opaque_session_generation_for_one_browser(monkeypatch) -> None:
    monkeypatch.setenv("DF_WEB_PROXY_SECRET", "test-proxy-secret")
    client = TestClient(app)
    headers = trusted_headers(actor_id="actor-a", tenant_id="tenant-a", email="owner@contoso.com")

    first = client.get("/api/auth/session", headers=headers)
    second = client.get("/api/auth/session", headers=headers)
    changed_actor = client.get(
        "/api/auth/session",
        headers=trusted_headers(actor_id="actor-b", tenant_id="tenant-a", email="owner2@contoso.com"),
    )

    assert first.json()["session_ref"] == second.json()["session_ref"]
    assert first.json()["session_ref"] != changed_actor.json()["session_ref"]


def test_auth_session_does_not_accept_untrusted_browser_identity(monkeypatch) -> None:
    monkeypatch.setenv("DF_WEB_PROXY_SECRET", "test-proxy-secret")

    response = TestClient(app).get(
        "/api/auth/session",
        headers={
            "x-dataforge-actor": '{"name":"Demo Admin","email":"admin@contoso.com"}',
            "x-ms-client-principal-name": "admin@contoso.com",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "authenticated": False,
        "identity_provider": "microsoft_entra",
        "identity_source": "unavailable",
    }
