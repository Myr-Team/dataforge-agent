from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping

import pytest
from fastapi.testclient import TestClient

import backend.control_plane as control_plane
import backend.finops.router as finops_router
import backend.roi_scenario_store as roi_scenario_store
from auth_fixtures import trusted_headers
from backend.app import app
from backend.finops.anomaly_store import FinOpsAnomalyService, InMemoryAnomalyRepository
from backend.finops.assistant import FinOpsAssistantService
from backend.finops.demo_initialize import initialize_demo_workspace
from backend.finops.demo_seed_repository import InMemoryDemoSeedRepository
from backend.finops.evidence_repository import InMemoryEvidenceAliasRepository
from backend.finops.insight_repository import InMemoryInsightRepository
from backend.finops.insight_service import FinOpsInsightService
from backend.finops.management import FinOpsManagementService, InMemoryManagementRepository
from backend.finops.member_budget_repository import InMemoryMemberBudgetRepository
from backend.finops.query import FinOpsQueryService
from backend.finops.repository import InMemoryFinOpsRepository
from backend.finops.risk_scans import InMemoryRiskScanRepository, RiskScanService
from backend.finops.synthetic_demo import DEMO_ANCHOR, DEMO_BATCH_ID


TENANT_REF = "tenantref_demo"
WORKSPACE_ID = "demo-corpus"
WINDOW = {
    "from": (DEMO_ANCHOR - timedelta(days=30)).isoformat().replace("+00:00", "Z"),
    "to": DEMO_ANCHOR.isoformat().replace("+00:00", "Z"),
}


def _run_projection(seed: Mapping[str, Any]) -> dict[str, Any]:
    attempt = seed["model_attempt"]
    occurred_at = WINDOW["from"]
    steps = [
        {
            "time": occurred_at,
            "event": step["event"],
            "data": dict(step.get("data") or {}),
        }
        for step in seed.get("steps") or ()
    ]
    steps.append(
        {
            "time": occurred_at,
            "event": "model_response",
            "data": {
                "agent": "synthetic_demo",
                "request_ref": seed["request_ref"],
                "correlation_ref": seed["correlation_ref"],
                "attempt_ref": attempt["attempt_ref"],
                "result_id": seed["result_id"],
                "provider_type": attempt["provider_type"],
                "model_id": attempt["model_id"],
                "deployment": attempt["model_id"],
                "route": attempt["route"],
                "route_evidence": attempt["route_evidence"],
                "provenance": seed["provenance"],
                "usage": dict(attempt["tokens"]),
                "result_cache": dict(attempt["result_cache"]),
                "provider_cache": dict(attempt["provider_cache"]),
                "gateway_coverage": attempt["gateway_coverage"],
                "cost_estimate": {
                    "amount": attempt["cost_usd"],
                    "currency": "USD",
                    "official_price_key": attempt["official_price_key"],
                    "price_card_revision": attempt["price_card_revision"],
                },
            },
        }
    )
    return {
        "run_id": seed["run_id"],
        "workspace_id": WORKSPACE_ID,
        "message": seed["message"],
        "status": seed["status"],
        "origin": "synthetic_demo",
        "started_at": occurred_at,
        "completed_at": occurred_at,
        "trace": {
            "trace_id": seed["correlation_ref"],
            "agent_id": "synthetic_demo",
        },
        "steps": steps,
        "models": [steps[-1]["data"]],
        "final": {"text": seed["final_text"]},
    }


