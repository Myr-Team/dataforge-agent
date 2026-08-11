from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from backend.finops.gateway_unmatched import InMemoryGatewayUnmatchedRepository
from backend.finops.models import FinOpsRequestEvent, TokenUsage
from backend.finops.query import FinOpsQuery, FinOpsQueryService
from backend.finops.repository import InMemoryFinOpsRepository
from backend.finops.rollups import aggregate_rollups


def _event(
    request_ref: str,
    *,
    tenant: str = "tenant-a",
    workspace: str = "ws-a",
    actor: str = "actor-a",
    department: str | None = None,
    model: str = "gpt-5-mini",
    total: int | None = 100,
    cost: float | None = None,
) -> FinOpsRequestEvent:
    return FinOpsRequestEvent.model_validate(
        {
            "request_ref": request_ref,
            "occurred_at": datetime(2026, 7, 24, 2, 0, tzinfo=timezone.utc),
            "call_class": "model",
            "tenant_ref": tenant,
            "workspace_id": workspace,
            "department_id": department,
            "actor_ref": actor,
            "status": "succeeded",
            "tokens": TokenUsage(total=total),
            "gateway_coverage": "app_observed",
            "estimated_cost": {
                "amount": cost,
                "currency": "USD",
                "status": "estimated" if cost is not None else "unavailable",
                "price_card_revision": "price-1" if cost is not None else None,
            },
            "evidence_state": "partial" if total is None else "observed",
        }
    )


def test_query_service_scopes_tenant_and_authorized_workspaces() -> None:
    repository = InMemoryFinOpsRepository()
    repository.upsert_events(
        [
            _event("req_aaaaaaaaaaaa", workspace="ws-a"),
            _event("req_bbbbbbbbbbbb", workspace="ws-b"),
            _event("req_cccccccccccc", tenant="tenant-b", workspace="ws-a"),
        ]
    )
    service = FinOpsQueryService(repository)

    response = service.requests(
        FinOpsQuery(
            tenant_ref="tenant-a",
            authorized_workspace_ids=("ws-a",),
            from_value="2026-07-01T00:00:00Z",
            to_value="2026-07-25T00:00:00Z",
        )
    )

    assert [row["request_ref"] for row in response["items"]] == ["req_aaaaaaaaaaaa"]
    assert response["scope"]["workspace_ids"] == ["ws-a"]
    assert response["currency"] == "USD"
    assert response["data_status"] == "partial"


def test_query_service_uses_cursor_pagination_without_exposing_internal_keys() -> None:
    repository = InMemoryFinOpsRepository()
    repository.upsert_events(
        [
            _event("req_aaaaaaaaaaaa"),
            _event("req_bbbbbbbbbbbb"),
            _event("req_cccccccccccc"),
        ]
    )
    service = FinOpsQueryService(repository)
    query = FinOpsQuery(
        tenant_ref="tenant-a",
        authorized_workspace_ids=("ws-a",),
        from_value="2026-07-01T00:00:00Z",
        to_value="2026-07-25T00:00:00Z",
        limit=2,
    )

    first = service.requests(query)
    second = service.requests(query.model_copy(update={"cursor": first["next_cursor"]}))

    assert len(first["items"]) == 2
    assert len(second["items"]) == 1
    assert first["next_cursor"]
    assert all("internal_correlation_key" not in row for row in first["items"])


def test_overview_keeps_unpriced_requests_visible_instead_of_inventing_cost() -> None:
    repository = InMemoryFinOpsRepository()
    repository.upsert_events(
        [
            _event("req_aaaaaaaaaaaa", total=100, cost=0.01),
            _event("req_bbbbbbbbbbbb", total=50, cost=None),
        ]
    )
    service = FinOpsQueryService(repository)

    response = service.overview(
        FinOpsQuery(
            tenant_ref="tenant-a",
            authorized_workspace_ids=("ws-a",),
            from_value="2026-07-01T00:00:00Z",
            to_value="2026-07-25T00:00:00Z",
        )
    )

    assert response["metrics"]["requests"] == 2
    assert response["metrics"]["tokens"]["total"] == 150
    assert response["metrics"]["estimated_cost"] == {
        "amount": 0.01,
        "priced_requests": 1,
        "unpriced_requests": 1,
        "status": "partial",
    }
    assert response["metrics"]["success_rate_pct"] == 100.0
    assert response["data_status"] == "partial"


