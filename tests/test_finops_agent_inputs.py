from __future__ import annotations

from datetime import datetime, timezone

from backend.finops.agent_inputs import (
    build_finops_assistant_input,
    build_finops_agent_input,
    build_roi_agent_input,
)
from backend.finops.models import FinOpsRequestEvent, TokenUsage
from backend.finops.query import FinOpsQuery, FinOpsQueryService
from backend.finops.repository import InMemoryFinOpsRepository


def _query_service() -> tuple[FinOpsQuery, FinOpsQueryService]:
    repository = InMemoryFinOpsRepository()
    repository.upsert_events(
        [
            FinOpsRequestEvent.model_validate(
                {
                    "request_ref": "req_aaaaaaaaaaaa",
                    "occurred_at": datetime(2026, 7, 24, 2, 0, tzinfo=timezone.utc),
                    "call_class": "model",
                    "tenant_ref": "tenant-a",
                    "department_id": "commerce",
                    "workspace_id": "ws-a",
                    "agent_id": "df-coordinator",
                    "deployment": "gpt-5-mini",
                    "status": "succeeded",
                    "latency_ms": 900,
                    "tokens": TokenUsage(input=10, output=2, total=12),
                    "estimated_cost": {
                        "amount": 0.001,
                        "status": "estimated",
                        "price_card_revision": "price-1",
                    },
                    "evidence_state": "observed",
                }
            )
        ]
    )
    query = FinOpsQuery(
        tenant_ref="tenant-a",
        authorized_workspace_ids=("ws-a",),
        workspace_id="ws-a",
        from_value="2026-07-23T00:00:00Z",
        to_value="2026-07-25T00:00:00Z",
    )
    return query, FinOpsQueryService(repository)


def test_finops_agent_input_is_bounded_and_contains_only_scoped_evidence() -> None:
    query, service = _query_service()
    payload = build_finops_agent_input(
        query,
        service,
        anomalies=[
            {
                "anomaly_id": "anom-a",
                "policy_type": "daily_cost_budget",
                "severity": "warning",
                "status": "open",
                "observed_value": 82,
                "threshold_value": 80,
                "sample_count": 30,
                "internal_error": "must-not-escape",
                "prompt": "must-not-escape",
            }
        ],
        price_card_revision="price-1",
    )

    assert payload["status"] == "ready"
    assert payload["evidence_refs"] == ["req_aaaaaaaaaaaa"]
    assert payload["overview"]["metrics"]["requests"] == 1
    assert payload["anomalies"][0]["policy_type"] == "daily_cost_budget"
    serialized = str(payload)
    for forbidden in ("prompt", "answer", "raw identity", "internal_error", "must-not-escape"):
        assert forbidden not in serialized


def test_finops_agent_input_projects_only_requested_authorized_evidence() -> None:
    repository = InMemoryFinOpsRepository()
    repository.upsert_events(
        [
            FinOpsRequestEvent.model_validate(
                {
                    "request_ref": request_ref,
                    "occurred_at": datetime(2026, 7, 24, hour, 0, tzinfo=timezone.utc),
                    "call_class": "model",
                    "tenant_ref": "tenant-a",
                    "workspace_id": "ws-a",
                    "agent_id": "df-coordinator",
                    "deployment": "gpt-5-mini",
                    "status": "succeeded",
                    "latency_ms": latency_ms,
                    "tokens": TokenUsage(input=10, output=2, total=12),
                    "estimated_cost": {"amount": 0.001, "status": "estimated"},
                    "evidence_state": "observed",
                }
            )
            for request_ref, hour, latency_ms in (
                ("req_latency_authorized", 2, 6200),
                ("req_unselected_authorized", 3, 1800),
            )
        ]
    )
    query = FinOpsQuery(
        tenant_ref="tenant-a",
        authorized_workspace_ids=("ws-a",),
        workspace_id="ws-a",
        from_value="2026-07-23T00:00:00Z",
        to_value="2026-07-25T00:00:00Z",
    )

    payload = build_finops_agent_input(
        query,
        FinOpsQueryService(repository),
        evidence_refs=["req_latency_authorized"],
    )

    assert payload["evidence_refs"] == ["req_latency_authorized"]
    assert [item["ref"] for item in payload["evidence_catalog"]] == [
        "req_latency_authorized"
    ]
    assert "req_unselected_authorized" not in str(payload)

    empty = build_finops_agent_input(
        query,
        FinOpsQueryService(repository),
        evidence_refs=["req_not_authorized"],
    )
    assert empty["status"] == "insufficient_data"
    assert empty["evidence_refs"] == []


