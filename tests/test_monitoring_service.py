from __future__ import annotations

import backend.control_plane as control_plane
from backend.monitoring_service import build_monitoring_snapshot


def test_monitoring_snapshot_marks_gateway_verified_only_from_workspace_metric_evidence() -> None:
    snapshot = build_monitoring_snapshot(
        {"totals": {}},
        {"count": 0},
        gateway_enabled=True,
        expected_gateway_id="dfmonapim721",
        gateway_evidence={"state": "verified", "governed_calls": 4, "provenance": "apim_custom_metric"},
    )

    assert snapshot["gateway"] == {
        "state": "verified",
        "governed_calls": 4,
        "provenance": "apim_custom_metric",
    }


def test_governance_summary_exposes_truthful_monitoring_snapshot(monkeypatch) -> None:
    monkeypatch.setenv("DF_MEMBER_PSEUDONYM_SALT", "monitoring-test-salt")
    observed_run = {
        "run_id": "run-observed",
        "workspace_id": "ws-monitoring",
        "completed_at": "2026-07-21T08:00:00Z",
        "actor": {"actor_id": "owner-oid", "tenant_id": "tenant-a", "source": "easy_auth"},
        "tokens": {"total": 150, "prompt": 100, "completion": 50},
        "status": "completed",
    }
    unmetered_run = {
        "run_id": "run-unmetered",
        "workspace_id": "ws-monitoring",
        "completed_at": "2026-07-21T08:01:00Z",
        "actor": {"actor_id": "member-oid", "tenant_id": "tenant-a", "source": "easy_auth"},
        "status": "completed",
    }
    runs = [observed_run, unmetered_run]

    monkeypatch.setattr(control_plane, "list_runs", lambda _workspace_id: runs)
    monkeypatch.setattr(control_plane, "get_run", lambda run_id: next(item for item in runs if item["run_id"] == run_id))
    monkeypatch.setattr(control_plane, "workspace_audit_events", lambda *_args: {"count": 3, "events": []})
    monkeypatch.setattr(control_plane, "list_outcome_events", lambda _workspace_id: [])
    monkeypatch.setattr(control_plane, "_workspace_members_by_key", lambda _workspace_id: {})
    monkeypatch.setattr(
        control_plane,
        "observability_snapshot",
        lambda: {"tracing": {"app_insights": True, "otel_sdk": True, "service_name": "dataforge"}},
    )
    monkeypatch.setenv("DF_APIM_GATEWAY_ENABLED", "true")
    monkeypatch.setenv("DF_APIM_EXPECTED_GATEWAY_ID", "dfmonapim721")
    monkeypatch.setattr(
        control_plane,
        "get_gateway_metric_evidence",
        lambda _workspace_id, _gateway_id: {"state": "verified", "governed_calls": 2, "provenance": "apim_custom_metric"},
        raising=False,
    )

    summary = control_plane.workspace_governance_summary("ws-monitoring")

    assert summary["monitoring"] == {
        "evidence_source": "run_store",
        "usage": {
            "status": "partial",
            "total_tokens": 150,
            "input_tokens": 100,
            "output_tokens": 50,
            "known_runs": 1,
            "unknown_runs": 1,
        },
        "gateway": {
            "state": "verified",
            "governed_calls": 2,
            "provenance": "apim_custom_metric",
        },
        "reliability": {
            "completed_runs": 2,
            "failed_runs": 0,
            "audit_events": 3,
            "provenance": "run_store_and_audit_store",
        },
        "models": {
            "state": "available",
            "default_route": "default",
            "routes": [
                {
                    "id": "default",
                    "deployment": "gpt-5.1",
                    "label": "gpt-5.1",
                    "capabilities": ["analysis", "chat", "research"],
                }
            ],
        },
    }
