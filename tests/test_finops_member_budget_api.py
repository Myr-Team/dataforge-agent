from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
import pytest

from backend.app import app
from backend.finops.member_budget_repository import MemberBudgetConflictError
from backend.finops.member_budget_repository import InMemoryMemberBudgetRepository
from backend.finops.member_budget_service import MemberBudgetService
from backend.finops.member_budgets import BudgetAlert, MemberBudget
from backend.finops.member_directory import FinOpsMember, MemberMonthlyCost
from backend.finops.acs_email import AcsEmailError, EmailDeliveryResult
from backend.finops.budget_subjects import BudgetSubject
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

    def is_budget_member_authorized(self, **_kwargs: Any) -> bool:
        return True

    def save_budget(self, **_kwargs: Any) -> dict[str, Any]:
        self.writes += 1
        return {"budget_id": "budget-safe", "revision": 1}

    def disable_budget(self, **_kwargs: Any) -> dict[str, Any]:
        self.writes += 1
        return {"budget_id": "budget-safe", "enabled": False, "revision": 2}

    def get_notification(self, **_kwargs: Any) -> dict[str, Any] | None:
        return None

    def save_notification(self, **_kwargs: Any) -> dict[str, Any]:
        self.writes += 1
        return {
            "recipient_email": _kwargs["payload"].get("recipient_email"),
            "revision": 1,
        }

    def list_alerts(self, **_kwargs: Any) -> dict[str, Any]:
        return {"items": [], "cursor": {"next": None, "limit": _kwargs.get("limit", 50)}, "currency": "USD", "freshness": "recorded", "coverage": "request_estimated_cost", "data_status": "complete"}

    def send_test_email(self, **_kwargs: Any) -> EmailDeliveryResult:
        self.writes += 1
        return EmailDeliveryResult(state="sent", sent_at=None, safe_error_category=None)


class _WorkspaceScopedClient(TestClient):
    def request(self, method: str, url: Any, **kwargs: Any):
        path = str(url)
        if path.startswith("/api/finops/") and "workspace_id=" not in path:
            separator = "&" if "?" in path else "?"
            path = f"{path}{separator}workspace_id=ws-0"
        return super().request(method, path, **kwargs)


def _client(
    monkeypatch,
    *,
    roles: tuple[str, ...] = ("owner",),
    trusted: bool = True,
    app_roles: tuple[str, ...] = ("DataForge.FinOpsAdmin",),
) -> tuple[TestClient, _Service]:
    monkeypatch.setenv("DF_FINOPS_MEMBER_BUDGETS_ENABLED", "1")
    monkeypatch.setenv("DF_FINOPS_EMAIL_CONFIGURATION_ENABLED", "1")
    monkeypatch.setenv("DF_FINOPS_HMAC_SECRET", "test-secret")
    service = _Service()
    monkeypatch.setattr(budget_router, "get_member_budget_service", lambda: service)
    monkeypatch.setattr(
        budget_router,
        "actor_from_request",
        lambda *_args, **_kwargs: {
            "tenant_id": "tenant-a",
            "actor_id": "actor-a",
            "roles": list(app_roles),
            "source": "easy_auth" if trusted else "ui_context",
        },
    )
    monkeypatch.setattr(budget_router, "is_trusted_tenant_identity", lambda _actor: trusted)
    monkeypatch.setattr(
        budget_router,
        "active_workspace_role",
        lambda workspace_id, _actor: (
            roles[int(workspace_id.rsplit("-", 1)[-1])]
            if workspace_id.startswith("ws-")
            and workspace_id.rsplit("-", 1)[-1].isdigit()
            and int(workspace_id.rsplit("-", 1)[-1]) < len(roles)
            else None
        ),
    )
    monkeypatch.setattr(budget_router, "record_audit_event", lambda *_args, **_kwargs: {"event_id": "audit-safe"})
    return _WorkspaceScopedClient(app), service


def test_member_budget_routes_are_default_disabled(monkeypatch) -> None:
    monkeypatch.delenv("DF_FINOPS_MEMBER_BUDGETS_ENABLED", raising=False)
    assert TestClient(app).get("/api/finops/member-budgets").status_code == 404


