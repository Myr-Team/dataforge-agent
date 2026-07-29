from __future__ import annotations

import pytest
from datetime import datetime, timezone

from backend.finops.acs_email import (
    AcsEmailError,
    AcsEmailSender,
    EmailMessage,
    acs_email_sender_from_environment,
    render_template,
)
from backend.finops.member_budget_repository import InMemoryMemberBudgetRepository
from backend.finops.member_budget_service import MemberBudgetService
from backend.finops.member_budgets import NotificationSetting


def test_template_renderer_accepts_only_approved_variables() -> None:
    assert render_template("{{member_name}} used {{usage_percent}}%", {"member_name": "Finance Admin", "usage_percent": "95"}) == "Finance Admin used 95%"
    with pytest.raises(ValueError, match="template_variable_not_allowed"):
        render_template("{{secret}}", {"secret": "marker"})
    with pytest.raises(ValueError, match="template_variable_not_allowed"):
        render_template("{{member_name}}", {"member_name": "Finance Admin", "secret": "marker"})
    with pytest.raises(ValueError, match="template_variable_not_allowed"):
        render_template("{{member_name.__class__}}", {"member_name": "Finance Admin"})


def test_sender_uses_token_credential_plain_text_and_bounded_poll() -> None:
    captured: dict[str, object] = {}

    class _Poller:
        def result(self, timeout: float):
            captured["timeout"] = timeout
            return {"status": "Succeeded"}

    class _Client:
        def begin_send(self, message, operation_id: str):
            captured["message"] = message
            captured["operation_id"] = operation_id
            return _Poller()

    result = AcsEmailSender(client=_Client(), sender_address="sender@example.test", poll_timeout_seconds=3).send(
        EmailMessage(recipient="admin@example.test", sender_display_name="DataForge", subject="[test] member budget", plain_text="test only"),
        operation_id="11111111-1111-5111-8111-111111111111",
    )
    assert result.state == "sent"
    assert captured["message"]["content"].get("html") is None
    assert captured["message"]["recipients"]["to"][0]["address"] == "admin@example.test"
    assert captured["timeout"] == 3


@pytest.mark.parametrize("error,category", [(PermissionError(), "permission_required"), (TimeoutError(), "timeout"), (RuntimeError("sdk detail"), "service_unavailable")])
def test_sender_redacts_sdk_failures(error: Exception, category: str) -> None:
    class _Client:
        def begin_send(self, *_args, **_kwargs):
            raise error

    with pytest.raises(AcsEmailError, match=f"^{category}$"):
        AcsEmailSender(client=_Client(), sender_address="sender@example.test").send(EmailMessage(recipient="admin@example.test", sender_display_name="DataForge", subject="subject", plain_text="body"), operation_id="11111111-1111-5111-8111-111111111111")


def test_sender_requires_endpoint_and_sender(monkeypatch) -> None:
    monkeypatch.delenv("DF_ACS_EMAIL_ENDPOINT", raising=False)
    monkeypatch.delenv("DF_ACS_EMAIL_SENDER_ADDRESS", raising=False)
    with pytest.raises(AcsEmailError, match="^not_configured$"):
        acs_email_sender_from_environment()


def test_sender_handles_sdk_shaped_timeout_auth_and_incomplete_lro() -> None:
    ServiceRequestTimeoutError = type("ServiceRequestTimeoutError", (Exception,), {})
    HttpResponseError = type("HttpResponseError", (Exception,), {"status_code": 403})

    class _IncompletePoller:
        def result(self, timeout: float):
            return {"status": "Succeeded"}

        def done(self) -> bool:
            return False

    class _Client:
        def __init__(self, outcome):
            self.outcome = outcome

        def begin_send(self, *_args, **_kwargs):
            if isinstance(self.outcome, Exception):
                raise self.outcome
            return self.outcome

    message = EmailMessage(recipient="admin@example.test", sender_display_name="DataForge", subject="subject", plain_text="body")
    for outcome, category in [(_IncompletePoller(), "timeout"), (ServiceRequestTimeoutError(), "timeout"), (HttpResponseError(), "permission_required")]:
        with pytest.raises(AcsEmailError, match=f"^{category}$"):
            AcsEmailSender(client=_Client(outcome), sender_address="sender@example.test").send(message, operation_id="11111111-1111-5111-8111-111111111111")


def test_test_sends_use_fresh_operation_ids() -> None:
    now = datetime.now(timezone.utc)
    repository = InMemoryMemberBudgetRepository()
    repository.save_notification_setting("tenant-safe", NotificationSetting(recipient_actor_ref="actor-safe", recipient_email="admin@example.test", sender_display_name="DataForge", subject_template="test", body_template="test", enabled=True, revision=1, created_by_ref="actor-owner", updated_by_ref="actor-owner", created_at=now, updated_at=now), base_revision=0)
    operation_ids: list[str] = []

    class _Sender:
        def send(self, _message, operation_id: str):
            operation_ids.append(operation_id)
            return type("Result", (), {"state": "sent", "sent_at": now, "safe_error_category": None})()

    service = MemberBudgetService(repository, None, None)
    service.send_test_email(tenant_ref="tenant-safe", active_admins={"actor-safe": "admin@example.test"}, sender=_Sender())
    service.send_test_email(tenant_ref="tenant-safe", active_admins={"actor-safe": "admin@example.test"}, sender=_Sender())
    assert len(operation_ids) == 2
    assert operation_ids[0] != operation_ids[1]
