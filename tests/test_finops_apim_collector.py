from __future__ import annotations

from datetime import datetime, timezone

from backend.finops.apim_collector import (
    ApimLlmObservation,
    apim_usage_query,
    collect_apim_usage,
    default_correlation_ref,
    reconcile_apim_observations,
    summarize_gateway_only_errors,
)
from backend.finops.apim_backfill import run_apim_backfill
from backend.finops.models import FinOpsRequestEvent, TokenUsage
from backend.finops.repository import InMemoryFinOpsRepository
from backend.finops.sql_pricing import (
    DeploymentPriceMapping,
    InMemoryPriceMappingRepository,
)


def _app_event() -> FinOpsRequestEvent:
    return FinOpsRequestEvent.model_validate(
        {
            "request_ref": "req_aaaaaaaaaaaa",
            "occurred_at": datetime(2026, 7, 24, 2, 0, tzinfo=timezone.utc),
            "call_class": "model",
            "tenant_ref": "tenant-safe",
            "workspace_id": "ws-a",
            "run_id": "run-a",
            "routing_policy_revision": 7,
            "status": "succeeded",
            "tokens": TokenUsage(input=10, output=2, total=12),
            "gateway_coverage": "app_observed",
            "evidence_state": "observed",
            "usage_source": "provider",
            "correlation_ref": "corr_d9a17058cf0a174d9f7504bc",
        }
    )


def test_apim_query_projects_usage_without_request_or_response_messages() -> None:
    query = apim_usage_query(
        "2026-07-24T02:00:00Z",
        "2026-07-24T02:05:00Z",
    )
    assert "ApiManagementGatewayLlmLog" in query
    assert "ApiManagementGatewayLogs" in query
    assert "PromptTokens" in query
    assert "CompletionTokens" in query
    assert "RequestMessages" not in query
    assert "ResponseMessages" not in query
    assert "RequestBody" not in query
    assert "ResponseBody" not in query


def test_apim_streaming_tokens_are_estimated_and_reconciled_without_summing() -> None:
    app = _app_event()
    observation = ApimLlmObservation.model_validate(
        {
            "occurred_at": "2026-07-24T02:00:01Z",
            "correlation_id": "4f8b0f37b5824af5a2ac7ed9129ee70b",
            "prompt_tokens": 20,
            "completion_tokens": 5,
            "total_tokens": 25,
            "deployment": "gpt-5-mini",
            "is_streaming": True,
            "latency_ms": 1300,
            "status_code": 200,
        }
    )

    rows = reconcile_apim_observations(
        [app],
        [observation],
        hmac_secret="test-secret",
        correlation_ref_resolver=lambda _tenant, _correlation, _secret: app.correlation_ref,
    )

    assert len(rows) == 1
    assert rows[0].tokens.total == 12
    assert rows[0].gateway_coverage == "apim_governed"
    assert rows[0].usage_source == "provider"
    assert rows[0].apim_correlation_id == observation.correlation_id
    assert rows[0].routing_policy_revision == 7


def test_apim_usage_fills_missing_application_usage_as_estimated() -> None:
    app = _app_event().model_copy(
        update={
            "tokens": TokenUsage(),
            "usage_source": "application",
            "evidence_state": "partial",
        }
    )
    observation = ApimLlmObservation.model_validate(
        {
            "occurred_at": "2026-07-24T02:00:01Z",
            "correlation_id": "4f8b0f37b5824af5a2ac7ed9129ee70b",
            "prompt_tokens": 20,
            "completion_tokens": 5,
            "total_tokens": 25,
            "deployment": "gpt-5-mini",
            "is_streaming": True,
            "latency_ms": 1300,
            "status_code": 200,
        }
    )

    [result] = reconcile_apim_observations(
        [app],
        [observation],
        hmac_secret="test-secret",
        correlation_ref_resolver=lambda _tenant, _correlation, _secret: app.correlation_ref,
    )
    assert result.tokens.total == 25
    assert result.evidence_state == "estimated"
    assert result.usage_source == "apim"


