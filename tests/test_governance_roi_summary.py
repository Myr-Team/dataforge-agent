import json
from urllib.parse import quote

import backend.control_plane as control_plane
from backend.app import app
from fastapi.testclient import TestClient


class RequestStub:
    headers = {
        "x-dataforge-actor": quote(json.dumps({"name": "Owner", "email": "owner@contoso.com"})),
    }


def test_governance_summary_groups_token_cost_and_roi_by_actor(monkeypatch):
    owner = {"name": "Owner", "email": "owner@contoso.com", "actor_id": "oid-owner"}
    reviewer = {"name": "Reviewer", "email": "reviewer@contoso.com", "actor_id": "oid-reviewer"}
    runs = [
        {
            "run_id": "run-owner",
            "workspace_id": "ws-governance",
            "actor": owner,
            "status": "done",
            "title": "Store placement analysis",
            "tokens": {"total": 1000, "prompt": 600, "completion": 400},
            "completed_at": "2026-07-09T00:01:00Z",
            "steps": [{"event": "audit", "time": "2026-07-09T00:00:30Z", "data": {"verdict": "pass"}}],
        },
        {
            "run_id": "run-reviewer",
            "workspace_id": "ws-governance",
            "actor": reviewer,
            "status": "done",
            "title": "Follow-up pilot plan",
            "tokens": {"total": 3000, "prompt": 2000, "completion": 1000},
            "completed_at": "2026-07-09T00:02:00Z",
            "steps": [],
        },
    ]
    conversations = [
        {
            "conversation_id": "conv-reviewer",
            "workspace_id": "ws-governance",
            "title": "Pilot discussion",
            "updated_at": "2026-07-09T00:03:00Z",
            "turn_count": 2,
            "actors": [reviewer],
        }
    ]
    monkeypatch.setattr(control_plane, "list_runs", lambda workspace_id=None: runs)
    monkeypatch.setattr(control_plane, "get_run", lambda run_id: next(item for item in runs if item["run_id"] == run_id))
    monkeypatch.setattr(control_plane, "list_conversations", lambda workspace_id=None: conversations)
    result = control_plane.workspace_governance_summary("ws-governance", RequestStub())

    assert result["usage"]["totals"]["total_tokens"] == 4000
    assert result["roi"]["inputs"]["analysis_runs"] == 2
    assert result["roi"]["inputs"]["conversation_turns"] == 2
    assert result["roi"]["estimated_cost_usd"] is None
    assert result["roi"]["estimated_value_usd"] is None
    assert result["roi"]["net_value_usd"] is None
    assert result["roi"]["business_value"] is None
    reviewer_row = next(row for row in result["chargeback"]["members"] if row["actor"]["email"] == "reviewer@contoso.com")
    assert reviewer_row["total_tokens"] == 3000
    assert reviewer_row["estimated_cost_usd"] is None
    assert result["audit"]["count"] == 3
    assert result["security"]["identity_provider"] == "Microsoft Entra ID"


def test_roi_excludes_delivery_snapshots_from_analysis_value(monkeypatch):
    runs = [
        {
            "run_id": "run-analysis",
            "workspace_id": "ws-roi",
            "status": "completed",
            "tokens": {"total": 2000, "prompt": 1200, "completion": 800},
            "completed_at": "2026-07-11T00:01:00Z",
        },
        {
            "run_id": "run-artifact",
            "workspace_id": "ws-roi",
            "status": "completed",
            "version_kind": "artifact_generation",
            "tokens": {"total": 0, "prompt": 0, "completion": 0},
            "completed_at": "2026-07-11T00:02:00Z",
        },
        {
            "run_id": "run-plan",
            "workspace_id": "ws-roi",
            "status": "completed",
            "version_kind": "plan_draft",
            "tokens": {"total": 0, "prompt": 0, "completion": 0},
            "completed_at": "2026-07-11T00:03:00Z",
        },
    ]
    monkeypatch.setattr(control_plane, "list_runs", lambda workspace_id=None: runs)
    monkeypatch.setattr(control_plane, "get_run", lambda run_id: next(item for item in runs if item["run_id"] == run_id))
    monkeypatch.setattr(control_plane, "list_conversations", lambda workspace_id=None: [])

    result = control_plane.workspace_governance_summary("ws-roi", RequestStub())

    assert result["usage"]["totals"]["runs"] == 3
    assert result["usage"]["totals"]["agent_runs"] == 1
    assert result["usage"]["totals"]["snapshot_runs"] == 2
    assert result["roi"]["inputs"]["analysis_runs"] == 1
    assert result["roi"]["inputs"]["snapshot_runs_excluded"] == 2
    assert result["roi"]["confidence"] == "estimated"
    assert result["roi"]["assumptions_source"] == "not_configured"


def test_dashboard_default_workspace_is_not_a_static_demo_id(monkeypatch):
    monkeypatch.setattr(control_plane, "get_workspace_detail", lambda workspace_id: {"workspace_id": workspace_id})
    monkeypatch.setattr(control_plane, "list_workspaces", lambda: [{"workspace_id": "ws-requested"}])
    monkeypatch.setattr(control_plane, "list_runs", lambda workspace_id=None: [])
    monkeypatch.setattr(control_plane, "list_conversations", lambda workspace_id=None: [])
    monkeypatch.setattr(control_plane, "_cached_health", lambda: {"dependencies": {}, "dependency_details": {}})

    result = control_plane.build_workspace_dashboard("ws-requested")

    assert result["health"]["workspace_default"] == "ws-requested"


