from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from fastapi import APIRouter, HTTPException, Query, Request, Response
from starlette.concurrency import run_in_threadpool

try:
    from .blob_store import blob_configured, download_artifact, download_blob_json, probe_blob_container, upload_blob_json
    from .conversation_store import get_conversation, list_conversations
    from .data_workbench import list_workspace_files
    from .dependency_health import health_dependencies, health_dependency_details
    from .graph_client import GraphClientError, search_entra_users, send_graph_invitation
    from .identity import actor_from_request, default_actor, member_from_actor, public_actor
    from .observability import observability_snapshot
    from .outcome_store import list_outcome_events, record_outcome_event, verify_outcome_event
    from .pm_skills import playbook_suggestion
    from .run_store import get_run, list_runs
    from .workspace_store import WORKSPACES, get_workspace_detail, list_workspaces
    from .workspace_authz import rbac_enabled, require_workspace_permission, workspace_role
except ImportError:
    from blob_store import blob_configured, download_artifact, download_blob_json, probe_blob_container, upload_blob_json
    from conversation_store import get_conversation, list_conversations
    from data_workbench import list_workspace_files
    from dependency_health import health_dependencies, health_dependency_details
    from graph_client import GraphClientError, search_entra_users, send_graph_invitation
    from identity import actor_from_request, default_actor, member_from_actor, public_actor
    from observability import observability_snapshot
    from outcome_store import list_outcome_events, record_outcome_event, verify_outcome_event
    from pm_skills import playbook_suggestion
    from run_store import get_run, list_runs
    from workspace_store import WORKSPACES, get_workspace_detail, list_workspaces
    from workspace_authz import rbac_enabled, require_workspace_permission, workspace_role


router = APIRouter(tags=["control-plane"])

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = ROOT / "generated-outputs"
HEALTH_CACHE_SECONDS = float(os.environ.get("DF_DASHBOARD_HEALTH_CACHE_SECONDS", "8"))
WORKSPACE_MEMBER_ROLES = {"admin", "editor", "viewer"}
WORKSPACE_MEMBER_STATUSES = {"pending", "active"}

_HEALTH_CACHE: dict[str, Any] = {"expires": 0.0, "value": None}


@router.get("/api/workspaces/{workspace_id}/overview")
async def workspace_overview(workspace_id: str) -> dict[str, Any]:
    return await _call(build_workspace_overview, workspace_id)


@router.get("/api/workspaces/{workspace_id}/latest-analysis")
async def workspace_latest_analysis_endpoint(workspace_id: str) -> dict[str, Any]:
    return await _call(workspace_latest_analysis, workspace_id)


@router.get("/api/workspaces/{workspace_id}/pipeline")
async def workspace_pipeline(workspace_id: str) -> dict[str, Any]:
    return await _call(workspace_pipeline_status, workspace_id)


@router.post("/api/workspaces/{workspace_id}/action-plan")
async def workspace_action_plan(workspace_id: str, body: dict[str, Any]) -> dict[str, Any]:
    return await _call(build_method_action_plan, workspace_id, body)


@router.get("/api/workspaces/{workspace_id}/artifacts")
async def workspace_artifacts(workspace_id: str) -> dict[str, Any]:
    return await _call(list_workspace_artifacts, workspace_id)


@router.get("/api/workspaces/{workspace_id}/settings")
async def workspace_settings(workspace_id: str, request: Request) -> dict[str, Any]:
    return await _call(workspace_settings_summary, workspace_id, request)


@router.get("/api/workspaces/{workspace_id}/members")
async def workspace_members(workspace_id: str, request: Request) -> dict[str, Any]:
    return await _call(workspace_member_roles, workspace_id, request)


@router.get("/api/workspaces/{workspace_id}/members/entra-users")
async def workspace_member_entra_users(
    workspace_id: str,
    request: Request,
    query: str = Query(default="", max_length=80),
    limit: int = Query(default=8, ge=1, le=20),
) -> dict[str, Any]:
    return await _call(workspace_entra_users, workspace_id, request, query, limit)


@router.post("/api/workspaces/{workspace_id}/members/invite")
async def workspace_member_invite(workspace_id: str, body: dict[str, Any], request: Request) -> dict[str, Any]:
    return await _call(invite_workspace_member, workspace_id, body, request)


@router.post("/api/workspaces/{workspace_id}/members/entra-invite")
async def workspace_member_entra_invite(workspace_id: str, body: dict[str, Any], request: Request) -> dict[str, Any]:
    return await _call(invite_entra_workspace_member, workspace_id, body, request)


@router.patch("/api/workspaces/{workspace_id}/members/{email}")
async def workspace_member_update(workspace_id: str, email: str, body: dict[str, Any], request: Request) -> dict[str, Any]:
    return await _call(update_workspace_member_role, workspace_id, email, body, request)


@router.delete("/api/workspaces/{workspace_id}/members/{email}")
async def workspace_member_remove(workspace_id: str, email: str, request: Request) -> dict[str, Any]:
    return await _call(remove_workspace_member, workspace_id, email, request)


@router.get("/api/workspaces/{workspace_id}/usage-summary")
async def workspace_usage(workspace_id: str, request: Request) -> dict[str, Any]:
    return await _call(workspace_usage_summary, workspace_id, request)


@router.get("/api/workspaces/{workspace_id}/audit-events")
async def workspace_audit(workspace_id: str, request: Request) -> dict[str, Any]:
    return await _call(workspace_audit_events, workspace_id, request)


@router.get("/api/workspaces/{workspace_id}/governance-summary")
async def workspace_governance(workspace_id: str, request: Request) -> dict[str, Any]:
    return await _call(workspace_governance_summary, workspace_id, request)


@router.get("/api/workspaces/{workspace_id}/outcomes")
async def workspace_outcomes(workspace_id: str) -> dict[str, Any]:
    events = await _call(list_outcome_events, workspace_id)
    return {"workspace_id": workspace_id, "events": events, "count": len(events)}


@router.post("/api/workspaces/{workspace_id}/outcomes")
async def workspace_outcome_create(workspace_id: str, body: dict[str, Any], request: Request) -> dict[str, Any]:
    _require_workspace_action(workspace_id, request, "outcome.record")
    event = await _call(record_outcome_event, workspace_id, body, actor_from_request(request))
    return {"workspace_id": workspace_id, "event": event}


@router.post("/api/workspaces/{workspace_id}/outcomes/{event_id}/verify")
async def workspace_outcome_verify(
    workspace_id: str,
    event_id: str,
    body: dict[str, Any],
    request: Request,
) -> dict[str, Any]:
    _require_workspace_action(workspace_id, request, "outcome.verify")
    event = await _call(
        verify_outcome_event,
        workspace_id,
        event_id,
        actor_from_request(request),
        note=body.get("note"),
    )
    return {"workspace_id": workspace_id, "event": event}


@router.get("/api/runs/{run_id}/summary")
async def run_summary_endpoint(run_id: str) -> dict[str, Any]:
    return await _call(run_summary, run_id)


@router.get("/api/runs/{run_id}/trace")
async def run_trace_endpoint(run_id: str) -> list[dict[str, Any]]:
    return await _call(run_trace, run_id)


@router.get("/api/runs/{run_id}/pipeline")
async def run_pipeline_endpoint(run_id: str) -> dict[str, Any]:
    return await _call(run_pipeline_status, run_id)


@router.get("/api/runs/{run_id}/structured-result")
async def run_structured_result_endpoint(run_id: str) -> dict[str, Any]:
    return await _call(structured_result_for_run, run_id)


@router.get("/api/runs/{run_id}/log")
async def run_log_endpoint(run_id: str, format: str = Query(default="json", pattern="^(json|text)$")) -> Any:
    result = await _call(run_log, run_id)
    if format == "text":
        return Response(_run_log_text(result), media_type="text/plain; charset=utf-8")
    return result


@router.get("/api/conversations/{conversation_id}/structured-result")
async def conversation_structured_result_endpoint(conversation_id: str) -> dict[str, Any]:
    return await _call(structured_result_for_conversation, conversation_id)


@router.get("/api/conversations/{conversation_id}/context")
async def conversation_context_endpoint(conversation_id: str) -> dict[str, Any]:
    return await _call(conversation_context_summary, conversation_id)


@router.get("/api/conversations/{conversation_id}/quick-actions")
async def conversation_quick_actions_endpoint(conversation_id: str) -> dict[str, Any]:
    return await _call(conversation_quick_actions, conversation_id)


@router.get("/api/system-status")
async def system_status_endpoint() -> dict[str, Any]:
    return await _call(system_status)


