from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from backend.roi_service import (
    CostSummary,
    RoiWindowError,
    build_roi_snapshot,
    member_chargeback,
    parse_time_window,
)


PRICES = [
    {
        "version": "2026-07",
        "model": "gpt-5",
        "currency": "USD",
        "unit": "per_1m_tokens",
        "input_per_1m": 2.0,
        "output_per_1m": 8.0,
        "effective_from": "2026-07-01T00:00:00Z",
        "effective_to": None,
        "source": "pricing-fixture",
    }
]


def _window() -> dict[str, str]:
    return {"from": "2026-07-10T00:00:00Z", "to": "2026-07-11T00:00:00Z"}


def _run(actor_id: str = "actor-owner", model: str = "gpt-5") -> dict[str, object]:
    return {
        "run_id": "run-1",
        "workspace_id": "ws-roi",
        "started_at": "2026-07-10T12:00:00Z",
        "completed_at": "2026-07-10T12:01:00Z",
        "actor": {"actor_id": actor_id, "tenant_id": "tenant-a", "email": "spoofed@example.com", "name": "Spoofed", "source": "easy_auth"},
        "trusted_identity": True,
        "models": [
            {
                "model": model,
                "usage": {"input_tokens": 1_000, "output_tokens": 500, "total_tokens": 1_500},
            }
        ],
    }


def _outcome(*, verified: bool = False) -> dict[str, object]:
    verification: dict[str, object] = {"status": "unverified"}
    if verified:
        verification = {
            "status": "verified",
            "verification_event_id": "verification-1",
            "reviewer": {"actor_id": "actor-reviewer", "tenant_id": "tenant-a"},
            "trusted_identity": True,
        }
    return {
        "event_id": "outcome-1",
        "workspace_id": "ws-roi",
        "provenance": "observed",
        "observed_value": 12,
        "observed_at": "2026-07-10T13:00:00Z",
        "source": {"run_id": "run-1"},
        "actor": {"actor_id": "actor-owner", "tenant_id": "tenant-a", "source": "easy_auth"},
        "trusted_identity": True,
        "verification": verification,
        "business_value": {
            "value": 250,
            "currency": "USD",
            "source": "finance-ledger-42",
            "formula": "attributed_incremental_margin",
            "status": "measured",
        },
    }


def _verification(*, reviewer: str = "actor-reviewer", tenant_id: str = "tenant-a") -> dict[str, object]:
    return {
        "event_id": "verification-1",
        "workspace_id": "ws-roi",
        "kind": "outcome_verification",
        "outcome_event_id": "outcome-1",
        "actor": {"actor_id": reviewer, "tenant_id": tenant_id, "source": "easy_auth"},
        "trusted_identity": True,
    }


def test_usage_without_outcome_is_estimated_and_never_cash_value() -> None:
    snapshot = build_roi_snapshot("ws-roi", _window(), runs=[_run()], outcomes=[], prices=PRICES)

    assert snapshot["status"] == "estimated"
    assert snapshot["business_value"] is None
    assert snapshot["time_value"]["cash_value"] is None


def test_missing_model_price_is_partial_and_never_zero_cost() -> None:
    snapshot = build_roi_snapshot("ws-roi", _window(), runs=[_run(model="unknown-model")], outcomes=[], prices=PRICES)

    assert snapshot["cost"]["total"] is None
    assert snapshot["cost"]["status"] == "partial"
    assert snapshot["cost"]["unpriced_models"] == ["unknown-model"]


def test_verified_state_requires_source_linked_outcome_and_independent_reviewer_event() -> None:
    missing_event = _outcome(verified=True)
    missing_event["verification"] = {"status": "verified", "reviewer": {"actor_id": "actor-reviewer"}}
    same_reviewer = _outcome(verified=True)
    same_reviewer["verification"] = {
        "status": "verified",
        "verification_event_id": "verification-2",
        "reviewer": {"actor_id": "actor-owner"},
    }

    assert build_roi_snapshot("ws-roi", _window(), runs=[_run()], outcomes=[missing_event], prices=PRICES, source_validator=lambda *_: True)["status"] == "measured"
    assert build_roi_snapshot("ws-roi", _window(), runs=[_run()], outcomes=[same_reviewer], prices=PRICES, source_validator=lambda *_: True)["status"] == "measured"
    assert build_roi_snapshot("ws-roi", _window(), runs=[_run()], outcomes=[_outcome(verified=True)], prices=PRICES, source_validator=lambda *_: True, verification_events=[_verification()])["status"] == "verified"


def test_case_insensitive_actor_identity_cannot_bypass_independent_review() -> None:
    outcome = _outcome(verified=True)
    outcome["actor"] = {"actor_id": "OWNER", "tenant_id": "Tenant-A"}
    snapshot = build_roi_snapshot("ws-roi", _window(), runs=[_run()], outcomes=[outcome], prices=PRICES, source_validator=lambda *_: True, verification_events=[_verification(reviewer="owner", tenant_id="tenant-a")])

    assert snapshot["status"] == "measured"


