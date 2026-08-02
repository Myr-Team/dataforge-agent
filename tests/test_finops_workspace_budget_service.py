from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.finops.acs_email import EmailDeliveryResult
from backend.finops.budget_subjects import BudgetSubject, budget_subject_ref
from backend.finops.member_budget_repository import InMemoryMemberBudgetRepository
from backend.finops.member_budget_service import MemberBudgetService


NOW = datetime(2026, 7, 30, 9, 0, tzinfo=timezone.utc)


class _NoDirectory:
    def list_members(self, *_args):
        raise AssertionError("manual budget subjects must not query the identity directory")


class _Costs:
    def summarize_month(self, *_args):
        return {}


class _Sender:
    def __init__(self) -> None:
        self.recipients: list[str] = []

    def send(self, message, operation_id: str) -> EmailDeliveryResult:
        assert operation_id
        self.recipients.append(message.recipient)
        return EmailDeliveryResult(
            state="sent",
            sent_at=NOW,
            safe_error_category=None,
        )


def _service() -> tuple[MemberBudgetService, InMemoryMemberBudgetRepository]:
    repository = InMemoryMemberBudgetRepository()
    repository.upsert_budget_subjects(
        "tenant-safe",
        (
            BudgetSubject(
                subject_ref="subject_finance",
                workspace_id="ws-demo",
                display_name="林晓 · 财务负责人",
                department_label="财务",
                primary_model="gpt-5.6-terra",
                enabled=True,
                revision=1,
                updated_at=NOW,
            ),
        ),
    )
    return MemberBudgetService(repository, _NoDirectory(), _Costs()), repository


def test_budget_subject_ref_is_stable_opaque_and_workspace_specific() -> None:
    first = budget_subject_ref(
        workspace_id="ws-demo",
        display_name="林晓 · 财务负责人",
        secret="test-secret",
    )
    second = budget_subject_ref(
        workspace_id="ws-demo",
        display_name="  林晓   ·   财务负责人 ",
        secret="test-secret",
    )
    other_workspace = budget_subject_ref(
        workspace_id="ws-other",
        display_name="林晓 · 财务负责人",
        secret="test-secret",
    )

    assert first == second
    assert first != other_workspace
    assert "林晓" not in first


def test_service_lists_only_workspace_budget_subject_display_names() -> None:
    service, _repository = _service()

    value = service.list_eligible_members(
        tenant_ref="tenant-safe",
        identity_tenant_id="tenant-raw",
        workspace_ids=("ws-demo",),
        cursor=None,
        limit=50,
    )

    assert value["items"] == [
        {
            "member_ref": "subject_finance",
            "display_name": "林晓 · 财务负责人",
            "role": "member",
            "identity_state": "active",
            "workspace_ids": ("ws-demo",),
            "department_labels": ("财务",),
            "primary_model": "gpt-5.6-terra",
        }
    ]


def test_direct_email_must_be_tested_before_automatic_alerts_can_be_enabled() -> None:
    service, repository = _service()
    created = service.save_notification(
        tenant_ref="tenant-safe",
        actor_ref="admin-safe",
        payload={
            "recipient_email": "demo-admin@example.test",
            "sender_display_name": "DataForge",
            "subject_template": "{{member_name}} budget alert",
            "body_template": "Usage {{usage_percent}}.",
            "enabled": False,
            "base_revision": 0,
        },
    )

    assert created["recipient_email"] == "demo-admin@example.test"
    assert created["test_email_succeeded_at"] is None
    with pytest.raises(ValueError, match="test_email_required"):
        service.save_notification(
            tenant_ref="tenant-safe",
            actor_ref="admin-safe",
            payload={
                "recipient_email": "demo-admin@example.test",
                "enabled": True,
                "base_revision": 1,
            },
        )

    sender = _Sender()
    result = service.send_test_email(tenant_ref="tenant-safe", sender=sender)

    assert result.state == "sent"
    assert sender.recipients == ["demo-admin@example.test"]
    assert repository.get_notification_setting("tenant-safe").test_email_succeeded_at == NOW

    enabled = service.save_notification(
        tenant_ref="tenant-safe",
        actor_ref="admin-safe",
        payload={
            "recipient_email": "demo-admin@example.test",
            "enabled": True,
            "base_revision": 1,
        },
    )
    assert enabled["enabled"] is True
