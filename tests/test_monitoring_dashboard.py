from __future__ import annotations

from backend.monitoring_dashboard import build_monitor_dashboard


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
