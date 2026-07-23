from __future__ import annotations

import pytest

from backend.monitoring_dashboard import _usage_from_dict, build_monitor_dashboard


def test_monitor_dashboard_preserves_unknown_usage_and_groups_observed_model_routes() -> None:
    payload = build_monitor_dashboard(
        ["ws-a"],
        scope="current",
        from_value="2026-07-01T00:00:00Z",
        to_value="2026-07-08T00:00:00Z",
        actor={"actor_id": "owner-a"},
        run_loader=lambda _workspace_id: [
            {
                "run_id": "run-1",
                "workspace_id": "ws-a",
                "status": "completed",
                "completed_at": "2026-07-02T10:00:00Z",
                "duration_ms": 1200,
                "actor": {"actor_id": "owner-a"},
                "tokens": {"prompt": 80, "completion": 20, "total": 100},
                "models": [{"route": "follow_up", "deployment": "gpt-mini", "usage": {"total": 100}}],
            },
            {
                "run_id": "run-2",
                "workspace_id": "ws-a",
                "status": "completed",
                "completed_at": "2026-07-02T11:00:00Z",
            },
        ],
    )

    assert payload["summary"]["tokens"] == {
        "input": 80,
        "output": 20,
        "total": 100,
        "known_runs": 1,
        "unknown_runs": 1,
    }
    assert payload["models"] == [
        {"deployment": "gpt-mini", "route": "follow_up", "calls": 1, "total_tokens": 100}
    ]
    assert payload["summary"]["calls"]["observed"] == 2


def test_monitor_dashboard_keeps_audited_runs_unknown_when_only_activity_feed_exists() -> None:
    payload = build_monitor_dashboard(
        ["ws-a"],
        scope="current",
        from_value="2026-07-01T00:00:00Z",
        to_value="2026-07-08T00:00:00Z",
        actor={"actor_id": "owner-a"},
        run_loader=lambda _workspace_id: [],
        audit_loader=lambda _workspace_id: {"count": 5, "events": [{"type": "run"}]},
    )

    assert payload["summary"]["quality"] == {
        "evidence_coverage_pct": None,
        "audited_runs": None,
        "rework_runs": 0,
        "evaluator_coverage_pct": None,
        "context_optimization": {
            "status": "unavailable",
            "sample_count": None,
            "evaluator_version": None,
            "eligible": False,
        },
    }


def test_monitor_dashboard_reports_context_optimization_gate_without_claiming_success() -> None:
    payload = build_monitor_dashboard(
        ["ws-a"],
        scope="current",
        from_value="2026-07-01T00:00:00Z",
        to_value="2026-07-08T00:00:00Z",
        actor={"actor_id": "owner-a"},
        run_loader=lambda _workspace_id: [],
        evaluation_loader=lambda route_id="followup": {
            "status": "stale" if route_id == "followup" else "unavailable",
            "sample_count": 12,
            "evaluator_version": "context-v1",
            "eligible": False,
        },
    )

    assert payload["summary"]["quality"]["context_optimization"] == {
        "status": "stale",
        "sample_count": 12,
        "evaluator_version": "context-v1",
        "eligible": False,
    }


def test_monitor_dashboard_fail_closes_unknown_context_optimization_status() -> None:
    payload = build_monitor_dashboard(
        ["ws-a"],
        scope="current",
        from_value="2026-07-01T00:00:00Z",
        to_value="2026-07-08T00:00:00Z",
        actor={"actor_id": "owner-a"},
        run_loader=lambda _workspace_id: [],
        evaluation_loader=lambda _route_id="followup": {
            "status": "passed",
            "sample_count": 12,
            "evaluator_version": "context-v1",
            "eligible": True,
        },
    )

    assert payload["summary"]["quality"]["context_optimization"] == {
        "status": "malformed",
        "sample_count": 12,
        "evaluator_version": "context-v1",
        "eligible": False,
    }


def test_usage_from_dict_preserves_zero_totals_and_missing_splits() -> None:
    assert _usage_from_dict({"total": 0}) == {"input": None, "output": None, "total": 0}
    assert _usage_from_dict({"total": 50}) == {"input": None, "output": None, "total": 50}


def test_monitor_dashboard_keeps_split_tokens_unknown_when_only_total_is_observed() -> None:
    payload = build_monitor_dashboard(
        ["ws-a"],
        scope="current",
        from_value="2026-07-01T00:00:00Z",
        to_value="2026-07-08T00:00:00Z",
        actor={"actor_id": "owner-a"},
        run_loader=lambda _workspace_id: [
            {
                "run_id": "run-1",
                "workspace_id": "ws-a",
                "status": "completed",
                "completed_at": "2026-07-02T10:00:00Z",
                "tokens": {"total": 50},
            },
            {
                "run_id": "run-2",
                "workspace_id": "ws-a",
                "status": "completed",
                "completed_at": "2026-07-02T11:00:00Z",
                "tokens": {"total": 0},
            },
        ],
    )

    assert payload["summary"]["tokens"] == {
        "input": None,
        "output": None,
        "total": 50,
        "known_runs": 2,
        "unknown_runs": 0,
    }