def test_trends_distinguish_closed_days_from_the_current_incomplete_day() -> None:
    now = datetime.now(timezone.utc)
    current = _event("req_current_bucket", cost=1.25).model_copy(
        update={"occurred_at": now - timedelta(minutes=5)}
    )
    closed = _event("req_closed_bucket", cost=2.5).model_copy(
        update={"occurred_at": now - timedelta(days=1)}
    )
    repository = InMemoryFinOpsRepository()
    repository.upsert_events([closed, current])

    response = FinOpsQueryService(repository).trends(
        FinOpsQuery(
            tenant_ref="tenant-a",
            authorized_workspace_ids=("ws-a",),
            from_value=(now - timedelta(days=2)).isoformat(),
            to_value=(now + timedelta(minutes=1)).isoformat(),
        ),
        "day",
        metric="estimated_cost",
    )

    assert [item["bucket_status"] for item in response["items"]] == [
        "complete",
        "in_progress",
    ]


def test_overview_exposes_recorded_token_and_cache_composition() -> None:
    raw_events = []
    for request_ref, tokens, cache in (
        (
            "req_aaaaaaaaaaaa",
            {"input": 80, "output": 20, "cached_input": 10, "total": 100},
            {"state": "hit", "eligible": True},
        ),
        (
            "req_bbbbbbbbbbbb",
            {"input": 40, "output": 10, "reasoning": 5, "total": 55},
            {"state": "miss", "eligible": True},
        ),
        (
            "req_cccccccccccc",
            {},
            {"state": "bypassed", "eligible": False},
        ),
    ):
        payload = _event(request_ref, total=None).model_dump(mode="python")
        payload["tokens"] = tokens
        payload["cache"] = cache
        raw_events.append(FinOpsRequestEvent.model_validate(payload))
    repository = InMemoryFinOpsRepository()
    repository.upsert_events(raw_events)

    response = FinOpsQueryService(repository).overview(
        FinOpsQuery(
            tenant_ref="tenant-a",
            authorized_workspace_ids=("ws-a",),
            from_value="2026-07-01T00:00:00Z",
            to_value="2026-07-25T00:00:00Z",
        )
    )

    assert response["metrics"]["tokens"] == {
        "input": 120,
        "output": 30,
        "cached_input": 10,
        "reasoning": 5,
        "total": 155,
        "known_requests": 2,
        "unknown_requests": 1,
    }
    assert response["metrics"]["cache"] == {
        "eligible_requests": 2,
        "hit": 1,
        "miss": 1,
        "bypassed": 1,
        "unavailable": 0,
        "avoided_tokens": None,
        "estimated_savings": None,
        "data_status": "unavailable",
    }


def test_overview_separates_result_cache_requests_from_provider_cache_tokens() -> None:
    raw_events = []
    for request_ref, result_cache, provider_cache in (
        (
            "req_aaaaaaaaaaaa",
            {"state": "hit", "eligible": True, "reason": "eligible", "policy_revision": 2},
            {
                "state": "partial_hit",
                "hit_tokens": 80,
                "miss_tokens": 20,
                "hit_rate_pct": 80,
                "evidence_state": "observed",
            },
        ),
        (
            "req_bbbbbbbbbbbb",
            {"state": "miss", "eligible": True, "reason": "eligible", "policy_revision": 2},
            {
                "state": "miss",
                "hit_tokens": 0,
                "miss_tokens": 100,
                "hit_rate_pct": 0,
                "evidence_state": "observed",
            },
        ),
    ):
        payload = _event(request_ref).model_dump(mode="python")
        payload["result_cache"] = result_cache
        payload["cache"] = {"state": result_cache["state"], "eligible": True}
        payload["provider_cache"] = provider_cache
        raw_events.append(FinOpsRequestEvent.model_validate(payload))
    repository = InMemoryFinOpsRepository()
    repository.upsert_events(raw_events)

    response = FinOpsQueryService(repository).overview(
        FinOpsQuery(
            tenant_ref="tenant-a",
            authorized_workspace_ids=("ws-a",),
            from_value="2026-07-01T00:00:00Z",
            to_value="2026-07-25T00:00:00Z",
        )
    )

    assert response["metrics"]["result_cache"]["hit_rate_pct"] == 50
    assert response["metrics"]["provider_cache"] == {
        "known_requests": 2,
        "hit_tokens": 80,
        "miss_tokens": 120,
        "hit_rate_pct": 40.0,
        "data_status": "available",
    }


