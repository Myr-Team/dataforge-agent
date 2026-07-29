from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient

from backend.app import app
from backend.finops.member_budget_repository import MemberBudgetConflictError
from backend.finops.member_budget_repository import InMemoryMemberBudgetRepository
from backend.finops.member_budget_service import MemberBudgetService
from backend.finops.member_budgets import MemberBudget
from backend.finops.member_directory import FinOpsMember, MemberMonthlyCost
import backend.finops.member_budget_router as budget_router


class _Service:
    def __init__(self) -> None:
        self.writes = 0

    def list_budgets(self, **_kwargs: Any) -> dict[str, Any]:
        return {"items": [], "cursor": {"next": None, "limit": 50}, "freshness": "recorded", "coverage": "request_estimated_cost", "currency": "USD"}

    def save_budget(self, **_kwargs: Any) -> dict[str, Any]:
        self.writes += 1
        return {"budget_id": "budget-safe", "revision": 1}

    def disable_budget(self, **_kwargs: Any) -> dict[str, Any]:
        self.writes += 1
        return {"budget_id": "budget-safe", "enabled": False, "revision": 2}

    def get_notification(self, **_kwargs: Any) -> dict[str, Any] | None:
        return None

    def save_notification(self, *, active_admins: dict[str, str], **_kwargs: Any) -> dict[str, Any]:
        if "actor-safe" not in active_admins:
            raise PermissionError("recipient must be an active tenant administrator")
        self.writes += 1
        return {"recipient_actor_ref": "actor-safe", "revision": 1}

    def list_alerts(self, **_kwargs: Any) -> dict[str, Any]:
        return {"items": [], "currency": "USD", "freshness": "recorded"}


def _client(monkeypatch, *, roles: tuple[str, ...] = ("owner",), trusted: bool = True) -> tuple[TestClient, _Service]:
    monkeypatch.setenv("DF_FINOPS_MEMBER_BUDGETS_ENABLED", "1")
    monkeypatch.setenv("DF_FINOPS_HMAC_SECRET", "test-secret")
    service = _Service()
    monkeypatch.setattr(budget_router, "get_member_budget_service", lambda: service)
    monkeypatch.setattr(budget_router, "actor_from_request", lambda *_args, **_kwargs: {"tenant_id": "tenant-a", "actor_id": "actor-a"})
    monkeypatch.setattr(budget_router, "is_trusted_tenant_identity", lambda _actor: trusted)
    monkeypatch.setattr(budget_router, "list_workspaces", lambda: [{"workspace_id": f"ws-{index}"} for index, _role in enumerate(roles)])
    role_by_workspace = {f"ws-{index}": role for index, role in enumerate(roles)}
    monkeypatch.setattr(budget_router, "active_workspace_role", lambda workspace_id, _actor: role_by_workspace[workspace_id])
    monkeypatch.setattr(budget_router, "record_audit_event", lambda *_args, **_kwargs: {"event_id": "audit-safe"})
    monkeypatch.setattr(budget_router, "_active_admins", lambda *_args: {"actor-safe": "admin@example.test"})
    return TestClient(app), service


def test_member_budget_routes_are_default_disabled(monkeypatch) -> None:
    monkeypatch.delenv("DF_FINOPS_MEMBER_BUDGETS_ENABLED", raising=False)
    assert TestClient(app).get("/api/finops/member-budgets").status_code == 404


def test_member_budget_rejects_member_role(monkeypatch) -> None:
    client, _service = _client(monkeypatch, roles=("viewer",))
    assert client.get("/api/finops/member-budgets").status_code == 403


def test_member_budget_rejects_mixed_roles_and_untrusted_tenant(monkeypatch) -> None:
    client, _service = _client(monkeypatch, roles=("owner", "editor"))
    assert client.get("/api/finops/member-budgets").status_code == 403
    client, _service = _client(monkeypatch, trusted=False)
    assert client.get("/api/finops/member-budgets").status_code == 403


def test_stale_member_budget_write_returns_409_without_mutation(monkeypatch) -> None:
    client, service = _client(monkeypatch)
    monkeypatch.setattr(service, "save_budget", lambda **_kwargs: (_ for _ in ()).throw(MemberBudgetConflictError("stale")))
    response = client.patch("/api/finops/member-budgets/budget-safe", json={"base_revision": 1, "amount_usd": 300, "thresholds_pct": [80, 95, 100], "enabled": True})
    assert response.status_code == 409
    assert service.writes == 0


def test_mutation_requires_audit_before_persistence(monkeypatch) -> None:
    client, service = _client(monkeypatch)
    monkeypatch.setattr(budget_router, "record_audit_event", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("audit down")))
    response = client.post("/api/finops/member-budgets", json={"member_ref": "actor-safe", "amount_usd": 200, "thresholds_pct": [80, 95, 100], "enabled": True, "base_revision": 0})
    assert response.status_code == 503
    assert service.writes == 0


def test_notification_rejects_arbitrary_recipient_and_hostile_payload(monkeypatch) -> None:
    client, service = _client(monkeypatch)
    monkeypatch.setattr(budget_router, "_active_admins", lambda *_args: {})
    bad_recipient = client.put("/api/finops/notification-settings", json={"recipient_actor_ref": "actor-other", "base_revision": 0})
    assert bad_recipient.status_code == 403
    hostile = client.post("/api/finops/member-budgets", json={"member_ref": "actor-safe", "amount_usd": 200, "base_revision": 0, "tenant_ref": "tenant-other"})
    assert hostile.status_code == 422
    assert service.writes == 0


def test_removed_member_is_visible_but_has_no_alert_recipient(monkeypatch) -> None:
    client, _service = _client(monkeypatch)
    monkeypatch.setattr(budget_router, "_active_admins", lambda *_args: {})
    response = client.put("/api/finops/notification-settings", json={"recipient_actor_ref": "former-member", "base_revision": 0})
    assert response.status_code == 403


def test_owner_lists_friendly_member_budget_with_partial_coverage() -> None:
    now = datetime.now(timezone.utc)
    repository = InMemoryMemberBudgetRepository()
    repository.save_budget("tenant-safe", MemberBudget(member_ref="actor-safe", amount_usd=Decimal("200"), thresholds_pct=(80, 95, 100), enabled=True, budget_id="budget-safe", revision=1, created_by_ref="actor-owner", updated_by_ref="actor-owner", created_at=now, updated_at=now), base_revision=0)

    class _Directory:
        def list_members(self, _tenant_id: str, _workspace_ids: tuple[str, ...]):
            return (FinOpsMember(member_ref="actor-safe", display_name="Finance Admin", email="finance@example.test", role="admin", identity_state="active", workspace_ids=("ws-safe",), department_labels=("finance",)),)

    class _Costs:
        def summarize_month(self, *_args: Any):
            return {"actor-safe": MemberMonthlyCost(actor_ref="actor-safe", estimated_spend_usd=Decimal("190"), priced_requests=19, total_requests=20, unpriced_requests=1, pricing_coverage_pct=95, data_status="partial")}

    value = MemberBudgetService(repository, _Directory(), _Costs()).list_budgets(tenant_ref="tenant-safe", identity_tenant_id="tenant-raw", workspace_ids=("ws-safe",), cursor=None, limit=50)
    item = value["items"][0]
    assert item["member"]["display_name"] == "Finance Admin"
    assert item["progress"]["estimated_spend_usd"] == 190
    assert item["progress"]["pricing_coverage_pct"] == 95
    assert item["data_status"] == "partial"
    assert "oid-" not in str(value)
