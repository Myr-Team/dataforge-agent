from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import backend.finops.router as finops_router
import backend.control_plane as control_plane
from backend.app import app
from backend.finops.models import FinOpsRequestEvent, TokenUsage
from backend.finops.query import FinOpsQueryService
from backend.finops.repository import InMemoryFinOpsRepository
from backend.finops.sql_repository import FinOpsPersistenceError
from backend.finops.governance import FinOpsActionService, InMemoryActionRepository, RecordingExecutor
from backend.finops.management import FinOpsManagementService, InMemoryManagementRepository
from backend.finops.anomaly_store import FinOpsAnomalyService, InMemoryAnomalyRepository
from backend.finops.anomalies import DetectedAnomaly
from backend.finops.assistant import FinOpsAssistantService
from backend.finops.planning import FinOpsPlanningService, InMemoryPlanningRepository
from backend.finops.saved_views import FinOpsSavedViewService, InMemorySavedViewRepository
from backend.finops.insight_repository import (
    InMemoryInsightRepository,
    InsightPage,
)
from backend.finops.insights import FinOpsInsight
from auth_fixtures import trusted_headers


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
                    "tenant_ref": "tenantref-a",
                    "workspace_id": "ws-a",
                    "actor_ref": "actor-safe",
                    "run_id": "run-a",
                    "agent_id": "df-coordinator",
                    "deployment": "gpt-5-mini",
                    "route": "analysis",
                    "status": "succeeded",
                    "tokens": TokenUsage(input=10, output=2, total=12),
                    "gateway_coverage": "apim_governed",
                    "estimated_cost": {
                        "amount": 0.001,
                        "currency": "USD",
                        "status": "estimated",
                        "price_card_revision": "price-1",
                    },
                    "evidence_state": "observed",
                    "correlation_ref": "corr-safe",
                    "usage_source": "provider",
                    "internal_correlation_key": "join-secret",
                }
            )
        ]
    )
    return value


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch,
    repository: InMemoryFinOpsRepository,
) -> TestClient:
    monkeypatch.setenv("DF_WEB_PROXY_SECRET", "test-proxy-secret")
    monkeypatch.setenv("DF_FINOPS_HMAC_SECRET", "finops-test-secret")
    monkeypatch.setenv("DF_FINOPS_READ_ENABLED", "1")
    monkeypatch.setenv("DF_FINOPS_SQL_ENABLED", "0")
    monkeypatch.setattr(
        finops_router,
        "_EVIDENCE_REPOSITORY",
        finops_router.InMemoryEvidenceAliasRepository(),
    )
    monkeypatch.setattr(
        finops_router,
        "get_finops_query_service",
        lambda: FinOpsQueryService(repository),
    )
    monkeypatch.setattr(
        finops_router,
        "_authorized_workspace_roles",
        lambda _actor: {"ws-a": "owner"},
    )
    monkeypatch.setattr(
        finops_router,
        "_tenant_ref",
        lambda _actor: "tenantref-a",
    )
    return TestClient(app)


def _planning_services(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        finops_router,
        "get_finops_planning_service",
        lambda: FinOpsPlanningService(InMemoryPlanningRepository()),
    )
    monkeypatch.setattr(
        finops_router,
        "get_finops_saved_view_service",
        lambda: FinOpsSavedViewService(InMemorySavedViewRepository()),
    )


