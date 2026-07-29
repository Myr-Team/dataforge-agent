from __future__ import annotations

import pytest

from backend.finops.acs_email import (
    AcsEmailError,
    AcsEmailSender,
    EmailMessage,
    acs_email_sender_from_environment,
    render_template,
)


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