def test_disabled_feature_hides_query_and_body_validation(monkeypatch) -> None:
    monkeypatch.delenv("DF_FINOPS_MEMBER_BUDGETS_ENABLED", raising=False)
    client = TestClient(app)
    assert client.get("/api/finops/member-budgets?limit=not-an-integer").status_code == 404
    assert client.post("/api/finops/member-budgets").status_code == 404
    assert client.put("/api/finops/notification-settings", json=["not", "an", "object"]).status_code == 404


def test_member_budget_rejects_untrusted_tenant(monkeypatch) -> None:
    client, _service = _client(monkeypatch, trusted=False)
    assert client.get("/api/finops/member-budgets").status_code == 403


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/finops/member-budgets?limit=invalid", None),
        ("GET", "/api/finops/member-budget-members?limit=invalid", None),
        ("POST", "/api/finops/member-budgets", ["invalid"]),
        ("PATCH", "/api/finops/member-budgets/budget-safe", ["invalid"]),
        ("POST", "/api/finops/member-budgets/budget-safe/disable", ["invalid"]),
        ("GET", "/api/finops/budget-alerts?limit=invalid", None),
    ],
)
def test_member_budget_routes_require_workspace_admin_before_service_audit_or_validation(
    monkeypatch,
    method: str,
    path: str,
    body: object,
) -> None:
    client, service = _client(monkeypatch, roles=("viewer",))
    monkeypatch.setattr(
        budget_router,
        "record_audit_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unauthorized route reached audit")),
    )
    for name in (
        "list_budgets",
        "list_eligible_members",
        "is_eligible_member",
        "is_budget_member_authorized",
        "save_budget",
        "disable_budget",
        "list_alerts",
    ):
        monkeypatch.setattr(
            service,
            name,
            lambda **_kwargs: (_ for _ in ()).throw(AssertionError("unauthorized route reached service")),
        )

    response = client.request(method, path, json=body)

    assert response.status_code == 403
    assert response.json()["detail"] == "Workspace administrator role required"


def test_workspace_budget_mutation_uses_explicit_audit_scope(monkeypatch) -> None:
    client, _service = _client(monkeypatch)
    audit_scopes: list[str] = []
    monkeypatch.setattr(
        budget_router,
        "record_audit_event",
        lambda _actor, _action, details, **_kwargs: audit_scopes.append(details["workspace_id"]),
    )

    response = client.post(
        "/api/finops/member-budgets",
        json={"member_ref": "actor-safe", "amount_usd": 200, "base_revision": 0},
    )

    assert response.status_code == 200
    assert audit_scopes == ["ws-0"]


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


def test_notification_rejects_invalid_email_and_hostile_payload(monkeypatch) -> None:
    client, service = _client(monkeypatch)
    bad_recipient = client.put(
        "/api/finops/notification-settings",
        json={"recipient_email": ["not", "an", "email"], "base_revision": 0},
    )
    assert bad_recipient.status_code == 422
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


def test_out_of_scope_budget_mutations_are_hidden_before_audit_or_mutation(monkeypatch) -> None:
    client, service = _client(monkeypatch)
    audits: list[object] = []
    monkeypatch.setattr(service, "is_budget_member_authorized", lambda **_kwargs: False)
    monkeypatch.setattr(budget_router, "record_audit_event", lambda *_args, **_kwargs: audits.append(object()))

    updated = client.patch(
        "/api/finops/member-budgets/budget-outside",
        json={"amount_usd": 300, "enabled": True, "base_revision": 1},
    )
    disabled = client.post(
        "/api/finops/member-budgets/budget-outside/disable",
        json={"base_revision": 1},
    )

    assert [updated.status_code, disabled.status_code] == [404, 404]
    assert not audits
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
    monkeypatch.setattr(
        budget_router,
        "actor_from_request",
        lambda *_args, **_kwargs: {
            "tenant_id": "tenant-a",
            "actor_id": "actor-a",
            "roles": ["DataForge.FinOpsAdmin"],
            "source": "easy_auth",
        },
    )
    monkeypatch.setattr(budget_router, "is_trusted_tenant_identity", lambda _actor: True)
    monkeypatch.setattr(
        budget_router,
        "active_workspace_role",
        lambda workspace_id, _actor: "owner" if workspace_id == "ws-safe" else None,
    )
    assert TestClient(app).get("/api/finops/member-budgets?workspace_id=ws-safe").status_code == 503


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
    assert alert_response.json()["data_status"] == "complete"