def test_model_breakdown_exposes_cached_uncached_and_output_token_composition() -> None:
    first = _event("req_model_cache_0001").model_copy(update={
        "deployment": "deepseek-v4-flash",
        "tokens": TokenUsage(input=100, cached_input=70, output=20, total=120),
    })
    second = _event("req_model_cache_0002").model_copy(update={
        "deployment": "deepseek-v4-flash",
        "tokens": TokenUsage(input=60, cached_input=0, output=15, total=75),
    })
    repository = InMemoryFinOpsRepository()
    repository.upsert_events([first, second])

    response = FinOpsQueryService(repository).breakdowns(
        FinOpsQuery(
            tenant_ref="tenant-a",
            authorized_workspace_ids=("ws-a",),
            from_value="2026-07-01T00:00:00Z",
            to_value="2026-07-25T00:00:00Z",
        ),
        "model",
    )

    assert response["items"][0]["token_composition"] == {
        "input": 160,
        "cached_input": 70,
        "uncached_input": 90,
        "output": 35,
        "reasoning": None,
        "known_requests": 2,
        "data_status": "available",
    }


def test_bootstrap_reuses_query_metrics_and_bounds_department_summary() -> None:
    repository = InMemoryFinOpsRepository()
    repository.upsert_events(
        [
            _event(f"req_{index:012d}", department=f"department-{index}", cost=0.01)
            for index in range(6)
        ]
    )
    service = FinOpsQueryService(repository)
    query = FinOpsQuery(
        tenant_ref="tenant-a",
        authorized_workspace_ids=("ws-a",),
        workspace_id="ws-a",
        from_value="2026-07-01T00:00:00Z",
        to_value="2026-07-25T00:00:00Z",
    )

    payload = service.bootstrap(query)

    assert payload["overview"]["metrics"]["requests"] == 6
    assert payload["trend"]["bucket"] == "day"
    assert payload["departments"]["count"] == 5
    assert len(payload["departments"]["items"]) == 5
    assert payload["filters"]["workspaces"] == ["ws-a"]


def test_bootstrap_never_contains_request_or_trace_identifiers() -> None:
    repository = InMemoryFinOpsRepository()
    event = _event("req_aaaaaaaaaaaa", department="commerce", cost=0.01).model_copy(
        update={
            "run_id": "run-secret",
            "apim_correlation_id": "4f8b0f37b5824af5a2ac7ed9129ee70b",
        }
    )
    repository.upsert_events([event])
    service = FinOpsQueryService(repository)

    payload = service.bootstrap(
        FinOpsQuery(
            tenant_ref="tenant-a",
            authorized_workspace_ids=("ws-a",),
            workspace_id="ws-a",
            from_value="2026-07-01T00:00:00Z",
            to_value="2026-07-25T00:00:00Z",
        )
    )
    serialized = json.dumps(payload)

    assert "request_ref" not in serialized
    assert "run-secret" not in serialized
    assert "4f8b0f37b5824af5a2ac7ed9129ee70b" not in serialized


def test_overview_exposes_pricing_token_and_apim_trust() -> None:
    repository = InMemoryFinOpsRepository()
    first = _event("req_aaaaaaaaaaaa", total=100, cost=0.01)
    second = _event("req_bbbbbbbbbbbb", total=None, cost=None).model_copy(
        update={"gateway_coverage": "apim_governed"}
    )
    repository.upsert_events([first, second])
    service = FinOpsQueryService(repository)
    query = FinOpsQuery(
        tenant_ref="tenant-a",
        authorized_workspace_ids=("ws-a",),
        from_value="2026-07-01T00:00:00Z",
        to_value="2026-07-25T00:00:00Z",
    )

    trust = service.overview(query)["trust"]

    assert trust["pricing"] == {
        "priced_requests": 1,
        "unpriced_requests": 1,
        "coverage_pct": 50.0,
        "state": "partial",
    }
    assert trust["tokens"] == {
        "known_requests": 1,
        "unknown_requests": 1,
        "coverage_pct": 50.0,
        "state": "partial",
    }
    assert trust["apim"]["apim_governed_requests"] == 1
    assert trust["apim"]["state"] == "reconciliation_pending"


