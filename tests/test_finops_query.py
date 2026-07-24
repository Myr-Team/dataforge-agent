from __future__ import annotations

import json
from datetime import datetime, timezone

from backend.finops.models import FinOpsRequestEvent, TokenUsage
from backend.finops.query import FinOpsQuery, FinOpsQueryService
from backend.finops.repository import InMemoryFinOpsRepository


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
