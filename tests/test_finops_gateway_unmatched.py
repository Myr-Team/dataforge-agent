from __future__ import annotations

from datetime import datetime, timezone

from backend.finops.gateway_unmatched import (
    InMemoryGatewayUnmatchedRepository,
    gateway_status_class,
)


def _rows() -> list[dict[str, object]]:
    return [
        {"occurred_at": "2026-07-24T02:10:00Z", "status_code": 401, "correlation_id": "a" * 32},
        {"occurred_at": "2026-07-24T02:40:00Z", "status_code": 429, "correlation_id": "b" * 32},
        {"occurred_at": "2026-07-24T02:50:00Z", "status_code": 503, "correlation_id": "c" * 32},
    ]


def test_status_class_only_buckets_4xx_and_5xx() -> None:
    assert gateway_status_class(200) is None
    assert gateway_status_class(404) == "client_error_4xx"
    assert gateway_status_class(429) == "client_error_4xx"
    assert gateway_status_class(500) == "server_error_5xx"
    assert gateway_status_class(503) == "server_error_5xx"
    assert gateway_status_class("nan") is None


def test_summary_is_scope_labelled_and_never_carries_correlation_ids() -> None:
    repo = InMemoryGatewayUnmatchedRepository()
    now = datetime(2026, 7, 24, 3, 0, tzinfo=timezone.utc)
    repo.record_gateway_errors(_rows(), data_source="apim_gateway_logs", now=now)

    summary = repo.summarize("2026-07-24T00:00:00Z", "2026-07-25T00:00:00Z")

    assert summary["scope"] == "unattributed"
    assert summary["unmatched_gateway_errors"] == {
        "total": 3,
        "client_error_4xx": 2,
        "server_error_5xx": 1,
    }
    assert summary["data_source"] == "apim_gateway_logs"
    assert summary["updated_at"] == "2026-07-24T03:00:00Z"
    # Aggregate evidence must not leak per-request identifiers.
    assert "a" * 32 not in str(summary)


def test_repeated_collection_is_idempotent_and_never_double_counts() -> None:
    repo = InMemoryGatewayUnmatchedRepository()
    now = datetime(2026, 7, 24, 3, 0, tzinfo=timezone.utc)
    repo.record_gateway_errors(_rows(), now=now)
    repo.record_gateway_errors(_rows(), now=now)
    repo.record_gateway_errors(_rows(), now=now)

    summary = repo.summarize("2026-07-24T00:00:00Z", "2026-07-25T00:00:00Z")

    assert summary["unmatched_gateway_errors"]["total"] == 3


def test_summary_only_covers_requested_window() -> None:
    repo = InMemoryGatewayUnmatchedRepository()
    repo.record_gateway_errors(
        [
            {"occurred_at": "2026-07-24T02:10:00Z", "status_code": 500},
            {"occurred_at": "2026-07-25T09:10:00Z", "status_code": 502},
        ],
        now=datetime(2026, 7, 25, 10, 0, tzinfo=timezone.utc),
    )

    first_day = repo.summarize("2026-07-24T00:00:00Z", "2026-07-25T00:00:00Z")
    second_day = repo.summarize("2026-07-25T00:00:00Z", "2026-07-26T00:00:00Z")

    assert first_day["unmatched_gateway_errors"]["total"] == 1
    assert first_day["unmatched_gateway_errors"]["server_error_5xx"] == 1
    assert second_day["unmatched_gateway_errors"]["total"] == 1


def test_empty_window_reports_zeroed_unattributed_scope() -> None:
    repo = InMemoryGatewayUnmatchedRepository()
    summary = repo.summarize("2026-07-24T00:00:00Z", "2026-07-25T00:00:00Z")

    assert summary["scope"] == "unattributed"
    assert summary["unmatched_gateway_errors"]["total"] == 0
    assert summary["updated_at"] is None