def test_trends_selects_metric_and_preserves_exact_value() -> None:
    repository = InMemoryFinOpsRepository()
    repository.upsert_events(
        [
            _event("req_aaaaaaaaaaaa", total=100, cost=0.01),
            _event("req_bbbbbbbbbbbb", total=50, cost=None),
        ]
    )
    service = FinOpsQueryService(repository)
    query = FinOpsQuery(
        tenant_ref="tenant-a",
        authorized_workspace_ids=("ws-a",),
        from_value="2026-07-01T00:00:00Z",
        to_value="2026-07-25T00:00:00Z",
    )

    tokens = service.trends(query, "day", metric="tokens")
    cost = service.trends(query, "day", metric="estimated_cost")

    assert tokens["metric"] == "tokens"
    assert tokens["unit"] == "Token"
    assert tokens["items"][0]["value"] == 150
    assert cost["metric"] == "estimated_cost"
    assert cost["unit"] == "USD"
    assert cost["items"][0]["value"] == 0.01


def test_unit_economics_trend_uses_rollups_without_reading_historical_request_facts() -> None:
    event = _event("req_aaaaaaaaaaaa", total=100, cost=0.01)
    hourly, daily = aggregate_rollups([event])

    class Rollups:
        def __init__(self) -> None:
            self.calls = 0

        def read(self, query, bucket):
            self.calls += 1
            return daily if bucket == "day" else hourly

    class NoFactReads(InMemoryFinOpsRepository):
        def list_events(self, **kwargs):
            raise AssertionError("historical windows must use persisted rollups")

    rollups = Rollups()
    result = FinOpsQueryService(NoFactReads(), rollup_repository=rollups).unit_economics_trend(
        FinOpsQuery(
            tenant_ref="tenant-a",
            authorized_workspace_ids=("ws-a",),
            from_value="2026-07-01T00:00:00Z",
            to_value="2026-07-25T00:00:00Z",
        )
    )

    assert rollups.calls == 1
    assert result["items"] == [{
        "bucket_at": "2026-07-24",
        "successful_requests": 1,
        "estimated_cost": 0.01,
        "cost_per_successful_request": 0.01,
        "data_status": "available",
    }]


def test_unit_economics_merges_only_current_incomplete_bucket_request_facts() -> None:
    now = datetime.now(timezone.utc)
    current_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    historical = _event("req_aaaaaaaaaaaa", total=100, cost=0.01).model_copy(
        update={"occurred_at": current_day - timedelta(days=1, hours=-1)}
    )
    current = _event("req_bbbbbbbbbbbb", total=100, cost=0.02).model_copy(
        update={"occurred_at": now - timedelta(seconds=1)}
    )
    _, historical_rollups = aggregate_rollups([historical])

    class Rollups:
        def __init__(self) -> None:
            self.calls = []

        def read(self, query, bucket):
            self.calls.append((query.from_value, query.to_value, bucket))
            return historical_rollups

    class Facts(InMemoryFinOpsRepository):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def list_events(self, **kwargs):
            self.calls += 1
            return super().list_events(**kwargs)

    facts = Facts()
    facts.upsert_events([historical, current])
    rollups = Rollups()
    result = FinOpsQueryService(facts, rollup_repository=rollups).unit_economics_trend(
        FinOpsQuery(
            tenant_ref="tenant-a",
            authorized_workspace_ids=("ws-a",),
            from_value=(current_day - timedelta(days=1)).isoformat().replace("+00:00", "Z"),
            to_value=now.isoformat().replace("+00:00", "Z"),
        )
    )

    assert len(rollups.calls) == 1
    assert facts.calls == 1
    assert sum(item["successful_requests"] for item in result["items"]) == 2


