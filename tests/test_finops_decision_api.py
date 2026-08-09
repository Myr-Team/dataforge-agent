from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import backend.finops.router as finops_router
import backend.control_plane as control_plane
import backend.outcome_store as outcome_store
from auth_fixtures import trusted_headers
from backend.app import app
from backend.finops.anomalies import DetectedAnomaly
from backend.finops.anomaly_store import FinOpsAnomalyService, InMemoryAnomalyRepository
from backend.finops.models import FinOpsRequestEvent
from backend.finops.query import FinOpsQueryService
from backend.finops.query_cache import FinOpsCacheBusy
from backend.finops.repository import InMemoryFinOpsRepository
from backend.finops.risk_scans import InMemoryRiskScanRepository, RiskScanService


@pytest.fixture
def repository() -> InMemoryFinOpsRepository:
    value = InMemoryFinOpsRepository()
    value.upsert_events(
        [
            FinOpsRequestEvent.model_validate(
                {
                    "request_ref": "req_aaaaaaaaaaaa",
                    "occurred_at": datetime(2026, 7, 24, 2, 0, tzinfo=timezone.utc),
                    "call_class": "model",
                    "tenant_ref": "tenant-a",
                    "workspace_id": "ws-a",
                    "agent_id": "coordinator",
                    "deployment": "gpt-5-mini",
                    "status": "succeeded",
                    "tokens": {"total": 10},
                    "gateway_coverage": "apim_governed",
                    "estimated_cost": {"amount": 0.001, "currency": "USD", "status": "estimated"},
                    "evidence_state": "observed",
                }
            )
        ]
    )
    return value


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, repository: InMemoryFinOpsRepository) -> TestClient:
    monkeypatch.setenv("DF_WEB_PROXY_SECRET", "test-proxy-secret")
    monkeypatch.setenv("DF_FINOPS_HMAC_SECRET", "finops-test-secret")
    monkeypatch.setenv("DF_FINOPS_READ_ENABLED", "1")
    monkeypatch.setenv("DF_FINOPS_SQL_ENABLED", "0")
    monkeypatch.setattr(finops_router, "get_finops_query_service", lambda: FinOpsQueryService(repository))
    monkeypatch.setattr(finops_router, "_authorized_workspace_roles", lambda _actor: {"ws-a": "owner"})
    monkeypatch.setattr(finops_router, "_tenant_ref", lambda _actor: "tenant-a")
    monkeypatch.setattr(
        finops_router,
        "workspace_roi_snapshot",
        lambda workspace_id, from_value, to_value: {
            "workspace_id": workspace_id,
            "usage": {"runs": 1},
            "cost_evidence": {"status": "incomplete", "total": None, "currency": None},
            "observed_run_ids": [],
        },
        raising=False,
    )
    monkeypatch.setattr(
        finops_router,
        "workspace_cost_value_snapshot",
        lambda workspace_id, from_value, to_value: {
            "workspace_id": workspace_id,
            "cost_evidence": {"status": "incomplete", "total": None, "currency": None},
            "outcome_evidence": {"status": "not_recorded", "outcome_event_ids": [], "verified_outcome_event_ids": []},
            "realized_roi": {"status": "not_recorded", "value": None, "currency": None, "net_value": None},
            "artifact_count": 0,
            "output_trend": [],
            "scenarios": [],
        },
        raising=False,
    )
    return TestClient(app)


@pytest.fixture
def owner_headers() -> dict[str, str]:
    return trusted_headers(actor_id="owner-a", tenant_id="tenant-a")


@pytest.fixture
def member_headers() -> dict[str, str]:
    return trusted_headers(actor_id="member-a", tenant_id="tenant-a")


