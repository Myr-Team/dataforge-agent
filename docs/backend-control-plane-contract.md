# Backend Control Plane Contract

This document covers the backend-only APIs added for the new DataForge enterprise console pages. Frontend UI can consume these endpoints from `feat/foundry-ui` without touching existing production UI flows.

## Overview

`GET /api/workspaces/{workspace_id}/overview`

Returns one aggregated payload for the Overview page.

```json
{
  "workspace_id": "demo-corpus",
  "generated_at": "2026-06-29T12:00:00+00:00",
  "duration_ms": 123,
  "workspace": { "workspace_id": "demo-corpus", "row_count": 182, "field_count": 15 },
  "metrics": {
    "workspace_count": 3,
    "run_count": 5,
    "conversation_count": 5,
    "artifact_count": 4,
    "file_count": 6
  },
  "files": { "groups": [], "storage": { "used_bytes": 0, "total_bytes": 5368709120 } },
  "runs": [],
  "conversations": [],
  "health": {},
  "dependency_details": {},
  "latest_result": {},
  "pipeline": { "run_id": "run-id", "stages": [] },
  "artifacts": []
}
```

The existing `GET /api/workspaces/{workspace_id}/dashboard` now uses the same parallel backend loader but keeps the old response shape.

## File Counts

`GET /api/workspaces/{workspace_id}/files`

Existing file entries now also include:

```json
{
  "record_count": 182,
  "records": 182,
  "field_count": 15,
  "fields": 15
}
```

Use `records` and `fields` for UI labels; keep `record_count` for backward compatibility.

## Method-Specific Action Plan

`POST /api/workspaces/{workspace_id}/action-plan`

Body:

```json
{
  "playbook": "opportunity_tree",
  "run_id": "optional-run-id",
  "feasibility": {}
}
```

Response:

```json
{
  "workspace_id": "demo-corpus",
  "run_id": "run-id",
  "playbook": "opportunity_tree",
  "method_name": "Opportunity Tree",
  "opportunity": "Current opportunity",
  "recommendation": "Use Opportunity Tree...",
  "action_plan": ["step 1", "step 2", "step 3"],
  "evidence_refs": [],
  "source": "run_artifact"
}
```

Supported playbooks follow `backend/pm_skills.py`: `jtbd`, `opportunity_tree`, `prd`, `roadmap`, `pricing`, `experiment`.

## Runs

`GET /api/runs`

Run summaries now include:

```json
{
  "started_at": "2026-06-29T12:00:00+00:00",
  "finished_at": "2026-06-29T12:01:00+00:00",
  "duration_ms": 60000
}
```

`GET /api/runs/{run_id}/summary`

```json
{
  "status": "completed",
  "verdict": "conditional",
  "duration_ms": 60000,
  "agent_count": 4,
  "tool_calls": { "total": 3, "ok": 3, "fail": 0 },
  "tokens": { "total": 1200, "prompt": 800, "completion": 400 },
  "audit": { "status": "pass", "risks": [], "warnings": [] },
  "started_at": "2026-06-29T12:00:00+00:00",
  "finished_at": "2026-06-29T12:01:00+00:00"
}
```

`GET /api/runs/{run_id}/trace`

```json
[
  {
    "index": 0,
    "time": "2026-06-29T12:00:00+00:00",
    "event": "route",
    "agent": "df-corpus-analyst",
    "role": "event",
    "status": "completed",
    "summary": "route: feasibility_analysis",
    "duration_ms": 500,
    "tool_calls": 0,
    "tokens": { "total": 0, "prompt": 0, "completion": 0 },
    "detail": {}
  }
]
```

`GET /api/runs/{run_id}/pipeline`

Returns stage status inferred from persisted trace events.

`GET /api/runs/{run_id}/log?format=json|text`

Returns full persisted run log with credential-like fields redacted.

## Structured Result

`GET /api/runs/{run_id}/structured-result`

`GET /api/conversations/{conversation_id}/structured-result`

```json
{
  "summary": ["paragraph"],
  "advice": [{ "title": "Recommendation", "value": "...", "sub": "conditional" }],
  "basis": [{ "source": "asset_data", "desc": "..." }],
  "evidence": [{ "name": "file.csv", "format": "csv", "bytes": 1234 }],
  "sources_count": 1,
  "verdict": "conditional",
  "confidence": "speculative",
  "audit": {}
}
```

`GET /api/conversations/{conversation_id}/context`

Returns workspace summary, current data sources, recent conclusion, and audit status.

`GET /api/conversations/{conversation_id}/quick-actions`

Returns backend endpoints for PDF, concept image, PRD, and roadmap actions.

## Produce Kinds

`POST /api/produce` and the backend helper `produce_from_existing_report` now accept two additional explicit `kinds` values for the Outputs page:

```json
{
  "workspace_id": "demo-corpus",
  "kinds": ["roadmap", "validation_plan"],
  "feasibility": {
    "opportunity_id": "pilot-validation",
    "verdict": "conditional",
    "gap_list": ["补齐转化证据"],
    "action_plan": ["定义试点样本"]
  }
}
```

Response fields follow existing artifact shape:

```json
{
  "kinds": ["roadmap", "validation_plan"],
  "artifact_urls": {
    "roadmap": "/api/artifacts/pilot-validation-roadmap-123.md",
    "validation_plan": "/api/artifacts/pilot-validation-validation_plan-123.md"
  },
  "roadmap": { "kind": "roadmap", "content_type": "text/markdown; charset=utf-8", "markdown": "..." },
  "validation_plan": { "kind": "validation_plan", "content_type": "text/markdown; charset=utf-8", "markdown": "..." }
}
```

Both artifacts are generated from the run proposal, feasibility gaps/actions, roadmap, risk register, and evidence appendix. They do not call the LLM or use dataset-name rules.

## Artifacts

`GET /api/workspaces/{workspace_id}/artifacts`

```json
{
  "workspace_id": "demo-corpus",
  "artifacts": [
    {
      "name": "proposal.pdf",
      "type": "pdf",
      "bytes": 12000,
      "created_at": "2026-06-29T12:00:00+00:00",
      "status": "ready",
      "url": "https://...",
      "run_id": "run-id",
      "content_type": "application/pdf"
    }
  ]
}
```

Artifacts are derived from persisted run artifact URLs, so workspace attribution follows the run.

## Settings / System Status

`GET /api/system-status`

Aggregates health, dependency details, model config, RAG config, connector modes, compliance flags, and observability.

`GET /api/workspaces/{workspace_id}/settings`

Adds workspace storage usage and member role placeholder data to `/api/system-status`.

`GET /api/workspaces/{workspace_id}/members`

Minimal workspace role contract:

```json
{
  "workspace_id": "demo-corpus",
  "rbac_enforced": false,
  "source": "workspace_placeholder",
  "roles": ["owner", "admin", "editor", "viewer"],
  "members": [{ "user": "Workspace owner", "email": "owner@example.com", "role": "owner", "status": "active" }]
}
```

This does not implement real RBAC and does not change auth.