def test_verified_state_rejects_outcome_or_reviewer_without_tenant() -> None:
    outcome = _outcome(verified=True)
    outcome["actor"] = {"actor_id": "actor-owner", "source": "easy_auth"}
    missing_outcome_tenant = build_roi_snapshot("ws-roi", _window(), runs=[_run()], outcomes=[outcome], prices=PRICES, source_validator=lambda *_: True, verification_events=[_verification()])
    missing_reviewer_tenant = _outcome(verified=True)
    missing_reviewer_tenant["verification"]["reviewer"] = {"actor_id": "actor-reviewer", "source": "easy_auth"}
    snapshot = build_roi_snapshot("ws-roi", _window(), runs=[_run()], outcomes=[missing_reviewer_tenant], prices=PRICES, source_validator=lambda *_: True, verification_events=[_verification()])

    assert missing_outcome_tenant["status"] == "measured"
    assert snapshot["status"] == "measured"


def test_cost_summary_enforces_complete_partial_and_unknown_contracts() -> None:
    complete = CostSummary(total=1.25, status="complete", currency="USD", by_currency={"USD": 1.25})
    partial = CostSummary(total=None, status="partial", currency=None, by_currency={"USD": 1.25})
    unknown = CostSummary(total=None, status="unknown", currency=None, by_currency={})

    assert complete.total == 1.25 and partial.by_currency == {"USD": 1.25} and unknown.by_currency == {}
    for payload in (
        {"total": None, "status": "complete", "currency": "USD", "by_currency": {}},
        {"total": 1.25, "status": "partial", "currency": "USD", "by_currency": {"USD": 1.25}},
        {"total": None, "status": "unknown", "currency": None, "by_currency": {"USD": 1.25}},
    ):
        with pytest.raises(ValidationError):
            CostSummary.model_validate(payload)


def test_snapshot_excludes_other_workspace_outcomes_and_lists_evidence_assumptions() -> None:
    foreign = _outcome(verified=True)
    foreign["workspace_id"] = "ws-other"
    snapshot = build_roi_snapshot("ws-roi", _window(), runs=[_run()], outcomes=[foreign], prices=PRICES)

    assert snapshot["status"] == "estimated"
    assert snapshot["outcome_event_ids"] == []
    assert all({"source", "formula", "status"} <= set(item) for item in snapshot["assumptions"])


def test_chargeback_uses_actor_id_and_current_membership_not_telemetry_profile() -> None:
    result = member_chargeback(
        "ws-roi",
        _window(),
        runs=[_run()],
        messages=[{"workspace_id": "ws-roi", "updated_at": "2026-07-10T12:00:00Z", "actor": {"actor_id": "actor-departed", "tenant_id": "tenant-a", "email": "leak@example.com", "source": "easy_auth"}, "trusted_identity": True, "message_id": "message-departed"}],
        tasks=[{"workspace_id": "ws-other", "created_at": "2026-07-10T12:00:00Z", "actor": {"actor_id": "actor-other"}}],
        memberships=[{"actor_id": "actor-owner", "tenant_id": "tenant-a", "email": "owner@example.com", "name": "Owner", "status": "active"}],
        prices=PRICES,
        pseudonym_salt="test-salt",
    )

    owner = next(row for row in result["members"] if row["member"]["actor_id"] == "actor-owner")
    unknown = next(row for row in result["members"] if row["member"]["status"] == "unknown_or_departed")
    assert owner["member"]["email"] == "owner@example.com"
    assert "spoofed@example.com" not in str(result)
    assert "leak@example.com" not in str(result)
    assert unknown["member"]["email"] is None
    assert unknown["member"]["name"] is None
    assert unknown["member"]["actor_id"].startswith("actor_")


def test_message_only_chargeback_never_turns_missing_model_cost_into_zero() -> None:
    result = member_chargeback(
        "ws-roi",
        _window(),
        runs=[],
        messages=[{"workspace_id": "ws-roi", "updated_at": "2026-07-10T12:00:00Z", "actor": {"actor_id": "actor-owner", "tenant_id": "tenant-a", "source": "easy_auth"}, "trusted_identity": True, "message_id": "message-owner"}],
        tasks=[],
        memberships=[{"actor_id": "actor-owner", "tenant_id": "tenant-a", "email": "owner@example.com", "name": "Owner", "status": "active"}],
        prices=PRICES,
        pseudonym_salt="test-salt",
    )

    assert result["groups"][0]["cost"]["total"] is None
    assert result["groups"][0]["cost"]["status"] == "unknown"
    assert result["groups"][0]["cost"]["currency"] is None


def test_time_window_requires_utc_bounds_and_rejects_expensive_range() -> None:
    assert parse_time_window("2026-07-10T00:00:00Z", "2026-07-11T00:00:00Z")["from"] == "2026-07-10T00:00:00+00:00"
    with pytest.raises(RoiWindowError):
        parse_time_window("2026-07-10", "2026-07-11T00:00:00Z")
    with pytest.raises(RoiWindowError):
        parse_time_window("2026-07-01T00:00:00Z", "2026-08-02T00:00:00Z")
