from __future__ import annotations

from datetime import datetime, timezone

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

    items = directory.list_members("tenant-safe", ("ws-a", "ws-b"))

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

    items = directory.list_members("tenant-safe", ("ws-a",))

    assert [item.identity_state for item in items] == ["inactive"]
    assert items[0].display_name == "Former Member"
    assert "oid-disabled" not in items[0].model_dump_json()


def test_cost_reader_uses_utc_month_bounds_and_returns_repository_cost_coverage() -> None:
    calls: list[tuple[str, str, str]] = []

    class _Repository:
        def summarize_member_costs(self, *, tenant_ref: str, from_value: str, to_value: str):
            calls.append((tenant_ref, from_value, to_value))
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
    )

    assert calls == [("tenant-safe", "2026-07-01T00:00:00Z", "2026-08-01T00:00:00Z")]
    assert values["actor-safe"].estimated_spend_usd == 190
    assert values["actor-safe"].pricing_coverage_pct == 95
    assert values["actor-safe"].data_status == "partial"
