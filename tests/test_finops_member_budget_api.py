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
        return {"items": [], "cursor": {"next": None, "limit": _kwargs.get("limit", 50)}, "freshness": "recorded", "coverage": "request_estimated_cost", "data_status": "unavailable", "currency": "USD"}

    def list_eligible_members(self, **_kwargs: Any) -> dict[str, Any]:
        return {"items": [{"member_ref": "actor-safe", "display_name": "Finance Admin", "identity_state": "active"}], "cursor": {"next": None, "limit": 50}, "freshness": "recorded", "coverage": "trusted_member_directory", "data_status": "complete", "currency": "USD"}

    def is_eligible_member(self, **_kwargs: Any) -> bool:
        return True

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
        return {"items": [], "cursor": {"next": None, "limit": _kwargs.get("limit", 50)}, "currency": "USD", "freshness": "recorded", "coverage": "request_estimated_cost", "data_status": "unavailable"}


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


def test_disabled_feature_hides_query_and_body_validation(monkeypatch) -> None:
    monkeypatch.delenv("DF_FINOPS_MEMBER_BUDGETS_ENABLED", raising=False)
    client = TestClient(app)
    assert client.get("/api/finops/member-budgets?limit=not-an-integer").status_code == 404
    assert client.post("/api/finops/member-budgets").status_code == 404
    assert client.put("/api/finops/notification-settings", json=["not", "an", "object"]).status_code == 404


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


def test_unknown_member_is_rejected_before_audit_or_mutation(monkeypatch) -> None:
    client, service = _client(monkeypatch)
    events: list[object] = []
    monkeypatch.setattr(service, "is_eligible_member", lambda **_kwargs: False)
    monkeypatch.setattr(budget_router, "record_audit_event", lambda *_args, **_kwargs: events.append(object()))
    response = client.post("/api/finops/member-budgets", json={"member_ref": "actor-unknown", "amount_usd": 200, "thresholds_pct": [80, 95, 100], "enabled": True, "base_revision": 0})
    assert response.status_code == 404
    assert not events
    assert service.writes == 0


def test_strict_revision_and_boolean_types_reject_coercion(monkeypatch) -> None:
    client, service = _client(monkeypatch)
    fractional_revision = client.post("/api/finops/member-budgets", json={"member_ref": "actor-safe", "amount_usd": 200, "base_revision": 0.9})
    string_boolean = client.post("/api/finops/member-budgets", json={"member_ref": "actor-safe", "amount_usd": 200, "enabled": "false", "base_revision": 0})
    disable_fraction = client.post("/api/finops/member-budgets/budget-safe/disable", json={"base_revision": 1.9})
    assert [response.status_code for response in (fractional_revision, string_boolean, disable_fraction)] == [422, 422, 422]
    assert service.writes == 0


def test_decimal_amounts_are_accepted_while_invalid_values_stop_before_audit(monkeypatch) -> None:
    client, service = _client(monkeypatch)
    audits: list[object] = []
    monkeypatch.setattr(budget_router, "record_audit_event", lambda *_args, **_kwargs: audits.append(object()))
    created = client.post("/api/finops/member-budgets", json={"member_ref": "actor-safe", "amount_usd": 200.5, "base_revision": 0})
    updated = client.patch("/api/finops/member-budgets/budget-safe", json={"amount_usd": 200.5, "base_revision": 1})
    assert [response.status_code for response in (created, updated)] == [200, 200]
    assert service.writes == 2
    assert len(audits) == 2

    non_finite = client.post("/api/finops/member-budgets", content='{"member_ref":"actor-safe","amount_usd":NaN,"base_revision":0}', headers={"content-type": "application/json"})
    non_positive = client.post("/api/finops/member-budgets", json={"member_ref": "actor-safe", "amount_usd": 0, "base_revision": 0})
    assert [response.status_code for response in (non_finite, non_positive)] == [422, 422]
    assert service.writes == 2
    assert len(audits) == 2


def test_enabled_feature_requires_sql_unless_test_service_is_overridden(monkeypatch) -> None:
    monkeypatch.setenv("DF_FINOPS_MEMBER_BUDGETS_ENABLED", "1")
    monkeypatch.setenv("DF_FINOPS_HMAC_SECRET", "test-secret")
    monkeypatch.setenv("DF_FINOPS_SQL_ENABLED", "0")
    monkeypatch.setattr(budget_router, "_service", None)
    monkeypatch.setattr(budget_router, "actor_from_request", lambda *_args, **_kwargs: {"tenant_id": "tenant-a", "actor_id": "actor-a"})
    monkeypatch.setattr(budget_router, "is_trusted_tenant_identity", lambda _actor: True)
    monkeypatch.setattr(budget_router, "list_workspaces", lambda: [{"workspace_id": "ws-safe"}])
    monkeypatch.setattr(budget_router, "active_workspace_role", lambda *_args: "owner")
    assert TestClient(app).get("/api/finops/member-budgets").status_code == 503


def test_response_envelopes_include_budget_metadata_and_bounded_alert_cursor(monkeypatch) -> None:
    client, _service = _client(monkeypatch)
    budget_response = client.get("/api/finops/member-budgets?limit=2")
    alert_response = client.get("/api/finops/budget-alerts?limit=2")
    mutation_response = client.post("/api/finops/member-budgets", json={"member_ref": "actor-safe", "amount_usd": 200, "base_revision": 0})
    for response in (budget_response, alert_response, mutation_response):
        assert response.status_code == 200
        assert {"freshness", "coverage", "data_status", "currency"}.issubset(response.json())
        assert response.json()["currency"] == "USD"
    assert alert_response.json()["cursor"] == {"next": None, "limit": 2}


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