def test_roi_deduplicates_multi_actor_conversation_turns() -> None:
    usage = {
        "totals": {
            "runs": 1,
            "agent_runs": 1,
            "snapshot_runs": 0,
            "total_tokens": 100,
        }
    }
    audit = {
        "events": [
            {"type": "conversation", "conversation_id": "conv-shared", "turn_count": 4, "actor": {"email": "a@example.com"}},
            {"type": "conversation", "conversation_id": "conv-shared", "turn_count": 4, "actor": {"email": "b@example.com"}},
        ]
    }

    result = control_plane._workspace_roi_summary(usage, audit)

    assert result["inputs"]["conversation_turns"] == 4


def test_governance_keeps_all_unknown_usage_null(monkeypatch) -> None:
    runs = [
        {
            "run_id": "run-owner-unknown",
            "workspace_id": "ws-unknown",
            "actor": {"name": "Owner", "email": "owner@contoso.com"},
            "status": "completed",
            "completed_at": "2026-07-12T00:01:00Z",
        },
        {
            "run_id": "run-reviewer-unknown",
            "workspace_id": "ws-unknown",
            "actor": {"name": "Reviewer", "email": "reviewer@contoso.com"},
            "status": "completed",
            "completed_at": "2026-07-12T00:02:00Z",
        },
    ]
    monkeypatch.setattr(control_plane, "list_runs", lambda workspace_id=None: runs)
    monkeypatch.setattr(control_plane, "get_run", lambda run_id: next(item for item in runs if item["run_id"] == run_id))
    monkeypatch.setattr(control_plane, "list_conversations", lambda workspace_id=None: [])

    result = control_plane.workspace_governance_summary("ws-unknown", RequestStub())

    totals = result["usage"]["totals"]
    assert totals["total_tokens"] is None
    assert totals["prompt_tokens"] is None
    assert totals["completion_tokens"] is None
    assert totals["known_usage_runs"] == 0
    assert totals["unknown_usage_runs"] == 2
    assert totals["usage_status"] == "unknown"
    assert all(item["usage"]["total_tokens"] is None for item in result["usage"]["members"])
    assert result["roi"]["inputs"]["total_tokens"] is None
    assert result["roi"]["estimated_cost_usd"] is None
    assert result["roi"]["net_value_usd"] is None
    assert result["chargeback"]["totals"]["total_tokens"] is None
    assert all(item["estimated_cost_usd"] is None for item in result["chargeback"]["members"])

    response = TestClient(app).get("/api/workspaces/ws-unknown/governance-summary")
    assert response.status_code == 200
    body = response.json()
    assert body["usage"]["totals"]["total_tokens"] is None
    assert body["usage"]["totals"]["usage_status"] == "unknown"
    assert body["roi"]["estimated_cost_usd"] is None


def test_governance_sums_known_usage_and_marks_mixed_data_partial(monkeypatch) -> None:
    actor = {"name": "Owner", "email": "owner@contoso.com"}
    runs = [
        {
            "run_id": "run-known",
            "workspace_id": "ws-partial",
            "actor": actor,
            "status": "completed",
            "tokens": {"total": 120, "prompt": 80, "completion": 40},
            "completed_at": "2026-07-12T00:01:00Z",
        },
        {
            "run_id": "run-unknown",
            "workspace_id": "ws-partial",
            "actor": actor,
            "status": "completed",
            "completed_at": "2026-07-12T00:02:00Z",
        },
    ]
    monkeypatch.setattr(control_plane, "list_runs", lambda workspace_id=None: runs)
    monkeypatch.setattr(control_plane, "get_run", lambda run_id: next(item for item in runs if item["run_id"] == run_id))
    monkeypatch.setattr(control_plane, "list_conversations", lambda workspace_id=None: [])

    result = control_plane.workspace_governance_summary("ws-partial", RequestStub())

    totals = result["usage"]["totals"]
    assert totals["total_tokens"] == 120
    assert totals["prompt_tokens"] == 80
    assert totals["completion_tokens"] == 40
    assert totals["known_usage_runs"] == 1
    assert totals["unknown_usage_runs"] == 1
    assert totals["usage_status"] == "partial"
    member = result["usage"]["members"][0]["usage"]
    assert member["known_usage_runs"] == 1
    assert member["unknown_usage_runs"] == 1
    assert member["usage_status"] == "partial"
    assert result["roi"]["inputs"]["usage_status"] == "partial"
    assert result["chargeback"]["totals"]["usage_status"] == "partial"


def test_governance_reports_foundry_compatible_observability_truthfully(monkeypatch) -> None:
    monkeypatch.setattr(control_plane, "list_runs", lambda workspace_id=None: [])
    monkeypatch.setattr(control_plane, "list_conversations", lambda workspace_id=None: [])
    monkeypatch.setattr(control_plane, "list_outcome_events", lambda workspace_id: [])
    monkeypatch.setattr(
        control_plane,
        "observability_snapshot",
        lambda: {
            "tracing": {
                "app_insights": True,
                "otel_sdk": True,
                "exporter": "azure-monitor-opentelemetry",
                "service_name": "dataforge-backend",
            }
        },
    )

    result = control_plane.workspace_governance_summary("ws-observability", RequestStub())

    monitoring = result["foundry_monitoring"]
    assert monitoring["status"] == "partial"
    assert "remote trace delivery" in monitoring["note"]
    assert monitoring["gen_ai_semantic_conventions"] is True
    assert monitoring["source"] == "application_insights"
    assert result["roi"]["native_foundry_roi"]["status"] == "not_configured"
