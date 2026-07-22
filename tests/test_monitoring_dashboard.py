from __future__ import annotations

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