def test_roi_decision_returns_one_composed_payload(client: TestClient, owner_headers: dict[str, str]) -> None:
    response = client.get(
        "/api/finops/roi/decision",
        params={"workspace_id": "ws-a", "from": "2026-07-01T00:00:00Z", "to": "2026-08-01T00:00:00Z"},
        headers=owner_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["scope"]["workspace_ids"] == ["ws-a"]
    assert body["window"]["from"] == "2026-07-01T00:00:00Z"
    assert "decision" in body and "value_bridge" in body
    assert "provider_response_id" not in response.text


def test_roi_decision_maps_only_authorized_stage_runs_to_request_refs(
    client: TestClient,
    owner_headers: dict[str, str],
    repository: InMemoryFinOpsRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository.upsert_events(
        [
            FinOpsRequestEvent.model_validate(
                {
                    "request_ref": "req_usage_000001",
                    "occurred_at": datetime(2026, 7, 24, 3, 0, tzinfo=timezone.utc),
                    "call_class": "model",
                    "tenant_ref": "tenant-a",
                    "workspace_id": "ws-a",
                    "run_id": "run-usage",
                    "status": "succeeded",
                    "tokens": {"total": 12},
                    "estimated_cost": {
                        "amount": 0.001,
                        "currency": "USD",
                        "status": "estimated",
                    },
                    "evidence_state": "observed",
                }
            ),
            FinOpsRequestEvent.model_validate(
                {
                    "request_ref": "req_output_000001",
                    "occurred_at": datetime(2026, 7, 24, 4, 0, tzinfo=timezone.utc),
                    "call_class": "model",
                    "tenant_ref": "tenant-a",
                    "workspace_id": "ws-a",
                    "run_id": "run-output",
                    "status": "succeeded",
                    "tokens": {"total": 18},
                    "estimated_cost": {
                        "amount": 0.002,
                        "currency": "USD",
                        "status": "estimated",
                    },
                    "evidence_state": "observed",
                }
            ),
            FinOpsRequestEvent.model_validate(
                {
                    "request_ref": "req_other_workspace",
                    "occurred_at": datetime(2026, 7, 24, 4, 0, tzinfo=timezone.utc),
                    "call_class": "model",
                    "tenant_ref": "tenant-a",
                    "workspace_id": "ws-b",
                    "run_id": "run-output",
                    "status": "succeeded",
                    "tokens": {"total": 99},
                    "estimated_cost": {
                        "amount": 0.099,
                        "currency": "USD",
                        "status": "estimated",
                    },
                    "evidence_state": "observed",
                }
            ),
        ]
    )
    monkeypatch.setattr(
        finops_router,
        "workspace_roi_snapshot",
        lambda workspace_id, from_value, to_value: {
            "workspace_id": workspace_id,
            "usage": {"runs": 2},
            "observed_run_ids": ["run-usage", "run-output"],
            "cost_evidence": {
                "status": "complete",
                "observed_run_ids": ["run-usage", "run-output"],
                "priced_run_ids": ["run-usage"],
            },
        },
    )
    monkeypatch.setattr(
        finops_router,
        "workspace_cost_value_snapshot",
        lambda workspace_id, from_value, to_value: {
            "workspace_id": workspace_id,
            "cost_evidence": {
                "status": "complete",
                "observed_run_ids": ["run-usage", "run-output"],
            },
            "outcome_evidence": {
                "status": "verified",
                "outcome_event_ids": ["outcome-second"],
                "verified_outcome_event_ids": ["outcome-second"],
            },
            "realized_roi": {
                "status": "not_recorded",
                "value": None,
                "currency": None,
                "net_value": None,
            },
            "artifact_count": 1,
            "output_trend": [],
            "scenarios": [],
        },
    )
    monkeypatch.setattr(
        control_plane,
        "list_workspace_artifacts",
        lambda workspace_id, run_limit=None: {
            "workspace_id": workspace_id,
            "artifacts": [
                {
                    "workspace_id": "ws-a",
                    "run_id": "run-output",
                    "created_at": "2026-07-24T04:05:00Z",
                },
                {
                    "workspace_id": "ws-b",
                    "run_id": "run-usage",
                    "created_at": "2026-07-24T04:06:00Z",
                },
                {
                    "run_id": "run-usage",
                    "created_at": "2026-07-24T04:07:00Z",
                },
            ],
        },
    )
    monkeypatch.setattr(
        outcome_store,
        "list_outcome_events",
        lambda workspace_id: [
            {
                "event_id": "outcome-second",
                "workspace_id": workspace_id,
                "source": {"run_id": "run-output"},
            },
            {
                "event_id": "outcome-second",
                "workspace_id": "ws-b",
                "source": {"run_id": "run-usage"},
            },
        ],
    )

    response = client.get(
        "/api/finops/roi/decision",
        params={
            "workspace_id": "ws-a",
            "from": "2026-07-01T00:00:00Z",
            "to": "2026-08-01T00:00:00Z",
        },
        headers=owner_headers,
    )

    assert response.status_code == 200, response.text
    stages = {
        item["id"]: item for item in response.json()["evidence_maturity"]["stages"]
    }
    assert stages["usage"]["evidence_refs"] == [
        "req_usage_000001",
        "req_output_000001",
    ]
    assert stages["investment"]["evidence_refs"] == [
        "req_usage_000001",
        "req_output_000001",
    ]
    assert stages["output"]["evidence_refs"] == ["req_output_000001"]
    assert stages["output"]["complete"] is True
    assert stages["outcome"]["evidence_refs"] == ["req_output_000001"]
    assert "req_other_workspace" not in response.text


def test_request_ref_index_prioritizes_required_lineage_beyond_global_bound() -> None:
    events = [
        FinOpsRequestEvent.model_validate(
            {
                "request_ref": f"req_noise_{index:06d}",
                "occurred_at": datetime(2026, 7, 24, 2, 0, tzinfo=timezone.utc),
                "call_class": "model",
                "tenant_ref": "tenant-a",
                "workspace_id": "ws-a",
                "run_id": f"run-noise-{index:03d}",
                "status": "succeeded",
                "tokens": {"total": 10},
                "estimated_cost": {
                    "amount": 0.001,
                    "currency": "USD",
                    "status": "estimated",
                },
                "evidence_state": "observed",
            }
        )
        for index in range(301)
    ]
    events.append(
        FinOpsRequestEvent.model_validate(
            {
                "request_ref": "req_required_000001",
                "occurred_at": datetime(2026, 7, 24, 3, 0, tzinfo=timezone.utc),
                "call_class": "model",
                "tenant_ref": "tenant-a",
                "workspace_id": "ws-a",
                "run_id": "run-required",
                "status": "succeeded",
                "tokens": {"total": 10},
                "estimated_cost": {
                    "amount": 0.001,
                    "currency": "USD",
                    "status": "estimated",
                },
                "evidence_state": "observed",
            }
        )
    )

    index = finops_router._request_refs_by_run(
        events,
        run_limit=300,
        preferred_run_ids=(
            "run-required",
            *(f"run-noise-{item:03d}" for item in range(301)),
        ),
    )

    assert index["run-required"] == ["req_required_000001"]
    assert len(index) == 300


def test_roi_decision_uses_authorized_ledger_for_usage_and_priced_lineage(
    client: TestClient,
    owner_headers: dict[str, str],
    repository: InMemoryFinOpsRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository.upsert_events(
        [
            FinOpsRequestEvent.model_validate(
                {
                    "request_ref": "req_ledger_priced_001",
                    "occurred_at": datetime(2026, 7, 24, 3, 0, tzinfo=timezone.utc),
                    "call_class": "model",
                    "tenant_ref": "tenant-a",
                    "workspace_id": "ws-a",
                    "run_id": "run-ledger-priced",
                    "status": "succeeded",
                    "tokens": {"total": 12},
                    "estimated_cost": {
                        "amount": 0.001,
                        "currency": "USD",
                        "status": "estimated",
                    },
                    "evidence_state": "observed",
                }
            ),
            FinOpsRequestEvent.model_validate(
                {
                    "request_ref": "req_ledger_unpriced_01",
                    "occurred_at": datetime(2026, 7, 24, 4, 0, tzinfo=timezone.utc),
                    "call_class": "model",
                    "tenant_ref": "tenant-a",
                    "workspace_id": "ws-a",
                    "run_id": "run-ledger-unpriced",
                    "status": "succeeded",
                    "tokens": {"total": 18},
                    "estimated_cost": {
                        "amount": None,
                        "currency": "USD",
                        "status": "unavailable",
                    },
                    "evidence_state": "partial",
                }
            ),
        ]
    )
    monkeypatch.setattr(
        finops_router,
        "workspace_roi_snapshot",
        lambda workspace_id, from_value, to_value: {
            "workspace_id": workspace_id,
            "usage": {"runs": 0},
            "observed_run_ids": [],
            "cost_evidence": {
                "status": "not_configured",
                "priced_run_ids": [],
                "lineage_complete": False,
            },
        },
    )

    response = client.get(
        "/api/finops/roi/decision",
        params={
            "workspace_id": "ws-a",
            "from": "2026-07-01T00:00:00Z",
            "to": "2026-08-01T00:00:00Z",
        },
        headers=owner_headers,
    )

    assert response.status_code == 200, response.text
    stages = {
        item["id"]: item
        for item in response.json()["evidence_maturity"]["stages"]
    }
    assert stages["usage"]["value"] == 3
    assert "req_ledger_priced_001" in stages["usage"]["evidence_refs"]
    assert "req_ledger_unpriced_01" in stages["usage"]["evidence_refs"]
    assert stages["investment"]["evidence_refs"] == ["req_ledger_priced_001"]
    assert "模型计价未完整覆盖" in stages["investment"]["evidence_gap"]


def test_risk_decision_does_not_trigger_agent(
    client: TestClient, owner_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    called = False

    def fail_agent(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("read endpoint must not run an agent")

    monkeypatch.setattr("backend.finops.router.run_finops_analysis", fail_agent, raising=False)
    response = client.get("/api/finops/risk/decision", params={"workspace_id": "ws-a"}, headers=owner_headers)
    assert response.status_code == 200
    assert called is False


def test_owner_can_run_and_reload_a_persisted_read_only_risk_scan(
    client: TestClient,
    owner_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scan_service = RiskScanService(InMemoryRiskScanRepository())
    monkeypatch.setattr(
        finops_router,
        "get_finops_risk_scan_service",
        lambda: scan_service,
    )

    def fail_action_service() -> None:
        raise AssertionError("a read-only scan must not load governance executors")

    monkeypatch.setattr(finops_router, "get_finops_action_service", fail_action_service)
    payload = {
        "workspace_id": "ws-a",
        "from": "2026-07-01T00:00:00Z",
        "to": "2026-08-01T00:00:00Z",
    }

    created = client.post(
        "/api/finops/risk/scans",
        json=payload,
        headers=owner_headers,
    )

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "completed"
    assert body["scope"]["workspace_id"] == "ws-a"
    assert body["scope"]["from"] == payload["from"]
    assert body["scope"]["to"] == payload["to"]
    assert len(body["findings"]) == 7
    assert len(body["evidence_sets"]) == 7
    assert "tenant_ref" not in created.text
    assert "initiated_by_ref" not in created.text

    latest = client.get(
        "/api/finops/risk/scans/latest",
        params=payload,
        headers=owner_headers,
    )
    assert latest.status_code == 200, latest.text
    assert latest.json()["scan_ref"] == body["scan_ref"]

    history = client.get(
        "/api/finops/risk/scans",
        params={"workspace_id": "ws-a", "limit": 5},
        headers=owner_headers,
    )
    assert history.status_code == 200, history.text
    assert history.json()["items"][0]["scan_ref"] == body["scan_ref"]
    assert history.json()["items"][0]["rule_count"] == 7

    detail = client.get(
        f"/api/finops/risk/scans/{body['scan_ref']}",
        params={"workspace_id": "ws-a"},
        headers=owner_headers,
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["scan_ref"] == body["scan_ref"]
    assert len(detail.json()["findings"]) == 7


def test_latest_risk_scan_keeps_persisted_evidence_until_it_expires(
    client: TestClient,
    owner_headers: dict[str, str],
    repository: InMemoryFinOpsRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scan_service = RiskScanService(InMemoryRiskScanRepository())
    monkeypatch.setattr(
        finops_router,
        "get_finops_risk_scan_service",
        lambda: scan_service,
    )
    events = [
        FinOpsRequestEvent.model_validate(
            {
                "request_ref": f"req_scan_api_{index:012d}",
                "occurred_at": datetime(2026, 7, 24, 2, 0, tzinfo=timezone.utc)
                + timedelta(seconds=index),
                "call_class": "model",
                "tenant_ref": "tenant-a",
                "workspace_id": "ws-a",
                "status": "failed" if index in {18, 19} else "succeeded",
                "latency_ms": 500,
                "tokens": {"total": 100 + index},
                "gateway_coverage": "apim_governed",
                "estimated_cost": {
                    "amount": 0.001,
                    "currency": "USD",
                    "status": "estimated",
                },
                "evidence_state": "observed",
            }
        )
        for index in range(20)
    ]
    repository.upsert_events(events)
    params = {
        "workspace_id": "ws-a",
        "from": "2026-07-01T00:00:00Z",
        "to": "2026-08-01T00:00:00Z",
    }

    created = client.post(
        "/api/finops/risk/scans",
        json=params,
        headers=owner_headers,
    )
    assert created.status_code == 201, created.text
    error_finding = next(
        item for item in created.json()["findings"]
        if item["policy_type"] == "error_rate"
    )
    persisted_refs = error_finding["evidence_refs"]
    assert len(persisted_refs) == 2, error_finding

    newest = FinOpsRequestEvent.model_validate(
        {
            "request_ref": "req_scan_api_newest",
            "occurred_at": datetime(2026, 7, 25, 2, 0, tzinfo=timezone.utc),
            "call_class": "model",
            "tenant_ref": "tenant-a",
            "workspace_id": "ws-a",
            "status": "failed",
            "latency_ms": 500,
            "tokens": {"total": 150},
            "gateway_coverage": "apim_governed",
            "estimated_cost": {
                "amount": 0.001,
                "currency": "USD",
                "status": "estimated",
            },
            "evidence_state": "observed",
        }
    )
    repository.upsert_events([newest])

    latest = client.get(
        "/api/finops/risk/scans/latest",
        params=params,
        headers=owner_headers,
    )
    assert latest.status_code == 200, latest.text
    evidence_set = next(
        item for item in latest.json()["evidence_sets"]
        if item["policy_type"] == "error_rate"
    )
    assert [item["request_ref"] for item in evidence_set["items"]] == persisted_refs

    repository.delete_events(
        tenant_ref="tenant-a",
        workspace_id="ws-a",
        request_refs=persisted_refs[:1],
    )
    partially_expired = client.get(
        "/api/finops/risk/scans/latest",
        params=params,
        headers=owner_headers,
    )
    partial_set = next(
        item for item in partially_expired.json()["evidence_sets"]
        if item["policy_type"] == "error_rate"
    )
    assert [item["request_ref"] for item in partial_set["items"]] == persisted_refs[1:]

    repository.delete_events(
        tenant_ref="tenant-a",
        workspace_id="ws-a",
        request_refs=persisted_refs[1:],
    )
    fallback = client.get(
        "/api/finops/risk/scans/latest",
        params=params,
        headers=owner_headers,
    )
    fallback_set = next(
        item for item in fallback.json()["evidence_sets"]
        if item["policy_type"] == "error_rate"
    )
    assert fallback_set["items"][0]["request_ref"] == newest.request_ref


def test_member_cannot_run_or_read_risk_scans(
    client: TestClient,
    member_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scan_service = RiskScanService(InMemoryRiskScanRepository())
    monkeypatch.setattr(
        finops_router,
        "get_finops_risk_scan_service",
        lambda: scan_service,
    )
    monkeypatch.setattr(
        finops_router,
        "_authorized_workspace_roles",
        lambda _actor: {"ws-a": "member"},
    )
    payload = {"workspace_id": "ws-a"}

    created = client.post(
        "/api/finops/risk/scans",
        json=payload,
        headers=member_headers,
    )
    latest = client.get(
        "/api/finops/risk/scans/latest",
        params=payload,
        headers=member_headers,
    )
    history = client.get(
        "/api/finops/risk/scans",
        params={"workspace_id": "ws-a"},
        headers=member_headers,
    )

    assert created.status_code == 403
    assert latest.status_code == 403
    assert history.status_code == 403


def test_risk_scan_fails_closed_when_audit_persistence_is_unavailable(
    client: TestClient,
    owner_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scan_service = RiskScanService(InMemoryRiskScanRepository())
    monkeypatch.setattr(finops_router, "get_finops_risk_scan_service", lambda: scan_service)
    monkeypatch.setattr(
        finops_router,
        "record_audit_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("audit unavailable")),
    )

    response = client.post(
        "/api/finops/risk/scans",
        json={"workspace_id": "ws-a"},
        headers=owner_headers,
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Audit persistence is required"
    assert scan_service.list(tenant_ref="tenant-a", workspace_id="ws-a", limit=5) == []


def test_metric_evidence_endpoint_returns_subject_specific_bounded_requests(
    client: TestClient,
    owner_headers: dict[str, str],
    repository: InMemoryFinOpsRepository,
) -> None:
    repository.upsert_events(
        [
            FinOpsRequestEvent.model_validate(
                {
                    "request_ref": "req_latency_endpoint",
                    "occurred_at": datetime(2026, 7, 24, 2, 2, tzinfo=timezone.utc),
                    "call_class": "model",
                    "tenant_ref": "tenant-a",
                    "workspace_id": "ws-a",
                    "status": "succeeded",
                    "latency_ms": 8_200,
                    "tokens": {"total": 20},
                    "gateway_coverage": "apim_governed",
                    "estimated_cost": {"amount": 0.002, "currency": "USD", "status": "estimated"},
                    "evidence_state": "observed",
                }
            ),
            FinOpsRequestEvent.model_validate(
                {
                    "request_ref": "req_failure_endpoint",
                    "occurred_at": datetime(2026, 7, 24, 2, 3, tzinfo=timezone.utc),
                    "call_class": "model",
                    "tenant_ref": "tenant-a",
                    "workspace_id": "ws-a",
                    "status": "failed",
                    "latency_ms": 600,
                    "tokens": {"total": 15},
                    "gateway_coverage": "apim_governed",
                    "estimated_cost": {"amount": 0.001, "currency": "USD", "status": "estimated"},
                    "evidence_state": "observed",
                }
            ),
        ]
    )

    latency = client.get(
        "/api/finops/evidence",
        params={"workspace_id": "ws-a", "metric_id": "p95"},
        headers=owner_headers,
    )
    failures = client.get(
        "/api/finops/evidence",
        params={"workspace_id": "ws-a", "policy_type": "error_rate"},
        headers=owner_headers,
    )

    assert latency.status_code == 200, latency.text
    assert failures.status_code == 200, failures.text
    assert latency.json()["subject_id"] == "p95"
    assert latency.json()["items"][0]["request_ref"] == "req_latency_endpoint"
    assert failures.json()["items"][0]["request_ref"] == "req_failure_endpoint"
    assert len(latency.json()["items"]) <= 3


def test_risk_decision_returns_policy_specific_evidence_sets(
    client: TestClient,
    owner_headers: dict[str, str],
    repository: InMemoryFinOpsRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository.upsert_events(
        [
            FinOpsRequestEvent.model_validate(
                {
                    "request_ref": "req_latency_specific",
                    "occurred_at": datetime(2026, 7, 24, 2, 1, tzinfo=timezone.utc),
                    "call_class": "model",
                    "tenant_ref": "tenant-a",
                    "workspace_id": "ws-a",
                    "status": "succeeded",
                    "latency_ms": 9_500,
                    "tokens": {"total": 800},
                    "gateway_coverage": "apim_governed",
                    "estimated_cost": {"amount": 0.02, "currency": "USD", "status": "estimated"},
                    "evidence_state": "observed",
                }
            ),
            FinOpsRequestEvent.model_validate(
                {
                    "request_ref": "req_cache_specific",
                    "occurred_at": datetime(2026, 7, 24, 2, 2, tzinfo=timezone.utc),
                    "call_class": "model",
                    "tenant_ref": "tenant-a",
                    "workspace_id": "ws-a",
                    "status": "succeeded",
                    "latency_ms": 700,
                    "tokens": {"total": 400},
                    "cache": {"state": "miss", "eligible": True},
                    "gateway_coverage": "apim_governed",
                    "estimated_cost": {"amount": 0.01, "currency": "USD", "status": "estimated"},
                    "evidence_state": "observed",
                }
            ),
        ]
    )
    monkeypatch.setattr(
        finops_router,
        "evaluate_default_anomalies",
        lambda _value: [
            _managed_finding(
                anomaly_id="anomaly_latency_specific",
                workspace_id="ws-a",
                policy_type="p95_latency",
            ).model_copy(update={"evidence_refs": ["req_latency_specific"]}),
            _managed_finding(
                anomaly_id="anomaly_cache_specific",
                workspace_id="ws-a",
                policy_type="cache_hit_rate",
            ).model_copy(update={"evidence_refs": ["req_cache_specific"]}),
        ],
    )

    response = client.get(
        "/api/finops/risk/decision",
        params={"workspace_id": "ws-a", "refresh": "1"},
        headers=owner_headers,
    )

    assert response.status_code == 200, response.text
    evidence_sets = {
        item["policy_type"]: item for item in response.json()["evidence_sets"]
    }
    assert evidence_sets["p95_latency"]["items"][0]["request_ref"] == "req_latency_specific"
    assert evidence_sets["p95_latency"]["items"][0]["signal"] == {
        "metric": "latency_ms",
        "value": 9_500.0,
        "unit": "ms",
    }
    assert evidence_sets["cache_hit_rate"]["items"][0]["request_ref"] == "req_cache_specific"
    assert evidence_sets["cache_hit_rate"]["items"][0]["signal"]["value"] == "miss"
    assert all(
        item["signal"]["metric"] != "request"
        for item in response.json()["selected_evidence_summaries"]
    )


def _managed_finding(
    *,
    anomaly_id: str,
    workspace_id: str,
    policy_type: str = "cache_hit_rate",
) -> DetectedAnomaly:
    return DetectedAnomaly(
        anomaly_id=anomaly_id,
        policy_type=policy_type,
        severity="warning",
        observed_value=10,
        threshold_value=5,
        sample_count=25,
        workspace_ids=[workspace_id],
        recommendation="Use the bounded server recommendation.",
        evidence_refs=["req_aaaaaaaaaaaa"],
    )


def test_risk_decision_preserves_managed_anomaly_state_and_server_contract(
    client: TestClient,
    owner_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anomaly_service = FinOpsAnomalyService(InMemoryAnomalyRepository())
    anomaly_service.reconcile(
        tenant_ref="tenant-b",
        findings=[_managed_finding(anomaly_id="anomaly_other_tenant", workspace_id="ws-a")],
        scope_workspace_ids=("ws-a",),
    )
    anomaly_service.reconcile(
        tenant_ref="tenant-a",
        findings=[_managed_finding(anomaly_id="anomaly_other_workspace", workspace_id="ws-b")],
        scope_workspace_ids=("ws-b",),
    )
    anomaly_service.reconcile(
        tenant_ref="tenant-a",
        findings=[_managed_finding(anomaly_id="anomaly_unscoped", workspace_id="")],
    )
    monkeypatch.setattr(finops_router, "get_finops_anomaly_service", lambda: anomaly_service)
    monkeypatch.setattr(
        finops_router,
        "evaluate_default_anomalies",
        lambda _value: [_managed_finding(anomaly_id="anomaly_cache_ws_a", workspace_id="ws-a")],
    )
    monkeypatch.setattr(
        finops_router,
        "current_remediation_base_version",
        lambda tenant_ref, workspace_id, action_kind: "cache-policy-v7",
    )

    first = client.get(
        "/api/finops/risk/decision",
        params={"workspace_id": "ws-a", "refresh": "1"},
        headers=owner_headers,
    )
    assert first.status_code == 200, first.text
    first_priority = first.json()["priorities"][0]
    assert first_priority["anomaly_id"] == "anomaly_cache_ws_a"
    assert first_priority["anomaly_status"] == "open"
    assert first_priority["applicable_actions"] == ["acknowledge", "suppress"]
    assert first_priority["base_version"] == "cache-policy-v7"
    assert first.json()["optimization_portfolio"][0]["x_effort"] in {1, 2, 3}
    assert "anomaly_other_tenant" not in first.text
    assert "anomaly_other_workspace" not in first.text
    assert "anomaly_unscoped" not in first.text

    anomaly_service.acknowledge(
        tenant_ref="tenant-a",
        anomaly_id="anomaly_cache_ws_a",
        actor_ref="secret-actor",
    )
    acknowledged = client.get(
        "/api/finops/risk/decision",
        params={"workspace_id": "ws-a", "refresh": "1"},
        headers=owner_headers,
    )
    assert acknowledged.status_code == 200, acknowledged.text
    priority = acknowledged.json()["priorities"][0]
    assert priority["anomaly_status"] == "acknowledged"
    assert priority["applicable_actions"] == ["suppress"]
    assert "secret-actor" not in acknowledged.text


def test_risk_decision_degrades_only_failed_base_version_resolution(
    client: TestClient,
    owner_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anomaly_service = FinOpsAnomalyService(InMemoryAnomalyRepository())
    monkeypatch.setattr(finops_router, "get_finops_anomaly_service", lambda: anomaly_service)
    monkeypatch.setattr(
        finops_router,
        "evaluate_default_anomalies",
        lambda _value: [
            _managed_finding(anomaly_id="anomaly_cache", workspace_id="ws-a"),
            _managed_finding(
                anomaly_id="anomaly_latency",
                workspace_id="ws-a",
                policy_type="p95_latency",
            ),
        ],
    )

    def unavailable(*_args: object) -> str:
        raise RuntimeError("private resolver failure")

    monkeypatch.setattr(finops_router, "current_remediation_base_version", unavailable)
    response = client.get(
        "/api/finops/risk/decision",
        params={"workspace_id": "ws-a", "refresh": "1"},
        headers=owner_headers,
    )

    assert response.status_code == 200, response.text
    by_policy = {item["policy_type"]: item for item in response.json()["priorities"]}
    assert by_policy["cache_hit_rate"]["base_version"] is None
    assert by_policy["p95_latency"]["base_version"] == "remediation-template-v1"
    assert "private resolver failure" not in response.text


def test_member_cannot_read_admin_decision_scope(
    client: TestClient, member_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(finops_router, "_authorized_workspace_roles", lambda _actor: {"ws-a": "member"})
    response = client.get("/api/finops/risk/decision", params={"workspace_id": "ws-a"}, headers=member_headers)
    assert response.status_code == 403


def test_refresh_is_forwarded_only_after_authorization_and_uses_role_scope(
    client: TestClient,
    owner_headers: dict[str, str],
    repository: InMemoryFinOpsRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ComposedService:
        def __init__(self) -> None:
            self.delegate = FinOpsQueryService(repository)
            self.compose_calls: list[tuple[str, object, bool]] = []

        def __getattr__(self, name: str):
            return getattr(self.delegate, name)

        def compose(self, operation, query, compute, *, force_refresh=False):
            self.compose_calls.append((operation, query, force_refresh))
            return compute()

    service = _ComposedService()
    monkeypatch.setattr(finops_router, "get_finops_query_service", lambda: service)

    response = client.get(
        "/api/finops/roi/decision",
        params={"workspace_id": "ws-a", "refresh": "1"},
        headers=owner_headers,
    )

    assert response.status_code == 200
    assert len(service.compose_calls) == 1
    operation, query, force_refresh = service.compose_calls[0]
    expected_scope = hashlib.sha256(
        json.dumps(
            [["ws-a", "owner"]],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:16]
    assert operation == "roi_decision"
    assert force_refresh is True
    assert query.permission_scope == expected_scope


def test_unauthorized_refresh_never_reaches_cache_or_compute(
    client: TestClient,
    member_headers: dict[str, str],
    repository: InMemoryFinOpsRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _GuardedService:
        def __init__(self) -> None:
            self.delegate = FinOpsQueryService(repository)
            self.compose_calls = 0

        def __getattr__(self, name: str):
            return getattr(self.delegate, name)

        def compose(self, operation, query, compute, *, force_refresh=False):
            self.compose_calls += 1
            raise AssertionError("authorization must run before cache refresh")

    service = _GuardedService()
    monkeypatch.setattr(finops_router, "get_finops_query_service", lambda: service)
    monkeypatch.setattr(
        finops_router,
        "_authorized_workspace_roles",
        lambda _actor: {"ws-a": "member"},
    )

    response = client.get(
        "/api/finops/risk/decision",
        params={"workspace_id": "ws-a", "refresh": "1"},
        headers=member_headers,
    )

    assert response.status_code == 403
    assert service.compose_calls == 0


def test_cache_single_flight_timeout_returns_safe_retryable_503(
    client: TestClient,
    owner_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BusyService:
        def compose(self, operation, query, compute, *, force_refresh=False):
            raise FinOpsCacheBusy("internal cache key must not escape")

    monkeypatch.setattr(
        finops_router,
        "get_finops_query_service",
        _BusyService,
    )

    response = client.get(
        "/api/finops/roi/decision",
        params={"workspace_id": "ws-a"},
        headers=owner_headers,
    )

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert response.json() == {
        "detail": "FinOps query refresh is busy; retry shortly"
    }
    assert "internal cache key" not in response.text


def test_roi_decision_uses_rollup_unit_economics_when_sql_is_enabled(
    client: TestClient,
    owner_headers: dict[str, str],
    repository: InMemoryFinOpsRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeRollupRepository:
        read_calls: list[tuple[object, ...]] = []

        def read(self, query, bucket):
            self.read_calls.append((query.tenant_ref, query.authorized_workspace_ids, query.from_value, query.to_value, bucket))
            return []

    fake = FakeRollupRepository()
    monkeypatch.setattr(
        finops_router,
        "get_finops_query_service",
        lambda: FinOpsQueryService(repository, rollup_repository=fake),
    )
    current_day_value = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    window_start = (
        current_day_value - timedelta(days=31)
    ).isoformat().replace("+00:00", "Z")
    window_end = (
        current_day_value + timedelta(days=1)
    ).isoformat().replace("+00:00", "Z")
    response = client.get(
        "/api/finops/roi/decision",
        params={"workspace_id": "ws-a", "from": window_start, "to": window_end},
        headers=owner_headers,
    )
    assert response.status_code == 200
    current_day = current_day_value.isoformat().replace("+00:00", "Z")
    assert fake.read_calls == [("tenant-a", ("ws-a",), window_start, current_day, "day")]