def test_monitor_dashboard_does_not_fabricate_member_rows_without_chargeback_evidence() -> None:
    payload = build_monitor_dashboard(
        ["ws-a"],
        scope="current",
        from_value="2026-07-01T00:00:00Z",
        to_value="2026-07-08T00:00:00Z",
        actor={"actor_id": "owner-a"},
        run_loader=lambda _workspace_id: [
            {
                "run_id": "run-1",
                "workspace_id": "ws-a",
                "status": "completed",
                "completed_at": "2026-07-02T10:00:00Z",
                "actor": {"actor_id": "owner-a"},
            }
        ],
        chargeback_loader=lambda *_args: {},
    )

    assert payload["members"] == []


def test_monitor_dashboard_aggregates_persisted_estimates_by_route_and_execution_kind() -> None:
    payload = build_monitor_dashboard(
        ["ws-model"],
        scope="current",
        from_value="2026-07-23T00:00:00Z",
        to_value="2026-07-24T00:00:00Z",
        actor={},
        run_loader=lambda _workspace_id: [
            {
                "run_id": "run-priced",
                "workspace_id": "ws-model",
                "status": "completed",
                "completed_at": "2026-07-23T12:00:00Z",
                "models": [
                    {
                        "response_id": "response-1",
                        "route": "terra",
                        "deployment": "gpt-5.6-terra",
                        "selection": "manual",
                        "execution_kind": "full_analysis",
                        "usage": {"prompt": 1_000, "completion": 500, "total": 1_500},
                        "cost_estimate": {"status": "estimated", "currency": "USD", "amount": 0.012, "price_card_revision": 3, "route_id": "terra"},
                    },
                    {
                        "response_id": "response-1",
                        "route": "terra",
                        "deployment": "gpt-5.6-terra",
                        "selection": "manual",
                        "execution_kind": "full_analysis",
                        "usage": {"prompt": 1_000, "completion": 500, "total": 1_500},
                        "cost_estimate": {"status": "estimated", "currency": "USD", "amount": 0.012, "price_card_revision": 3, "route_id": "terra"},
                    },
                    {
                        "response_id": "response-unpriced",
                        "route": "luna",
                        "deployment": "gpt-5.6-luna",
                        "selection": "workspace_policy",
                        "execution_kind": "direct_reply",
                        "usage": {"prompt": 10, "completion": 2, "total": 12},
                        "cost_estimate": {"status": "unavailable", "reason": "price_not_configured"},
                    },
                ],
            }
        ],
    )

    assert payload["summary"]["cost"] == {
        "status": "partial",
        "amount": None,
        "currency": None,
        "unpriced_calls": 1,
    }
    assert payload["routes"] == [
        {"route": "terra", "calls": 1, "total_tokens": 1_500, "selection_counts": {"manual": 1}, "estimated_cost": {"status": "estimated", "amount": 0.012, "currency": "USD", "unpriced_calls": 0}},
        {"route": "luna", "calls": 1, "total_tokens": 12, "selection_counts": {"workspace_policy": 1}, "estimated_cost": {"status": "unavailable", "amount": None, "currency": None, "unpriced_calls": 1}},
    ]
    assert payload["execution_kinds"] == [
        {"execution_kind": "direct_reply", "calls": 1, "total_tokens": 12, "selection_counts": {"workspace_policy": 1}, "estimated_cost": {"status": "unavailable", "amount": None, "currency": None, "unpriced_calls": 1}},
        {"execution_kind": "full_analysis", "calls": 1, "total_tokens": 1_500, "selection_counts": {"manual": 1}, "estimated_cost": {"status": "estimated", "amount": 0.012, "currency": "USD", "unpriced_calls": 0}},
    ]


