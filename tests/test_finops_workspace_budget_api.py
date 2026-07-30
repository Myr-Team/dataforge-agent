from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from backend.app import app
import backend.finops.member_budget_router as budget_router


class _WorkspaceBudgetService:
    def __init__(self) -> None:
        self.notification_payload: dict[str, Any] | None = None

    def list_budgets(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "items": [],
            "cursor": {"next": None, "limit": kwargs.get("limit", 50)},
            "freshness": "recorded",
            "coverage": "request_estimated_cost",
            "data_status": "complete",
            "currency": "USD",
        }

    def save_notification(self, **kwargs: Any) -> dict[str, Any]:
        self.notification_payload = dict(kwargs["payload"])
        return {
            "recipient_email": kwargs["payload"]["recipient_email"],
            "sender_display_name": "DataForge",
            "subject_template": "Budget alert",
            "body_template": "Budget threshold reached",
            "enabled": False,
            "revision": 1,
        }


def _client(monkeypatch, *, workspace_role: str | None) -> tuple[TestClient, _WorkspaceBudgetService]:
    monkeypatch.setenv("DF_FINOPS_MEMBER_BUDGETS_ENABLED", "1")
    monkeypatch.setenv("DF_FINOPS_EMAIL_CONFIGURATION_ENABLED", "1")
    monkeypatch.setenv("DF_FINOPS_HMAC_SECRET", "test-secret")
    service = _WorkspaceBudgetService()
    monkeypatch.setattr(budget_router, "get_member_budget_service", lambda: service)
    monkeypatch.setattr(
        budget_router,
        "actor_from_request",
        lambda *_args, **_kwargs: {
            "tenant_id": "tenant-a",
            "actor_id": "owner-a",
            "roles": [],
            "source": "easy_auth",
        },
    )
    monkeypatch.setattr(budget_router, "is_trusted_tenant_identity", lambda _actor: True)
    monkeypatch.setattr(
        budget_router,
        "active_workspace_role",
        lambda workspace_id, _actor: workspace_role if workspace_id == "ws-demo" else None,
        raising=False,
    )
    monkeypatch.setattr(
        budget_router,
        "record_audit_event",
        lambda *_args, **_kwargs: {"event_id": "audit-safe"},
    )
    return TestClient(app), service


def test_workspace_owner_can_read_budgets_without_dedicated_app_role(monkeypatch) -> None:
    client, _service = _client(monkeypatch, workspace_role="owner")

    response = client.get("/api/finops/member-budgets?workspace_id=ws-demo")

    assert response.status_code == 200


def test_workspace_viewer_cannot_read_or_mutate_budget_configuration(monkeypatch) -> None:
    client, service = _client(monkeypatch, workspace_role="viewer")

    response = client.get("/api/finops/member-budgets?workspace_id=ws-demo")

    assert response.status_code == 403
    assert response.json()["detail"] == "Workspace administrator role required"
    assert service.notification_payload is None


def test_budget_routes_require_an_explicit_workspace_scope(monkeypatch) -> None:
    client, _service = _client(monkeypatch, workspace_role="owner")

    response = client.get("/api/finops/member-budgets")

    assert response.status_code == 422
    assert response.json()["detail"] == "workspace_id is required"


def test_notification_configuration_accepts_a_direct_admin_email(monkeypatch) -> None:
    client, service = _client(monkeypatch, workspace_role="admin")

    response = client.put(
        "/api/finops/notification-settings?workspace_id=ws-demo",
        json={
            "recipient_email": "demo-admin@example.test",
            "sender_display_name": "DataForge",
            "subject_template": "{{member_name}} budget alert",
            "body_template": "Usage reached {{usage_percent}}.",
            "enabled": False,
            "base_revision": 0,
        },
    )

    assert response.status_code == 200
    assert response.json()["item"]["recipient_email"] == "demo-admin@example.test"
    assert service.notification_payload is not None
    assert service.notification_payload["recipient_email"] == "demo-admin@example.test"
