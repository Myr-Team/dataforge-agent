from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from backend.roi_service import PriceCatalog, PriceEntry, build_roi_snapshot, member_chargeback


WINDOW = {"from": "2026-07-10T00:00:00Z", "to": "2026-07-11T00:00:00Z"}
PRICE_USD = {
    "version": "v1", "model": "gpt-5", "currency": "USD", "unit": "per_1m_tokens",
    "effective_from": "2026-07-01T00:00:00Z", "effective_to": None, "source": "approved-price-file",
    "input_per_1m": 2, "output_per_1m": 8,
}
PRICE_EUR = {**PRICE_USD, "version": "v2", "currency": "EUR", "model": "gpt-4", "input_per_1m": 3, "output_per_1m": 9}


def _run(*, run_id: str = "run-1", actor_id: str = "owner", trusted: bool = True, model: str = "gpt-5", currency_usage: dict | None = None) -> dict:
    return {
        "run_id": run_id, "workspace_id": "ws", "completed_at": "2026-07-10T12:00:00Z",
        "actor": {"actor_id": actor_id, "email": "telemetry-leak@example.com"}, "trusted_identity": trusted,
        "models": [{"model": model, "usage": currency_usage or {"input_tokens": 100, "output_tokens": 50}, "usage_event_id": f"usage-{run_id}"}],
    }


def _outcome(event_id: str, *, verified: bool) -> dict:
    return {
        "event_id": event_id, "workspace_id": "ws", "provenance": "observed", "observed_value": 1,
        "observed_at": "2026-07-10T13:00:00Z", "source": {"run_id": "run-1"},
        "actor": {"actor_id": "owner"}, "trusted_identity": True,
        "verification": ({"status": "verified", "verification_event_id": f"verify-{event_id}", "reviewer": {"actor_id": "reviewer"}, "trusted_identity": True} if verified else {"status": "unverified"}),
        "business_value": {"value": 10, "currency": "USD", "source": "ledger", "formula": "margin", "status": "measured"},
    }


def test_price_catalog_rejects_missing_fields_nonfinite_and_overlapping_windows() -> None:
    with pytest.raises(ValidationError):
        PriceEntry.model_validate({key: value for key, value in PRICE_USD.items() if key != "source"})
    with pytest.raises(ValidationError):
        PriceEntry.model_validate({**PRICE_USD, "input_per_1m": math.inf})
    with pytest.raises(ValueError, match="overlapping"):
        PriceCatalog.model_validate({"prices": [PRICE_USD, {**PRICE_USD, "version": "v2", "effective_from": "2026-07-15T00:00:00Z"}]})


def test_roi_requires_complete_token_split_and_all_business_outcomes_verified() -> None:
    partial = build_roi_snapshot("ws", WINDOW, runs=[_run(currency_usage={"total_tokens": 150})], outcomes=[], prices=[PRICE_USD])
    mixed = build_roi_snapshot("ws", WINDOW, runs=[_run()], outcomes=[_outcome("one", verified=True), _outcome("two", verified=False)], prices=[PRICE_USD], source_validator=lambda *_: True)

    assert partial["cost"]["total"] is None and partial["cost"]["status"] == "partial"
    assert mixed["status"] == "measured"
    assert mixed["unverified_outcome_event_ids"] == ["two"]


def test_chargeback_excludes_untrusted_events_and_uses_workspace_scoped_hmac_and_currency_groups() -> None:
    result = member_chargeback(
        "ws", WINDOW,
        runs=[_run(run_id="trusted"), _run(run_id="untrusted", actor_id="spoofed", trusted=False), _run(run_id="eur", model="gpt-4")],
        messages=[{"workspace_id": "ws", "time": "2026-07-10T12:00:00Z", "actor": {"actor_id": "owner"}, "trusted_identity": True, "message_id": "m1"}],
        tasks=[{"workspace_id": "ws", "created_at": "2026-07-10T12:00:00Z", "actor": {"actor_id": "owner"}, "trusted_identity": True, "task_id": "t1", "task_type": "analysis"}],
        memberships=[{"actor_id": "owner", "email": "owner@example.com", "name": "Owner", "role": "owner", "status": "active"}],
        prices=[PRICE_USD, PRICE_EUR], pseudonym_salt="salt",
    )

    assert len(result["groups"]) == 4
    assert result["totals"]["total"] is None and result["totals"]["status"] == "partial"
    assert "telemetry-leak@example.com" not in str(result)
    assert all("spoofed" not in str(row) for row in result["groups"])
    departed_ws = member_chargeback("ws", WINDOW, runs=[_run(actor_id="departed")], messages=[], tasks=[], memberships=[], prices=[PRICE_USD], pseudonym_salt="salt")
    departed_other = member_chargeback("other", WINDOW, runs=[{**_run(actor_id="departed"), "workspace_id": "other"}], messages=[], tasks=[], memberships=[], prices=[PRICE_USD], pseudonym_salt="salt")
    assert departed_ws["members"][0]["member"]["actor_id"] != departed_other["members"][0]["member"]["actor_id"]


def test_duplicate_usage_and_forged_source_are_rejected_from_snapshot() -> None:
    duplicate = build_roi_snapshot("ws", WINDOW, runs=[_run(), _run()], outcomes=[], prices=[PRICE_USD])
    forged = build_roi_snapshot("ws", WINDOW, runs=[_run()], outcomes=[_outcome("forged", verified=True)], prices=[PRICE_USD], source_validator=lambda *_: False)

    assert duplicate["usage"]["total_tokens"] == 150
    assert forged["status"] == "estimated"