def test_dashboard_aggregates_cache_only_for_eligible_model_events() -> None:
    dashboard = build_monitor_dashboard(
        ["ws-cache"],
        scope="current",
        from_value="2026-07-23T00:00:00Z",
        to_value="2026-07-24T00:00:00Z",
        actor={},
        run_loader=lambda _workspace_id: [
            {
                "run_id": "run-hit",
                "workspace_id": "ws-cache",
                "status": "completed",
                "completed_at": "2026-07-23T12:00:00Z",
                "models": [
                    {
                        "route": "analysis",
                        "deployment": "gpt-cache",
                        "usage": {"total": 4},
                        "cache": {
                            "state": "hit",
                            "provider": "redis",
                            "elapsed_ms": 2,
                            "source_usage": {"prompt": 100, "completion": 20, "total": 120},
                            "source_cost_estimate": {
                                "status": "estimated",
                                "currency": "USD",
                                "amount": 0.001,
                                "price_card_revision": 1,
                                "route_id": "analysis",
                            },
                        },
                    }
                ],
            },
            {
                "run_id": "run-miss",
                "workspace_id": "ws-cache",
                "status": "completed",
                "completed_at": "2026-07-23T11:00:00Z",
                "models": [{"route": "analysis", "deployment": "gpt-cache", "cache": {"state": "miss", "provider": "redis"}}],
            },
            {
                "run_id": "run-unavailable",
                "workspace_id": "ws-cache",
                "status": "completed",
                "completed_at": "2026-07-23T10:00:00Z",
                "models": [{"route": "analysis", "deployment": "gpt-cache", "cache": {"state": "unavailable", "provider": "redis"}}],
            },
            {
                "run_id": "run-bypassed",
                "workspace_id": "ws-cache",
                "status": "completed",
                "completed_at": "2026-07-23T09:00:00Z",
                "models": [{"route": "analysis", "deployment": "gpt-cache", "cache": {"state": "bypassed", "provider": "redis"}}],
            },
            {
                "run_id": "run-legacy",
                "workspace_id": "ws-cache",
                "status": "completed",
                "completed_at": "2026-07-23T08:00:00Z",
                "models": [{"route": "analysis", "deployment": "gpt-cache", "cache": {"state": "legacy"}}],
            },
        ],
    )

    assert dashboard["summary"]["cache"] == {
        "eligible": 3,
        "hits": 1,
        "misses": 1,
        "unavailable": 1,
        "hit_rate_pct": pytest.approx(33.33),
        "avoided_tokens": 120,
        "avoided_cost": {"status": "estimated", "amount": 0.001, "currency": "USD", "unpriced_hits": 0},
    }
    assert dashboard["requests"][0]["cache"]["state"] == "hit"


def test_dashboard_marks_cache_avoidance_unavailable_without_source_price() -> None:
    dashboard = build_monitor_dashboard(
        ["ws-cache"],
        scope="current",
        from_value="2026-07-23T00:00:00Z",
        to_value="2026-07-24T00:00:00Z",
        actor={},
        run_loader=lambda _workspace_id: [
            {
                "run_id": "run-unpriced-hit",
                "workspace_id": "ws-cache",
                "status": "completed",
                "completed_at": "2026-07-23T12:00:00Z",
                "models": [
                    {
                        "route": "analysis",
                        "deployment": "gpt-cache",
                        "cache": {
                            "state": "hit",
                            "provider": "redis",
                            "source_usage": {"total": 120},
                        },
                    }
                ],
            }
        ],
    )

    assert dashboard["summary"]["cache"]["avoided_tokens"] == 120
    assert dashboard["summary"]["cache"]["avoided_cost"] == {
        "status": "unavailable",
        "amount": None,
        "currency": None,
        "unpriced_hits": 1,
    }


def test_dashboard_projects_model_event_time_latency_and_trusted_member_only() -> None:
    dashboard = build_monitor_dashboard(
        ["ws-cache"],
        scope="current",
        from_value="2026-07-23T00:00:00Z",
        to_value="2026-07-25T00:00:00Z",
        actor={},
        run_loader=lambda _workspace_id: [
            {
                "run_id": "run-ordered",
                "workspace_id": "ws-cache",
                "status": "completed",
                "completed_at": "2026-07-23T23:59:00Z",
                "duration_ms": 9_999,
                "trusted_identity": True,
                "actor": {"actor_id": "actor-a", "tenant_id": "tenant-a"},
                "models": [
                    {
                        "route": "analysis",
                        "deployment": "lexically-later-but-older",
                        "time": "2026-07-24T01:00:00+02:00",
                        "latency_ms": 11,
                    },
                    {
                        "route": "analysis",
                        "deployment": "chronologically-newer",
                        "time": "2026-07-23T23:30:00Z",
                        "latency_ms": 22,
                    },
                ],
            }
        ],
    )

    requests = dashboard["requests"]

    assert [item["deployment"] for item in requests] == [
        "chronologically-newer",
        "lexically-later-but-older",
    ]
    assert requests[0]["duration_ms"] == 22
    assert requests[1]["duration_ms"] == 11
    assert all(item["member_label"] is None for item in requests)
