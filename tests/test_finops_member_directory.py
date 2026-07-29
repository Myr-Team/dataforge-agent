from __future__ import annotations

from datetime import datetime, timezone

import backend.control_plane as control_plane
from backend.finops.member_directory import MemberCostReader, MemberDirectory
from backend.finops.member_budgets import MemberCostSummary
from backend.finops.normalization import opaque_ref


def test_directory_deduplicates_trusted_member_by_tenant_actor_and_keeps_controlled_metadata() -> None:
    directory = MemberDirectory(
        identity_loader=lambda _workspace: [
            {
                "tenant_id": "tenant-a",
                "actor_id": "oid-a",
                "name": "Finance Admin",
                "email": "finance.admin@company.com",
                "role": "admin",
                "status": "active",
            }
        ],
        hmac_secret="test-secret",
        department_loader=lambda _tenant_ref, workspace_id: (
            "finance" if workspace_id == "ws-a" else None
        ),
    )

    items = directory.list_members("tenant-a", ("ws-a", "ws-b"))

    assert len(items) == 1
    assert items[0].member_ref == opaque_ref("actor", "tenant-a", "oid-a", secret="test-secret")
    assert items[0].display_name == "Finance Admin"
    assert items[0].email == "finance.admin@company.com"
    assert items[0].workspace_ids == ("ws-a", "ws-b")
    assert items[0].department_labels == ("finance",)
    assert "oid-a" not in items[0].model_dump_json()


def test_directory_rejects_untrusted_identities_and_retains_disabled_history() -> None:
    directory = MemberDirectory(
        identity_loader=lambda _workspace: [
            {
                "tenant_id": "tenant-a",
                "actor_id": "oid-disabled",
                "name": "Former Member",
                "email": "former@example.test",
                "role": "viewer",
                "status": "disabled",
            },
            {
                "tenant_id": "tenant-a",
                "actor_id": "",
                "name": "Untrusted",
                "email": "untrusted@example.test",
                "role": "viewer",
                "status": "active",
            },
        ],
        hmac_secret="test-secret",
    )

    items = directory.list_members("tenant-a", ("ws-a",))

    assert [item.identity_state for item in items] == ["inactive"]
    assert items[0].display_name == "Former Member"
    assert "oid-disabled" not in items[0].model_dump_json()


def test_cost_reader_uses_utc_month_bounds_and_returns_repository_cost_coverage() -> None:
    calls: list[tuple[str, str, str, tuple[str, ...]]] = []

    class _Repository:
        def summarize_member_costs(self, *, tenant_ref: str, from_value: str, to_value: str, workspace_ids: tuple[str, ...]):
            calls.append((tenant_ref, from_value, to_value, workspace_ids))
            return {
                "actor-safe": MemberCostSummary(
                    actor_ref="actor-safe",
                    estimated_spend_usd="190",
                    priced_requests=19,
                    total_requests=20,
                    primary_model="gpt-5.6-terra",
                )
            }

    values = MemberCostReader(_Repository()).summarize_month(
        "tenant-safe",
        datetime(2026, 7, 1, tzinfo=timezone.utc),
        datetime(2026, 8, 1, tzinfo=timezone.utc),
        ("ws-a", "ws-a", "ws-b"),
    )

    assert calls == [("tenant-safe", "2026-07-01T00:00:00Z", "2026-08-01T00:00:00Z", ("ws-a", "ws-b"))]
    assert values["actor-safe"].estimated_spend_usd == 190
    assert values["actor-safe"].pricing_coverage_pct == 95
    assert values["actor-safe"].data_status == "partial"
    assert values["actor-safe"].unpriced_requests == 1
    assert values["actor-safe"].currency == "USD"
    assert values["actor-safe"].freshness == "recorded"


def test_directory_filters_cross_tenant_metadata_before_hashing_or_projecting() -> None:
    directory = MemberDirectory(
        identity_loader=lambda _workspace: [
            {"tenant_id": "tenant-a", "actor_id": "actor-a", "name": "Allowed", "email": "allowed@example.test", "role": "viewer", "status": "active"},
            {"tenant_id": "tenant-other", "actor_id": "actor-other", "name": "Wrong Tenant", "email": "wrong@example.test", "role": "admin", "status": "active"},
        ],
        hmac_secret="test-secret",
    )

    values = directory.list_members("tenant-a", ("ws-a",))

    assert len(values) == 1
    assert values[0].display_name == "Allowed"
    assert "Wrong Tenant" not in values[0].model_dump_json()
    assert "actor-other" not in values[0].model_dump_json()


def test_cost_reader_empty_authorized_scope_returns_no_data_without_querying_repository() -> None:
    class _Repository:
        def summarize_member_costs(self, **_kwargs):
            raise AssertionError("empty scope must not query tenant-wide facts")

    assert MemberCostReader(_Repository()).summarize_month(
        "tenant-safe",
        datetime(2026, 7, 1, tzinfo=timezone.utc),
        datetime(2026, 8, 1, tzinfo=timezone.utc),
        (),
    ) == {}


def test_real_loader_projects_only_owner_and_accepted_invited_identities(monkeypatch) -> None:
    monkeypatch.setattr(
        control_plane,
        "_load_workspace_meta",
        lambda _workspace_id: {
            "workspace_owner": {"actor_id": "owner-raw", "tenant_id": "tenant-a", "name": "Owner", "email": "owner@example.test", "source": "easy_auth"},
            "workspace_members": [
                {"actor_id": "accepted-raw", "tenant_id": "tenant-a", "name": "Accepted", "email": "accepted@example.test", "role": "editor", "status": "active", "invitation_id": "invite-accepted"},
                {"actor_id": "disabled-raw", "tenant_id": "tenant-a", "name": "Disabled", "email": "disabled@example.test", "role": "viewer", "status": "disabled", "invitation_id": "invite-disabled"},
                {"actor_id": "pending-raw", "tenant_id": "tenant-a", "name": "Pending", "email": "pending@example.test", "role": "admin", "status": "active", "invitation_id": "invite-pending"},
            ],
            "workspace_invitation_events": [
                {"event_type": "state", "invitation_id": "invite-accepted", "state": "accepted", "accepted_identity": {"actor_id": "accepted-raw", "tenant_id": "tenant-a"}},
                {"event_type": "state", "invitation_id": "invite-disabled", "state": "accepted", "accepted_identity": {"actor_id": "disabled-raw", "tenant_id": "tenant-a"}},
                {"event_type": "state", "invitation_id": "invite-pending", "state": "pending", "accepted_identity": {}},
            ],
        },
    )
    directory = MemberDirectory(
        identity_loader=control_plane.workspace_finops_member_identities,
        hmac_secret="test-secret",
    )

    values = directory.list_members("tenant-a", ("ws-a",))

    by_state = {(item.identity_state, item.display_name, item.email) for item in values}
    assert ("active", "Owner", "owner@example.test") in by_state
    assert ("active", "Former member", "") in by_state
    assert ("inactive", "Former member", "") in by_state
    serialized = "".join(item.model_dump_json() for item in values)
    assert "-raw" not in serialized
    assert "pending@example.test" not in serialized
    assert "accepted@example.test" not in serialized
    assert "disabled@example.test" not in serialized