def test_apim_backfill_queries_once_and_reconciles_each_sql_scope() -> None:
    repository = InMemoryFinOpsRepository()
    first = _app_event()
    second = first.model_copy(
        update={
            "tenant_ref": "tenant-other",
            "workspace_id": "ws-b",
            "request_ref": "req_bbbbbbbbbbbb",
            "correlation_ref": "corr_other_safe",
        }
    )
    repository.upsert_events([first, second])
    requested_queries: list[str] = []

    result = run_apim_backfill(
        repository=repository,
        scopes={"tenant-safe": ("ws-a",), "tenant-other": ("ws-b",)},
        query_rows=lambda query: requested_queries.append(query) or [],
        from_value="2026-07-24T01:55:00Z",
        to_value="2026-07-24T02:05:00Z",
        hmac_secret="test-secret",
    )

    assert len(requested_queries) == 1
    assert result["scope_count"] == 2
    assert result["application_events"] == 2
    assert result["unmatched_observations"] == 0
    assert "tenant-safe" not in str(result)
    assert "ws-a" not in str(result)


def test_apim_backfill_surfaces_gateway_only_errors_once() -> None:
    repository = InMemoryFinOpsRepository()
    repository.upsert_events([_app_event()])
    rows = [
        {
            "occurred_at": "2026-07-24T02:00:02Z",
            "correlation_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "status_code": 401,
            "record_kind": "gateway_error",
        },
        {
            "occurred_at": "2026-07-24T02:00:03Z",
            "correlation_id": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "status_code": 500,
            "record_kind": "gateway_error",
        },
    ]

    result = run_apim_backfill(
        repository=repository,
        scopes={"tenant-safe": ("ws-a",), "tenant-other": ("ws-b",)},
        query_rows=lambda _query: rows,
        from_value="2026-07-24T01:55:00Z",
        to_value="2026-07-24T02:05:00Z",
        hmac_secret="test-secret",
    )

    # The gateway error set is tenant-independent and must not be double counted.
    assert result["gateway_only_errors"]["total"] == 2
    assert result["gateway_only_errors"]["client_error_4xx"] == 1
    assert result["gateway_only_errors"]["server_error_5xx"] == 1


def test_apim_backfill_persists_gateway_evidence_once_across_tenants() -> None:
    from backend.finops.gateway_unmatched import InMemoryGatewayUnmatchedRepository

    repository = InMemoryFinOpsRepository()
    repository.upsert_events([_app_event()])
    gateway = InMemoryGatewayUnmatchedRepository()
    rows = [
        {
            "occurred_at": "2026-07-24T02:00:02Z",
            "correlation_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "status_code": 401,
            "record_kind": "gateway_error",
        },
        {
            "occurred_at": "2026-07-24T02:00:03Z",
            "correlation_id": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "status_code": 500,
            "record_kind": "gateway_error",
        },
    ]

    kwargs = dict(
        repository=repository,
        scopes={"tenant-safe": ("ws-a",), "tenant-other": ("ws-b",)},
        query_rows=lambda _query: rows,
        from_value="2026-07-24T01:55:00Z",
        to_value="2026-07-24T02:05:00Z",
        hmac_secret="test-secret",
        gateway_unmatched_repository=gateway,
    )
    run_apim_backfill(**kwargs)
    # Idempotent: a second identical run must not inflate the aggregate.
    run_apim_backfill(**kwargs)

    summary = gateway.summarize("2026-07-24T00:00:00Z", "2026-07-25T00:00:00Z")
    assert summary["scope"] == "unattributed"
    assert summary["unmatched_gateway_errors"]["total"] == 2
    assert summary["unmatched_gateway_errors"]["client_error_4xx"] == 1
    assert summary["unmatched_gateway_errors"]["server_error_5xx"] == 1