def _budget_alert(
    *,
    alert_id: str,
    pricing_coverage_pct: float | None,
    budget_id: str = "budget-safe",
    actor_ref: str = "actor-safe",
    budget_amount_usd: Decimal = Decimal("200"),
    estimated_spend_usd: Decimal = Decimal("190"),
) -> BudgetAlert:
    now = datetime(2026, 7, 29, tzinfo=timezone.utc)
    return BudgetAlert(
        alert_id=alert_id,
        tenant_ref="tenant-safe",
        budget_id=budget_id,
        actor_ref=actor_ref,
        period_key="2026-07",
        threshold_pct=95,
        budget_amount_usd=budget_amount_usd,
        estimated_spend_usd=estimated_spend_usd,
        pricing_coverage_pct=pricing_coverage_pct,
        budget_revision=1,
        notification_revision=1,
        delivery_state="sent",
        triggered_at=now,
        sent_at=now,
        updated_at=now,
    )


class _SafeAlertDirectory:
    def list_members(self, _tenant_id: str, _workspace_ids: tuple[str, ...]):
        return (
            FinOpsMember(
                member_ref="actor-safe",
                display_name="Finance Admin",
                email="finance@example.test",
                role="admin",
                identity_state="active",
                workspace_ids=("ws-safe",),
                department_labels=("finance",),
            ),
        )


def _seed_budget_subject(
    repository: InMemoryMemberBudgetRepository,
    *,
    subject_ref: str = "actor-safe",
    workspace_id: str = "ws-safe",
    display_name: str = "Finance Admin",
) -> None:
    repository.upsert_budget_subjects(
        "tenant-safe",
        (
            BudgetSubject(
                subject_ref=subject_ref,
                workspace_id=workspace_id,
                display_name=display_name,
                department_label="finance",
                primary_model="gpt-5.6-terra",
                enabled=True,
                revision=1,
                updated_at=datetime.now(timezone.utc),
            ),
        ),
    )


def test_alert_service_marks_successful_empty_ledger_complete() -> None:
    value = MemberBudgetService(InMemoryMemberBudgetRepository(), _SafeAlertDirectory(), None).list_alerts(
        tenant_ref="tenant-safe",
        identity_tenant_id="tenant-raw",
        workspace_ids=("ws-safe",),
        cursor=None,
        limit=50,
    )

    assert value["items"] == []
    assert value["data_status"] == "complete"


def test_alert_service_marks_full_pricing_coverage_complete_and_keeps_rows() -> None:
    repository = InMemoryMemberBudgetRepository()
    _seed_budget_subject(repository)
    alert = _budget_alert(alert_id="alert-complete", pricing_coverage_pct=100)
    assert repository.claim_alert(alert) is True

    value = MemberBudgetService(repository, _SafeAlertDirectory(), None).list_alerts(
        tenant_ref="tenant-safe",
        identity_tenant_id="tenant-raw",
        workspace_ids=("ws-safe",),
        cursor=None,
        limit=50,
    )

    assert value["data_status"] == "complete"
    assert [item["alert_id"] for item in value["items"]] == ["alert-complete"]


def test_alert_service_marks_partial_pricing_coverage_partial_and_api_preserves_it(monkeypatch) -> None:
    repository = InMemoryMemberBudgetRepository()
    _seed_budget_subject(repository)
    alert = _budget_alert(alert_id="alert-partial", pricing_coverage_pct=90)
    assert repository.claim_alert(alert) is True
    service_value = MemberBudgetService(repository, _SafeAlertDirectory(), None).list_alerts(
        tenant_ref="tenant-safe",
        identity_tenant_id="tenant-raw",
        workspace_ids=("ws-safe",),
        cursor=None,
        limit=50,
    )

    assert service_value["data_status"] == "partial"
    assert [item["alert_id"] for item in service_value["items"]] == ["alert-partial"]

    client, service = _client(monkeypatch)
    monkeypatch.setattr(service, "list_alerts", lambda **_kwargs: service_value)
    response = client.get("/api/finops/budget-alerts?limit=50")

    assert response.status_code == 200
    assert response.json()["data_status"] == "partial"
    assert [item["alert_id"] for item in response.json()["items"]] == ["alert-partial"]


