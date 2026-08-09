from __future__ import annotations

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
    assert response.json() == {
        "authenticated": True,
        "name": "owner",
        "email": "owner@contoso.com",
        "identity_provider": "microsoft_entra",
        "identity_source": "trusted_proxy",
    }
    serialized = response.text.lower()
    assert "object-id" not in serialized
    assert "tenant-id" not in serialized
    assert "claims" not in serialized
    assert "groups" not in serialized


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
