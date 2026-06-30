from __future__ import annotations

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

from fastapi import APIRouter, HTTPException, Query, Response
from starlette.concurrency import run_in_threadpool

try:
    from .blob_store import blob_configured, download_artifact, probe_blob_container
    from .conversation_store import get_conversation, list_conversations
    from .data_workbench import list_workspace_files
    from .dependency_health import health_dependencies, health_dependency_details
    from .observability import observability_snapshot
    from .pm_skills import playbook_suggestion
    from .run_store import get_run, list_runs
    from .workspace_store import get_workspace_detail, list_workspaces
except ImportError:
    from blob_store import blob_configured, download_artifact, probe_blob_container
    from conversation_store import get_conversation, list_conversations
    from data_workbench import list_workspace_files
    from dependency_health import health_dependencies, health_dependency_details
    from observability import observability_snapshot
    from pm_skills import playbook_suggestion
    from run_store import get_run, list_runs
    from workspace_store import get_workspace_detail, list_workspaces


router = APIRouter(tags=["control-plane"])

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = ROOT / "generated-outputs"
HEALTH_CACHE_SECONDS = float(os.environ.get("DF_DASHBOARD_HEALTH_CACHE_SECONDS", "8"))

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
async def workspace_settings(workspace_id: str) -> dict[str, Any]:
    return await _call(workspace_settings_summary, workspace_id)


@router.get("/api/workspaces/{workspace_id}/members")
async def workspace_members(workspace_id: str) -> dict[str, Any]:
    return await _call(workspace_member_roles, workspace_id)


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
            "workspace_default": "upload-cn-abe76cb16b-20260620102932",
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
        artifact = _strip_market_dump_from_artifact(_artifact(run))
        if not _has_full_analysis_artifact(artifact):
            continue
        feasibility = artifact.get("feasibility") if isinstance(artifact.get("feasibility"), dict) else {}
        return {
            "workspace_id": workspace_id,
            "found": True,
            "run_id": run.get("run_id") or run_id,
            "conversation_id": run.get("conversation_id"),
            "artifact": artifact,
            "feasibility": feasibility,
            "trace": _flow_trace_from_run(run),
            "run_trace": trace_from_run(run),
            "pipeline": pipeline_from_run(run),
            "summary": _safe_value(lambda: run_summary(run_id), {}),
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
        "audit": audit,
        "started_at": run.get("started_at"),
        "finished_at": run.get("completed_at"),
        "title": run.get("title"),
        "summary": run.get("summary") if isinstance(run.get("summary"), str) else _text_summary(run),
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
        duration = _duration_ms(step.get("time"), next_step.get("time") if isinstance(next_step, dict) else None)
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
            }
        )
    return items


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
            item = _artifact_item(str(kind), str(url or ""), run)
            key = str(item.get("url") or item.get("name"))
            if key and key not in seen:
                seen.add(key)
                items.append(item)
        for kind in ("pdf", "concept_image", "audio_summary", "pilot_plan", "action_plan"):
            value = proposal.get(kind)
            if isinstance(value, dict) and value.get("artifact_url"):
                item = _artifact_item(kind, str(value.get("artifact_url")), run)
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
    return {
        "ok": all(bool(value) for value in (health.get("dependencies") or {}).values()),
        "checked_at": _now(),
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


def workspace_settings_summary(workspace_id: str) -> dict[str, Any]:
    files = _safe_value(lambda: list_workspace_files(workspace_id), {"storage": {}, "groups": []})
    status = system_status()
    return {
        "workspace_id": workspace_id,
        "system_status": status,
        "storage": files.get("storage") or {},
        "members": workspace_member_roles(workspace_id).get("members") or [],
        "configuration": {
            "models": status.get("models") or {},
            "rag": status.get("rag") or {},
            "compliance": status.get("compliance") or {},
        },
    }


def workspace_member_roles(workspace_id: str) -> dict[str, Any]:
    owner_email = os.environ.get("DF_WORKSPACE_OWNER_EMAIL") or os.environ.get("USER_EMAIL") or "owner@example.com"
    owner_name = os.environ.get("DF_WORKSPACE_OWNER_NAME") or "Workspace owner"
    return {
        "workspace_id": workspace_id,
        "rbac_enforced": False,
        "source": "workspace_placeholder",
        "roles": ["owner", "admin", "editor", "viewer"],
        "members": [
            {"user": owner_name, "email": owner_email, "role": "owner", "status": "active"},
            {"user": "DataForge demo reviewer", "email": None, "role": "viewer", "status": "placeholder"},
        ],
    }


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


def _flow_trace_from_run(run: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for step in run.get("steps") or []:
        if not isinstance(step, dict):
            continue
        event = str(step.get("event") or "").strip()
        if not event:
            continue
        data = step.get("data") if isinstance(step.get("data"), dict) else {}
        events.append({"event": event, "data": _sanitize_detail(data, depth=0), "time": step.get("time")})
    if not any(item.get("event") == "final" for item in events) and _has_full_analysis_artifact(_artifact(run)):
        events.append({"event": "final", "data": {"artifact": _artifact(run)}, "time": run.get("completed_at") or run.get("updated_at")})
    return events[-60:]


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
        return _clean(f"运行已建立：{data.get('conversation_id') or data.get('run_id') or '等待智能体输出'}", 180)
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
        return _clean(f"route: {data.get('intent') or 'unknown'} {experts}", 180)
    if event in {"tool_call", "tool_result"}:
        return _clean(f"{event}: {data.get('name') or data.get('tool') or data.get('agent') or ''}", 180)
    if event == "model_response":
        usage = _usage_from_dict(data)
        return _clean(f"model: {data.get('agent') or data.get('mode') or ''} tokens={usage.get('total') or 0}", 180)
    if event == "audit":
        return _clean(f"audit: {data.get('verdict') or data.get('status') or ''}", 180)
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


def _token_usage(run: dict[str, Any]) -> dict[str, int]:
    total = {"total": 0, "prompt": 0, "completion": 0}
    sources = [item.get("usage") for item in run.get("models") or [] if isinstance(item, dict)]
    if not sources:
        for step in run.get("steps") or []:
            data = step.get("data") if isinstance(step, dict) and isinstance(step.get("data"), dict) else {}
            if data.get("usage"):
                sources.append(data.get("usage"))
    for usage in sources:
        item = _usage_from_dict(usage if isinstance(usage, dict) else {})
        total["total"] += item.get("total") or 0
        total["prompt"] += item.get("prompt") or 0
        total["completion"] += item.get("completion") or 0
    return total


def _usage_from_dict(data: dict[str, Any]) -> dict[str, int]:
    usage = data.get("usage") if "usage" in data else data
    if not isinstance(usage, dict):
        return {"total": 0, "prompt": 0, "completion": 0}
    prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    completion = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
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


def _artifact_item(kind: str, url: str, run: dict[str, Any]) -> dict[str, Any]:
    name = _artifact_name(url) or f"{kind}-{run.get('run_id') or 'artifact'}"
    local = ARTIFACT_DIR / name
    bytes_value = local.stat().st_size if local.exists() else None
    content_type = mimetypes.guess_type(name)[0] or _content_type_for_kind(kind)
    if bytes_value is None:
        downloaded = download_artifact(name)
        if downloaded:
            bytes_value = len(downloaded[0])
            content_type = downloaded[1] or content_type
    return {
        "name": name,
        "type": _artifact_type(kind, name),
        "bytes": bytes_value,
        "created_at": run.get("completed_at") or run.get("updated_at") or run.get("started_at"),
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