def test_apim_collection_counts_only_observations_matched_in_current_window() -> None:
    secret = "test-secret"
    matched_id = "4f8b0f37b5824af5a2ac7ed9129ee70b"
    unmatched_id = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    matched = _app_event().model_copy(
        update={
            "correlation_ref": default_correlation_ref(
                "tenant-safe",
                matched_id,
                secret,
            )
        }
    )
    previously_governed = _app_event().model_copy(
        update={
            "request_ref": "req_bbbbbbbbbbbb",
            "correlation_ref": "corr_previous",
            "gateway_coverage": "apim_governed",
        }
    )
    repository = InMemoryFinOpsRepository()
    repository.upsert_events([matched, previously_governed])
    rows = [
        {
            "occurred_at": "2026-07-24T02:00:01Z",
            "correlation_id": matched_id,
            "total_tokens": 25,
            "status_code": 200,
        },
        {
            "occurred_at": "2026-07-24T02:00:02Z",
            "correlation_id": unmatched_id,
            "total_tokens": 10,
            "status_code": 200,
        },
    ]

    result = collect_apim_usage(
        repository=repository,
        query_rows=lambda _query: rows,
        tenant_ref="tenant-safe",
        workspace_ids=("ws-a",),
        from_value="2026-07-24T01:55:00Z",
        to_value="2026-07-24T02:05:00Z",
        hmac_secret=secret,
    )

    assert result["reconciled_events"] == 1
    assert result["unmatched_observations"] == 1


def test_apim_query_captures_gateway_only_errors_without_message_bodies() -> None:
    query = apim_usage_query(
        "2026-07-24T02:00:00Z",
        "2026-07-24T02:05:00Z",
    )
    # Gateway-only error rows must be surfaced via a left-anti join and tagged.
    assert "leftanti" in query
    assert "record_kind" in query
    assert "gateway_error" in query
    assert "ResponseCode" in query
    assert "RequestMessages" not in query
    assert "ResponseBody" not in query


def test_summarize_gateway_only_errors_aggregates_by_status_class_only() -> None:
    rows = [
        {"correlation_id": "a" * 32, "status_code": 401, "record_kind": "gateway_error"},
        {"correlation_id": "b" * 32, "status_code": 429, "record_kind": "gateway_error"},
        {"correlation_id": "c" * 32, "status_code": 500, "record_kind": "gateway_error"},
        {"correlation_id": "d" * 32, "status_code": 503, "record_kind": "gateway_error"},
        {"correlation_id": "e" * 32, "status_code": 200, "record_kind": "gateway_error"},
    ]

    summary = summarize_gateway_only_errors(rows)

    assert summary["total"] == 4
    assert summary["client_error_4xx"] == 2
    assert summary["server_error_5xx"] == 2
    assert summary["status_breakdown"] == {"401": 1, "429": 1, "500": 1, "503": 1}
    # Aggregate evidence must never leak per-request correlation identifiers.
    assert "a" * 32 not in str(summary)


def test_collect_apim_usage_surfaces_gateway_only_error_aggregate() -> None:
    secret = "test-secret"
    matched_id = "4f8b0f37b5824af5a2ac7ed9129ee70b"
    matched = _app_event().model_copy(
        update={
            "correlation_ref": default_correlation_ref(
                "tenant-safe",
                matched_id,
                secret,
            )
        }
    )
    repository = InMemoryFinOpsRepository()
    repository.upsert_events([matched])
    rows = [
        {
            "occurred_at": "2026-07-24T02:00:01Z",
            "correlation_id": matched_id,
            "total_tokens": 25,
            "status_code": 200,
            "record_kind": "llm",
        },
        {
            "occurred_at": "2026-07-24T02:00:02Z",
            "correlation_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "status_code": 429,
            "record_kind": "gateway_error",
        },
        {
            "occurred_at": "2026-07-24T02:00:03Z",
            "correlation_id": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "status_code": 503,
            "record_kind": "gateway_error",
        },
    ]

    result = collect_apim_usage(
        repository=repository,
        query_rows=lambda _query: rows,
        tenant_ref="tenant-safe",
        workspace_ids=("ws-a",),
        from_value="2026-07-24T01:55:00Z",
        to_value="2026-07-24T02:05:00Z",
        hmac_secret=secret,
    )

    assert result["reconciled_events"] == 1
    assert result["gateway_only_errors"]["total"] == 2
    assert result["gateway_only_errors"]["client_error_4xx"] == 1
    assert result["gateway_only_errors"]["server_error_5xx"] == 1


