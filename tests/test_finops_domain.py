from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from backend.finops.models import (
    CacheEvidence,
    EstimatedCost,
    FinOpsRequestEvent,
    TokenUsage,
)
from backend.finops.normalization import normalize_run_event
from backend.finops.reconciliation import reconcile_events


def _event(**overrides: object) -> FinOpsRequestEvent:
    payload: dict[str, object] = {
        "request_ref": "req_3b7c9f1d4a2e",
        "occurred_at": datetime(2026, 7, 24, 1, 2, tzinfo=timezone.utc),
        "call_class": "model",
        "tenant_ref": "tenant-a",
        "department_id": None,
        "workspace_id": "ws-a",
        "actor_ref": "actor_3f12",
        "run_id": "run-a",
        "agent_id": "df-coordinator",
        "model": "gpt-5-mini",
        "deployment": "gpt-5-mini-prod",
        "route": "analysis",
        "execution_kind": "full_analysis",
        "status": "succeeded",
        "error_category": None,
        "latency_ms": 1200,
        "tokens": TokenUsage(input=80, output=20, total=100),
        "cache": CacheEvidence(state="miss", eligible=True),
        "gateway_coverage": "app_observed",
        "estimated_cost": EstimatedCost(
            amount=0.0012,
            currency="USD",
            status="estimated",
            price_card_revision="price-7",
        ),
        "evidence_state": "observed",
        "correlation_ref": "corr_9a21",
        "usage_source": "provider",
        "streaming": False,
    }
    payload.update(overrides)
    return FinOpsRequestEvent.model_validate(payload)


def test_finops_event_rejects_raw_provider_and_content_fields() -> None:
    payload = _event().model_dump()
    payload["provider_response_id"] = "resp-secret"
    with pytest.raises(ValidationError):
        FinOpsRequestEvent.model_validate(payload)

    payload = _event().model_dump()
    payload["prompt"] = "private prompt"
    with pytest.raises(ValidationError):
        FinOpsRequestEvent.model_validate(payload)


def test_normalize_run_event_hashes_identity_and_preserves_unknown_token_categories() -> None:
    event = normalize_run_event(
        {
            "run_id": "run-raw",
            "workspace_id": "ws-a",
            "status": "completed",
            "completed_at": "2026-07-24T01:02:03Z",
            "actor": {"actor_id": "member-object-id", "tenant_id": "tenant-a"},
            "message": "must never enter the ledger",
            "models": [
                {
                    "agent": "df-coordinator",
                    "deployment": "gpt-5-mini-prod",
                    "route": "analysis",
                    "latency_ms": 800,
                    "response_id": "provider-response-id",
                    "usage": {"prompt": 12, "completion": 3, "total": 15},
                }
            ],
        },
        model_index=0,
        tenant_id="tenant-a",
        hmac_secret="test-secret",
    )

    assert event.actor_ref.startswith("actor_")
    assert "member-object-id" not in event.actor_ref
    assert event.tokens == TokenUsage(
        input=12,
        output=3,
        cached_input=None,
        reasoning=None,
        total=15,
    )
    public = event.model_dump(mode="json")
    assert "message" not in public
    assert "response_id" not in public
    assert "provider_response_id" not in public


def test_reconciliation_prefers_observed_provider_usage_and_never_sums_apim_usage() -> None:
    app_event = _event()
    apim_event = _event(
        tokens=TokenUsage(input=90, output=30, total=120),
        gateway_coverage="apim_governed",
        evidence_state="estimated",
        usage_source="apim",
        latency_ms=1300,
    )

    result = reconcile_events(app_event, apim_event)

    assert result.tokens.total == 100
    assert result.tokens.input == 80
    assert result.gateway_coverage == "apim_governed"
    assert result.usage_source == "provider"


def test_reconciliation_uses_estimated_apim_streaming_usage_only_when_provider_usage_missing() -> None:
    app_event = _event(
        tokens=TokenUsage(),
        evidence_state="partial",
        usage_source="application",
        streaming=True,
    )
    apim_event = _event(
        tokens=TokenUsage(input=40, output=10, total=50),
        gateway_coverage="apim_governed",
        evidence_state="estimated",
        usage_source="apim",
        streaming=True,
    )

    result = reconcile_events(app_event, apim_event)

    assert result.tokens.total == 50
    assert result.evidence_state == "estimated"
    assert result.usage_source == "apim"


def test_apim_correlation_is_a_validated_public_trace_key_not_a_provider_response_id() -> None:
    event = _event(apim_correlation_id="4f8b0f37b5824af5a2ac7ed9129ee70b")
    public = event.model_dump(mode="json")
    assert public["apim_correlation_id"] == "4f8b0f37b5824af5a2ac7ed9129ee70b"
    with pytest.raises(ValidationError):
        _event(apim_correlation_id="not-an-apim-correlation")
