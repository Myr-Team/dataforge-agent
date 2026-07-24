from __future__ import annotations

import pytest

from backend.finops.management import (
    FinOpsManagementService,
    InMemoryManagementRepository,
    PriceCardItem,
    estimate_request_cost,
)
from backend.finops.models import TokenUsage


def test_workspace_can_have_only_one_department_and_unassigned_is_explicit() -> None:
    service = FinOpsManagementService(InMemoryManagementRepository())
    service.create_department(
        tenant_ref="tenant-a",
        department_id="engineering",
        display_name="Engineering",
        actor_ref="actor-owner",
    )
    service.create_department(
        tenant_ref="tenant-a",
        department_id="finance",
        display_name="Finance",
        actor_ref="actor-owner",
    )
    service.assign_workspace(
        tenant_ref="tenant-a",
        workspace_id="ws-a",
        department_id="engineering",
        actor_ref="actor-owner",
    )
    service.assign_workspace(
        tenant_ref="tenant-a",
        workspace_id="ws-a",
        department_id="finance",
        actor_ref="actor-owner",
    )

    assert service.workspace_department("tenant-a", "ws-a") == "finance"
    service.assign_workspace(
        tenant_ref="tenant-a",
        workspace_id="ws-a",
        department_id=None,
        actor_ref="actor-owner",
    )
    assert service.workspace_department("tenant-a", "ws-a") is None


def test_price_card_cost_matches_manual_token_math() -> None:
    item = PriceCardItem(
        deployment="gpt-5-mini",
        input_per_million=2.0,
        output_per_million=8.0,
        cached_input_per_million=0.5,
        reasoning_per_million=10.0,
    )
    cost = estimate_request_cost(
        TokenUsage(
            input=1_000_000,
            output=500_000,
            cached_input=200_000,
            reasoning=100_000,
            total=1_600_000,
        ),
        item,
    )
    # cached_input is a priced subset of input, not an additional token pool.
    assert cost == pytest.approx(6.7)


def test_price_card_requires_different_reviewer_and_explicit_action_flag_to_activate() -> None:
    service = FinOpsManagementService(InMemoryManagementRepository())
    revision = service.create_price_card(
        tenant_ref="tenant-a",
        actor_ref="actor-author",
        items=[
            {
                "deployment": "gpt-5-mini",
                "input_per_million": 2,
                "output_per_million": 8,
            }
        ],
    )
    with pytest.raises(PermissionError):
        service.review_price_card(
            tenant_ref="tenant-a",
            revision_id=revision.revision_id,
            actor_ref="actor-author",
        )
    reviewed = service.review_price_card(
        tenant_ref="tenant-a",
        revision_id=revision.revision_id,
        actor_ref="actor-reviewer",
    )
    assert reviewed.status == "under_review"
    with pytest.raises(PermissionError):
        service.activate_price_card(
            tenant_ref="tenant-a",
            revision_id=revision.revision_id,
            actor_ref="actor-reviewer",
            actions_enabled=False,
        )
    active = service.activate_price_card(
        tenant_ref="tenant-a",
        revision_id=revision.revision_id,
        actor_ref="actor-reviewer",
        actions_enabled=True,
    )
    assert active.status == "active"


def test_policy_payload_is_typed_and_rejects_scripts_or_arbitrary_xml() -> None:
    service = FinOpsManagementService(InMemoryManagementRepository())
    policy = service.create_policy(
        tenant_ref="tenant-a",
        actor_ref="actor-owner",
        policy_type="error_rate",
        configuration={"threshold_pct": 5, "minimum_requests": 20, "window_minutes": 15},
    )
    assert policy.configuration["threshold_pct"] == 5
    with pytest.raises(ValueError):
        service.create_policy(
            tenant_ref="tenant-a",
            actor_ref="actor-owner",
            policy_type="error_rate",
            configuration={"threshold_pct": 5, "minimum_requests": 20, "window_minutes": 15, "script": "rm -rf"},
        )

    token_policy = service.create_policy(
        tenant_ref="tenant-a",
        actor_ref="actor-owner",
        policy_type="token_spike",
        configuration={"multiplier": 2.5, "lookback_days": 7},
    )
    unpriced_policy = service.create_policy(
        tenant_ref="tenant-a",
        actor_ref="actor-owner",
        policy_type="unpriced_requests",
        configuration={"threshold_pct": 4},
    )
    assert token_policy.configuration == {"multiplier": 2.5, "lookback_days": 7}
    assert unpriced_policy.configuration == {"threshold_pct": 4.0}
