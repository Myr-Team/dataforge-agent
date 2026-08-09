from __future__ import annotations

from datetime import datetime, timezone

from backend.finops.evidence_repository import InMemoryEvidenceAliasRepository
from backend.finops.models import FinOpsRequestEvent, TokenUsage
from backend.finops.query import FinOpsQuery, FinOpsQueryService
from backend.finops.repository import InMemoryFinOpsRepository
from backend.finops.request_detail import (
    FinOpsRequestDetailService,
    build_foundry_trace_link,
)


def _query() -> FinOpsQuery:
    return FinOpsQuery(
        tenant_ref="tenantref-a",
        authorized_workspace_ids=("ws-a",),
        workspace_id="ws-a",
        from_value="2026-07-01T00:00:00Z",
        to_value="2026-07-25T00:00:00Z",
    )


def _service(
    *,
    run: dict[str, object] | None = None,
) -> FinOpsRequestDetailService:
    repository = InMemoryFinOpsRepository()
    repository.upsert_events(
        [
            FinOpsRequestEvent.model_validate(
                {
                    "request_ref": "req_aaaaaaaaaaaa",
                    "occurred_at": datetime(2026, 7, 24, 2, 42, tzinfo=timezone.utc),
                    "call_class": "model",
                    "tenant_ref": "tenantref-a",
                    "workspace_id": "ws-a",
                    "run_id": "run-a",
                    "agent_id": "df-coordinator",
                    "deployment": "gpt-5-mini",
                    "route": "analysis",
                    "status": "succeeded",
                    "latency_ms": 1300,
                    "tokens": TokenUsage(input=10, output=2, total=12),
                    "cache": {"state": "miss", "eligible": True},
                    "gateway_coverage": "apim_governed",
                    "estimated_cost": {
                        "amount": 0.001,
                        "currency": "USD",
                        "status": "estimated",
                        "price_card_revision": "price-1",
                    },
                    "evidence_state": "observed",
                    "apim_correlation_id": "4f8b0f37b5824af5a2ac7ed9129ee70b",
                }
            )
        ]
    )
    stored_run = run or {
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
        "trace": {
            "trace_id": "0123456789abcdef0123456789abcdef",
            "agent_id": "df-coordinator",
        },
    }
    return FinOpsRequestDetailService(
        query_service=FinOpsQueryService(repository),
        alias_repository=InMemoryEvidenceAliasRepository(),
        run_loader=lambda run_id: stored_run if run_id == "run-a" else {},
        workspace_name_resolver=lambda _workspace_id: "Commerce",
    )


def test_request_detail_projects_friendly_application_evidence() -> None:
    detail = _service().build(
        _query(),
        "req_aaaaaaaaaaaa",
        can_trace=True,
    )

    assert detail is not None
    assert detail["request_ref"] == "req_aaaaaaaaaaaa"
    assert detail["display"] == {
        "name": "Commerce · 分析运行 · 7月24日 10:42",
        "operation": "分析运行",
        "occurred_at": "2026-07-24T02:42:00Z",
    }
    assert detail["business_request"] == {
        "text": "分析本月销售异常",
        "status": "recorded",
    }
    assert detail["business_response"] == {
        "text": "已定位主要变化来自华东区域。",
        "status": "recorded",
    }
    assert detail["technical_refs"]["request_ref"] == "req_aaaaaaaaaaaa"
    assert detail["technical_refs"]["run_id"] == "run-a"
    assert detail["technical_refs"]["apim_correlation_id"] == (
        "4f8b0f37b5824af5a2ac7ed9129ee70b"
    )
    serialized = str(detail)
    for forbidden in (
        "system_prompt",
        "provider_response",
        "must-not-escape",
        "internal_error",
    ):
        assert forbidden not in serialized


def test_request_detail_omits_technical_refs_without_trace_capability() -> None:
    detail = _service().build(
        _query(),
        "req_aaaaaaaaaaaa",
        can_trace=False,
    )

    assert detail is not None
    assert "technical_refs" not in detail
    assert detail["links"] == {}


def test_failed_request_never_projects_internal_failure_text_as_response() -> None:
    service = _service(
        run={
            "run_id": "run-a",
            "workspace_id": "ws-a",
            "status": "failed",
            "message": "分析本月销售异常",
            "final": {"text": "database password=customer-secret"},
            "error": {"message": "customer-secret"},
        }
    )
    event = service.query_service._repository.get_event(
        tenant_ref="tenantref-a",
        workspace_ids=("ws-a",),
        request_ref="req_aaaaaaaaaaaa",
    )
    assert event is not None
    service.query_service._repository.upsert_events(
        [event.model_copy(update={"status": "failed", "error_category": "upstream"})]
    )

    detail = service.build(_query(), "req_aaaaaaaaaaaa", can_trace=True)

    assert detail is not None
    assert detail["business_response"] == {"text": None, "status": "unavailable"}
    assert "customer-secret" not in str(detail)


def test_foundry_trace_link_accepts_only_server_owned_azure_hosts() -> None:
    trace_id = "0123456789abcdef0123456789abcdef"

    assert build_foundry_trace_link(
        "https://ai.azure.com/trace/{trace_id}",
        trace_id,
    ) == f"https://ai.azure.com/trace/{trace_id}"
    assert build_foundry_trace_link(
        "https://portal.azure.cn/#view/DataForge/{trace_id}",
        trace_id,
    ) == f"https://portal.azure.cn/#view/DataForge/{trace_id}"
    assert build_foundry_trace_link(
        "http://ai.azure.com/trace/{trace_id}",
        trace_id,
    ) is None
    assert build_foundry_trace_link(
        "https://example.com/trace/{trace_id}",
        trace_id,
    ) is None
    assert build_foundry_trace_link(
        "https://ai.azure.com/trace/{trace_id}",
        "../escape",
    ) is None
    assert build_foundry_trace_link(
        "https://ai.azure.com/trace/static",
        trace_id,
    ) is None