def _install_demo_http_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Any, dict[str, dict[str, Any]]]:
    monkeypatch.setenv("DF_WEB_PROXY_SECRET", "test-proxy-secret")
    monkeypatch.setenv("DF_FINOPS_HMAC_SECRET", "finops-test-secret")
    monkeypatch.setenv("DF_FINOPS_READ_ENABLED", "1")
    monkeypatch.setenv("DF_FINOPS_SQL_ENABLED", "0")
    monkeypatch.setenv("DF_FINOPS_QUICK_MODEL_ENABLED", "0")

    monkeypatch.setattr(roi_scenario_store, "SCENARIO_DIR", tmp_path / "scenarios")
    monkeypatch.setattr(roi_scenario_store, "blob_configured", lambda: False)
    monkeypatch.setattr(roi_scenario_store, "upload_blob_json", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(roi_scenario_store, "download_blob_json_strict", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(roi_scenario_store, "_linked_run_is_valid", lambda *_args, **_kwargs: True)

    ledger = InMemoryFinOpsRepository()
    budgets = InMemoryMemberBudgetRepository()
    anomalies = InMemoryAnomalyRepository()
    insights = InMemoryInsightRepository()
    risk_scans = InMemoryRiskScanRepository()
    captured_runs: dict[str, dict[str, Any]] = {}

    def write_runs(
        workspace_id: str,
        values: tuple[Mapping[str, Any], ...],
        *,
        seed_key: str,
    ) -> None:
        assert workspace_id == WORKSPACE_ID
        assert seed_key == DEMO_BATCH_ID
        captured_runs.update({str(value["run_id"]): _run_projection(value) for value in values})

    result = initialize_demo_workspace(
        tenant_ref=TENANT_REF,
        allowed_tenant_ref=TENANT_REF,
        workspace_id=WORKSPACE_ID,
        allowed_workspace_id=WORKSPACE_ID,
        ledger_repository=ledger,
        seed_repository=InMemoryDemoSeedRepository(),
        budget_repository=budgets,
        hmac_secret="finops-test-secret",
        roi_writer=lambda workspace_id, payload, *, seed_key: roi_scenario_store.upsert_demo_roi_scenario(
            workspace_id,
            payload,
            actor=None,
            seed_key=seed_key,
        ),
        run_writer=write_runs,
        anomaly_repository=anomalies,
        insight_repository=insights,
        now=DEMO_ANCHOR,
    )

    management = FinOpsManagementService(InMemoryManagementRepository())
    policy_configs = {
        "error_rate": {},
        "p95_latency": {},
        "daily_cost_budget": {"daily_budget_usd": 5},
        "token_spike": {},
        "apim_coverage": {},
        "unpriced_requests": {},
        "cache_hit_rate": {},
    }
    for policy_type, configuration in policy_configs.items():
        management.create_policy(
            tenant_ref=TENANT_REF,
            actor_ref="actor_demo_owner",
            policy_type=policy_type,
            configuration=configuration,
        )

    query_service = FinOpsQueryService(ledger)
    insight_service = FinOpsInsightService(
        repository=insights,
        runner=object(),
        now=lambda: DEMO_ANCHOR,
    )
    monkeypatch.setattr(finops_router, "get_finops_query_service", lambda: query_service)
    monkeypatch.setattr(finops_router, "get_finops_member_budget_repository", lambda: budgets)
    monkeypatch.setattr(finops_router, "get_finops_management_service", lambda: management)
    monkeypatch.setattr(finops_router, "get_finops_anomaly_service", lambda: FinOpsAnomalyService(anomalies))
    monkeypatch.setattr(finops_router, "get_finops_insight_service", lambda: insight_service)
    monkeypatch.setattr(
        finops_router,
        "get_finops_risk_scan_service",
        lambda: RiskScanService(risk_scans),
    )
    monkeypatch.setattr(finops_router, "get_finops_evidence_alias_repository", lambda: InMemoryEvidenceAliasRepository())
    monkeypatch.setattr(finops_router, "_authorized_workspace_roles", lambda _actor: {WORKSPACE_ID: "owner"})
    monkeypatch.setattr(finops_router, "_tenant_ref", lambda _actor: TENANT_REF)
    monkeypatch.setattr(finops_router, "_workspace_name", lambda _workspace_id: "深圳选址演示")
    monkeypatch.setattr(finops_router, "record_audit_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        finops_router,
        "get_finops_assistant_service",
        lambda: FinOpsAssistantService(model_runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("quick AI must stay grounded"))),
    )
    monkeypatch.setattr(finops_router, "load_workspace_model_configuration", lambda _workspace_id: {})

    def roi_snapshot(workspace_id: str, from_value: str, to_value: str) -> dict[str, Any]:
        return {
            "workspace_id": workspace_id,
            "window": {"from": from_value, "to": to_value},
            "usage": {"runs": 0},
            "cost_evidence": {},
            "outcome_evidence": {},
            "foundry_integration": {"status": "unavailable"},
        }

    def cost_value_snapshot(workspace_id: str, from_value: str, to_value: str) -> dict[str, Any]:
        scenarios = [
            roi_scenario_store.scenario_projection(workspace_id, item)
            for item in roi_scenario_store.list_roi_scenarios(workspace_id)
        ]
        return {
            "workspace_id": workspace_id,
            "window": {"from": from_value, "to": to_value},
            "cost_evidence": {},
            "outcome_evidence": {},
            "realized_roi": {},
            "artifact_count": 0,
            "output_trend": [],
            "scenarios": scenarios,
        }

    monkeypatch.setattr(finops_router, "workspace_roi_snapshot", roi_snapshot)
    monkeypatch.setattr(finops_router, "workspace_cost_value_snapshot", cost_value_snapshot)

    def get_seeded_run(run_id: str) -> dict[str, Any]:
        try:
            return captured_runs[run_id]
        except KeyError as exc:
            raise FileNotFoundError(run_id) from exc

    monkeypatch.setattr(finops_router, "get_run", get_seeded_run)
    monkeypatch.setattr(control_plane, "get_run", get_seeded_run)
    monkeypatch.setattr(control_plane, "_require_workspace_action", lambda *_args, **_kwargs: "owner")
    return result, captured_runs


def test_shenzhen_demo_initializer_reaches_real_http_routes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    result, captured_runs = _install_demo_http_dependencies(monkeypatch, tmp_path)
    headers = trusted_headers(actor_id="owner-demo", tenant_id="tenant-demo")
    client = TestClient(app)
    params = {"workspace_id": WORKSPACE_ID, **WINDOW}

    bootstrap_response = client.get("/api/finops/bootstrap", params=params, headers=headers)
    assert bootstrap_response.status_code == 200
    bootstrap = bootstrap_response.json()
    metrics = bootstrap["overview"]["metrics"]
    assert metrics["requests"] == 2480
    assert metrics["estimated_cost"]["amount"] == pytest.approx(206.4)
    assert metrics["estimated_cost"]["unpriced_requests"] == 160
    assert metrics["cache"]["avoided_tokens"] is None
    assert metrics["cache"]["estimated_savings"] is None
    assert metrics["cache"]["data_status"] == "unavailable"
    assert metrics["provider_cache"]["data_status"] == "available"
    assert {item["policy_type"] for item in bootstrap["anomalies"]["items"]}
    assert bootstrap["insights"]["finops"]["evidence_state"] == "synthetic_demo"

    scan_response = client.post(
        "/api/finops/risk/scans",
        json={"workspace_id": WORKSPACE_ID, **WINDOW},
        headers=headers,
    )
    assert scan_response.status_code == 201
    scan = scan_response.json()
    assert scan["request_sample_count"] == 2480
    assert scan["rules_evaluated"] == 7
    assert {item["policy_type"] for item in scan["findings"]} == {
        "error_rate",
        "p95_latency",
        "daily_cost_budget",
        "token_spike",
        "apim_coverage",
        "unpriced_requests",
        "cache_hit_rate",
    }
    policy_refs = {item["policy_type"]: item["evidence_refs"] for item in scan["findings"]}
    flattened_refs = [ref for refs in policy_refs.values() for ref in refs]
    assert all(policy_refs.values())
    assert len(flattened_refs) == len(set(flattened_refs))
    for finding, evidence_set in zip(scan["findings"], scan["evidence_sets"], strict=True):
        bound_refs = [item["request_ref"] for item in evidence_set["items"]]
        assert bound_refs
        assert set(bound_refs).issubset(set(finding["evidence_refs"]))

    latest_response = client.get("/api/finops/risk/scans/latest", params=params, headers=headers)
    assert latest_response.status_code == 200
    assert latest_response.json()["scan_ref"] == scan["scan_ref"]

    for policy_type in policy_refs:
        evidence_response = client.get(
            "/api/finops/evidence",
            params={**params, "policy_type": policy_type},
            headers=headers,
        )
        assert evidence_response.status_code == 200
        evidence_refs = [item["request_ref"] for item in evidence_response.json()["items"]]
        assert evidence_refs
        assert set(evidence_refs).issubset({event.request_ref for event in result.events})

    selected_policy = "cache_hit_rate"
    assistant_response = client.post(
        "/api/finops/assistant/query",
        headers=headers,
        json={
            "question": "请解释深圳选址演示中的缓存命中风险。",
            "mode": "quick",
            "metric_context": {
                "metric_id": "risk_cache_hit_rate",
                "label": "缓存命中率",
                "value": metrics["cache_hit_rate_pct"],
                "unit": "%",
                "window": WINDOW,
                "filters": {"workspace_id": WORKSPACE_ID},
                "data_status": "partial",
                "evidence_state": "partial",
                "policy_type": selected_policy,
                "evidence_refs": policy_refs[selected_policy],
            },
        },
    )
    assert assistant_response.status_code == 200
    assistant = assistant_response.json()
    assert assistant["status"] == "ready"
    assert set(assistant["evidence_refs"]).issubset(set(policy_refs[selected_policy]))

    roi_response = client.get(
        "/api/finops/roi/decision",
        params={**params, "refresh": "true"},
        headers=headers,
    )
    assert roi_response.status_code == 200
    roi = roi_response.json()["scenarios"][0]
    demo_evidence = roi["demo_evidence"]
    assert round(roi["result"]["roi_ratio"] * 100, 1) == 293.3
    assert demo_evidence["process"] == {
        "analysis_tasks": 96,
        "reports": 78,
        "evidence_reviews": 18,
        "reviewed_savings_hours": 174.6,
    }
    assert demo_evidence["label"] == "演示验证结果 · 合成数据"

    miss_event, hit_event = result.events[:2]
    miss_detail_response = client.get(
        f"/api/finops/requests/{miss_event.request_ref}",
        params=params,
        headers=headers,
    )
    hit_detail_response = client.get(
        f"/api/finops/requests/{hit_event.request_ref}",
        params=params,
        headers=headers,
    )
    assert miss_detail_response.status_code == hit_detail_response.status_code == 200
    miss_detail = miss_detail_response.json()
    hit_detail = hit_detail_response.json()
    assert miss_detail["metrics"]["result_cache"]["state"] == "miss"
    assert hit_detail["metrics"]["result_cache"]["state"] == "hit"
    assert hit_detail["metrics"]["result_cache"]["source_result_version"]
    assert miss_detail["metrics"]["provider_cache"]["evidence_state"] == "synthetic"
    assert miss_detail["technical_refs"]["request_ref"] == miss_event.request_ref
    assert miss_detail["technical_refs"]["run_id"] == miss_event.run_id

    miss_trace_response = client.get(f"/api/runs/{miss_event.run_id}/trace", headers=headers)
    hit_trace_response = client.get(f"/api/runs/{hit_event.run_id}/trace", headers=headers)
    assert miss_trace_response.status_code == hit_trace_response.status_code == 200
    trace_text = json.dumps(miss_trace_response.json(), ensure_ascii=False)
    for value in (
        miss_event.request_ref,
        miss_event.correlation_ref,
        miss_event.attempt_ref,
        "synthetic_demo",
        "provider_cache",
        "result_cache",
    ):
        assert value in trace_text
    assert hit_event.result_cache.source_result_version in json.dumps(hit_trace_response.json(), ensure_ascii=False)
    assert len(captured_runs) == 2480

    pricing_response = client.get("/api/finops/pricing/catalog", headers=headers)
    assert pricing_response.status_code == 200
    catalog = pricing_response.json()
    catalog_keys = {item["price_key"] for item in catalog["items"]}
    assert "azure-openai:gpt-5.6-terra:global-standard:global" in catalog_keys
    assert "deepseek:deepseek-v4-flash:official" in catalog_keys
    assert miss_event.estimated_cost.official_price_key in catalog_keys
