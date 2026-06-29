from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app import app  # noqa: E402
from backend.conversation_store import append_message  # noqa: E402
from backend.run_store import complete_run, purge_workspace_runs, record_event, start_run  # noqa: E402
from backend.workspace_store import create_workspace_upload_job, delete_workspace, run_workspace_ingest_job  # noqa: E402


def _create_workspace() -> dict[str, Any]:
    csv_content = (
        "account,segment,revenue,risk\n"
        "A,enterprise,1200,low\n"
        "B,midmarket,800,medium\n"
        "C,enterprise,,high\n"
    ).encode("utf-8")
    result = create_workspace_upload_job(
        files=[{"filename": "pipeline_accounts.csv", "content": csv_content, "content_type": "text/csv"}],
        name=f"Control Plane Contract {uuid.uuid4().hex[:6]}",
    )
    if result.get("ingest_job_id"):
        run_workspace_ingest_job(result["workspace_id"], result["ingest_job_id"])
    return result


def _create_run(workspace_id: str) -> str:
    run_id = str(uuid.uuid4())
    start_run(run_id, workspace_id, "Analyze expansion risk and create a pilot plan.")
    record_event(run_id, "ready", {"conversation_id": run_id, "workspace_id": workspace_id})
    record_event(
        run_id,
        "route",
        {
            "intent": "feasibility_analysis",
            "experts": ["df-corpus-analyst", "df-feasibility-analyst", "df-auditor", "df-producer"],
            "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
        },
    )
    record_event(run_id, "role_change", {"agent": "df-corpus-analyst"})
    record_event(run_id, "tool_call", {"agent": "df-corpus-analyst", "name": "search_workspace"})
    record_event(run_id, "tool_result", {"agent": "df-corpus-analyst", "name": "search_workspace", "status": "ok"})
    record_event(run_id, "model_response", {"agent": "df-feasibility-analyst", "usage": {"input_tokens": 200, "output_tokens": 80, "total_tokens": 280}})
    record_event(run_id, "audit", {"verdict": "pass", "issues": []})
    artifact = {
        "workspace_id": workspace_id,
        "conversation_id": run_id,
        "feasibility": {
            "opportunity_id": "Expansion risk pilot",
            "verdict": "conditional",
            "overall_confidence": "data_confirmed",
            "recommendation": "Pilot expansion scoring on enterprise accounts with missing revenue reviewed first.",
            "action_plan": ["Profile missing revenue by segment.", "Run a two-week pilot on enterprise accounts.", "Audit false positives before scaling."],
            "gap_list": ["Revenue is missing for one account, so rollout claims need validation."],
            "dimensions": [
                {"name": "asset_data", "score": 4, "rationale": "The uploaded account file has segment and risk fields."},
                {"name": "resource_cost", "score": 2, "rationale": "Cost evidence is not present yet."},
            ],
        },
        "answer": {
            "text": "Expansion risk pilot is conditionally feasible.\n\nStart with the enterprise segment and audit missing revenue before scaling.",
            "citations": [{"marker": "1", "source_file": "pipeline_accounts.csv", "snippet": "enterprise accounts have risk labels"}],
        },
        "citations": [{"marker": "1", "source_file": "pipeline_accounts.csv", "snippet": "enterprise accounts have risk labels"}],
        "audit": {"verdict": "pass", "issues": []},
        "proposal": {
            "artifact_urls": {
                "pdf": "/api/artifacts/expansion-risk-pilot.pdf",
                "action_plan": "/api/artifacts/expansion-risk-action_plan.md",
            }
        },
    }
    complete_run(run_id, status="completed", final={"text": "done", "artifact": artifact}, artifact=artifact)
    append_message(run_id, workspace_id=workspace_id, role="user", text="Analyze expansion risk.", remote_load=False)
    append_message(run_id, workspace_id=workspace_id, role="assistant", text="Expansion risk pilot is conditionally feasible.", verdict="conditional", remote_load=False)
    return run_id


def test_control_plane_endpoints() -> None:
    workspace = _create_workspace()
    workspace_id = workspace["workspace_id"]
    run_id = _create_run(workspace_id)
    client = TestClient(app)
    try:
        files = client.get(f"/api/workspaces/{workspace_id}/files").json()
        first_file = files["groups"][0]["files"][0]
        assert first_file["records"] == 3
        assert first_file["fields"] == 4

        overview = client.get(f"/api/workspaces/{workspace_id}/overview")
        assert overview.status_code == 200
        overview_payload = overview.json()
        assert overview_payload["metrics"]["run_count"] >= 1
        assert overview_payload["duration_ms"] >= 0

        summary = client.get(f"/api/runs/{run_id}/summary").json()
        assert summary["status"] == "completed"
        assert summary["verdict"] == "conditional"
        assert summary["agent_count"] >= 2
        assert summary["tool_calls"]["total"] == 1
        assert summary["tokens"]["total"] >= 280

        trace = client.get(f"/api/runs/{run_id}/trace").json()
        assert any(item["event"] == "tool_call" for item in trace)
        assert all("detail" in item for item in trace)

        structured = client.get(f"/api/runs/{run_id}/structured-result").json()
        assert structured["summary"]
        assert structured["advice"]
        assert structured["evidence"][0]["name"] == "pipeline_accounts.csv"

        context = client.get(f"/api/conversations/{run_id}/context").json()
        assert context["workspace_id"] == workspace_id
        assert context["current_data_sources"][0]["records"] == 3

        artifacts = client.get(f"/api/workspaces/{workspace_id}/artifacts").json()
        assert {item["type"] for item in artifacts["artifacts"]} >= {"pdf", "markdown"}

        action_tree = client.post(f"/api/workspaces/{workspace_id}/action-plan", json={"playbook": "opportunity_tree", "run_id": run_id}).json()
        action_roadmap = client.post(f"/api/workspaces/{workspace_id}/action-plan", json={"playbook": "roadmap", "run_id": run_id}).json()
        assert action_tree["action_plan"] != action_roadmap["action_plan"]
        assert action_tree["source"] == "run_artifact"

        status = client.get("/api/system-status").json()
        assert "dependencies" in status
        settings = client.get(f"/api/workspaces/{workspace_id}/settings").json()
        assert settings["members"][0]["role"] == "owner"
    finally:
        purge_workspace_runs(workspace_id)
        delete_workspace(workspace_id)


def main() -> None:
    test_control_plane_endpoints()
    print("PASS test_control_plane_endpoints")


if __name__ == "__main__":
    main()