def test_unit_economics_future_window_uses_only_closed_rollups_and_current_facts() -> None:
    now = datetime.now(timezone.utc)
    current_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    historical = _event("req_aaaaaaaaaaaa", total=100, cost=0.01).model_copy(
        update={"occurred_at": current_day - timedelta(days=1, hours=-1)}
    )
    current = _event("req_bbbbbbbbbbbb", total=100, cost=0.02).model_copy(
        update={"occurred_at": now - timedelta(seconds=1)}
    )
    future = _event("req_cccccccccccc", total=100, cost=0.03).model_copy(
        update={"occurred_at": current_day + timedelta(days=1, hours=1)}
    )
    _, historical_rollups = aggregate_rollups([historical])
    _, current_rollups = aggregate_rollups([current])
    _, future_rollups = aggregate_rollups([future])

    class Rollups:
        def __init__(self) -> None:
            self.calls = []

        def read(self, query, bucket):
            self.calls.append((query.from_value, query.to_value, bucket))
            # A malformed/stale repository response must not reintroduce an
            # open current bucket or synthesize future data into this result.
            return [*historical_rollups, *current_rollups, *future_rollups]

    class Facts(InMemoryFinOpsRepository):
        def __init__(self) -> None:
            super().__init__()
            self.calls = []

        def list_events(self, **kwargs):
            self.calls.append(kwargs)
            return super().list_events(**kwargs)

    facts = Facts()
    facts.upsert_events([historical, current, future])
    rollups = Rollups()
    result = FinOpsQueryService(facts, rollup_repository=rollups).unit_economics_trend(
        FinOpsQuery(
            tenant_ref="tenant-a",
            authorized_workspace_ids=("ws-a",),
            from_value=(current_day - timedelta(days=1)).isoformat().replace("+00:00", "Z"),
            to_value=(now + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        )
    )

    assert rollups.calls == [(
        (current_day - timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        current_day.isoformat().replace("+00:00", "Z"),
        "day",
    )]
    assert len(facts.calls) == 1
    assert facts.calls[0]["from_value"] == current_day.isoformat().replace("+00:00", "Z")
    fact_end = datetime.fromisoformat(facts.calls[0]["to_value"].replace("Z", "+00:00"))
    assert current_day < fact_end < now + timedelta(seconds=1)
    assert [item["bucket_at"] for item in result["items"]] == [
        (current_day - timedelta(days=1)).date().isoformat(),
        current_day.date().isoformat(),
    ]


def test_unit_economics_actor_scope_uses_request_facts_not_rollups() -> None:
    class Rollups:
        def read(self, query, bucket):
            raise AssertionError("actor scoped query must not read rollups")

    facts = InMemoryFinOpsRepository()
    facts.upsert_events([_event("req_aaaaaaaaaaaa", actor="actor-a", total=100, cost=0.01)])
    result = FinOpsQueryService(facts, rollup_repository=Rollups()).unit_economics_trend(
        FinOpsQuery(
            tenant_ref="tenant-a",
            authorized_workspace_ids=("ws-a",),
            actor_ref="actor-a",
            from_value="2026-07-01T00:00:00Z",
            to_value="2026-07-25T00:00:00Z",
        )
    )

    assert result["count"] == 1


def test_cache_economics_are_scoped_to_trends_and_breakdowns() -> None:
    raw_events = []
    for request_ref, state, avoided_tokens, cost, official_key in (
        ("req_aaaaaaaaaaaa", "miss", None, 0.01, "gpt-5-mini:global"),
        ("req_bbbbbbbbbbbb", "hit", 60, 0.004, "gpt-5-mini:global"),
        ("req_cccccccccccc", "hit", 40, None, None),
        ("req_dddddddddddd", "bypassed", None, 0.003, "gpt-5-mini:global"),
    ):
        payload = _event(
            request_ref,
            department="commerce",
            total=100,
            cost=cost,
        ).model_dump(mode="python")
        payload["cache"] = {
            "state": state,
            "eligible": state in {"hit", "miss"},
            "avoided_tokens": avoided_tokens,
        }
        payload["result_cache"] = {
            "state": state,
            "eligible": state in {"hit", "miss"},
            "reason": "eligible" if state in {"hit", "miss"} else "not_recorded",
            "policy_revision": 1,
        }
        payload["estimated_cost"]["official_price_key"] = official_key
        raw_events.append(FinOpsRequestEvent.model_validate(payload))
    repository = InMemoryFinOpsRepository()
    repository.upsert_events(raw_events)
    service = FinOpsQueryService(repository)
    query = FinOpsQuery(
        tenant_ref="tenant-a",
        authorized_workspace_ids=("ws-a",),
        from_value="2026-07-01T00:00:00Z",
        to_value="2026-07-25T00:00:00Z",
    )

    overview = service.overview(query)
    trend = service.trends(query, "day", metric="tokens")
    breakdown = service.breakdowns(query, "department")

    expected = {
        "eligible_requests": 3,
        "hit": 2,
        "miss": 1,
        "bypassed": 1,
        "unavailable": 0,
        "avoided_tokens": 100,
        "estimated_savings": 0.006,
        "data_status": "partial",
    }
    assert overview["metrics"]["cache"] == expected
    assert trend["items"][0]["cache"] == expected
    assert breakdown["items"][0]["cache_hit_rate_pct"] == 66.67
    assert breakdown["items"][0]["cache"] == expected


def test_overview_pipes_unattributed_gateway_evidence_into_apim_trust() -> None:
    repository = InMemoryFinOpsRepository()
    repository.upsert_events(
        [
            _event("req_aaaaaaaaaaaa", total=100, cost=0.01).model_copy(
                update={"gateway_coverage": "apim_governed"}
            )
        ]
    )
    gateway = InMemoryGatewayUnmatchedRepository()
    gateway.record_gateway_errors(
        [
            {"occurred_at": "2026-07-24T02:10:00Z", "status_code": 401},
            {"occurred_at": "2026-07-24T02:20:00Z", "status_code": 503},
        ]
    )
    service = FinOpsQueryService(repository, gateway_unmatched_repository=gateway)
    query = FinOpsQuery(
        tenant_ref="tenant-a",
        authorized_workspace_ids=("ws-a",),
        from_value="2026-07-01T00:00:00Z",
        to_value="2026-07-25T00:00:00Z",
    )

    apim = service.overview(query)["trust"]["apim"]

    assert apim["unmatched_metric_records"] == 2
    assert apim["gateway_unmatched"]["scope"] == "unattributed"
    assert apim["gateway_unmatched"]["unmatched_gateway_errors"] == {
        "total": 2,
        "client_error_4xx": 1,
        "server_error_5xx": 1,
    }
    assert apim["gateway_unmatched"]["linked_requests"] == 1


def test_gateway_evidence_is_system_scoped_and_identical_across_tenants() -> None:
    gateway = InMemoryGatewayUnmatchedRepository()
    gateway.record_gateway_errors(
        [{"occurred_at": "2026-07-24T02:10:00Z", "status_code": 500}]
    )

    def apim_for(tenant: str) -> dict:
        repository = InMemoryFinOpsRepository()
        repository.upsert_events([_event("req_aaaaaaaaaaaa", tenant=tenant)])
        service = FinOpsQueryService(
            repository, gateway_unmatched_repository=gateway
        )
        query = FinOpsQuery(
            tenant_ref=tenant,
            authorized_workspace_ids=("ws-a",),
            from_value="2026-07-01T00:00:00Z",
            to_value="2026-07-25T00:00:00Z",
        )
        return service.overview(query)["trust"]["apim"]

    tenant_a = apim_for("tenant-a")
    tenant_b = apim_for("tenant-b")

    # Unattributed system evidence is never presented as one tenant's own data;
    # it is identical and explicitly scope-labelled for every tenant.
    assert tenant_a["gateway_unmatched"]["scope"] == "unattributed"
    assert (
        tenant_a["gateway_unmatched"]["unmatched_gateway_errors"]
        == tenant_b["gateway_unmatched"]["unmatched_gateway_errors"]
    )


def test_trends_reports_observed_zero_as_zero_not_null() -> None:
    repository = InMemoryFinOpsRepository()
    repository.upsert_events([_event("req_aaaaaaaaaaaa", total=0, cost=0.0)])
    service = FinOpsQueryService(repository)
    query = FinOpsQuery(
        tenant_ref="tenant-a",
        authorized_workspace_ids=("ws-a",),
        from_value="2026-07-01T00:00:00Z",
        to_value="2026-07-25T00:00:00Z",
    )

    tokens = service.trends(query, "day", metric="tokens")
    cost = service.trends(query, "day", metric="estimated_cost")

    # A genuine observed zero must not collapse into a missing/null gap.
    assert tokens["items"][0]["value"] == 0
    assert tokens["items"][0]["tokens"]["total"] == 0
    assert tokens["items"][0]["data_status"] == "available"
    assert cost["items"][0]["value"] == 0.0
    assert cost["items"][0]["data_status"] == "available"


def test_trends_data_status_is_scoped_to_selected_metric() -> None:
    repository = InMemoryFinOpsRepository()
    repository.upsert_events(
        [
            _event("req_aaaaaaaaaaaa", total=100, cost=0.01),
            _event("req_bbbbbbbbbbbb", total=None, cost=None),
        ]
    )
    service = FinOpsQueryService(repository)
    query = FinOpsQuery(
        tenant_ref="tenant-a",
        authorized_workspace_ids=("ws-a",),
        from_value="2026-07-01T00:00:00Z",
        to_value="2026-07-25T00:00:00Z",
    )

    requests = service.trends(query, "day", metric="requests")
    tokens = service.trends(query, "day", metric="tokens")
    cost = service.trends(query, "day", metric="estimated_cost")

    # Request counts are always exact regardless of token/cost gaps.
    assert requests["items"][0]["data_status"] == "available"
    assert requests["items"][0]["value"] == 2
    # Tokens and cost are only partially observed in this bucket.
    assert tokens["items"][0]["data_status"] == "partial"
    assert cost["items"][0]["data_status"] == "partial"