def test_alert_api_hides_unknown_and_out_of_scope_budget_targets(monkeypatch) -> None:
    client, service = _client(monkeypatch)
    monkeypatch.setattr(
        service,
        "list_alerts",
        lambda **kwargs: (_ for _ in ()).throw(KeyError(kwargs.get("budget_id"))),
    )

    for budget_id in ("budget-outside", "budget-unknown"):
        response = client.get(f"/api/finops/budget-alerts?budget_id={budget_id}")
        assert response.status_code == 404
        assert response.json() == {"detail": "Not found"}
        assert budget_id not in response.text


def test_alert_list_filters_authorized_members_before_pagination() -> None:
    now = datetime.now(timezone.utc)
    repository = InMemoryMemberBudgetRepository()
    _seed_budget_subject(repository)
    for budget_id, member_ref, amount in (
        ("budget-outside", "actor-outside", Decimal("999")),
        ("budget-safe", "actor-safe", Decimal("200")),
    ):
        repository.save_budget(
            "tenant-safe",
            MemberBudget(
                member_ref=member_ref,
                amount_usd=amount,
                thresholds_pct=(80, 95, 100),
                enabled=True,
                budget_id=budget_id,
                revision=1,
                created_by_ref="actor-owner",
                updated_by_ref="actor-owner",
                created_at=now,
                updated_at=now,
            ),
            base_revision=0,
        )
    repository.claim_alert(
        _budget_alert(
            alert_id="alert-a-outside",
            budget_id="budget-outside",
            actor_ref="actor-outside",
            budget_amount_usd=Decimal("999"),
            estimated_spend_usd=Decimal("998"),
            pricing_coverage_pct=100,
        )
    )
    repository.claim_alert(
        _budget_alert(
            alert_id="alert-b-safe",
            pricing_coverage_pct=100,
        )
    )

    value = MemberBudgetService(repository, _SafeAlertDirectory(), None).list_alerts(
        tenant_ref="tenant-safe",
        identity_tenant_id="tenant-raw",
        workspace_ids=("ws-safe",),
        cursor=None,
        limit=1,
    )

    assert [item["alert_id"] for item in value["items"]] == ["alert-b-safe"]
    assert value["cursor"] == {"next": None, "limit": 1}
    assert "budget-outside" not in str(value)
    assert "actor-outside" not in str(value)
    assert "999" not in str(value)


def test_alert_budget_filter_hides_unknown_and_out_of_scope_targets() -> None:
    now = datetime.now(timezone.utc)
    repository = InMemoryMemberBudgetRepository()
    repository.save_budget(
        "tenant-safe",
        MemberBudget(
            member_ref="actor-outside",
            amount_usd=Decimal("999"),
            thresholds_pct=(80, 95, 100),
            enabled=True,
            budget_id="budget-outside",
            revision=1,
            created_by_ref="actor-other",
            updated_by_ref="actor-other",
            created_at=now,
            updated_at=now,
        ),
        base_revision=0,
    )
    repository.claim_alert(
        _budget_alert(
            alert_id="alert-outside",
            budget_id="budget-outside",
            actor_ref="actor-outside",
            budget_amount_usd=Decimal("999"),
            estimated_spend_usd=Decimal("998"),
            pricing_coverage_pct=100,
        )
    )

    class _Directory:
        def list_members(self, _tenant_id: str, _workspace_ids: tuple[str, ...]):
            return ()

    service = MemberBudgetService(repository, _Directory(), None)
    for budget_id in ("budget-outside", "budget-unknown"):
        with pytest.raises(KeyError, match=budget_id):
            service.list_alerts(
                tenant_ref="tenant-safe",
                identity_tenant_id="tenant-raw",
                workspace_ids=("ws-safe",),
                budget_id=budget_id,
                cursor=None,
                limit=50,
            )


