from __future__ import annotations

import pytest

from backend.model_policy import ModelRoute
from backend.workspace_model_config import (
    estimate_model_cost,
    normalize_workspace_price_card,
    validate_workspace_routing_policy,
)


def _routes() -> list[ModelRoute]:
    return [
        ModelRoute("sol", "gpt-5.6-sol", "GPT-5.6 Sol", frozenset({"chat", "analysis"})),
        ModelRoute("luna", "gpt-5.6-luna", "GPT-5.6 Luna", frozenset({"chat"})),
    ]


def test_workspace_policy_rejects_incompatible_route() -> None:
    policy = validate_workspace_routing_policy(
        {
            "assignments": {
                "full_analysis": {"primary_route_id": "sol", "fallback_route_id": "sol"},
                "direct_reply": {"primary_route_id": "luna", "fallback_route_id": "sol"},
            }
        },
        _routes(),
    )

    assert policy["assignments"]["full_analysis"]["primary_route_id"] == "sol"
    assert policy["assignments"]["direct_reply"]["primary_route_id"] == "luna"
    with pytest.raises(ValueError, match="capability"):
        validate_workspace_routing_policy(
            {"assignments": {"full_analysis": {"primary_route_id": "luna"}}},
            _routes(),
        )


def test_workspace_policy_uses_chat_capability_for_configured_follow_up() -> None:
    routes = [
        ModelRoute("chat", "gpt-5.6-chat", "Chat", frozenset({"chat"})),
        ModelRoute("followup", "gpt-5.6-followup", "Follow-up", frozenset({"followup"})),
    ]

    with pytest.raises(ValueError, match="capability"):
        validate_workspace_routing_policy(
            {"assignments": {"follow_up": {"primary_route_id": "followup"}}},
            routes,
        )

    policy = validate_workspace_routing_policy(
        {"assignments": {"follow_up": {"primary_route_id": "chat"}}},
        routes,
    )

    assert policy["assignments"]["follow_up"]["primary_route_id"] == "chat"


def test_workspace_policy_accepts_default_and_per_agent_routes() -> None:
    policy = validate_workspace_routing_policy(
        {
            "default_route_id": "sol",
            "agent_assignments": {
                "df-feasibility-analyst": {
                    "primary_route_id": "sol",
                    "fallback_route_id": "sol",
                }
            },
            "assignments": {},
        },
        _routes(),
    )

    assert policy["default_route_id"] == "sol"
    assert policy["agent_assignments"]["df-feasibility-analyst"][
        "primary_route_id"
    ] == "sol"
    with pytest.raises(ValueError, match="Agent"):
        validate_workspace_routing_policy(
            {
                "agent_assignments": {
                    "unknown-agent": {"primary_route_id": "sol"}
                }
            },
            _routes(),
        )


def test_price_card_estimate_requires_complete_usage_and_matching_price() -> None:
    card = normalize_workspace_price_card(
        {
            "currency": "USD",
            "entries": [
                {
                    "route_id": "sol",
                    "input_per_million": 2,
                    "output_per_million": 8,
                    "source_label": "Owner-maintained pricing reference",
                }
            ],
        },
        _routes(),
        revision=2,
        updated_at="2026-07-23T00:00:00Z",
    )

    estimate = estimate_model_cost(
        {"input_tokens": 1_000, "output_tokens": 500},
        {"route_id": "sol", "price_card_revision": 2},
        card,
    )

    assert estimate == {
        "status": "estimated",
        "currency": "USD",
        "amount": 0.006,
        "price_card_revision": 2,
        "route_id": "sol",
        "formula": "input_tokens/1_000_000*input_per_million + output_tokens/1_000_000*output_per_million",
    }
    assert estimate_model_cost(
        {"input_tokens": None, "output_tokens": 1},
        {"route_id": "sol"},
        card,
    ) == {"status": "unavailable", "reason": "usage_not_recorded"}
    assert estimate_model_cost(
        {"input_tokens": 1, "output_tokens": 1},
        {"route_id": "luna"},
        card,
    ) == {"status": "unavailable", "reason": "price_not_configured"}