def test_apim_reconciliation_prices_recovered_tokens_with_official_mapping() -> None:
    secret = "test-secret"
    correlation_id = "4f8b0f37b5824af5a2ac7ed9129ee70b"
    app = _app_event().model_copy(
        update={
            "tokens": TokenUsage(),
            "deployment": "gpt-5-mini",
            "usage_source": "application",
            "evidence_state": "partial",
            "correlation_ref": default_correlation_ref(
                "tenant-safe", correlation_id, secret
            ),
        }
    )
    repository = InMemoryFinOpsRepository()
    repository.upsert_events([app])
    mappings = InMemoryPriceMappingRepository()
    mappings.upsert(
        DeploymentPriceMapping(
            tenant_ref="tenant-safe",
            deployment="gpt-5-mini",
            official_price_key="azure-openai:gpt-5.1:global-standard:global",
            mapping_revision=1,
            updated_by_ref="actor-owner",
        ),
        base_revision=0,
    )
    rows = [
        {
            "occurred_at": "2026-07-24T02:00:01Z",
            "correlation_id": correlation_id,
            "prompt_tokens": 20,
            "completion_tokens": 5,
            "total_tokens": 25,
            "deployment": "gpt-5-mini",
            "is_streaming": False,
            "status_code": 200,
        }
    ]

    collect_apim_usage(
        repository=repository,
        query_rows=lambda _query: rows,
        tenant_ref="tenant-safe",
        workspace_ids=("ws-a",),
        from_value="2026-07-24T01:55:00Z",
        to_value="2026-07-24T02:05:00Z",
        hmac_secret=secret,
        price_mapping_repository=mappings,
    )

    [event] = repository.list_events(
        tenant_ref="tenant-safe",
        workspace_ids=("ws-a",),
        from_value="2026-07-24T00:00:00Z",
        to_value="2026-07-25T00:00:00Z",
    )
    assert event.tokens.total == 25
    assert event.estimated_cost.amount == 7.5e-05
    assert (
        event.estimated_cost.official_price_key
        == "azure-openai:gpt-5.1:global-standard:global"
    )
    assert event.estimated_cost.mapping_revision == 1


def test_apim_reconciliation_leaves_unmapped_deployment_unpriced() -> None:
    secret = "test-secret"
    correlation_id = "4f8b0f37b5824af5a2ac7ed9129ee70b"
    app = _app_event().model_copy(
        update={
            "tokens": TokenUsage(),
            "deployment": "unmapped-deployment",
            "usage_source": "application",
            "evidence_state": "partial",
            "correlation_ref": default_correlation_ref(
                "tenant-safe", correlation_id, secret
            ),
        }
    )
    repository = InMemoryFinOpsRepository()
    repository.upsert_events([app])
    rows = [
        {
            "occurred_at": "2026-07-24T02:00:01Z",
            "correlation_id": correlation_id,
            "prompt_tokens": 20,
            "completion_tokens": 5,
            "total_tokens": 25,
            "deployment": "unmapped-deployment",
            "is_streaming": False,
            "status_code": 200,
        }
    ]

    collect_apim_usage(
        repository=repository,
        query_rows=lambda _query: rows,
        tenant_ref="tenant-safe",
        workspace_ids=("ws-a",),
        from_value="2026-07-24T01:55:00Z",
        to_value="2026-07-24T02:05:00Z",
        hmac_secret=secret,
        price_mapping_repository=InMemoryPriceMappingRepository(),
    )

    [event] = repository.list_events(
        tenant_ref="tenant-safe",
        workspace_ids=("ws-a",),
        from_value="2026-07-24T00:00:00Z",
        to_value="2026-07-25T00:00:00Z",
    )
    assert event.tokens.total == 25
    assert event.estimated_cost.amount is None