def test_test_email_has_its_own_disabled_gate(monkeypatch) -> None:
    client, _service = _client(monkeypatch)
    monkeypatch.delenv("DF_FINOPS_EMAIL_CONFIGURATION_ENABLED", raising=False)
    monkeypatch.setenv("DF_FINOPS_EMAIL_ALERTS_ENABLED", "0")
    assert client.post("/api/finops/notification-settings/test-email").status_code == 404


def test_email_configuration_gate_hides_read_and_write_before_service_or_audit(monkeypatch) -> None:
    client, service = _client(monkeypatch)
    monkeypatch.delenv("DF_FINOPS_EMAIL_CONFIGURATION_ENABLED", raising=False)
    monkeypatch.setenv("DF_FINOPS_EMAIL_ALERTS_ENABLED", "1")
    audits: list[object] = []
    monkeypatch.setattr(
        service,
        "get_notification",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("disabled read reached service")),
    )
    monkeypatch.setattr(
        service,
        "save_notification",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("disabled write reached service")),
    )
    monkeypatch.setattr(
        service,
        "send_test_email",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("disabled test reached service")),
    )
    monkeypatch.setattr(
        budget_router,
        "actor_from_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("disabled route resolved identity")),
    )
    monkeypatch.setattr(budget_router, "record_audit_event", lambda *_args, **_kwargs: audits.append(object()))

    read = client.get("/api/finops/notification-settings")
    write = client.put(
        "/api/finops/notification-settings",
        json={"recipient_email": "admin@example.test", "base_revision": 0},
    )
    test_email = client.post("/api/finops/notification-settings/test-email")

    assert [read.status_code, write.status_code, test_email.status_code] == [404, 404, 404]
    assert [response.json()["detail"] for response in (read, write, test_email)] == [
        "email_configuration_disabled",
        "email_configuration_disabled",
        "email_configuration_disabled",
    ]
    assert not audits


def test_member_budget_gate_precedes_identity_workspace_service_and_body_validation(monkeypatch) -> None:
    client, service = _client(monkeypatch)
    monkeypatch.delenv("DF_FINOPS_MEMBER_BUDGETS_ENABLED", raising=False)
    monkeypatch.setattr(
        budget_router,
        "actor_from_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("disabled route resolved identity")),
    )
    monkeypatch.setattr(
        service,
        "list_budgets",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("disabled route reached service")),
    )
    monkeypatch.setattr(
        budget_router,
        "record_audit_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("disabled route reached audit")),
    )

    assert client.get("/api/finops/member-budgets?limit=invalid").status_code == 404
    assert client.post("/api/finops/member-budgets", json=["invalid"]).status_code == 404


def test_test_email_works_with_alerts_disabled_and_redacts_delivery(monkeypatch) -> None:
    client, service = _client(monkeypatch)
    monkeypatch.setenv("DF_FINOPS_EMAIL_CONFIGURATION_ENABLED", "1")
    monkeypatch.setenv("DF_FINOPS_EMAIL_ALERTS_ENABLED", "0")
    monkeypatch.setattr(budget_router, "acs_email_sender_from_environment", lambda: object())
    response = client.post("/api/finops/notification-settings/test-email")
    assert response.status_code == 200
    assert response.json()["state"] == "sent"
    assert response.json()["safe_error_category"] is None
    assert set(response.json()) == {"state", "sent_at", "safe_error_category"}
    assert "admin@example.test" not in response.text
    assert service.writes == 1


def test_test_email_reports_only_safe_adapter_categories(monkeypatch) -> None:
    client, service = _client(monkeypatch)
    monkeypatch.setenv("DF_FINOPS_EMAIL_CONFIGURATION_ENABLED", "1")
    monkeypatch.setattr(budget_router, "acs_email_sender_from_environment", lambda: (_ for _ in ()).throw(AcsEmailError("not_configured")))
    response = client.post("/api/finops/notification-settings/test-email")
    assert response.status_code == 200
    assert response.json()["safe_error_category"] == "not_configured"
    assert service.writes == 0