async def _call(func: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return await run_in_threadpool(func, *args, **kwargs)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _require_workspace_action(workspace_id: str, request: Request | None, action: str) -> str:
    try:
        return require_workspace_permission(workspace_id, actor_from_request(request), action)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def build_workspace_dashboard(workspace_id: str) -> dict[str, Any]:
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {
            "workspace": pool.submit(get_workspace_detail, workspace_id),
            "workspaces": pool.submit(list_workspaces),
            "runs": pool.submit(list_runs, workspace_id),
            "conversations": pool.submit(list_conversations, workspace_id),
            "health": pool.submit(_cached_health),
        }
        workspace = futures["workspace"].result()
        workspaces = _future_result(futures["workspaces"], [])
        runs = _with_duration(_future_result(futures["runs"], []))[:12]
        conversations = _future_result(futures["conversations"], [])[:12]
        health_value = _future_result(futures["health"], None) or _uncached_health()
    dependencies = health_value.get("dependencies") or {}
    return {
        "workspace_id": workspace_id,
        "workspace": workspace,
        "workspaces": workspaces,
        "runs": runs,
        "conversations": conversations,
        "health": {
            "ok": True,
            "service": "dataforge-backend",
            "search_endpoint": bool(os.environ.get("SEARCH_ENDPOINT")),
            "workspace_default": os.environ.get("DF_DEFAULT_WORKSPACE_ID") or workspace_id,
            "dependencies": dependencies,
        },
        "dependency_details": health_value.get("dependency_details") or {},
        "dashboard_meta": {
            "duration_ms": _elapsed_ms(started),
            "health_cache_seconds": HEALTH_CACHE_SECONDS,
            "loaded_at": _now(),
        },
    }


def build_workspace_overview(workspace_id: str) -> dict[str, Any]:
    started = time.perf_counter()
    dashboard = build_workspace_dashboard(workspace_id)
    files = _safe_value(lambda: list_workspace_files(workspace_id), {"groups": [], "storage": {}})
    runs = _with_duration(list_runs(workspace_id))
    latest_run = _load_first_run(runs)
    structured = structured_result_from_run(latest_run) if latest_run else {}
    pipeline = pipeline_from_run(latest_run) if latest_run else {"run_id": None, "stages": []}
    artifacts = list_workspace_artifacts(workspace_id)
    return {
        "workspace_id": workspace_id,
        "generated_at": _now(),
        "duration_ms": _elapsed_ms(started),
        "workspace": dashboard["workspace"],
        "metrics": {
            "workspace_count": len(dashboard.get("workspaces") or []),
            "run_count": len(runs),
            "conversation_count": len(list_conversations(workspace_id)),
            "artifact_count": len(artifacts.get("artifacts") or []),
            "file_count": sum(len(group.get("files") or []) for group in files.get("groups") or []),
        },
        "files": files,
        "runs": runs[:12],
        "conversations": dashboard.get("conversations") or [],
        "health": dashboard.get("health") or {},
        "dependency_details": dashboard.get("dependency_details") or {},
        "latest_result": structured,
        "pipeline": pipeline,
        "artifacts": artifacts.get("artifacts") or [],
    }


def workspace_latest_analysis(workspace_id: str) -> dict[str, Any]:
    started = time.perf_counter()
    checked = 0
    for summary in _with_duration(list_runs(workspace_id))[:100]:
        run_id = str(summary.get("run_id") or "")
        if not run_id:
            continue
        try:
            run = get_run(run_id)
        except FileNotFoundError:
            continue
        checked += 1
        if _is_lightweight_followup_run(run):
            continue
        artifact = _strip_market_dump_from_artifact(_artifact(run))
        if not _has_full_analysis_artifact(artifact):
            continue
        feasibility = artifact.get("feasibility") if isinstance(artifact.get("feasibility"), dict) else {}
        trace_run = _analysis_trace_run(run)
        return {
            "workspace_id": workspace_id,
            "found": True,
            "run_id": run.get("run_id") or run_id,
            "conversation_id": run.get("conversation_id"),
            "source_run_id": trace_run.get("run_id") if trace_run is not run else run.get("source_run_id"),
            "artifact": artifact,
            "feasibility": feasibility,
            "trace": _flow_trace_from_run(trace_run, artifact),
            "run_trace": trace_from_run(trace_run),
            "pipeline": pipeline_from_run(trace_run),
            "summary": _safe_value(lambda: run_summary(str(trace_run.get("run_id") or run_id)), {}),
            "structured_result": structured_result_from_run(run),
            "completed_at": run.get("completed_at") or run.get("updated_at") or summary.get("finished_at") or summary.get("time"),
            "checked_runs": checked,
            "duration_ms": _elapsed_ms(started),
        }
    return {
        "workspace_id": workspace_id,
        "found": False,
        "run_id": None,
        "conversation_id": None,
        "artifact": {},
        "feasibility": {},
        "trace": [],
        "run_trace": [],
        "pipeline": {"workspace_id": workspace_id, "run_id": None, "stages": []},
        "summary": {},
        "structured_result": {},
        "checked_runs": checked,
        "duration_ms": _elapsed_ms(started),
    }


def workspace_pipeline_status(workspace_id: str) -> dict[str, Any]:
    latest = _load_first_run(list_runs(workspace_id))
    if not latest:
        return {"workspace_id": workspace_id, "run_id": None, "stages": []}
    result = pipeline_from_run(latest)
    result["workspace_id"] = workspace_id
    return result


def run_summary(run_id: str) -> dict[str, Any]:
    run = get_run(run_id)
    artifact = _artifact(run)
    audit = _audit_summary(run)
    evidence = _run_dynamic_evidence(run)
    return {
        "run_id": run.get("run_id") or run_id,
        "conversation_id": run.get("conversation_id"),
        "workspace_id": run.get("workspace_id"),
        "status": run.get("status"),
        "verdict": run.get("verdict") or _dig(artifact, "feasibility", "verdict") or _dig(artifact, "verdict", "verdict_after"),
        "confidence": run.get("confidence") or _dig(artifact, "feasibility", "overall_confidence"),
        "duration_ms": _duration_ms(run.get("started_at"), run.get("completed_at") or run.get("updated_at")),
        "agent_count": len(_agents(run)),
        "tool_calls": _tool_counts(run),
        "tokens": _token_usage(run),
        "maf": run.get("maf") if isinstance(run.get("maf"), dict) else None,
        "actor": public_actor(run.get("actor") if isinstance(run.get("actor"), dict) else {}),
        "audit": audit,
        "started_at": run.get("started_at"),
        "finished_at": run.get("completed_at"),
        "title": run.get("title"),
        "summary": run.get("summary") if isinstance(run.get("summary"), str) else _text_summary(run),
        "evidence": evidence,
    }


def run_trace(run_id: str) -> list[dict[str, Any]]:
    run = get_run(run_id)
    return trace_from_run(run)


def run_pipeline_status(run_id: str) -> dict[str, Any]:
    return pipeline_from_run(get_run(run_id))


def run_log(run_id: str) -> dict[str, Any]:
    run = get_run(run_id)
    return {
        "run_id": run.get("run_id") or run_id,
        "workspace_id": run.get("workspace_id"),
        "status": run.get("status"),
        "summary": run_summary(run_id),
        "trace": trace_from_run(run),
        "raw": _sanitize_detail(run, depth=0),
    }


def trace_from_run(run: dict[str, Any]) -> list[dict[str, Any]]:
    steps = [step for step in run.get("steps") or [] if isinstance(step, dict)]
    items: list[dict[str, Any]] = []
    for index, step in enumerate(steps):
        data = step.get("data") if isinstance(step.get("data"), dict) else {}
        next_step = steps[index + 1] if index + 1 < len(steps) else None
        next_time = next_step.get("time") if isinstance(next_step, dict) else None
        duration = _duration_ms(step.get("time"), next_time)
        agent = _step_agent(step)
        items.append(
            {
                "index": index,
                "time": step.get("time"),
                "event": step.get("event"),
                "agent": agent,
                "role": _step_role(step),
                "status": _step_status(step),
                "summary": _step_summary(step),
                "duration_ms": duration,
                "tool_calls": 1 if step.get("event") in {"tool_call", "tool_result"} else 0,
                "tokens": _usage_from_dict(data),
                "detail": _sanitize_detail(data, depth=0),
                "source": "run_store.steps",
                "dynamic": True,
                "evidence": {
                    "event": step.get("event"),
                    "time": step.get("time"),
                    "next_time": next_time,
                    "duration": "step.time -> next_step.time",
                    "tokens": "step.data.usage" if isinstance(data.get("usage"), dict) else "",
                    "detail": "step.data",
                },
            }
        )
    return items


def _run_dynamic_evidence(run: dict[str, Any]) -> dict[str, Any]:
    finished_basis = "run.completed_at" if run.get("completed_at") else "run.updated_at"
    token_source = "run.models[].usage"
    if not run.get("models"):
        token_source = "run.steps[].data.usage"
    return {
        "dynamic": True,
        "source": "run_store",
        "run_id": run.get("run_id"),
        "duration": f"run.started_at -> {finished_basis}",
        "agent_count": "unique run.steps[].data.agent / target_expert / name",
        "tool_calls": "run.steps event=tool_call/tool_result",
        "tokens": "run.models[].usage or steps[].data.usage",
        "token_source": token_source,
        "trace": "run.steps",
        "step_count": len(run.get("steps") or []),
    }


def pipeline_from_run(run: dict[str, Any]) -> dict[str, Any]:
    seen: dict[str, dict[str, Any]] = {}
    for item in trace_from_run(run):
        agent = str(item.get("agent") or "")
        if not agent:
            continue
        stage = seen.setdefault(
            agent,
            {
                "agent": agent,
                "status": "pending",
                "started_at": item.get("time"),
                "completed_at": None,
                "duration_ms": 0,
                "events": 0,
            },
        )
        stage["events"] += 1
        stage["completed_at"] = item.get("time") or stage.get("completed_at")
        if item.get("status") == "failed":
            stage["status"] = "failed"
        elif stage["status"] != "failed":
            stage["status"] = "completed"
        if stage.get("started_at") and stage.get("completed_at"):
            stage["duration_ms"] = _duration_ms(stage["started_at"], stage["completed_at"])
    stages = list(seen.values())
    return {
        "run_id": run.get("run_id"),
        "workspace_id": run.get("workspace_id"),
        "status": run.get("status"),
        "stages": stages,
    }


def structured_result_for_run(run_id: str) -> dict[str, Any]:
    return structured_result_from_run(get_run(run_id))


def structured_result_for_conversation(conversation_id: str) -> dict[str, Any]:
    try:
        return structured_result_from_run(get_run(conversation_id))
    except FileNotFoundError:
        conversation = get_conversation(conversation_id)
        messages = [item for item in conversation.get("messages") or [] if isinstance(item, dict)]
        assistant = [item for item in messages if item.get("role") == "assistant"]
        last = assistant[-1] if assistant else (messages[-1] if messages else {})
        text = str(last.get("text") or "")
        return {
            "conversation_id": conversation_id,
            "workspace_id": conversation.get("workspace_id"),
            "summary": _paragraphs(text, 3),
            "advice": [],
            "basis": [],
            "evidence": [],
            "sources_count": 0,
            "verdict": conversation.get("last_verdict"),
        }


def structured_result_from_run(run: dict[str, Any]) -> dict[str, Any]:
    artifact = _artifact(run)
    feasibility = artifact.get("feasibility") if isinstance(artifact.get("feasibility"), dict) else {}
    answer = artifact.get("answer") if isinstance(artifact.get("answer"), dict) else {}
    citations = _citations(artifact)
    docs = _workspace_documents(str(run.get("workspace_id") or artifact.get("workspace_id") or ""))
    summary = _paragraphs(answer.get("markdown") or answer.get("text") or _text_summary(run), 4)
    advice = _advice_items(feasibility, artifact)
    basis = _basis_items(feasibility, citations)
    evidence = _evidence_items(citations, docs)
    return {
        "run_id": run.get("run_id"),
        "conversation_id": run.get("conversation_id"),
        "workspace_id": run.get("workspace_id") or artifact.get("workspace_id"),
        "summary": summary,
        "advice": advice,
        "basis": basis,
        "evidence": evidence,
        "sources_count": len({str(item.get("name") or item.get("source") or item.get("ref") or "") for item in evidence if item}),
        "verdict": run.get("verdict") or feasibility.get("verdict"),
        "confidence": run.get("confidence") or feasibility.get("overall_confidence"),
        "audit": _audit_summary(run),
    }


def conversation_context_summary(conversation_id: str) -> dict[str, Any]:
    conversation = get_conversation(conversation_id)
    workspace_id = str(conversation.get("workspace_id") or "")
    workspace = _safe_value(lambda: get_workspace_detail(workspace_id), {}) if workspace_id else {}
    latest = _load_first_run(list_runs(workspace_id)) if workspace_id else None
    return {
        "conversation_id": conversation_id,
        "workspace_id": workspace_id or None,
        "workspace": {
            "name": workspace.get("name"),
            "format": workspace.get("format"),
            "row_count": workspace.get("row_count"),
            "field_count": workspace.get("field_count"),
        },
        "current_data_sources": _asset_rows(_safe_value(lambda: list_workspace_files(workspace_id), {"groups": []})) if workspace_id else [],
        "recent_conclusion": structured_result_from_run(latest) if latest else {},
        "audit_status": _audit_summary(latest) if latest else {},
        "turn_count": conversation.get("turn_count"),
        "updated_at": conversation.get("updated_at"),
    }


def conversation_quick_actions(conversation_id: str) -> dict[str, Any]:
    conversation = get_conversation(conversation_id)
    workspace_id = conversation.get("workspace_id")
    return {
        "conversation_id": conversation_id,
        "workspace_id": workspace_id,
        "actions": [
            {"id": "produce_pdf", "label": "Project PDF", "endpoint": "/api/produce", "method": "POST", "kind": "pdf"},
            {"id": "produce_concept_image", "label": "Concept image", "endpoint": "/api/produce", "method": "POST", "kind": "concept_image"},
            {"id": "produce_prd", "label": "PRD outline", "endpoint": "/api/playbook", "method": "POST", "playbook": "prd"},
            {"id": "produce_roadmap", "label": "Roadmap", "endpoint": "/api/workspaces/{workspace_id}/action-plan", "method": "POST", "playbook": "roadmap"},
        ],
    }


def build_method_action_plan(workspace_id: str, body: dict[str, Any]) -> dict[str, Any]:
    playbook = str(body.get("playbook") or body.get("method") or "opportunity_tree")
    run = _run_from_body_or_latest(workspace_id, body)
    artifact = _artifact(run) if run else {}
    feasibility = body.get("feasibility") if isinstance(body.get("feasibility"), dict) else {}
    if not feasibility:
        feasibility = artifact.get("feasibility") if isinstance(artifact.get("feasibility"), dict) else {}
    spec = playbook_suggestion(playbook, _workspace_profile_context(workspace_id))
    opportunity = _clean(feasibility.get("opportunity_id") or body.get("opportunity") or _dig(artifact, "corpus", "opportunities", 0, "title") or "current opportunity", 80)
    gaps = [_clean(item, 120) for item in feasibility.get("gap_list") or [] if _clean(item, 120)]
    dims = [item for item in feasibility.get("dimensions") or [] if isinstance(item, dict)]
    evidence = _evidence_items(_citations(artifact), _workspace_documents(workspace_id))
    steps = _method_steps(spec.get("playbook"), opportunity, gaps, dims, evidence)
    recommendation = _method_recommendation(spec.get("label"), opportunity, gaps, evidence)
    return {
        "workspace_id": workspace_id,
        "run_id": run.get("run_id") if run else None,
        "playbook": spec.get("playbook"),
        "method_name": spec.get("label"),
        "opportunity": opportunity,
        "recommendation": recommendation,
        "action_plan": steps,
        "evidence_refs": evidence[:5],
        "source": "run_artifact" if run else "workspace_context",
    }


def list_workspace_artifacts(workspace_id: str) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for summary in list_runs(workspace_id)[:80]:
        run_id = str(summary.get("run_id") or "")
        if not run_id:
            continue
        try:
            run = get_run(run_id)
        except FileNotFoundError:
            continue
        artifact = _artifact(run)
        proposal = artifact.get("proposal") if isinstance(artifact.get("proposal"), dict) else {}
        urls = proposal.get("artifact_urls") if isinstance(proposal.get("artifact_urls"), dict) else {}
        for kind, url in urls.items():
            item = _artifact_item(str(kind), str(url or ""), run, proposal)
            key = str(item.get("url") or item.get("name"))
            if key and key not in seen:
                seen.add(key)
                items.append(item)
        for kind in ("pdf", "concept_image", "audio_summary", "pilot_plan", "action_plan"):
            value = proposal.get(kind)
            if isinstance(value, dict) and value.get("artifact_url"):
                item = _artifact_item(kind, str(value.get("artifact_url")), run, proposal)
                key = str(item.get("url") or item.get("name"))
                if key and key not in seen:
                    seen.add(key)
                    items.append(item)
    items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return {"workspace_id": workspace_id, "artifacts": items}


def system_status() -> dict[str, Any]:
    health = _cached_health()
    obs = observability_snapshot()
    details = health.get("dependency_details") or {}
    release = {
        "version": os.environ.get("DATAFORGE_VERSION", "1.0.0"),
        "build": os.environ.get("DATAFORGE_BUILD_ID") or os.environ.get("BUILD_ID") or "local",
        "environment": (
            "production"
            if os.environ.get("CONTAINER_APP_NAME") or os.environ.get("WEBSITE_SITE_NAME")
            else "local"
        ),
    }
    return {
        "ok": all(bool(value) for value in (health.get("dependencies") or {}).values()),
        "checked_at": _now(),
        "release": release,
        "dependencies": health.get("dependencies") or {},
        "dependency_details": details,
        "models": obs.get("models") or {},
        "rag": {
            "provider": "Azure AI Search",
            "configured": bool(os.environ.get("SEARCH_ENDPOINT")),
            "endpoint": _redact_url(os.environ.get("SEARCH_ENDPOINT")),
            "index": os.environ.get("SEARCH_INDEX_NAME", "dataforge-workspaces"),
        },
        "storage": {
            "provider": "Azure Blob Storage" if blob_configured() else "local",
            "configured": blob_configured(),
            "container": _dig(details, "blob", "container"),
        },
        "connectors": {
            "azure_blob": {"mode": "manual_credentials", "status": "available"},
            "sql_database": {"mode": "manual_credentials", "status": "available"},
            "azure_data_lake": {"mode": "demo_only", "status": "demo"},
            "identity_discovery": {"mode": "placeholder", "status": "not_configured"},
        },
        "compliance": {
            "content_safety": bool((health.get("dependencies") or {}).get("content_safety")),
            "entra": bool(os.environ.get("WEBSITE_AUTH_ENABLED") or os.environ.get("DF_STORAGE_ACCOUNT")),
            "data_residency": os.environ.get("AZURE_REGION") or os.environ.get("REGION_NAME") or "eastus2",
            "otel": bool(_dig(obs, "tracing", "app_insights")),
            "audit_retention": {"runs": 300, "conversations": 500},
        },
        "observability": obs,
    }


def workspace_settings_summary(workspace_id: str, request: Request | None = None) -> dict[str, Any]:
    files = _safe_value(lambda: list_workspace_files(workspace_id), {"storage": {}, "groups": []})
    status = system_status()
    return {
        "workspace_id": workspace_id,
        "system_status": status,
        "storage": files.get("storage") or {},
        "members": workspace_member_roles(workspace_id, request).get("members") or [],
        "configuration": {
            "models": status.get("models") or {},
            "rag": status.get("rag") or {},
            "compliance": status.get("compliance") or {},
        },
    }


def workspace_member_roles(workspace_id: str, request: Request | None = None) -> dict[str, Any]:
    current_actor = actor_from_request(request)
    usage = _workspace_usage_by_actor(workspace_id)
    owner_actor = current_actor if not rbac_enabled() else default_actor()
    if workspace_role(workspace_id, current_actor) == "owner":
        owner_actor = {**default_actor(), **current_actor}
    owner = member_from_actor(owner_actor, role="owner")
    owner["status"] = "active"
    owner["source"] = owner.get("source") or current_actor.get("source") or "workspace_default"
    members_by_key: dict[str, dict[str, Any]] = {_actor_key(owner) or "workspace_owner": owner}
    stored = _workspace_invited_members(workspace_id)
    for item in stored:
        key = _actor_key(item)
        if not key:
            continue
        if key in members_by_key:
            members_by_key[key].update({k: v for k, v in item.items() if k not in {"role"} or members_by_key[key].get("role") != "owner"})
            continue
        members_by_key[key] = dict(item)
    for item in usage.get("members") or []:
        actor = item.get("actor") if isinstance(item, dict) else {}
        key = _actor_key(actor if isinstance(actor, dict) else {})
        if not key:
            continue
        if key in members_by_key:
            row = members_by_key[key]
            if row.get("status") == "pending":
                row["status"] = "active"
            if not row.get("source") or row.get("source") == "workspace_invite":
                row["source"] = "workspace_invite"
        else:
            row = member_from_actor(actor, role="editor", status="active")
            members_by_key[key] = row
        row["usage"] = item.get("usage") or {}
        row["last_seen_at"] = item.get("last_seen_at")
    members = sorted(
        members_by_key.values(),
        key=lambda item: (0 if str(item.get("role") or "").lower() == "owner" else 1, str(item.get("status") or ""), str(item.get("email") or "")),
    )
    return {
        "workspace_id": workspace_id,
        "rbac_enforced": rbac_enabled(),
        "source": current_actor.get("source") or "workspace_default",
        "roles": ["owner", "admin", "editor", "viewer"],
        "members": members,
        "usage": usage,
        "invite": {
            "status": "available",
            "mode": "workspace_members_with_optional_entra_graph",
            "message": "Members are persisted in the workspace for collaboration, token attribution, and audit display. Entra directory search and email invite are used when Microsoft Graph permissions are available.",
        },
    }


def workspace_entra_users(workspace_id: str, request: Request | None = None, query: str = "", limit: int = 8) -> dict[str, Any]:
    _safe_workspace_id(workspace_id)
    result = search_entra_users(query, request, limit=limit)
    return {
        "workspace_id": workspace_id,
        "query": _clean_text(query),
        **result,
    }


def invite_workspace_member(workspace_id: str, body: dict[str, Any], request: Request | None = None) -> dict[str, Any]:
    require_workspace_permission(workspace_id, actor_from_request(request), "member.manage")
    email = _member_email((body or {}).get("email"))
    if not email:
        raise ValueError("A valid member email is required")
    role = _member_role((body or {}).get("role"))
    name = _clean_text((body or {}).get("name")) or _display_name_from_email(email)
    current_actor = public_actor(actor_from_request(request))
    meta = _load_workspace_meta(workspace_id)
    members = _stored_workspace_members(meta)
    now = _now()
    invited_by = public_actor(current_actor)
    updated = False
    for member in members:
        if str(member.get("email") or "").lower() != email:
            continue
        member.update(
            {
                "user": name,
                "name": name,
                "email": email,
                "role": role,
                "status": member.get("status") if member.get("status") in WORKSPACE_MEMBER_STATUSES else "pending",
                "updated_at": now,
                "invited_by": member.get("invited_by") or invited_by,
            }
        )
        updated = True
        break
    if not updated:
        members.append(
            {
                "user": name,
                "name": name,
                "email": email,
                "role": role,
                "status": "pending",
                "source": "workspace_invite",
                "invited_at": now,
                "updated_at": now,
                "invited_by": invited_by,
            }
        )
    meta["workspace_members"] = members
    _save_workspace_meta(workspace_id, meta)
    result = workspace_member_roles(workspace_id, request)
    result["invited_member"] = next((item for item in result.get("members") or [] if str(item.get("email") or "").lower() == email), None)
    return result


def invite_entra_workspace_member(workspace_id: str, body: dict[str, Any], request: Request | None = None) -> dict[str, Any]:
    require_workspace_permission(workspace_id, actor_from_request(request), "member.manage")
    payload = dict(body or {})
    email = _member_email(payload.get("email"))
    if not email:
        raise ValueError("A valid member email is required")
    name = _clean_text(payload.get("name")) or _display_name_from_email(email)
    role = _member_role(payload.get("role"))
    send_email = bool(payload.get("send_email"))
    fallback = payload.get("fallback_to_workspace_member", True) is not False
    graph_invite: dict[str, Any] = {"status": "skipped", "source": "microsoft_graph"}
    if send_email:
        try:
            graph_invite = send_graph_invitation(
                email,
                _graph_invite_redirect_url(payload),
                request,
                display_name=name,
                message=_clean_text(payload.get("message")),
            )
        except GraphClientError as exc:
            graph_invite = {
                "status": "unavailable" if exc.code in {"graph_token_missing", "graph_permission_denied"} else "failed",
                "source": "microsoft_graph",
                "error": exc.to_payload(),
            }
            if not fallback:
                return {
                    "workspace_id": workspace_id,
                    "members": workspace_member_roles(workspace_id, request).get("members") or [],
                    "graph_invite": graph_invite,
                }
    result = invite_workspace_member(workspace_id, {"email": email, "name": name, "role": role}, request)
    result["graph_invite"] = graph_invite
    return result


def update_workspace_member_role(workspace_id: str, email: str, body: dict[str, Any], request: Request | None = None) -> dict[str, Any]:
    require_workspace_permission(workspace_id, actor_from_request(request), "member.manage")
    target = _member_email(email)
    if not target:
        raise ValueError("A valid member email is required")
    role = _member_role((body or {}).get("role"))
    current_actor = public_actor(actor_from_request(request))
    if target == _actor_key(current_actor):
        raise ValueError("The current owner role cannot be changed from the members panel")
    meta = _load_workspace_meta(workspace_id)
    members = _stored_workspace_members(meta)
    now = _now()
    updated = False
    for member in members:
        if str(member.get("email") or "").lower() != target:
            continue
        member["role"] = role
        member["updated_at"] = now
        member["updated_by"] = public_actor(current_actor)
        updated = True
        break
    if not updated:
        raise ValueError("Workspace member not found")
    meta["workspace_members"] = members
    _save_workspace_meta(workspace_id, meta)
    result = workspace_member_roles(workspace_id, request)
    result["updated_member"] = next((item for item in result.get("members") or [] if str(item.get("email") or "").lower() == target), None)
    return result


def remove_workspace_member(workspace_id: str, email: str, request: Request | None = None) -> dict[str, Any]:
    require_workspace_permission(workspace_id, actor_from_request(request), "member.manage")
    target = _member_email(email)
    if not target:
        raise ValueError("A valid member email is required")
    current_key = _actor_key(actor_from_request(request))
    if target == current_key:
        raise ValueError("The current owner cannot be removed from the workspace")
    meta = _load_workspace_meta(workspace_id)
    members = [item for item in _stored_workspace_members(meta) if str(item.get("email") or "").lower() != target]
    meta["workspace_members"] = members
    _save_workspace_meta(workspace_id, meta)
    result = workspace_member_roles(workspace_id, request)
    result["removed_member"] = {"email": target}
    return result


def workspace_usage_summary(workspace_id: str, request: Request | None = None) -> dict[str, Any]:
    current_actor = public_actor(actor_from_request(request))
    usage = _workspace_usage_by_actor(workspace_id)
    return {
        "workspace_id": workspace_id,
        "current_actor": current_actor,
        **usage,
    }


def workspace_audit_events(workspace_id: str, request: Request | None = None) -> dict[str, Any]:
    current_actor = public_actor(actor_from_request(request))
    events: list[dict[str, Any]] = []
    for summary in list_runs(workspace_id)[:120]:
        run = _safe_value(lambda run_id=summary.get("run_id"): get_run(str(run_id)), summary)
        actor = public_actor((run or {}).get("actor") if isinstance(run, dict) else {})
        tokens = _token_usage(run if isinstance(run, dict) else summary)
        events.append(
            {
                "type": "run",
                "action": "analysis_completed" if not run.get("version_kind") else str(run.get("version_kind")),
                "at": run.get("completed_at") or run.get("updated_at") or summary.get("time"),
                "actor": actor,
                "run_id": run.get("run_id") or summary.get("run_id"),
                "title": run.get("title") or summary.get("title"),
                "summary": run.get("summary") if isinstance(run.get("summary"), str) else summary.get("summary"),
                "tokens": tokens,
                "status": run.get("status") or summary.get("status"),
            }
        )
    for conv in list_conversations(workspace_id)[:80]:
        for actor in conv.get("actors") or []:
            if not isinstance(actor, dict):
                continue
            events.append(
                {
                    "type": "conversation",
                    "action": "message_sent",
                    "at": conv.get("updated_at"),
                    "actor": public_actor(actor),
                    "conversation_id": conv.get("conversation_id"),
                    "title": conv.get("title"),
                    "turn_count": conv.get("turn_count") or 0,
                }
            )
    events = sorted(events, key=lambda item: str(item.get("at") or ""), reverse=True)[:120]
    return {
        "workspace_id": workspace_id,
        "current_actor": current_actor,
        "events": events,
        "count": len(events),
    }


def workspace_governance_summary(workspace_id: str, request: Request | None = None) -> dict[str, Any]:
    current_actor = public_actor(actor_from_request(request))
    usage = _workspace_usage_by_actor(workspace_id)
    audit = workspace_audit_events(workspace_id, request)
    roi = _workspace_roi_summary(usage, audit, list_outcome_events(workspace_id))
    foundry_monitoring = _foundry_monitoring_status()
    return {
        "workspace_id": workspace_id,
        "generated_at": _now(),
        "current_actor": current_actor,
        "security": {
            "identity_provider": "Microsoft Entra ID",
            "auth_surface": "Azure Container Apps Easy Auth",
            "rbac_enforced": rbac_enabled(),
            "actor_attribution": "easy_auth_or_client_actor_header",
            "graph_directory": {
                "status": "optional",
                "permissions": ["User.ReadBasic.All", "User.Invite.All"],
                "note": "Directory search and email invite activate after Microsoft Graph admin consent; workspace-local invite remains available.",
            },
            "controls": [
                {"name": "登录身份", "status": "enabled", "detail": "Easy Auth 保护前端入口，并把当前账号用于审计归因。"},
                {"name": "成员用量归因", "status": "enabled", "detail": "每次运行和会话消息保存 actor、token 与时间戳。"},
                {"name": "Graph 邀请邮件", "status": "permission_required", "detail": "需要 Graph 权限授权后才会发送真实 Entra 邀请邮件。"},
            ],
        },
        "usage": usage,
        "chargeback": _workspace_chargeback(usage, roi),
        "foundry_monitoring": foundry_monitoring,
        "audit": {
            "count": audit.get("count") or 0,
            "events": (audit.get("events") or [])[:20],
            "by_action": _count_by_key(audit.get("events") or [], "action"),
            "by_actor": _count_audit_by_actor(audit.get("events") or []),
        },
        "roi": roi,
    }


def _foundry_monitoring_status() -> dict[str, Any]:
    snapshot = observability_snapshot()
    tracing = snapshot.get("tracing") if isinstance(snapshot.get("tracing"), dict) else {}
    app_insights = bool(tracing.get("app_insights"))
    otel_sdk = bool(tracing.get("otel_sdk"))
    status = "connected" if app_insights and otel_sdk else "partial" if app_insights or otel_sdk else "not_configured"
    registered = str(os.environ.get("DF_FOUNDRY_AGENT_REGISTERED") or "0").strip().lower() in {"1", "true", "yes", "on"}
    return {
        "status": status,
        "source": "application_insights" if app_insights else None,
        "exporter": tracing.get("exporter"),
        "service_name": tracing.get("service_name"),
        "gen_ai_semantic_conventions": app_insights and otel_sdk,
        "foundry_agent_registered": registered,
        "native_roi_status": "configured" if registered and str(os.environ.get("DF_FOUNDRY_ROI_ENABLED") or "0") == "1" else "not_configured",
        "note": (
            "Runtime spans are exported with gen_ai semantic attributes. Business outcomes remain sourced from the DataForge outcome ledger."
            if status == "connected"
            else "Connect Application Insights and the OpenTelemetry exporter before claiming Foundry-compatible monitoring."
        ),
    }


def _workspace_usage_by_actor(workspace_id: str) -> dict[str, Any]:
    by_actor: dict[str, dict[str, Any]] = {}
    totals = {
        "runs": 0,
        "agent_runs": 0,
        "snapshot_runs": 0,
        "known_usage_runs": 0,
        "unknown_usage_runs": 0,
        "total_tokens": None,
        "prompt_tokens": None,
        "completion_tokens": None,
    }
    for summary in list_runs(workspace_id)[:300]:
        run_id = str(summary.get("run_id") or "")
        detail = _safe_value(lambda: get_run(run_id), summary) if run_id else summary
        if not isinstance(detail, dict):
            detail = summary
        actor = public_actor(detail.get("actor") if isinstance(detail.get("actor"), dict) else summary.get("actor") if isinstance(summary.get("actor"), dict) else {})
        key = _actor_key(actor) or "workspace_default"
        row = by_actor.setdefault(
            key,
            {
                "actor": actor,
                "usage": {
                    "runs": 0,
                    "agent_runs": 0,
                    "snapshot_runs": 0,
                    "known_usage_runs": 0,
                    "unknown_usage_runs": 0,
                    "total_tokens": None,
                    "prompt_tokens": None,
                    "completion_tokens": None,
                },
                "last_seen_at": None,
                "last_run_id": None,
            },
        )
        detail_tokens = detail.get("tokens") if isinstance(detail.get("tokens"), dict) else None
        summary_tokens = summary.get("tokens") if isinstance(summary.get("tokens"), dict) else None
        direct_tokens = detail_tokens if _usage_is_observed(detail_tokens) else summary_tokens
        tokens = _usage_from_dict(direct_tokens) if _usage_is_observed(direct_tokens) else _token_usage(detail)
        usage = row["usage"]
        is_snapshot = bool(str(detail.get("version_kind") or "").strip())
        usage["runs"] += 1
        usage["snapshot_runs" if is_snapshot else "agent_runs"] += 1
        coverage_key = "known_usage_runs" if tokens is not None else "unknown_usage_runs"
        usage[coverage_key] += 1
        if tokens is not None:
            for output_key, token_key in (("total_tokens", "total"), ("prompt_tokens", "prompt"), ("completion_tokens", "completion")):
                usage[output_key] = int(usage[output_key] or 0) + int(tokens.get(token_key) or 0)
        seen_at = detail.get("completed_at") or detail.get("updated_at") or summary.get("time")
        if seen_at and str(seen_at) > str(row.get("last_seen_at") or ""):
            row["last_seen_at"] = seen_at
            row["last_run_id"] = run_id
        totals["runs"] += 1
        totals["snapshot_runs" if is_snapshot else "agent_runs"] += 1
        totals[coverage_key] += 1
        if tokens is not None:
            for output_key, token_key in (("total_tokens", "total"), ("prompt_tokens", "prompt"), ("completion_tokens", "completion")):
                totals[output_key] = int(totals[output_key] or 0) + int(tokens.get(token_key) or 0)
    for usage in [totals, *(item["usage"] for item in by_actor.values())]:
        known = int(usage.get("known_usage_runs") or 0)
        unknown = int(usage.get("unknown_usage_runs") or 0)
        usage["usage_status"] = "partial" if known and unknown else "complete" if known else "unknown"
    members = sorted(by_actor.values(), key=lambda item: str(item.get("last_seen_at") or ""), reverse=True)
    return {
        "totals": totals,
        "members": members,
        "source": "run_store",
    }


def _workspace_roi_summary(
    usage: dict[str, Any],
    audit: dict[str, Any],
    outcomes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    totals = usage.get("totals") if isinstance(usage.get("totals"), dict) else {}
    events = audit.get("events") if isinstance(audit.get("events"), list) else []
    total_tokens_value = totals.get("total_tokens")
    total_tokens = int(total_tokens_value) if total_tokens_value is not None else None
    usage_status = str(totals.get("usage_status") or ("complete" if total_tokens is not None else "unknown"))
    known_usage_runs = int(totals.get("known_usage_runs") or (totals.get("runs") if total_tokens is not None else 0) or 0)
    unknown_usage_runs = int(totals.get("unknown_usage_runs") or 0)
    analysis_runs = int(totals.get("agent_runs") or totals.get("runs") or 0)
    snapshot_runs = int(totals.get("snapshot_runs") or 0)
    conversation_turns_by_id: dict[str, int] = {}
    for index, item in enumerate(events):
        if item.get("type") != "conversation":
            continue
        conversation_id = str(item.get("conversation_id") or f"conversation_event_{index}")
        conversation_turns_by_id[conversation_id] = max(
            conversation_turns_by_id.get(conversation_id, 0),
            int(item.get("turn_count") or 0),
        )
    conversation_turns = sum(conversation_turns_by_id.values())
    token_cost_per_1m = _float_env("DF_ROI_TOKEN_COST_PER_1M", 3.0)
    hourly_value = _float_env("DF_ROI_HOURLY_VALUE_USD", 80.0)
    analysis_minutes = _float_env("DF_ROI_MINUTES_SAVED_PER_ANALYSIS", 45.0)
    followup_minutes = _float_env("DF_ROI_MINUTES_SAVED_PER_FOLLOWUP", 8.0)
    estimated_cost = (total_tokens / 1_000_000.0) * token_cost_per_1m if total_tokens is not None else None
    estimated_hours_saved = ((analysis_runs * analysis_minutes) + (conversation_turns * followup_minutes)) / 60.0
    estimated_value = estimated_hours_saved * hourly_value
    complete_cost = estimated_cost if usage_status == "complete" else None
    roi_multiple = (estimated_value / complete_cost) if complete_cost is not None and complete_cost > 0 else None
    assumption_names = (
        "DF_ROI_TOKEN_COST_PER_1M",
        "DF_ROI_HOURLY_VALUE_USD",
        "DF_ROI_MINUTES_SAVED_PER_ANALYSIS",
        "DF_ROI_MINUTES_SAVED_PER_FOLLOWUP",
    )
    configured_assumptions = sum(1 for name in assumption_names if str(os.environ.get(name) or "").strip())
    assumptions_source = (
        "environment"
        if configured_assumptions == len(assumption_names)
        else "environment_with_defaults"
        if configured_assumptions
        else "defaults"
    )
    outcome_items = [item for item in (outcomes or []) if isinstance(item, dict)]
    observed_outcomes = [
        item
        for item in outcome_items
        if item.get("provenance") == "observed" and item.get("observed_value") is not None
    ]
    verified_outcomes = [
        item
        for item in observed_outcomes
        if isinstance(item.get("verification"), dict)
        and item["verification"].get("status") == "verified"
    ]
    synthetic_outcomes = [item for item in outcome_items if item.get("provenance") == "synthetic"]
    outcome_status = (
        "verified"
        if observed_outcomes and len(verified_outcomes) == len(observed_outcomes)
        else "measured"
        if observed_outcomes
        else "estimated"
    )
    latest_observed_at = max(
        (str(item.get("observed_at") or "") for item in observed_outcomes),
        default="",
    ) or None
    return {
        "method": "outcome_ledger" if observed_outcomes else "dataforge_estimate",
        "status": outcome_status,
        "native_foundry_roi": {
            "status": "not_configured",
            "availability": "private_preview",
            "note": "Use this workspace estimate until Azure AI Foundry ROI reporting is connected for the project.",
        },
        "currency": "USD",
        "estimated_cost_usd": _round_money(estimated_cost) if estimated_cost is not None else None,
        "estimated_value_usd": _round_money(estimated_value),
        "net_value_usd": _round_money(estimated_value - complete_cost) if complete_cost is not None else None,
        "roi_multiple": round(roi_multiple, 2) if roi_multiple is not None else None,
        "estimated_hours_saved": round(estimated_hours_saved, 2),
        "inputs": {
            "total_tokens": total_tokens,
            "known_usage_runs": known_usage_runs,
            "unknown_usage_runs": unknown_usage_runs,
            "usage_status": usage_status,
            "analysis_runs": analysis_runs,
            "snapshot_runs_excluded": snapshot_runs,
            "conversation_turns": conversation_turns,
            "token_cost_per_1m": token_cost_per_1m,
            "hourly_value_usd": hourly_value,
            "minutes_saved_per_analysis": analysis_minutes,
            "minutes_saved_per_followup": followup_minutes,
        },
        "assumptions_source": assumptions_source,
        "confidence": "estimated" if usage_status == "complete" else "partial_usage" if usage_status == "partial" else "usage_unknown",
        "outcomes": {
            "count": len(outcome_items),
            "observed_count": len(observed_outcomes),
            "verified_count": len(verified_outcomes),
            "synthetic_count": len(synthetic_outcomes),
            "latest_observed_at": latest_observed_at,
            "metrics": [
                {
                    key: item.get(key)
                    for key in (
                        "event_id",
                        "metric_name",
                        "unit",
                        "baseline_value",
                        "target_value",
                        "observed_value",
                        "observed_at",
                        "provenance",
                        "source",
                        "verification",
                    )
                    if item.get(key) is not None
                }
                for item in outcome_items[:20]
            ],
        },
    }


def _workspace_chargeback(usage: dict[str, Any], roi: dict[str, Any]) -> dict[str, Any]:
    totals = usage.get("totals") if isinstance(usage.get("totals"), dict) else {}
    total_tokens_value = totals.get("total_tokens")
    total_tokens = max(0, int(total_tokens_value)) if total_tokens_value is not None else None
    usage_status = totals.get("usage_status") or ("complete" if total_tokens is not None else "unknown")
    cost_per_1m = float((roi.get("inputs") or {}).get("token_cost_per_1m") or 0)
    rows = []
    for item in usage.get("members") or []:
        actor = public_actor(item.get("actor") if isinstance(item, dict) else {})
        item_usage = item.get("usage") if isinstance(item.get("usage"), dict) else {}
        item_tokens = item_usage.get("total_tokens")
        tokens = int(item_tokens) if item_tokens is not None else None
        share = (tokens / total_tokens) if tokens is not None and total_tokens is not None and total_tokens > 0 else None
        rows.append(
            {
                "actor": actor,
                "runs": int(item_usage.get("runs") or 0),
                "total_tokens": tokens,
                "prompt_tokens": item_usage.get("prompt_tokens"),
                "completion_tokens": item_usage.get("completion_tokens"),
                "known_usage_runs": int(item_usage.get("known_usage_runs") or 0),
                "unknown_usage_runs": int(item_usage.get("unknown_usage_runs") or 0),
                "usage_status": item_usage.get("usage_status") or "unknown",
                "token_share_pct": round(share * 100, 1) if share is not None else None,
                "estimated_cost_usd": _round_money((tokens / 1_000_000.0) * cost_per_1m) if tokens is not None else None,
                "last_seen_at": item.get("last_seen_at"),
                "last_run_id": item.get("last_run_id"),
            }
        )
    rows.sort(key=lambda item: (item.get("total_tokens") or 0, item.get("runs") or 0), reverse=True)
    return {
        "basis": "run_store_actor_token_usage",
        "members": rows,
        "totals": {
            "members": len(rows),
            "total_tokens": total_tokens,
            "known_usage_runs": int(totals.get("known_usage_runs") or 0),
            "unknown_usage_runs": int(totals.get("unknown_usage_runs") or 0),
            "usage_status": usage_status,
            "estimated_cost_usd": roi.get("estimated_cost_usd"),
        },
    }


def _count_by_key(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = _clean_text(item.get(key)) or "unknown"
        counts[value] = counts.get(value, 0) + 1
    return counts


def _count_audit_by_actor(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, dict[str, Any]] = {}
    for item in items:
        actor = public_actor(item.get("actor") if isinstance(item.get("actor"), dict) else {})
        key = _actor_key(actor) or "unknown"
        row = counts.setdefault(key, {"actor": actor, "events": 0})
        row["events"] += 1
    return sorted(counts.values(), key=lambda item: int(item.get("events") or 0), reverse=True)


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _round_money(value: float) -> float:
    return round(float(value or 0), 2)


def _actor_key(actor: dict[str, Any] | None) -> str:
    if not isinstance(actor, dict):
        return ""
    return str(actor.get("email") or actor.get("actor_id") or actor.get("name") or "").strip().lower()


def _workspace_invited_members(workspace_id: str) -> list[dict[str, Any]]:
    try:
        meta = _load_workspace_meta(workspace_id)
    except FileNotFoundError:
        return []
    return _stored_workspace_members(meta)


def _stored_workspace_members(meta: dict[str, Any]) -> list[dict[str, Any]]:
    raw = meta.get("workspace_members")
    if not isinstance(raw, list):
        raw = []
    members: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        member = _normalize_workspace_member(item if isinstance(item, dict) else {})
        key = _actor_key(member)
        if not key or key in seen:
            continue
        seen.add(key)
        members.append(member)
    return members


def _normalize_workspace_member(item: dict[str, Any]) -> dict[str, Any]:
    email = _member_email(item.get("email"))
    if not email:
        return {}
    name = _clean_text(item.get("user") or item.get("name")) or _display_name_from_email(email)
    role = str(item.get("role") or "viewer").strip().lower()
    if role not in WORKSPACE_MEMBER_ROLES:
        role = "viewer"
    status = str(item.get("status") or "pending").strip().lower()
    if status not in WORKSPACE_MEMBER_STATUSES:
        status = "pending"
    invited_by = item.get("invited_by") if isinstance(item.get("invited_by"), dict) else {}
    return {
        "user": name,
        "name": name,
        "email": email,
        "actor_id": _clean_text(item.get("actor_id")),
        "tenant_id": _clean_text(item.get("tenant_id")),
        "role": role,
        "status": status,
        "source": "workspace_invite",
        "invited_at": _clean_text(item.get("invited_at")),
        "updated_at": _clean_text(item.get("updated_at")),
        "invited_by": public_actor(invited_by),
    }


def _load_workspace_meta(workspace_id: str) -> dict[str, Any]:
    workspace_id = _safe_workspace_id(workspace_id)
    blob_name = f"workspaces/{workspace_id}/workspace.json"
    meta = _safe_value(lambda: download_blob_json(blob_name), {}) or {}
    meta_path = WORKSPACES / workspace_id / "workspace.json"
    if not isinstance(meta, dict) or not meta:
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if not isinstance(meta, dict) or not meta:
        raise FileNotFoundError(workspace_id)
    return dict(meta)


def _save_workspace_meta(workspace_id: str, meta: dict[str, Any]) -> None:
    workspace_id = _safe_workspace_id(workspace_id)
    meta = dict(meta or {})
    meta["workspace_id"] = str(meta.get("workspace_id") or workspace_id)
    meta["updated_at"] = _now()
    workspace_dir = WORKSPACES / workspace_id
    workspace_dir.mkdir(parents=True, exist_ok=True)
    (workspace_dir / "workspace.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    upload_blob_json(f"workspaces/{workspace_id}/workspace.json", meta)


def _safe_workspace_id(workspace_id: str) -> str:
    value = str(workspace_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise ValueError("Invalid workspace_id")
    return value


def _member_email(value: Any) -> str:
    email = _clean_text(value).lower()
    return email if _looks_like_email(email) else ""


def _member_role(value: Any) -> str:
    role = str(value or "viewer").strip().lower()
    if role == "owner":
        raise ValueError("Owner role cannot be assigned by invitation")
    if role not in WORKSPACE_MEMBER_ROLES:
        raise ValueError("role must be admin, editor, or viewer")
    return role


def _graph_invite_redirect_url(body: dict[str, Any]) -> str:
    raw = _clean_text(body.get("redirect_url")) or os.environ.get("DF_WEB_BASE_URL") or os.environ.get("WEB_BASE_URL")
    if raw:
        parsed = urlparse(raw)
        if parsed.scheme in {"https", "http"} and parsed.netloc:
            return raw
    return "https://ca-dataforge-web.thankfultree-c0fc8321.eastus2.azurecontainerapps.io/"


def _current_user_from_request(request: Request | None) -> dict[str, str]:
    if request is None:
        return {}
    headers = request.headers
    principal = _decoded_easy_auth_principal(headers.get("x-ms-client-principal"))
    claims = principal.get("claims") if isinstance(principal.get("claims"), list) else []
    header_name = _clean_text(headers.get("x-ms-client-principal-name"))
    email = (
        _claim_value(claims, "preferred_username", "upn", "email", "emailaddress")
        or principal.get("userDetails")
        or principal.get("user_details")
        or header_name
    )
    email = _clean_text(email)
    if not _looks_like_email(email):
        email = ""
    name = (
        _claim_value(claims, "name", "displayname", "given_name")
        or principal.get("name")
        or principal.get("displayName")
        or ""
    )
    name = _clean_text(name)
    if not name and email:
        name = _display_name_from_email(email)
    return {k: v for k, v in {"name": name, "email": email}.items() if v}


def _decoded_easy_auth_principal(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        padded = raw + ("=" * (-len(raw) % 4))
        data = base64.urlsafe_b64decode(padded)
        parsed = json.loads(data.decode("utf-8"))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _claim_value(claims: list[Any], *names: str) -> str:
    wanted = {name.lower() for name in names}
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        typ = str(claim.get("typ") or claim.get("type") or claim.get("name") or "").lower()
        tail = typ.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
        if typ in wanted or tail in wanted:
            value = _clean_text(claim.get("val") or claim.get("value"))
            if value:
                return value
    return ""


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _looks_like_email(value: str) -> bool:
    return bool(value and "@" in value and "." in value.rsplit("@", 1)[-1])


def _display_name_from_email(email: str) -> str:
    if email.lower() == "fuzihao@gdjiuyun.onmicrosoft.com":
        return "傅子豪"
    local = email.split("@", 1)[0] if email else ""
    return local.replace(".", " ").replace("_", " ").strip().title() or "Workspace owner"


def _cached_health() -> dict[str, Any]:
    now = time.time()
    cached = _HEALTH_CACHE.get("value")
    if cached and float(_HEALTH_CACHE.get("expires") or 0) > now:
        return cached
    value = _uncached_health()
    _HEALTH_CACHE["value"] = value
    _HEALTH_CACHE["expires"] = now + HEALTH_CACHE_SECONDS
    return value


def _uncached_health() -> dict[str, Any]:
    dependencies = health_dependencies()
    return {"dependencies": dependencies, "dependency_details": health_dependency_details()}


def _future_result(future: Any, default: Any) -> Any:
    try:
        return future.result()
    except FileNotFoundError:
        raise
    except Exception:
        return default


def _with_duration(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**item, "duration_ms": item.get("duration_ms") or _duration_ms(item.get("started_at"), item.get("finished_at") or item.get("completed_at") or item.get("time"))} for item in runs]


def _load_first_run(summaries: list[dict[str, Any]]) -> dict[str, Any] | None:
    for item in summaries:
        run_id = item.get("run_id")
        if not run_id:
            continue
        try:
            return get_run(str(run_id))
        except FileNotFoundError:
            continue
    return None


def _run_from_body_or_latest(workspace_id: str, body: dict[str, Any]) -> dict[str, Any] | None:
    run_id = body.get("run_id") or body.get("conversation_id")
    if run_id:
        try:
            return get_run(str(run_id))
        except FileNotFoundError:
            return None
    return _load_first_run(list_runs(workspace_id))


def _artifact(run: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(run, dict):
        return {}
    artifact = run.get("artifact")
    if isinstance(artifact, dict):
        return artifact
    final = run.get("final")
    if isinstance(final, dict) and isinstance(final.get("artifact"), dict):
        return final["artifact"]
    return {}


def _strip_market_dump_from_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(artifact, dict):
        return {}
    clone = dict(artifact)
    answer = clone.get("answer")
    if isinstance(answer, dict):
        answer_clone = dict(answer)
        for key in ("text", "markdown"):
            if isinstance(answer_clone.get(key), str):
                answer_clone[key] = _strip_market_dump(answer_clone[key])
        clone["answer"] = answer_clone
    return clone


def _strip_market_dump(text: str) -> str:
    value = str(text or "")
    value = re.sub(r"(?ms)^[ \t]*(?:[-*][ \t]*)?外部市场只作为参考[：:][ \t]*\{.*?(?=^[ \t]*\*\*评分\*\*|^[ \t]*##|\Z)", "", value)
    value = re.sub(r"(?ms)^[ \t]*(?:[-*][ \t]*)?市场补充[：:][ \t]*\{.*?(?=^[ \t]*##|\Z)", "", value)
    return value.strip()


def _has_full_analysis_artifact(artifact: dict[str, Any]) -> bool:
    feasibility = artifact.get("feasibility") if isinstance(artifact, dict) else None
    if not isinstance(feasibility, dict):
        return False
    dimensions = feasibility.get("dimensions")
    return isinstance(dimensions, list) and any(isinstance(item, dict) for item in dimensions)


def _is_lightweight_followup_run(run: dict[str, Any]) -> bool:
    if not isinstance(run, dict) or run.get("version_kind") == "artifact_generation":
        return False
    status = str(run.get("status") or "").strip().lower()
    artifact = _artifact(run)
    routing = artifact.get("routing") if isinstance(artifact.get("routing"), dict) else {}
    intent = str(routing.get("intent") or "").strip().lower()
    mode = str(routing.get("mode") or artifact.get("mode") or "").strip().lower()
    return status in {"followup", "followup_edit"} or intent == "followup_edit" or mode == "followup"


def _analysis_trace_run(run: dict[str, Any]) -> dict[str, Any]:
    """Artifact snapshots are versions, not the original Agent execution trace."""
    if not isinstance(run, dict) or run.get("version_kind") != "artifact_generation":
        return run
    source_run_id = str(run.get("source_run_id") or run.get("conversation_id") or "").strip()
    if not source_run_id or source_run_id == str(run.get("run_id") or ""):
        return run
    try:
        source = get_run(source_run_id)
    except FileNotFoundError:
        return run
    return source if _has_full_analysis_artifact(_artifact(source)) else run


def _flow_trace_from_run(run: dict[str, Any], artifact_override: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for step in run.get("steps") or []:
        if not isinstance(step, dict):
            continue
        event = str(step.get("event") or "").strip()
        if not event:
            continue
        data = step.get("data") if isinstance(step.get("data"), dict) else {}
        events.append({"event": event, "data": _sanitize_detail(data, depth=0), "time": step.get("time")})
    artifact = artifact_override if isinstance(artifact_override, dict) else _artifact(run)
    if not _has_web_search_event(events):
        market_event = _web_search_event_from_artifact(artifact, run)
        if market_event:
            events.append(market_event)
    if not any(item.get("event") == "final" for item in events) and _has_full_analysis_artifact(artifact):
        events.append({"event": "final", "data": {"artifact": artifact}, "time": run.get("completed_at") or run.get("updated_at")})
    return _keep_important_tail(events, limit=60)


def _has_web_search_event(events: list[dict[str, Any]]) -> bool:
    return any(
        item.get("event") == "tool_result"
        and isinstance(item.get("data"), dict)
        and item["data"].get("name") == "foundry_native_web_search"
        for item in events
    )


def _web_search_event_from_artifact(artifact: dict[str, Any], run: dict[str, Any]) -> dict[str, Any] | None:
    market = artifact.get("market") if isinstance(artifact, dict) and isinstance(artifact.get("market"), dict) else {}
    sources = market.get("sources") if isinstance(market.get("sources"), list) else []
    findings = market.get("external_findings") if isinstance(market.get("external_findings"), list) else []
    if not sources and not findings and not market.get("_llm"):
        return None
    provenance = (market.get("tool_provenance") or {}).get("foundry_native_web_search") if isinstance(market.get("tool_provenance"), dict) else None
    llm = market.get("_llm") if isinstance(market.get("_llm"), dict) else {}
    return {
        "event": "tool_result",
        "time": run.get("completed_at") or run.get("updated_at") or run.get("started_at"),
        "data": {
            "agent": "df-market-researcher",
            "name": "foundry_native_web_search",
            "count": len(findings),
            "sources": sources,
            "mode": llm.get("mode"),
            "verification": llm.get("verification"),
            "error": llm.get("error"),
            "provenance": provenance,
            "restored_from_artifact": True,
        },
    }


def _keep_important_tail(events: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    tail = events[-limit:]
    if _has_web_search_event(tail):
        return tail
    important = [
        item for item in events[:-limit]
        if item.get("event") == "tool_result"
        and isinstance(item.get("data"), dict)
        and item["data"].get("name") == "foundry_native_web_search"
    ]
    if not important:
        return tail
    merged = important[-1:] + tail
    return merged[-limit:]


def _agents(run: dict[str, Any]) -> set[str]:
    agents: set[str] = set()
    for step in run.get("steps") or []:
        agent = _step_agent(step if isinstance(step, dict) else {})
        if agent:
            agents.add(agent)
    for item in run.get("models") or []:
        if isinstance(item, dict) and item.get("agent"):
            agents.add(str(item["agent"]))
    return agents


def _step_agent(step: dict[str, Any]) -> str | None:
    data = step.get("data") if isinstance(step.get("data"), dict) else {}
    return data.get("agent") or data.get("target_expert") or data.get("name") if step.get("event") == "role_change" else data.get("agent")


def _step_role(step: dict[str, Any]) -> str:
    event = str(step.get("event") or "")
    if event == "model_response":
        return "model"
    if event in {"tool_call", "tool_result"}:
        return "tool"
    if event == "audit":
        return "audit"
    if event == "role_change":
        return "agent"
    return event or "event"


def _step_status(step: dict[str, Any]) -> str:
    data = step.get("data") if isinstance(step.get("data"), dict) else {}
    text = json.dumps(data, ensure_ascii=False).lower()
    if data.get("error") or "traceback" in text:
        return "failed"
    if data.get("status") in {"failed", "error"}:
        return "failed"
    if data.get("status") in {"running", "in_progress"}:
        return "running"
    return "completed"


def _step_summary(step: dict[str, Any]) -> str:
    event = str(step.get("event") or "event")
    data = step.get("data") if isinstance(step.get("data"), dict) else {}
    if event == "ready":
        return _clean(f"运行上下文已创建，运行 ID：{data.get('conversation_id') or data.get('run_id') or '等待写入'}", 180)
    if event in {"answer_delta", "delta"}:
        return "答案正在流式生成"
    if event == "final":
        return _clean(f"最终结果已保存：{data.get('mode') or data.get('status') or 'analysis'}", 180)
    if event == "error":
        return _clean(f"运行失败：{data.get('message') or data.get('error') or '未知错误'}", 180)
    if event == "progress":
        return _clean(f"进度：{data.get('message') or data.get('stage') or data.get('status') or '处理中'}", 180)
    if event == "clarify":
        clarify = data.get("clarify") if isinstance(data.get("clarify"), dict) else data
        return _clean(f"需要澄清：{clarify.get('question') or '补充关键信息'}", 180)
    if event == "route":
        experts = ", ".join(str(item) for item in data.get("experts") or [])
        mode = "轻量跟进" if data.get("mode") == "followup" else "完整分析"
        return _clean(f"{mode}路由：{data.get('intent') or 'unknown'}；参与节点 {experts or '待定'}", 180)
    if event in {"tool_call", "tool_result"}:
        name = data.get("name") or data.get("tool") or data.get("agent") or ""
        if name == "foundry_native_web_search":
            count = data.get("count")
            source_count = len(data.get("sources") or []) if isinstance(data.get("sources"), list) else 0
            if event == "tool_call":
                query = ((data.get("args") or {}).get("query") if isinstance(data.get("args"), dict) else "") or data.get("input")
                return _clean(f"联网检索：查询 {query or '市场参考信号'}", 180)
            return _clean(f"联网检索完成：{source_count or count or 0} 条外部来源，按 market_inferred 标记", 180)
        if name == "market_lookup":
            return _clean("市场参考检索完成，结果仅作为 market_inferred", 180)
        return _clean(f"{'工具调用' if event == 'tool_call' else '工具返回'}：{name}", 180)
    if event == "model_response":
        usage = _usage_from_dict(data)
        if usage is None:
            return _clean(
                f"model_response: {data.get('agent') or data.get('mode') or 'default_model'}",
                180,
            )
        return _clean(f"模型响应完成：{data.get('agent') or data.get('mode') or '默认模型'}，记录 {usage.get('total') or 0} tokens", 180)
    if event == "audit":
        return _clean(f"审计完成：{data.get('verdict') or data.get('status') or '已记录'}", 180)
    return _clean(f"{event}: {data.get('agent') or data.get('name') or data.get('status') or ''}", 180)


def _tool_counts(run: dict[str, Any]) -> dict[str, int]:
    calls = 0
    results = 0
    failed = 0
    for step in run.get("steps") or []:
        if not isinstance(step, dict):
            continue
        event = step.get("event")
        data = step.get("data") if isinstance(step.get("data"), dict) else {}
        if event == "tool_call":
            calls += 1
        if event == "tool_result":
            results += 1
            if data.get("error") or data.get("status") in {"failed", "error"}:
                failed += 1
    total = max(calls, results)
    return {"total": total, "ok": max(0, results - failed), "fail": failed}


def _token_usage(run: dict[str, Any]) -> dict[str, int] | None:
    total = {"total": 0, "prompt": 0, "completion": 0}
    model_sources = [item.get("usage") for item in run.get("models") or [] if isinstance(item, dict)]
    has_model_usage = any(_usage_is_observed(item) for item in model_sources)
    sources = list(model_sources)
    for step in run.get("steps") or []:
        if not isinstance(step, dict):
            continue
        if step.get("event") == "model_response" and has_model_usage:
            continue
        data = step.get("data") if isinstance(step.get("data"), dict) else {}
        if _usage_is_observed(data.get("usage")):
            sources.append(data.get("usage"))
    observed = False
    for usage in sources:
        if not _usage_is_observed(usage):
            continue
        item = _usage_from_dict(usage if isinstance(usage, dict) else {})
        if item is None:
            continue
        observed = True
        total["total"] += item.get("total") or 0
        total["prompt"] += item.get("prompt") or 0
        total["completion"] += item.get("completion") or 0
    return total if observed else None


def _usage_is_observed(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    usage = data.get("usage") if "usage" in data else data
    if not isinstance(usage, dict):
        return False
    return any(
        key in usage
        and isinstance(usage.get(key), (int, float))
        and not isinstance(usage.get(key), bool)
        for key in (
            "prompt_tokens",
            "prompt",
            "input_tokens",
            "completion_tokens",
            "completion",
            "output_tokens",
            "total_tokens",
            "total",
        )
    )


def _usage_from_dict(data: dict[str, Any]) -> dict[str, int] | None:
    usage = data.get("usage") if "usage" in data else data
    if not _usage_is_observed(usage):
        return None
    prompt = int(usage.get("prompt_tokens") or usage.get("prompt") or usage.get("input_tokens") or 0)
    completion = int(usage.get("completion_tokens") or usage.get("completion") or usage.get("output_tokens") or 0)
    total = int(usage.get("total_tokens") or usage.get("total") or prompt + completion)
    return {"total": total, "prompt": prompt, "completion": completion}


def _audit_summary(run: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(run, dict):
        return {}
    artifact = _artifact(run)
    audit = run.get("audit") if isinstance(run.get("audit"), dict) else artifact.get("audit")
    if not isinstance(audit, dict):
        audit = {}
    downgrade = artifact.get("verdict_downgrade") if isinstance(artifact.get("verdict_downgrade"), dict) else {}
    warnings = []
    for item in audit.get("issues") or audit.get("warnings") or []:
        text = _clean(item, 180)
        if text:
            warnings.append(text)
    risks = []
    for item in audit.get("risks") or artifact.get("risk_register") or []:
        text = _clean(item.get("risk") if isinstance(item, dict) else item, 180)
        if text:
            risks.append(text)
    return {
        "status": audit.get("verdict") or audit.get("status") or ("downgraded" if downgrade else "unknown"),
        "risks": risks[:6],
        "warnings": warnings[:8],
        "downgrade": downgrade or None,
    }


def _citations(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    value = artifact.get("citations")
    if not isinstance(value, list) and isinstance(artifact.get("answer"), dict):
        value = artifact["answer"].get("citations")
    return [item for item in value or [] if isinstance(item, dict)]


def _advice_items(feasibility: dict[str, Any], artifact: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    recommendation = _clean(feasibility.get("recommendation") or artifact.get("recommendation"), 240)
    if recommendation:
        items.append({"title": "Recommendation", "value": recommendation, "sub": feasibility.get("verdict")})
    for index, step in enumerate(feasibility.get("action_plan") or artifact.get("action_plan") or [], start=1):
        text = _clean(step, 220)
        if text:
            items.append({"title": f"Step {index}", "value": text, "sub": "action_plan"})
    for dim in feasibility.get("dimensions") or []:
        if isinstance(dim, dict):
            items.append({"title": str(dim.get("name") or "dimension"), "value": dim.get("score"), "sub": _clean(dim.get("rationale"), 160)})
    return items[:10]


def _basis_items(feasibility: dict[str, Any], citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for dim in feasibility.get("dimensions") or []:
        if isinstance(dim, dict) and dim.get("rationale"):
            items.append({"source": str(dim.get("name") or "dimension"), "desc": _clean(dim.get("rationale"), 220)})
    for citation in citations[:8]:
        desc = _clean(citation.get("snippet") or citation.get("quote") or citation.get("source_label"), 220)
        if desc:
            items.append({"source": _clean(citation.get("source_file") or citation.get("ref") or citation.get("marker"), 90), "desc": desc})
    return items[:12]


def _evidence_items(citations: list[dict[str, Any]], docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for citation in citations[:12]:
        name = _clean(citation.get("source_file") or citation.get("title") or citation.get("ref") or citation.get("marker"), 120)
        if name:
            items.append({"name": name, "format": Path(name).suffix.lstrip(".") or "citation", "bytes": citation.get("bytes"), "ref": citation.get("ref")})
    for doc in docs[:12]:
        name = _clean(doc.get("name") or doc.get("source_file"), 120)
        if name and not any(item.get("name") == name for item in items):
            items.append({"name": name, "format": doc.get("format"), "bytes": doc.get("bytes"), "records": doc.get("record_count"), "fields": doc.get("field_count")})
    return items[:16]


def _workspace_documents(workspace_id: str) -> list[dict[str, Any]]:
    if not workspace_id:
        return []
    try:
        return [item for item in get_workspace_detail(workspace_id).get("documents") or [] if isinstance(item, dict)]
    except Exception:
        return []


def _workspace_profile_context(workspace_id: str) -> dict[str, Any]:
    try:
        detail = get_workspace_detail(workspace_id)
    except Exception:
        return {}
    return {
        "tables": [
            {
                "row_count": detail.get("row_count"),
                "columns": [{"name": item.get("name"), "type": item.get("role")} for item in (detail.get("columns") or [])[:12] if isinstance(item, dict)],
            }
        ]
    }


def _method_steps(playbook: Any, opportunity: str, gaps: list[str], dims: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> list[str]:
    gap = gaps[0] if gaps else "the largest unsupported assumption"
    weak = _weak_dimension(dims)
    source = evidence[0].get("name") if evidence else "current workspace evidence"
    method = str(playbook or "opportunity_tree")
    templates = {
        "jtbd": [
            f"Interview the highest-priority user segment for {opportunity}; capture trigger, current workaround, and expected outcome against {source}.",
            f"Turn the top job into one measurable success metric, then verify whether the workspace contains that metric or needs a new capture field.",
            f"Prototype the before-after workflow and reject claims that do not reduce the gap: {gap}.",
        ],
        "opportunity_tree": [
            f"Map one business goal for {opportunity}, then split evidence-backed opportunities from assumptions using {source}.",
            f"Attach one solution idea to each opportunity branch and mark branches blocked by {gap}.",
            f"Run the smallest experiment for the strongest branch; promote only branches with observed signal, not opinions.",
        ],
        "prd": [
            f"Write the PRD problem statement for {opportunity} using the strongest evidence source: {source}.",
            f"Define MVP scope around the weakest feasibility dimension ({weak}) so the first release tests the riskiest claim.",
            f"Set acceptance metrics and non-goals; every metric must map to an existing field or an explicit data gap.",
        ],
        "roadmap": [
            f"Days 0-30: normalize the fields and evidence needed for {opportunity}, especially the gap: {gap}.",
            f"Days 31-60: run a controlled pilot around {source} and measure adoption, cost, and evidence quality.",
            f"Days 61-90: scale only if the pilot improves the weakest dimension ({weak}); otherwise narrow the offer.",
        ],
        "pricing": [
            f"Identify the chargeable unit for {opportunity} from actual usage or output volume in {source}.",
            f"Test willingness-to-pay with a constrained package while tracking cost drivers and the gap: {gap}.",
            f"Compare pricing only after the pilot proves repeat usage; do not price unsupported differentiators.",
        ],
        "experiment": [
            f"State the riskiest hypothesis for {opportunity}: whether evidence can overcome {gap}.",
            f"Run a small sample experiment using {source}; define pass/fail thresholds before collecting feedback.",
            f"Audit the result against {weak}; downgrade the conclusion if the sample does not support the claim.",
        ],
    }
    return templates.get(method, templates["opportunity_tree"])


def _method_recommendation(label: Any, opportunity: str, gaps: list[str], evidence: list[dict[str, Any]]) -> str:
    source = evidence[0].get("name") if evidence else "workspace evidence"
    gap = gaps[0] if gaps else "the main evidence gap"
    return f"Use {label or 'the selected method'} to narrow {opportunity} into a testable pilot anchored on {source}, and treat {gap} as the first validation gate."


def _weak_dimension(dims: list[dict[str, Any]]) -> str:
    if not dims:
        return "evidence strength"
    ordered = sorted(dims, key=lambda item: float(item.get("score") or 0))
    return str(ordered[0].get("name") or "evidence strength")


def _asset_rows(files: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for group in files.get("groups") or []:
        for item in group.get("files") or []:
            if isinstance(item, dict):
                rows.append(
                    {
                        "id": item.get("id"),
                        "name": item.get("name"),
                        "type": item.get("type"),
                        "records": item.get("records") or item.get("record_count"),
                        "fields": item.get("fields") or item.get("field_count"),
                        "status": item.get("status"),
                    }
                )
    return rows


def _artifact_item(kind: str, url: str, run: dict[str, Any], proposal: dict[str, Any] | None = None) -> dict[str, Any]:
    name = _artifact_name(url) or f"{kind}-{run.get('run_id') or 'artifact'}"
    local = ARTIFACT_DIR / name
    bytes_value = local.stat().st_size if local.exists() else None
    content_type = mimetypes.guess_type(name)[0] or _content_type_for_kind(kind)
    if bytes_value is None:
        downloaded = download_artifact(name)
        if downloaded:
            bytes_value = len(downloaded[0])
            content_type = downloaded[1] or content_type
    proposal = proposal if isinstance(proposal, dict) else {}
    generated_by_kind = proposal.get("artifact_generated_at") if isinstance(proposal.get("artifact_generated_at"), dict) else {}
    created_at = (
        generated_by_kind.get(kind)
        or proposal.get("generated_at")
        or run.get("completed_at")
        or run.get("updated_at")
        or run.get("started_at")
    )
    return {
        "name": name,
        "type": _artifact_type(kind, name),
        "bytes": bytes_value,
        "created_at": created_at,
        "status": "ready" if url else "missing",
        "url": url,
        "run_id": run.get("run_id"),
        "content_type": content_type,
    }


def _artifact_name(url: str) -> str:
    if not url:
        return ""
    path = unquote(urlparse(url).path or url)
    return Path(path).name


def _artifact_type(kind: str, name: str) -> str:
    suffix = Path(name).suffix.lower()
    if kind in {"pdf", "concept_image", "audio_summary", "pilot_plan", "action_plan"}:
        return {"concept_image": "image", "audio_summary": "audio", "pilot_plan": "markdown", "action_plan": "markdown"}.get(kind, kind)
    if suffix == ".pdf":
        return "pdf"
    if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        return "image"
    if suffix in {".wav", ".mp3", ".m4a"}:
        return "audio"
    if suffix in {".md", ".txt"}:
        return "markdown"
    return "file"


def _content_type_for_kind(kind: str) -> str:
    return {
        "pdf": "application/pdf",
        "concept_image": "image/png",
        "audio_summary": "audio/wav",
        "pilot_plan": "text/markdown; charset=utf-8",
        "action_plan": "text/markdown; charset=utf-8",
    }.get(kind, "application/octet-stream")


def _run_log_text(result: dict[str, Any]) -> str:
    lines = [f"run_id={result.get('run_id')} status={result.get('status')} workspace={result.get('workspace_id')}"]
    for item in result.get("trace") or []:
        lines.append(
            f"{item.get('index'):>3} {item.get('time') or ''} {item.get('event') or ''} "
            f"{item.get('agent') or '-'} {item.get('status') or ''} {item.get('summary') or ''}"
        )
    return "\n".join(lines) + "\n"


def _text_summary(run: dict[str, Any]) -> str:
    summary = run.get("summary")
    if isinstance(summary, str) and summary.strip():
        return summary.strip()
    final = run.get("final") if isinstance(run.get("final"), dict) else {}
    if isinstance(final, dict):
        text = _clean(final.get("text") or final.get("answer"), 400)
        if text:
            return text
    return _clean(run.get("message"), 240)


def _paragraphs(value: Any, limit: int) -> list[str]:
    text = str(value or "")
    text = re.sub(r"\r\n?", "\n", text)
    parts = [part.strip(" #*-") for part in re.split(r"\n{2,}|\n[-*]\s+", text) if part.strip(" #*-")]
    return [_clean(part, 360) for part in parts[:limit] if _clean(part, 360)]


def _duration_ms(start: Any, end: Any) -> int | None:
    start_dt = _parse_time(start)
    end_dt = _parse_time(end)
    if not start_dt or not end_dt:
        return None
    return max(0, int((end_dt - start_dt).total_seconds() * 1000))


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _sanitize_detail(value: Any, *, depth: int) -> Any:
    if depth > 5:
        return _clean(value, 500)
    if isinstance(value, dict):
        result = {}
        for key, item in list(value.items())[:80]:
            key_text = str(key)
            if key_text.lower() in {"password", "secret", "connection_string", "sas", "accountkey", "sig"}:
                result[key_text] = "[redacted]"
            else:
                result[key_text] = _sanitize_detail(item, depth=depth + 1)
        return result
    if isinstance(value, list):
        return [_sanitize_detail(item, depth=depth + 1) for item in value[:80]]
    return _clean(value, 5000) if isinstance(value, str) else value


def _safe_value(func: Any, default: Any) -> Any:
    try:
        return func()
    except Exception:
        return default


def _dig(value: Any, *keys: Any) -> Any:
    current = value
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        elif isinstance(current, list) and isinstance(key, int) and 0 <= key < len(current):
            current = current[key]
        else:
            return None
    return current


def _clean(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return ""
    return text[:limit].strip() if limit else text


def _redact_url(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    parsed = urlparse(text)
    if not parsed.netloc:
        return text
    return f"{parsed.scheme}://{parsed.netloc}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