def test_finops_assistant_input_uses_single_compact_bootstrap() -> None:
    query, service = _query_service()

    class BootstrapOnlyService:
        def __init__(self) -> None:
            self.bootstrap_calls = 0

        def bootstrap(self, selected_query: FinOpsQuery) -> dict[str, object]:
            self.bootstrap_calls += 1
            return service.bootstrap(selected_query)

        def trends(self, *_args: object) -> object:
            raise AssertionError("assistant input must not load trends separately")

        def breakdowns(self, *_args: object) -> object:
            raise AssertionError("assistant input must not load breakdowns separately")

    compact_service = BootstrapOnlyService()
    event = service.events(query)[0]
    payload = build_finops_assistant_input(
        query,
        compact_service,
        metric_context={"metric_id": "estimated_cost", "label": "估算成本"},
        evidence_items=[event],
    )

    assert compact_service.bootstrap_calls == 1
    assert payload["status"] == "ready"
    assert payload["evidence_refs"] == ["req_aaaaaaaaaaaa"]
    assert payload["overview"]["metrics"]["requests"] == 1
    assert len(str(payload).encode("utf-8")) < 24_000


def test_roi_agent_input_accepts_only_verified_outcome_events() -> None:
    snapshot = {
        "status": "verified",
        "workspace_id": "ws-a",
        "window": {
            "from": "2026-07-23T00:00:00Z",
            "to": "2026-07-25T00:00:00Z",
        },
        "cost": {"total": 10, "currency": "USD", "status": "complete"},
        "business_value": {
            "total": 25,
            "currency": "USD",
            "status": "measured",
        },
        "verified_outcome_event_ids": ["outcome-verified"],
        "outcome_event_ids": ["outcome-verified", "outcome-unverified"],
        "lineage_complete": True,
    }
    outcomes = [
        {
            "event_id": "outcome-verified",
            "workspace_id": "ws-a",
            "observed_at": "2026-07-24T01:00:00Z",
            "observed_value": 12,
            "verification": {"status": "verified"},
            "business_value": {"value": 25, "currency": "USD", "status": "measured"},
            "actor": {"email": "must-not-escape@example.com"},
        },
        {
            "event_id": "outcome-unverified",
            "workspace_id": "ws-a",
            "observed_value": 99,
            "verification": {"status": "unverified"},
        },
    ]

    payload = build_roi_agent_input(
        "ws-a",
        snapshot["window"],
        snapshot,
        outcomes,
    )

    assert payload["status"] == "ready"
    assert payload["evidence_refs"] == ["outcome-verified"]
    assert [item["event_id"] for item in payload["verified_outcomes"]] == [
        "outcome-verified"
    ]
    assert "outcome-unverified" not in str(payload)
    assert "must-not-escape@example.com" not in str(payload)


def test_roi_agent_input_returns_evidence_gap_without_verified_outcome() -> None:
    payload = build_roi_agent_input(
        "ws-a",
        {
            "from": "2026-07-23T00:00:00Z",
            "to": "2026-07-25T00:00:00Z",
        },
        {
            "status": "measured",
            "cost": {"total": 10, "currency": "USD", "status": "complete"},
            "verified_outcome_event_ids": [],
        },
        [
            {
                "event_id": "outcome-unverified",
                "workspace_id": "ws-a",
                "verification": {"status": "unverified"},
            }
        ],
    )

    assert payload == {
        "status": "insufficient_data",
        "agent_kind": "roi",
        "workspace_id": "ws-a",
        "window": {
            "from": "2026-07-23T00:00:00Z",
            "to": "2026-07-25T00:00:00Z",
        },
        "evidence_refs": [],
        "evidence_gaps": ["已验证结果事件不足"],
    }
