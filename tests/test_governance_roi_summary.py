import json
from urllib.parse import quote

import backend.control_plane as control_plane


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
    monkeypatch.setenv("DF_ROI_TOKEN_COST_PER_1M", "10")
    monkeypatch.setenv("DF_ROI_HOURLY_VALUE_USD", "120")
    monkeypatch.setenv("DF_ROI_MINUTES_SAVED_PER_ANALYSIS", "30")
    monkeypatch.setenv("DF_ROI_MINUTES_SAVED_PER_FOLLOWUP", "5")

    result = control_plane.workspace_governance_summary("ws-governance", RequestStub())

    assert result["usage"]["totals"]["total_tokens"] == 4000
    assert result["roi"]["inputs"]["analysis_runs"] == 2
    assert result["roi"]["inputs"]["conversation_turns"] == 2
    assert result["roi"]["estimated_cost_usd"] == 0.04
    assert result["roi"]["estimated_value_usd"] == 140.0
    assert result["roi"]["net_value_usd"] == 139.96
    reviewer_row = next(row for row in result["chargeback"]["members"] if row["actor"]["email"] == "reviewer@contoso.com")
    assert reviewer_row["total_tokens"] == 3000
    assert reviewer_row["estimated_cost_usd"] == 0.03
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
    assert result["roi"]["assumptions_source"] in {"defaults", "environment"}


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
