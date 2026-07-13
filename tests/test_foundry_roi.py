from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from backend.foundry_roi import (
    FoundryRoiStatus,
    ProviderRoiSnapshot,
    discover_foundry_roi,
    read_foundry_roi,
    reconcile_roi,
)


WINDOW = {"from": "2026-07-01T00:00:00+00:00", "to": "2026-07-02T00:00:00+00:00"}


def local_snapshot(amount: float = 100.0, *, status: str = "verified") -> dict:
    return {
        "workspace_id": "ws-1",
        "window": WINDOW,
        "generated_at": "2026-07-02T01:00:00+00:00",
        "status": status,
        "business_value": {"total": amount, "currency": "USD", "by_currency": {"USD": amount}, "status": "measured"},
        "outcome_event_ids": ["outcome-1"],
        "verified_outcome_event_ids": ["outcome-1"],
    }


def provider_snapshot(amount: float = 90.0, *, status: str = "estimated") -> ProviderRoiSnapshot:
    return ProviderRoiSnapshot(
        window=WINDOW,
        observed_at="2026-07-02T01:05:00+00:00",
        provider_version="2026-07-preview",
        status=status,
        business_value={"amount": amount, "currency": "USD", "unit": "currency"},
        mapped_run_ids=["run-1"],
        mapped_outcome_event_ids=["outcome-1"],
    )


def test_environment_flag_alone_does_not_mean_configured(monkeypatch) -> None:
    monkeypatch.setenv("DF_FOUNDRY_ROI_ENABLED", "1")

    status = discover_foundry_roi()

    assert status.state == "not_configured"
    assert status.configured is False
    assert "DF_FOUNDRY_ROI_ENABLED" not in status.model_dump_json()


def test_missing_provider_does_not_attempt_network_or_claim_connection(monkeypatch) -> None:
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", "https://project.services.ai.azure.com/api/projects/demo")
    monkeypatch.setenv("FOUNDRY_AGENT_ID", "agent-demo")

    status = discover_foundry_roi()

    assert status.state == "not_configured"
    assert status.configured is False
    assert "provider" in status.reason.lower()


def test_provider_must_discover_target_before_connected(monkeypatch) -> None:
    class Provider:
        def discover(self):
            return FoundryRoiStatus(state="connected", configured=True, reason="surface discovered")

        def read(self, window):
            return provider_snapshot()

    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", "https://project.services.ai.azure.com/api/projects/demo")
    monkeypatch.setenv("FOUNDRY_AGENT_ID", "agent-demo")

    result = read_foundry_roi(WINDOW, provider=Provider())

    assert result["status"]["state"] == "connected"
    assert result["snapshot"]["provider_version"] == "2026-07-preview"
    assert "project.services" not in str(result)
    assert "agent-demo" not in str(result)


def test_provider_failure_is_unavailable_and_local_reconciliation_is_unchanged(monkeypatch) -> None:
    class FailingProvider:
        def discover(self):
            raise RuntimeError("token=secret response body")

        def read(self, window):  # pragma: no cover - discovery prevents this call
            raise AssertionError("unexpected read")

    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", "https://project.services.ai.azure.com/api/projects/demo")
    monkeypatch.setenv("FOUNDRY_AGENT_ID", "agent-demo")

    provider = read_foundry_roi(WINDOW, provider=FailingProvider())
    reconciled = reconcile_roi(local=local_snapshot(), provider=provider)

    assert provider["status"]["state"] == "unavailable"
    assert "secret" not in str(provider)
    assert reconciled["local"]["business_value"]["total"] == 100.0
    assert reconciled["provider"] is None
    assert reconciled["difference"] is None


def test_provider_values_are_reconciled_not_substituted_or_promoted() -> None:
    result = reconcile_roi(local=local_snapshot(100.0, status="verified"), provider=provider_snapshot(90.0))

    assert result["local"]["business_value"]["total"] == 100.0
    assert result["local"]["status"] == "verified"
    assert result["provider"]["business_value"]["amount"] == 90.0
    assert result["provider"]["status"] == "estimated"
    assert result["difference"] == {"amount": -10.0, "currency": "USD", "unit": "currency"}
    assert result["reconciliation"]["status"] == "reconciled"


@pytest.mark.parametrize("amount", [math.nan, -1.0])
def test_provider_rejects_non_finite_or_negative_amounts(amount: float) -> None:
    with pytest.raises(ValidationError):
        provider_snapshot(amount)


def test_reconciliation_requires_matching_window_currency_unit_and_lineage() -> None:
    base = provider_snapshot().model_dump(mode="json")
    no_lineage = {**base, "mapped_outcome_event_ids": []}
    wrong_window = {**base, "window": {"from": "2026-07-03T00:00:00+00:00", "to": "2026-07-04T00:00:00+00:00"}}
    wrong_currency = {**base, "business_value": {"amount": 90.0, "currency": "EUR", "unit": "currency"}}

    for provider, reason in ((no_lineage, "lineage"), (wrong_window, "window"), (wrong_currency, "currency")):
        result = reconcile_roi(local=local_snapshot(), provider=provider)
        assert result["difference"] is None
        assert reason in result["reconciliation"]["reason"]