def test_test_email_rejects_missing_or_inactive_recipient_without_send(monkeypatch) -> None:
    client, service = _client(monkeypatch)
    monkeypatch.setenv("DF_FINOPS_EMAIL_CONFIGURATION_ENABLED", "1")
    monkeypatch.setattr(budget_router, "acs_email_sender_from_environment", lambda: object())
    monkeypatch.setattr(service, "send_test_email", lambda **_kwargs: (_ for _ in ()).throw(PermissionError("recipient must be an active tenant administrator")))
    response = client.post("/api/finops/notification-settings/test-email")
    assert response.status_code == 403
    assert service.writes == 0


def test_test_email_requires_persisted_notification_settings(monkeypatch) -> None:
    client, service = _client(monkeypatch)
    monkeypatch.setenv("DF_FINOPS_EMAIL_CONFIGURATION_ENABLED", "1")
    monkeypatch.setattr(budget_router, "acs_email_sender_from_environment", lambda: object())
    monkeypatch.setattr(service, "send_test_email", lambda **_kwargs: (_ for _ in ()).throw(KeyError("notification_setting")))
    assert client.post("/api/finops/notification-settings/test-email").status_code == 404
    assert service.writes == 0


def test_notification_template_is_rejected_before_audit_or_persistence(monkeypatch) -> None:
    client, service = _client(monkeypatch)
    audits: list[object] = []
    monkeypatch.setattr(budget_router, "record_audit_event", lambda *_args, **_kwargs: audits.append(object()))
    response = client.put("/api/finops/notification-settings", json={"recipient_email": "admin@example.test", "subject_template": "{{member_name.__class__}}", "body_template": "ok", "enabled": False, "base_revision": 0})
    assert response.status_code == 422
    assert not audits
    assert service.writes == 0


def test_owner_lists_friendly_member_budget_with_partial_coverage() -> None:
    now = datetime.now(timezone.utc)
    repository = InMemoryMemberBudgetRepository()
    _seed_budget_subject(repository)
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


def test_owner_list_omits_budgets_for_members_outside_authorized_workspaces() -> None:
    now = datetime.now(timezone.utc)
    repository = InMemoryMemberBudgetRepository()
    _seed_budget_subject(repository)
    repository.save_budget(
        "tenant-safe",
        MemberBudget(
            member_ref="actor-safe",
            amount_usd=Decimal("200"),
            thresholds_pct=(80, 95, 100),
            enabled=True,
            budget_id="budget-safe",
            revision=1,
            created_by_ref="actor-owner",
            updated_by_ref="actor-owner",
            created_at=now,
            updated_at=now,
        ),
        base_revision=0,
    )
    repository.save_budget(
        "tenant-safe",
        MemberBudget(
            member_ref="actor-outside",
            amount_usd=Decimal("999"),
            thresholds_pct=(80, 95, 100),
            enabled=True,
            budget_id="budget-outside",
            revision=1,
            created_by_ref="actor-other",
            updated_by_ref="actor-other",
            created_at=now,
            updated_at=now,
        ),
        base_revision=0,
    )

    class _Directory:
        def list_members(self, _tenant_id: str, _workspace_ids: tuple[str, ...]):
            return (
                FinOpsMember(
                    member_ref="actor-safe",
                    display_name="Finance Admin",
                    email="finance@example.test",
                    role="admin",
                    identity_state="active",
                    workspace_ids=("ws-safe",),
                    department_labels=("finance",),
                ),
            )

    class _Costs:
        def summarize_month(self, *_args: Any):
            return {}

    service = MemberBudgetService(repository, _Directory(), _Costs())
    value = service.list_budgets(
        tenant_ref="tenant-safe",
        identity_tenant_id="tenant-raw",
        workspace_ids=("ws-safe",),
        cursor=None,
        limit=1,
    )

    assert [item["budget_id"] for item in value["items"]] == ["budget-safe"]
    assert value["cursor"]["next"] is None
    assert "budget-outside" not in str(value)
    assert "actor-outside" not in str(value)
    assert "999" not in str(value)
    assert "Former member" not in str(value)
    assert service.is_budget_member_authorized(
        tenant_ref="tenant-safe",
        budget_id="budget-safe",
        identity_tenant_id="tenant-raw",
        workspace_ids=("ws-safe",),
    )
    assert not service.is_budget_member_authorized(
        tenant_ref="tenant-safe",
        budget_id="budget-outside",
        identity_tenant_id="tenant-raw",
        workspace_ids=("ws-safe",),
    )