def test_finops_read_flag_fails_closed(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DF_FINOPS_READ_ENABLED", "0")
    response = client.get(
        "/api/finops/overview?workspace_id=ws-a&from=2026-07-01T00:00:00Z&to=2026-07-25T00:00:00Z",
        headers=trusted_headers(actor_id="owner-a", tenant_id="tenant-a"),
    )
    assert response.status_code == 404


def test_finops_rejects_untrusted_and_out_of_scope_workspace(client: TestClient) -> None:
    untrusted = client.get(
        "/api/finops/overview?workspace_id=ws-a&from=2026-07-01T00:00:00Z&to=2026-07-25T00:00:00Z"
    )
    denied = client.get(
        "/api/finops/overview?workspace_id=ws-b&from=2026-07-01T00:00:00Z&to=2026-07-25T00:00:00Z",
        headers=trusted_headers(actor_id="owner-a", tenant_id="tenant-a"),
    )
    assert untrusted.status_code == 401
    assert denied.status_code == 403


def test_finops_bootstrap_is_bounded_and_omits_request_evidence(client: TestClient) -> None:
    response = client.get(
        "/api/finops/bootstrap?workspace_id=ws-a&from=2026-07-01T00:00:00Z&to=2026-07-25T00:00:00Z",
        headers=trusted_headers(actor_id="owner-a", tenant_id="tenant-a"),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["overview"]["metrics"]["requests"] == 1
    assert payload["trend"]["bucket"] == "day"
    assert payload["departments"]["count"] <= 5
    assert len(payload["anomalies"]["items"]) <= 3
    assert payload["insights"] == {"finops": None, "roi": None}
    serialized = response.text
    for forbidden in (
        "request_ref",
        "run_id",
        "trace_id",
        "correlation",
        "business_request",
        "business_response",
    ):
        assert forbidden not in serialized


def test_finops_bootstrap_requires_summary_permission(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        finops_router,
        "_authorized_workspace_roles",
        lambda _actor: {"ws-a": "viewer"},
    )

    response = client.get(
        "/api/finops/bootstrap?workspace_id=ws-a&from=2026-07-01T00:00:00Z&to=2026-07-25T00:00:00Z",
        headers=trusted_headers(actor_id="viewer-a", tenant_id="tenant-a"),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "workspace access denied for finops.summary.read"


def test_finops_bootstrap_projects_server_side_budget_usage(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    management = FinOpsManagementService(InMemoryManagementRepository())
    management.create_policy(
        tenant_ref="tenantref-a",
        actor_ref="actor-owner",
        policy_type="daily_cost_budget",
        configuration={
            "daily_budget_usd": 0.01,
            "warning_pct": 80,
            "critical_pct": 100,
        },
    )
    monkeypatch.setattr(
        finops_router,
        "get_finops_management_service",
        lambda: management,
    )

    response = client.get(
        "/api/finops/bootstrap?workspace_id=ws-a&from=2026-07-01T00:00:00Z&to=2026-07-25T00:00:00Z",
        headers=trusted_headers(actor_id="owner-a", tenant_id="tenant-a"),
    )

    assert response.status_code == 200
    assert response.json()["overview"]["metrics"]["budget"] == {
        "amount": 0.24,
        "used_amount": 0.001,
        "usage_pct": 0.4167,
        "status": "estimated",
        "source": "daily_cost_budget",
    }


def test_finops_budget_saved_view_and_csv_routes_are_bounded(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planning = FinOpsPlanningService(InMemoryPlanningRepository())
    views = FinOpsSavedViewService(InMemorySavedViewRepository())
    monkeypatch.setattr(finops_router, "get_finops_planning_service", lambda: planning)
    monkeypatch.setattr(finops_router, "get_finops_saved_view_service", lambda: views)
    headers = trusted_headers(actor_id="owner-a", tenant_id="tenant-a")

    created_budget = client.post(
        "/api/finops/budgets",
        headers=headers,
        json={
            "name": "工作区月度预算",
            "scope_type": "workspace",
            "scope_id": "ws-a",
            "period_start": "2026-07-01T00:00:00Z",
            "period_end": "2026-08-01T00:00:00Z",
            "amount": 100,
        },
    )
    created_view = client.post(
        "/api/finops/views",
        headers=headers,
        json={
            "name": "财务视图",
            "audience": "finance",
            "tab": "cost",
            "filters": {"workspace_id": "ws-a", "model": "gpt-5-mini"},
        },
    )
    budgets = client.get(
        "/api/finops/budgets?workspace_id=ws-a&from=2026-07-01T00:00:00Z&to=2026-07-25T00:00:00Z",
        headers=headers,
    )
    saved_views = client.get(
        "/api/finops/views?workspace_id=ws-a",
        headers=headers,
    )
    exported = client.get(
        "/api/finops/export.csv?group_by=model&workspace_id=ws-a&from=2026-07-01T00:00:00Z&to=2026-07-25T00:00:00Z",
        headers=headers,
    )

    assert created_budget.status_code == 201
    assert created_view.status_code == 201
    assert budgets.status_code == 200
    assert budgets.json()["items"][0]["progress"]["forecast_status"] == "estimated"
    assert saved_views.status_code == 200
    assert saved_views.json()["items"][0]["filters"]["workspace_id"] == "ws-a"
    assert exported.status_code == 200
    assert exported.content.startswith(b"\xef\xbb\xbf")
    assert "actor-safe" not in exported.text
    assert "provider" not in exported.text


def test_finops_saved_view_rejects_unsafe_filter_and_foreign_workspace(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    views = FinOpsSavedViewService(InMemorySavedViewRepository())
    monkeypatch.setattr(finops_router, "get_finops_saved_view_service", lambda: views)
    headers = trusted_headers(actor_id="owner-a", tenant_id="tenant-a")

    unsafe = client.post(
        "/api/finops/views",
        headers=headers,
        json={
            "name": "不安全",
            "tab": "cost",
            "filters": {"actor_ref": "raw-user"},
        },
    )
    foreign = client.post(
        "/api/finops/views",
        headers=headers,
        json={
            "name": "越权",
            "tab": "cost",
            "filters": {"workspace_id": "ws-b"},
        },
    )

    assert unsafe.status_code == 422
    assert foreign.status_code == 403


def test_finops_roi_economics_is_workspace_bounded_and_evidence_safe(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        control_plane,
        "workspace_roi_snapshot",
        lambda *_args: {"usage": {"runs": 1}},
    )
    monkeypatch.setattr(
        control_plane,
        "workspace_cost_value_snapshot",
        lambda *_args: {
            "cost_evidence": {"status": "complete", "total": 0.001, "currency": "USD"},
            "outcome_evidence": {"status": "not_recorded", "outcome_event_ids": []},
            "realized_roi": {"status": "not_recorded", "roi_ratio": None},
            "scenarios": [{"scenario_id": "roi_scenario_aaaaaaaaaaaaaaaa", "status": "estimated"}],
        },
    )

    response = client.get(
        "/api/finops/roi/economics?workspace_id=ws-a&from=2026-07-01T00:00:00Z&to=2026-07-25T00:00:00Z",
        headers=trusted_headers(actor_id="owner-a", tenant_id="tenant-a"),
    )
    denied = client.get(
        "/api/finops/roi/economics?workspace_id=ws-b",
        headers=trusted_headers(actor_id="owner-a", tenant_id="tenant-a"),
    )

    assert response.status_code == 200
    assert response.json()["verified_roi"]["value"] is None
    assert response.json()["scenarios"][0]["status"] == "estimated"
    assert denied.status_code == 403


def test_finops_opportunity_queue_never_executes_actions(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        finops_router,
        "evaluate_default_anomalies",
        lambda _value: [
            DetectedAnomaly(
                anomaly_id="anomaly-budget",
                policy_type="daily_cost_budget",
                severity="critical",
                observed_value=120,
                threshold_value=100,
                sample_count=40,
                workspace_ids=["ws-a"],
                recommendation="复核成本贡献来源",
            )
        ],
    )

    response = client.get(
        "/api/finops/opportunities?workspace_id=ws-a&from=2026-07-01T00:00:00Z&to=2026-07-25T00:00:00Z",
        headers=trusted_headers(actor_id="owner-a", tenant_id="tenant-a"),
    )

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["queue_state"] == "ready"
    assert item["action_status"] == "suggested"
    assert item["estimated_savings"] == 0.0001


def test_finops_assistant_query_is_workspace_bounded_and_evidence_cited(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def runner(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {
            "structured": {
                "answer": "当前模型范围内只有一条已观测请求，可继续扩大样本后比较。",
                "evidence_refs": ["req_aaaaaaaaaaaa"],
                "suggested_questions": ["与上一周期相比如何？"],
            }
        }

    monkeypatch.setattr(
        finops_router,
        "get_finops_assistant_service",
        lambda: FinOpsAssistantService(model_runner=runner),
    )
    payload = {
        "question": "这个模型的成本为什么变化？",
        "metric_context": {
            "metric_id": "model_cost",
            "label": "模型成本",
            "value": 0.001,
            "unit": "USD",
            "dimension": "model",
            "dimension_value": "gpt-5-mini",
            "window": {
                "from": "2026-07-01T00:00:00Z",
                "to": "2026-07-25T00:00:00Z",
            },
            "filters": {
                "workspace_id": "ws-a",
                "model": "gpt-5-mini",
            },
            "data_status": "partial",
            "evidence_state": "observed",
        },
        "history": [],
    }
    response = client.post(
        "/api/finops/assistant/query",
        json=payload,
        headers=trusted_headers(actor_id="owner-a", tenant_id="tenant-a"),
    )

    assert response.status_code == 200
    response_payload = response.json()
    assert response_payload.pop("conversation_ref").startswith("foc_")
    assert response_payload == {
        "status": "ready",
        "answer": "当前模型范围内只有一条已观测请求，可继续扩大样本后比较。",
        "evidence_refs": ["req_aaaaaaaaaaaa"],
        "evidence_state": "observed",
        "suggested_questions": ["与上一周期相比如何？"],
    }

    payload["metric_context"]["filters"]["workspace_id"] = "ws-b"
    denied = client.post(
        "/api/finops/assistant/query",
        json=payload,
        headers=trusted_headers(actor_id="owner-a", tenant_id="tenant-a"),
    )
    assert denied.status_code == 403


def test_finops_read_contract_and_request_detail_are_privacy_bounded(client: TestClient) -> None:
    headers = trusted_headers(actor_id="owner-a", tenant_id="tenant-a")
    overview = client.get(
        "/api/finops/overview?workspace_id=ws-a&from=2026-07-01T00:00:00Z&to=2026-07-25T00:00:00Z",
        headers=headers,
    )
    requests = client.get(
        "/api/finops/requests?workspace_id=ws-a&from=2026-07-01T00:00:00Z&to=2026-07-25T00:00:00Z",
        headers=headers,
    )
    detail = client.get(
        "/api/finops/requests/req_aaaaaaaaaaaa?workspace_id=ws-a&from=2026-07-01T00:00:00Z&to=2026-07-25T00:00:00Z",
        headers=headers,
    )

    assert overview.status_code == 200
    assert overview.json()["metrics"]["requests"] == 1
    assert requests.status_code == 200
    assert requests.json()["items"][0]["request_ref"] == "req_aaaaaaaaaaaa"
    assert detail.status_code == 200
    serialized = detail.text
    assert "join-secret" not in serialized
    assert "corr-safe" not in serialized
    assert "correlation_ref" not in serialized
    assert "provider_response_id" not in serialized


def test_finops_request_detail_requires_owner_or_admin(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        finops_router,
        "_authorized_workspace_roles",
        lambda _actor: {"ws-a": "viewer"},
    )

    response = client.get(
        "/api/finops/requests/req_aaaaaaaaaaaa?workspace_id=ws-a&from=2026-07-01T00:00:00Z&to=2026-07-25T00:00:00Z",
        headers=trusted_headers(actor_id="viewer-a", tenant_id="tenant-a"),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == (
        "workspace access denied for finops.request_detail.read"
    )
    assert "req_aaaaaaaaaaaa" not in response.text


def test_finops_request_detail_returns_application_request_and_visible_response(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        finops_router,
        "get_run",
        lambda _run_id: {
            "run_id": "run-a",
            "workspace_id": "ws-a",
            "status": "completed",
            "message": "分析本月销售异常",
            "final": {
                "text": "已定位主要变化来自华东区域。",
                "provider_response": "must-not-escape",
            },
            "system_prompt": "must-not-escape",
            "internal_error": "must-not-escape",
        },
    )
    monkeypatch.setattr(
        finops_router,
        "_workspace_name",
        lambda _workspace_id: "Commerce",
    )

    response = client.get(
        "/api/finops/requests/req_aaaaaaaaaaaa?workspace_id=ws-a&from=2026-07-01T00:00:00Z&to=2026-07-25T00:00:00Z",
        headers=trusted_headers(actor_id="owner-a", tenant_id="tenant-a"),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["display"]["name"].startswith("Commerce · 分析运行 · ")
    assert payload["business_request"]["text"] == "分析本月销售异常"
    assert payload["business_response"]["text"] == "已定位主要变化来自华东区域。"
    assert payload["technical_refs"]["request_ref"] == "req_aaaaaaaaaaaa"
    for forbidden in ("provider_response", "system_prompt", "internal_error", "must-not-escape"):
        assert forbidden not in response.text


def test_finops_request_detail_builds_server_owned_azure_monitor_link(
    client: TestClient,
    repository: InMemoryFinOpsRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = repository.get_event(
        tenant_ref="tenantref-a",
        workspace_ids=("ws-a",),
        request_ref="req_aaaaaaaaaaaa",
    )
    assert current is not None
    repository.upsert_events(
        [
            current.model_copy(
                update={"apim_correlation_id": "4f8b0f37b5824af5a2ac7ed9129ee70b"}
            )
        ]
    )
    monkeypatch.setenv(
        "DF_FINOPS_AZURE_MONITOR_LINK_TEMPLATE",
        "https://portal.azure.com/#blade/DataForge/query/{correlation_id}",
    )

    detail = client.get(
        "/api/finops/requests/req_aaaaaaaaaaaa?workspace_id=ws-a&from=2026-07-01T00:00:00Z&to=2026-07-25T00:00:00Z",
        headers=trusted_headers(actor_id="owner-a", tenant_id="tenant-a"),
    )

    assert detail.status_code == 200
    assert detail.json()["links"]["azure_monitor"].endswith(
        "/4f8b0f37b5824af5a2ac7ed9129ee70b"
    )


def test_finops_request_detail_builds_server_owned_foundry_trace_link(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace_id = "0123456789abcdef0123456789abcdef"
    monkeypatch.setattr(
        finops_router,
        "get_run",
        lambda _run_id: {
            "run_id": "run-a",
            "workspace_id": "ws-a",
            "status": "completed",
            "message": "分析销售变化",
            "final": {"text": "分析已完成。"},
            "trace": {"trace_id": trace_id, "agent_id": "df-coordinator"},
        },
    )
    monkeypatch.setenv(
        "DF_FINOPS_FOUNDRY_TRACE_LINK_TEMPLATE",
        "https://ai.azure.com/trace/{trace_id}",
    )

    response = client.get(
        "/api/finops/requests/req_aaaaaaaaaaaa?workspace_id=ws-a&from=2026-07-01T00:00:00Z&to=2026-07-25T00:00:00Z",
        headers=trusted_headers(actor_id="owner-a", tenant_id="tenant-a"),
    )

    assert response.status_code == 200
    assert response.json()["links"]["foundry_trace"] == (
        f"https://ai.azure.com/trace/{trace_id}"
    )


def _ready_insight() -> FinOpsInsight:
    return FinOpsInsight.model_validate(
        {
            "insight_id": "ins_aaaaaaaaaaaa",
            "agent_kind": "finops",
            "tenant_ref": "tenantref-a",
            "workspace_ids": ["ws-a"],
            "window": {
                "from": "2026-07-01T00:00:00Z",
                "to": "2026-07-25T00:00:00Z",
            },
            "trigger_type": "manual",
            "trigger_ref": "manual-a",
            "trigger_fingerprint": "a" * 64,
            "title": "成本变化",
            "summary": "主分析流程是当前主要成本驱动。",
            "findings": [
                {
                    "kind": "cost_driver",
                    "statement": "当前结论具备请求证据。",
                    "evidence_refs": ["req_aaaaaaaaaaaa"],
                }
            ],
            "evidence_refs": ["req_aaaaaaaaaaaa"],
            "evidence_state": "estimated",
            "confidence": 0.8,
            "source_revisions": {"input": "rev-1"},
            "evidence_gaps": [],
            "draft_suggestions": [],
            "generated_at": "2026-07-24T02:00:00Z",
            "expires_at": "2026-07-24T08:00:00Z",
            "status": "ready",
        }
    )


def test_finops_insight_reads_never_invoke_agent(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    insight = _ready_insight()

    class ReadOnlyInsightService:
        def list(self, **_kwargs):
            return InsightPage(items=[insight], count=1, next_cursor=None)

        def latest(self, **_kwargs):
            return insight

        def analyze(self, **_kwargs):
            nonlocal calls
            calls += 1
            raise AssertionError("GET must not invoke an analysis agent")

    monkeypatch.setattr(
        finops_router,
        "get_finops_insight_service",
        lambda: ReadOnlyInsightService(),
    )
    headers = trusted_headers(actor_id="owner-a", tenant_id="tenant-a")

    listing = client.get(
        "/api/finops/insights?agent_kind=finops&workspace_id=ws-a&from=2026-07-01T00:00:00Z&to=2026-07-25T00:00:00Z",
        headers=headers,
    )
    bootstrap = client.get(
        "/api/finops/bootstrap?workspace_id=ws-a&from=2026-07-01T00:00:00Z&to=2026-07-25T00:00:00Z",
        headers=headers,
    )

    assert listing.status_code == 200
    assert listing.json()["items"][0]["title"] == "成本变化"
    assert bootstrap.status_code == 200
    assert bootstrap.json()["insights"]["finops"]["title"] == "成本变化"
    assert calls == 0


def test_finops_manual_analysis_is_accepted_as_background_work(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    class RecordingInsightService:
        def fingerprint(self, **_kwargs):
            return "f" * 64

        def by_fingerprint(self, **_kwargs):
            return None

        def analyze(self, **kwargs):
            calls.append(kwargs)
            return _ready_insight()

    monkeypatch.setattr(
        finops_router,
        "get_finops_insight_service",
        lambda: RecordingInsightService(),
    )
    monkeypatch.setattr(
        finops_router,
        "_manual_insight_input",
        lambda **_kwargs: {
            "status": "ready",
            "evidence_refs": ["req_aaaaaaaaaaaa"],
        },
    )

    response = client.post(
        "/api/finops/insights/analyze",
        headers=trusted_headers(actor_id="owner-a", tenant_id="tenant-a"),
        json={
            "agent_kind": "finops",
            "workspace_id": "ws-a",
            "from": "2026-07-01T00:00:00Z",
            "to": "2026-07-25T00:00:00Z",
        },
    )

    assert response.status_code == 202
    assert response.json() == {
        "status": "scheduled",
        "agent_kind": "finops",
        "trigger_fingerprint": "f" * 64,
    }
    assert len(calls) == 1


def test_finops_manual_analysis_requires_corresponding_read_permission(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        finops_router,
        "_authorized_workspace_roles",
        lambda _actor: {"ws-a": "viewer"},
    )

    response = client.post(
        "/api/finops/insights/analyze",
        headers=trusted_headers(actor_id="viewer-a", tenant_id="tenant-a"),
        json={
            "agent_kind": "finops",
            "workspace_id": "ws-a",
            "from": "2026-07-01T00:00:00Z",
            "to": "2026-07-25T00:00:00Z",
        },
    )

    assert response.status_code == 403


def test_finops_actor_breakdown_requires_admin_or_owner(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        finops_router,
        "_authorized_workspace_roles",
        lambda _actor: {"ws-a": "viewer"},
    )
    response = client.get(
        "/api/finops/breakdowns?group_by=actor&workspace_id=ws-a&from=2026-07-01T00:00:00Z&to=2026-07-25T00:00:00Z",
        headers=trusted_headers(actor_id="viewer-a", tenant_id="tenant-a"),
    )
    assert response.status_code == 403


def test_finops_window_is_limited_to_ninety_days(client: TestClient) -> None:
    response = client.get(
        "/api/finops/overview?workspace_id=ws-a&from=2026-01-01T00:00:00Z&to=2026-07-25T00:00:00Z",
        headers=trusted_headers(actor_id="owner-a", tenant_id="tenant-a"),
    )
    assert response.status_code == 422


def test_finops_sql_read_failure_returns_bounded_service_unavailable(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingService:
        def overview(self, _query):
            raise FinOpsPersistenceError("server detail must not escape")

    monkeypatch.setattr(finops_router, "get_finops_query_service", lambda: FailingService())
    response = client.get(
        "/api/finops/overview?workspace_id=ws-a&from=2026-07-01T00:00:00Z&to=2026-07-25T00:00:00Z",
        headers=trusted_headers(actor_id="owner-a", tenant_id="tenant-a"),
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "FinOps persistence service is unavailable"
    assert "server detail" not in response.text


def test_finops_action_api_enforces_two_person_approval_and_execution_flag(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = FinOpsActionService(
        repository=InMemoryActionRepository(),
        executors={"cache_policy": RecordingExecutor(current_version="v1")},
    )
    monkeypatch.setattr(finops_router, "get_finops_action_service", lambda: service)
    proposer = trusted_headers(actor_id="proposer", tenant_id="tenant-a")
    approver = trusted_headers(actor_id="approver", tenant_id="tenant-a")
    created = client.post(
        "/api/finops/actions",
        headers=proposer,
        json={
            "action_type": "cache_policy",
            "payload": {
                "workspace_id": "ws-a",
                "enabled": True,
                "ttl_seconds": 600,
                "base_version": "v1",
            },
        },
    )
    assert created.status_code == 201
    action_id = created.json()["action"]["action_id"]
    assert client.post(f"/api/finops/actions/{action_id}/submit", headers=proposer).status_code == 200
    assert client.post(f"/api/finops/actions/{action_id}/approve", headers=proposer).status_code == 403
    assert client.post(f"/api/finops/actions/{action_id}/approve", headers=approver).status_code == 200

    monkeypatch.setenv("DF_FINOPS_ACTIONS_ENABLED", "0")
    assert client.post(f"/api/finops/actions/{action_id}/execute", headers=approver).status_code == 403
    monkeypatch.setenv("DF_FINOPS_ACTIONS_ENABLED", "1")
    assert client.post(f"/api/finops/actions/{action_id}/execute", headers=approver).json()["action"]["status"] == "verifying"
    assert client.post(f"/api/finops/actions/{action_id}/verify", headers=approver).json()["action"]["status"] == "succeeded"


def test_finops_action_transition_rechecks_target_workspace_admin_scope(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = FinOpsActionService(
        repository=InMemoryActionRepository(),
        executors={"cache_policy": RecordingExecutor(current_version="v1")},
    )
    monkeypatch.setattr(finops_router, "get_finops_action_service", lambda: service)
    monkeypatch.setattr(
        finops_router,
        "_authorized_workspace_roles",
        lambda actor: (
            {"ws-b": "owner"}
            if actor.get("actor_id") == "owner-b"
            else {"ws-a": "owner"}
        ),
    )
    owner_b = trusted_headers(actor_id="owner-b", tenant_id="tenant-a")
    owner_a = trusted_headers(actor_id="owner-a", tenant_id="tenant-a")
    created = client.post(
        "/api/finops/actions",
        headers=owner_b,
        json={
            "action_type": "cache_policy",
            "payload": {
                "workspace_id": "ws-b",
                "enabled": True,
                "ttl_seconds": 600,
                "base_version": "v1",
            },
        },
    )
    assert created.status_code == 201
    action_id = created.json()["action"]["action_id"]

    denied = client.post(f"/api/finops/actions/{action_id}/submit", headers=owner_a)

    assert denied.status_code == 403
    assert service.get(tenant_ref="tenantref-a", action_id=action_id).status == "draft"


def test_finops_anomaly_api_supports_admin_acknowledge_and_suppress(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anomaly_service = FinOpsAnomalyService(InMemoryAnomalyRepository())
    monkeypatch.setattr(finops_router, "get_finops_anomaly_service", lambda: anomaly_service)
    monkeypatch.setattr(
        finops_router,
        "evaluate_default_anomalies",
        lambda _value: [
            DetectedAnomaly(
                anomaly_id="anomaly_apim_ws-a",
                policy_type="apim_coverage",
                severity="warning",
                observed_value=90,
                threshold_value=95,
                sample_count=25,
                workspace_ids=["ws-a"],
                recommendation="Inspect unmanaged routes.",
            )
        ],
    )
    headers = trusted_headers(actor_id="owner-a", tenant_id="tenant-a")
    listing = client.get(
        "/api/finops/anomalies?workspace_id=ws-a&from=2026-07-01T00:00:00Z&to=2026-07-25T00:00:00Z",
        headers=headers,
    )
    assert listing.status_code == 200
    anomaly_id = next(
        item["anomaly_id"]
        for item in listing.json()["items"]
        if item["policy_type"] == "apim_coverage"
    )

    acknowledged = client.post(
        f"/api/finops/anomalies/{anomaly_id}/acknowledge",
        headers=headers,
    )
    assert acknowledged.status_code == 200
    assert acknowledged.json()["anomaly"]["status"] == "acknowledged"

    suppressed = client.post(
        f"/api/finops/anomalies/{anomaly_id}/suppress",
        headers=headers,
        json={"reason": "candidate maintenance"},
    )
    assert suppressed.status_code == 200
    assert suppressed.json()["anomaly"]["status"] == "suppressed"


def test_finops_anomaly_mutation_rechecks_all_target_workspace_scopes(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anomaly_service = FinOpsAnomalyService(InMemoryAnomalyRepository())
    anomaly_service.reconcile(
        tenant_ref="tenantref-a",
        findings=[
            DetectedAnomaly(
                anomaly_id="anomaly_private_ws-b",
                policy_type="apim_coverage",
                severity="warning",
                observed_value=90,
                threshold_value=95,
                sample_count=25,
                workspace_ids=["ws-b"],
                recommendation="Inspect unmanaged routes.",
            )
        ],
        scope_workspace_ids=("ws-b",),
    )
    monkeypatch.setattr(finops_router, "get_finops_anomaly_service", lambda: anomaly_service)
    monkeypatch.setattr(
        finops_router,
        "_authorized_workspace_roles",
        lambda _actor: {"ws-a": "owner"},
    )

    denied = client.post(
        "/api/finops/anomalies/anomaly_private_ws-b/acknowledge",
        headers=trusted_headers(actor_id="owner-a", tenant_id="tenant-a"),
    )

    assert denied.status_code == 403
    assert anomaly_service.get(
        tenant_ref="tenantref-a",
        anomaly_id="anomaly_private_ws-b",
    ).status == "open"


def test_finops_management_api_supports_department_mapping_and_typed_policies(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = FinOpsManagementService(InMemoryManagementRepository())
    monkeypatch.setattr(finops_router, "get_finops_management_service", lambda: service)
    headers = trusted_headers(actor_id="owner-a", tenant_id="tenant-a")
    created = client.post(
        "/api/finops/departments",
        headers=headers,
        json={"department_id": "engineering", "display_name": "Engineering"},
    )
    assigned = client.put(
        "/api/finops/workspace-assignments/ws-a",
        headers=headers,
        json={"department_id": "engineering"},
    )
    policies = client.post(
        "/api/finops/policies",
        headers=headers,
        json={
            "policy_type": "error_rate",
            "configuration": {"threshold_pct": 5, "minimum_requests": 20, "window_minutes": 15},
        },
    )
    rejected = client.post(
        "/api/finops/policies",
        headers=headers,
        json={
            "policy_type": "error_rate",
            "configuration": {
                "threshold_pct": 5,
                "minimum_requests": 20,
                "window_minutes": 15,
                "script": "not allowed",
            },
        },
    )
    disabled = client.delete(
        f"/api/finops/policies/{policies.json()['policy']['policy_id']}",
        headers=headers,
    )
    assert created.status_code == 201
    assert assigned.json()["assignment"]["department_id"] == "engineering"
    assert policies.status_code == 201
    assert rejected.status_code == 422
    assert disabled.status_code == 200
    assert disabled.json()["policy"]["status"] == "disabled"


def test_finops_apim_action_rejects_unsupported_token_window_at_creation(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/finops/actions",
        headers=trusted_headers(actor_id="owner-a", tenant_id="tenant-a"),
        json={
            "action_type": "apim_token_limit",
            "payload": {
                "workspace_id": "ws-a",
                "quota_tokens": 1000,
                "window_seconds": 30,
                "base_version": "etag-v1",
            },
        },
    )

    assert response.status_code == 422
