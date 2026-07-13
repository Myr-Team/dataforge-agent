from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi import BackgroundTasks
from fastapi import File
from fastapi import Form
from fastapi import HTTPException
from fastapi import Request
from fastapi import UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.responses import Response
from fastapi.responses import StreamingResponse
from pathlib import Path
from starlette.concurrency import run_in_threadpool

try:
    from .audit_store import record_audit_event
    from .artifact_jobs import ArtifactJobPersistenceError, create_artifact_job, get_artifact_job, list_artifact_jobs, recover_prepared_artifact_tasks, retry_artifact_task, run_artifact_job
    from .task_store import TaskPersistenceError, cancel_requested, claim_task, create_task, get_task, list_tasks, request_cancel, update_task
    from .blob_store import download_artifact
    from .conversation_store import get_conversation, list_conversations
    from .control_plane import build_workspace_dashboard, router as control_plane_router
    from .data_workbench import router as data_workbench_router
    from .dependency_health import health_dependencies, health_dependency_details
    from .identity import actor_from_request, merge_actor_into_ui_context
    from .observability import observability_snapshot
    from .orchestrator import extract_plan_metrics, generate_data_overview, generate_playbook_detail, orchestrate_chat, produce_from_existing_report
    from .rag import search
    from .run_store import get_flagship_plan, get_run, list_runs, set_flagship_plan
    from .speech_token import issue_speech_token
    from .tracing import configure_monitoring
    from .workspace_store import (
        create_workspace_upload_job,
        delete_workspace,
        get_reference_image_content,
        get_workspace_detail,
        list_workspaces,
        run_workspace_ingest_job,
        workspace_ingest_status,
        workspace_pending_ingest_jobs,
    )
    from .workspace_authz import authorize, rbac_enabled, require_workspace_permission, workspace_role
    from .schemas import (
        ChatRequest,
        ConversationDetailResponse,
        ConversationsResponse,
        GenerateImageRequest,
        NarrateSummaryRequest,
        ProduceRequest,
        PlaybookRequest,
        PlanFlagshipRequest,
        RenderPdfRequest,
        RunDetailResponse,
        RunsResponse,
        SearchPackContextRequest,
        SearchPackContextResponse,
        UploadResponse,
        WorkspaceDashboardResponse,
        WorkspaceDeleteResponse,
        WorkspaceDetailResponse,
        WorkspacesResponse,
    )
    from .tools.generate_image import generate_image
    from .tools.narrate_summary import narrate_summary
    from .tools.render_pdf import render_pdf_report
except ImportError:
    from audit_store import record_audit_event
    from artifact_jobs import ArtifactJobPersistenceError, create_artifact_job, get_artifact_job, list_artifact_jobs, recover_prepared_artifact_tasks, retry_artifact_task, run_artifact_job
    from task_store import TaskPersistenceError, cancel_requested, claim_task, create_task, get_task, list_tasks, request_cancel, update_task
    from blob_store import download_artifact
    from conversation_store import get_conversation, list_conversations
    from control_plane import build_workspace_dashboard, router as control_plane_router
    from data_workbench import router as data_workbench_router
    from dependency_health import health_dependencies, health_dependency_details
    from identity import actor_from_request, merge_actor_into_ui_context
    from observability import observability_snapshot
    from orchestrator import extract_plan_metrics, generate_data_overview, generate_playbook_detail, orchestrate_chat, produce_from_existing_report
    from rag import search
    from run_store import get_flagship_plan, get_run, list_runs, set_flagship_plan
    from speech_token import issue_speech_token
    from tracing import configure_monitoring
    from workspace_store import (
        create_workspace_upload_job,
        delete_workspace,
        get_reference_image_content,
        get_workspace_detail,
        list_workspaces,
        run_workspace_ingest_job,
        workspace_ingest_status,
        workspace_pending_ingest_jobs,
    )
    from workspace_authz import authorize, rbac_enabled, require_workspace_permission, workspace_role
    from schemas import (
        ChatRequest,
        ConversationDetailResponse,
        ConversationsResponse,
        GenerateImageRequest,
        NarrateSummaryRequest,
        ProduceRequest,
        PlaybookRequest,
        PlanFlagshipRequest,
        RenderPdfRequest,
        RunDetailResponse,
        RunsResponse,
        SearchPackContextRequest,
        SearchPackContextResponse,
        UploadResponse,
        WorkspaceDashboardResponse,
        WorkspaceDeleteResponse,
        WorkspaceDetailResponse,
        WorkspacesResponse,
    )
    from tools.generate_image import generate_image
    from tools.narrate_summary import narrate_summary
    from tools.render_pdf import render_pdf_report


configure_monitoring()

app = FastAPI(title="DataForge Tool Backend", version="0.10.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(data_workbench_router)
app.include_router(control_plane_router)

ARTIFACT_DIR = Path(__file__).resolve().parents[1] / "generated-outputs"
_INGEST_SEMAPHORE = asyncio.Semaphore(max(1, int(os.environ.get("DF_UPLOAD_INGEST_CONCURRENCY", "2"))))
_INGEST_TASKS: set[asyncio.Task[Any]] = set()
_INGEST_KEYS: set[str] = set()
_INGEST_START_DELAY_SECONDS = max(0.0, float(os.environ.get("DF_UPLOAD_INGEST_START_DELAY_SECONDS", "0.5")))


@app.get("/api/health")
async def health() -> dict[str, Any]:
    dependencies = health_dependencies()
    return {
        "ok": True,
        "service": "dataforge-backend",
        "search_endpoint": bool(os.environ.get("SEARCH_ENDPOINT")),
        "workspace_default": "upload-cn-abe76cb16b-20260620102932",
        "dependencies": dependencies,
        "dependency_details": health_dependency_details(),
    }


@app.get("/api/observability")
async def observability() -> dict[str, Any]:
    return observability_snapshot()


@app.post("/api/search-pack-context", response_model=SearchPackContextResponse)
async def search_pack_context(req: SearchPackContextRequest, request: Request) -> SearchPackContextResponse:
    _require_workspace_action(req.workspace_id, request, "workspace.read")
    hits = await run_in_threadpool(search, req.workspace_id, req.query, req.top_k)
    return SearchPackContextResponse(
        workspace_id=req.workspace_id,
        query=req.query,
        hits=hits,
        count=len(hits),
        source_index=os.environ.get("SEARCH_INDEX_NAME", "dataforge-workspaces"),
    )


@app.post("/api/upload", response_model=UploadResponse)
async def upload_workspace(
    request: Request,
    file: list[UploadFile] = File(...),
    name: str | None = Form(default=None),
    description: str | None = Form(default=None),
    workspace_id: str | None = Form(default=None),
    asset_role: str | None = Form(default=None),
) -> UploadResponse:
    if workspace_id:
        _require_workspace_action(workspace_id, request, "file.create")
        _audit_required(request, workspace_id, "file.create", "file", "upload")
    files = []
    for item in file:
        files.append(
            {
                "filename": item.filename or "upload",
                "content": await item.read(),
                "content_type": item.content_type,
            }
        )
    try:
        actor = actor_from_request(request)
        result = await run_in_threadpool(
            create_workspace_upload_job,
            files=files,
            name=name,
            description=description,
            requested_workspace_id=workspace_id,
            asset_role=asset_role,
            actor=actor,
        )
        if not workspace_id:
            _audit_required(request, str(result.get("workspace_id") or ""), "file.create", "file", "upload")
        _schedule_upload_ingest(result, actor=actor)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Workspace not found: {workspace_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TaskPersistenceError as exc:
        raise HTTPException(status_code=503, detail="Task persistence is unavailable") from exc
    return UploadResponse.model_validate(result)


@app.get("/api/workspaces", response_model=WorkspacesResponse)
async def workspaces(request: Request) -> WorkspacesResponse:
    items = await run_in_threadpool(list_workspaces)
    if rbac_enabled():
        actor = actor_from_request(request)
        items = [
            item
            for item in items
            if authorize(workspace_role(str(item.get("workspace_id") or ""), actor), "workspace.read")
        ]
    return WorkspacesResponse(workspaces=items)


@app.get("/api/workspaces/{workspace_id}", response_model=WorkspaceDetailResponse)
async def workspace_detail(workspace_id: str, request: Request) -> WorkspaceDetailResponse:
    _require_workspace_action(workspace_id, request, "workspace.read")
    await _recover_stale_upload_ingest(workspace_id)
    try:
        result = await run_in_threadpool(get_workspace_detail, workspace_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Workspace not found: {workspace_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return WorkspaceDetailResponse.model_validate(result)


@app.get("/api/workspaces/{workspace_id}/dashboard", response_model=WorkspaceDashboardResponse)
async def workspace_dashboard(workspace_id: str, request: Request) -> WorkspaceDashboardResponse:
    _require_workspace_action(workspace_id, request, "workspace.read")
    await _recover_stale_upload_ingest(workspace_id)
    try:
        result = await run_in_threadpool(build_workspace_dashboard, workspace_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Workspace not found: {workspace_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return WorkspaceDashboardResponse.model_validate(result)


@app.get("/api/workspaces/{workspace_id}/ingest-status")
async def ingest_status(workspace_id: str, request: Request) -> dict[str, Any]:
    _require_workspace_action(workspace_id, request, "workspace.read")
    await _recover_stale_upload_ingest(workspace_id)
    try:
        return await run_in_threadpool(workspace_ingest_status, workspace_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Workspace not found: {workspace_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/workspaces/{workspace_id}/manifest")
async def workspace_manifest(workspace_id: str, request: Request) -> dict[str, Any]:
    _require_workspace_action(workspace_id, request, "workspace.read")
    try:
        detail = await run_in_threadpool(get_workspace_detail, workspace_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Workspace not found: {workspace_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    manifest = detail.get("manifest") if isinstance(detail, dict) else None
    if not isinstance(manifest, dict) or not manifest:
        raise HTTPException(status_code=404, detail=f"Manifest not found: {workspace_id}")
    return manifest


@app.post("/api/workspaces/{workspace_id}/auto-analyze")
async def workspace_auto_analyze(workspace_id: str, request: Request) -> dict[str, Any]:
    _require_workspace_action(workspace_id, request, "analysis.run")
    _audit_required(request, workspace_id, "analysis.run", "analysis", "auto-analysis")
    _audit_required(request, workspace_id, "message.create", "message", "pending")
    body: dict[str, Any] = {}
    try:
        body = await request.json()
        if not isinstance(body, dict):
            body = {}
    except Exception:
        body = {}

    cache_bust = datetime.now(timezone.utc).isoformat()
    message = str(
        body.get("message")
        or "请基于当前工作区自动运行一次产品可行性分析，输出机会判断、五维评分、证据、风险缺口和下一步行动计划。"
    )
    req = ChatRequest(
        workspace_id=workspace_id,
        message=message,
        conversation_id=body.get("conversation_id"),
        playbook=body.get("playbook") or "opportunity_tree",
        artifact_mode=body.get("artifact_mode") or "report",
        ui_context=merge_actor_into_ui_context({
            **(body.get("ui_context") if isinstance(body.get("ui_context"), dict) else {}),
            "entrypoint": "workspace_dashboard",
            "auto_analyze": True,
            "cache_bust": cache_bust,
        }, actor_from_request(request)),
    )

    final_payload: dict[str, Any] | None = None
    error_payload: dict[str, Any] | None = None
    conversation_id = req.conversation_id
    answer_parts: list[str] = []
    events: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []
    trace_events = {
        "ready",
        "route",
        "plan",
        "role_change",
        "tool_call",
        "tool_result",
        "blind_verdict",
        "cache",
        "model_response",
        "audit",
        "revised_verdict",
        "progress",
    }

    async for raw_frame in orchestrate_chat(req):
        for event, data in _parse_sse_frame(raw_frame):
            if event == "answer_delta":
                if isinstance(data, dict):
                    answer_parts.append(str(data.get("delta") or ""))
                continue
            if event == "ready" and isinstance(data, dict):
                conversation_id = data.get("conversation_id") or conversation_id
            if event == "final" and isinstance(data, dict):
                final_payload = data
            elif event == "error":
                error_payload = data if isinstance(data, dict) else {"message": str(data)}

            item = {"event": event, "data": _compact_event_data(data)}
            events.append(item)
            if event in trace_events:
                trace.append(item)

    if error_payload is not None:
        raise HTTPException(status_code=502, detail={"message": "auto analyze failed", "error": error_payload, "events": events[-12:]})
    if final_payload is None:
        raise HTTPException(status_code=502, detail={"message": "auto analyze did not return final", "events": events[-12:]})

    artifact = final_payload.get("artifact") if isinstance(final_payload, dict) else {}
    return {
        "workspace_id": workspace_id,
        "conversation_id": conversation_id,
        "status": "completed",
        "final": final_payload,
        "artifact": artifact if isinstance(artifact, dict) else {},
        "text": str(final_payload.get("text") or "".join(answer_parts)),
        "events": events,
        "trace": trace,
        "answer_delta_chars": len("".join(answer_parts)),
        "cache_bust": cache_bust,
    }


@app.get("/api/workspaces/{workspace_id}/reference-images/{filename}")
async def workspace_reference_image(workspace_id: str, filename: str, request: Request) -> Response:
    _require_workspace_action(workspace_id, request, "workspace.read")
    result = await run_in_threadpool(get_reference_image_content, workspace_id, filename)
    if not result:
        raise HTTPException(status_code=404, detail=f"Reference image not found: {filename}")
    content, content_type = result
    return Response(content=content, media_type=content_type)


@app.delete("/api/workspaces/{workspace_id}", response_model=WorkspaceDeleteResponse)
async def remove_workspace(workspace_id: str, request: Request) -> WorkspaceDeleteResponse:
    try:
        _require_workspace_action(workspace_id, request, "workspace.delete")
        _audit_required(request, workspace_id, "workspace.delete", "workspace", workspace_id)
        result = await run_in_threadpool(delete_workspace, workspace_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Workspace not found: {workspace_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return WorkspaceDeleteResponse.model_validate(result)


@app.post("/api/render-pdf-report")
async def render_pdf(req: RenderPdfRequest) -> dict[str, Any]:
    return await run_in_threadpool(render_pdf_report, req.proposal, req.template)


@app.post("/api/generate-image")
async def image(req: GenerateImageRequest) -> dict[str, Any]:
    return await run_in_threadpool(generate_image, req.prompt, req.size, req.reference_image_urls)


@app.get("/api/artifacts/{name}")
def artifact(name: str, request: Request) -> Response:
    safe_name = Path(name).name
    if rbac_enabled():
        workspace_ids = _artifact_workspace_ids(safe_name)
        if len(workspace_ids) != 1:
            raise HTTPException(status_code=404, detail=f"Artifact not found: {safe_name}")
        _require_workspace_action(next(iter(workspace_ids)), request, "artifact.read")
    path = ARTIFACT_DIR / safe_name
    if path.exists():
        return FileResponse(path)
    blob = download_artifact(safe_name)
    if blob:
        content, content_type = blob
        return Response(content=content, media_type=content_type)
    raise HTTPException(status_code=404, detail=f"Artifact not found: {safe_name}")


@app.post("/api/narrate-summary")
async def narrate(req: NarrateSummaryRequest, request: Request) -> dict[str, Any]:
    result = await run_in_threadpool(narrate_summary, req.text, req.voice)
    local_path = result.get("local_path")
    if local_path:
        artifact_url = str(request.url_for("artifact", name=Path(str(local_path)).name))
        if artifact_url.startswith("http://"):
            artifact_url = "https://" + artifact_url.removeprefix("http://")
        result["audio_blob_url"] = artifact_url
    return result


@app.get("/api/speech/token")
async def speech_token() -> dict[str, Any]:
    try:
        return await run_in_threadpool(issue_speech_token)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/produce")
async def produce(req: ProduceRequest, request: Request) -> dict[str, Any]:
    _require_workspace_action(req.workspace_id, request, "artifact.generate")
    _audit_required(request, req.workspace_id, "artifact.generate", "artifact", "pending")
    return await run_in_threadpool(produce_from_existing_report, req.model_dump())


@app.post("/api/artifact-jobs", status_code=202)
async def artifact_job_create(
    req: ProduceRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    try:
        actor = actor_from_request(request)
        _require_workspace_action(req.workspace_id, request, "artifact.generate")
        _audit_required(request, req.workspace_id, "artifact.generate", "artifact", "pending")
        recovered_jobs = await run_in_threadpool(recover_prepared_artifact_tasks, req.workspace_id)
        job = await run_in_threadpool(
            create_artifact_job,
            req.model_dump(),
            actor=actor,
            idempotency_key=request.headers.get("idempotency-key"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ArtifactJobPersistenceError, TaskPersistenceError) as exc:
        raise HTTPException(status_code=503, detail="Durable artifact job storage is unavailable") from exc
    scheduled_job_ids: set[str] = set()
    for recovered in recovered_jobs:
        recovered_job_id = str(recovered.get("job_id") or "")
        if recovered_job_id and recovered.get("status") not in {"partial", "completed", "failed", "cancelled", "running"}:
            background_tasks.add_task(run_artifact_job, recovered_job_id)
            scheduled_job_ids.add(recovered_job_id)
    if job.get("status") not in {"partial", "completed", "failed", "cancelled", "running"} and job["job_id"] not in scheduled_job_ids:
        background_tasks.add_task(run_artifact_job, job["job_id"])
    return job


@app.get("/api/artifact-jobs/{job_id}")
async def artifact_job_detail(job_id: str, request: Request) -> dict[str, Any]:
    try:
        job = await run_in_threadpool(get_artifact_job, job_id)
        _require_workspace_action(str(job.get("workspace_id") or ""), request, "artifact.read")
        return job
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Artifact job not found: {job_id}") from exc


@app.get("/api/workspaces/{workspace_id}/artifact-jobs")
async def workspace_artifact_jobs(workspace_id: str, request: Request) -> dict[str, Any]:
    _require_workspace_action(workspace_id, request, "artifact.read")
    jobs = await run_in_threadpool(list_artifact_jobs, workspace_id)
    return {"workspace_id": workspace_id, "jobs": jobs, "count": len(jobs)}


@app.get("/api/workspaces/{workspace_id}/tasks")
async def workspace_tasks(workspace_id: str, request: Request) -> dict[str, Any]:
    _require_workspace_action(workspace_id, request, "workspace.read")
    try:
        tasks = await run_in_threadpool(list_tasks, workspace_id)
    except TaskPersistenceError as exc:
        raise HTTPException(status_code=503, detail="Task persistence is unavailable") from exc
    return {"workspace_id": workspace_id, "tasks": tasks, "count": len(tasks)}


@app.get("/api/tasks/{task_id}")
async def task_detail(task_id: str, request: Request) -> dict[str, Any]:
    try:
        task = await run_in_threadpool(get_task, task_id)
    except TaskPersistenceError as exc:
        raise HTTPException(status_code=503, detail="Task persistence is unavailable") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}") from exc
    _require_workspace_action(str(task.get("workspace_id") or ""), request, "workspace.read")
    return task


@app.post("/api/tasks/{task_id}/cancel")
async def task_cancel(task_id: str, request: Request) -> dict[str, Any]:
    try:
        task = await run_in_threadpool(get_task, task_id)
    except TaskPersistenceError as exc:
        raise HTTPException(status_code=503, detail="Task persistence is unavailable") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}") from exc
    _require_workspace_action(str(task.get("workspace_id") or ""), request, str(task.get("action") or "workspace.read"))
    return await run_in_threadpool(request_cancel, task_id, actor_from_request(request))


@app.post("/api/tasks/{task_id}/retry", status_code=202)
async def task_retry(task_id: str, request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
    try:
        task = await run_in_threadpool(get_task, task_id)
        _require_workspace_action(str(task.get("workspace_id") or ""), request, str(task.get("action") or "workspace.read"))
        if task.get("task_type") != "artifact.generate" or not task.get("retryable"):
            raise HTTPException(status_code=409, detail="Task retry is not supported")
        job = await run_in_threadpool(retry_artifact_task, task_id, actor_from_request(request))
        background_tasks.add_task(run_artifact_job, str(job["job_id"]))
        return await run_in_threadpool(get_task, str(job["task_id"]))
    except TaskPersistenceError as exc:
        raise HTTPException(status_code=503, detail="Task persistence is unavailable") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Task retry is not supported") from exc


@app.post("/api/playbook")
async def playbook(req: PlaybookRequest, request: Request) -> dict[str, Any]:
    _require_workspace_action(req.workspace_id, request, "analysis.run")
    _audit_required(request, req.workspace_id, "analysis.run", "analysis", "playbook")
    return await run_in_threadpool(generate_playbook_detail, req.model_dump())


@app.get("/api/workspaces/{workspace_id}/data-overview")
async def data_overview(workspace_id: str, request: Request) -> dict[str, Any]:
    _require_workspace_action(workspace_id, request, "workspace.read")
    return await run_in_threadpool(generate_data_overview, workspace_id)


@app.get("/api/runs", response_model=RunsResponse)
async def runs(request: Request, workspace_id: str | None = None) -> RunsResponse:
    if workspace_id:
        _require_workspace_action(workspace_id, request, "run.read")
    return RunsResponse(runs=await run_in_threadpool(list_runs, workspace_id))


@app.get("/api/runs/{run_id}", response_model=RunDetailResponse)
async def run_detail(run_id: str, request: Request) -> RunDetailResponse:
    try:
        result = await run_in_threadpool(get_run, run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}") from exc
    _require_workspace_action(str(result.get("workspace_id") or ""), request, "run.read")
    return RunDetailResponse.model_validate(result)


@app.get("/api/runs/{run_id}/plan-metrics")
async def run_plan_metrics(run_id: str, request: Request) -> dict:
    """抽取该次分析方案里【可回填迭代的关键指标】（供二次分析编辑）。"""
    run = await run_in_threadpool(get_run, run_id)
    _require_workspace_action(str(run.get("workspace_id") or ""), request, "run.read")
    return await run_in_threadpool(extract_plan_metrics, run_id)


@app.get("/api/workspaces/{workspace_id}/flagship")
async def workspace_flagship(workspace_id: str, request: Request) -> dict:
    _require_workspace_action(workspace_id, request, "workspace.read")
    return {"workspace_id": workspace_id, "flagship_run_id": await run_in_threadpool(get_flagship_plan, workspace_id)}


@app.post("/api/workspaces/{workspace_id}/flagship")
async def set_workspace_flagship(workspace_id: str, body: PlanFlagshipRequest, request: Request) -> dict:
    _require_workspace_action(workspace_id, request, "analysis.run")
    _audit_required(request, workspace_id, "analysis.run", "analysis", body.run_id, correlation={"run_id": body.run_id})
    return await run_in_threadpool(set_flagship_plan, workspace_id, body.run_id)


@app.get("/api/conversations", response_model=ConversationsResponse)
async def conversations(request: Request, workspace_id: str | None = None) -> ConversationsResponse:
    if workspace_id:
        _require_workspace_action(workspace_id, request, "workspace.read")
    return ConversationsResponse(conversations=await run_in_threadpool(list_conversations, workspace_id))


@app.get("/api/conversations/{conversation_id}", response_model=ConversationDetailResponse)
async def conversation_detail(conversation_id: str, request: Request) -> ConversationDetailResponse:
    try:
        result = await run_in_threadpool(get_conversation, conversation_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Conversation not found: {conversation_id}") from exc
    _require_workspace_action(str(result.get("workspace_id") or ""), request, "workspace.read")
    return ConversationDetailResponse.model_validate(result)


async def _sse_keepalive(agen, interval: float = 10.0):
    """Wrap an SSE async-generator so the connection never goes silent longer than
    `interval` seconds. Long agent steps (e.g. market web search) can leave a 30s+
    gap between frames, which proxies/ingress drop → the browser reports a network
    error mid-analysis. Emitting an SSE comment line (':' prefix, ignored by the
    client parser) during those waits keeps the stream alive."""
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

    async def consume_source() -> None:
        try:
            async for item in agen:
                await queue.put(("item", item))
        except asyncio.CancelledError:
            await queue.put(("cancelled", None))
            raise
        except Exception as exc:
            await queue.put(("error", exc))
        finally:
            await queue.put(("done", None))

    producer = asyncio.create_task(consume_source())
    try:
        while True:
            try:
                kind, payload = await asyncio.wait_for(queue.get(), timeout=interval)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            if kind == "item":
                yield payload
                continue
            if kind == "error":
                raise payload
            if kind == "cancelled":
                raise asyncio.CancelledError()
            if kind == "done":
                return
    finally:
        if not producer.done():
            producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)


async def _task_backed_chat_stream(req: ChatRequest, task_id: str):
    """Preserve the existing SSE frames while reflecting their terminal outcome in task_store."""
    terminal = False
    result: dict[str, str] = {}
    try:
        async for raw_frame in _sse_keepalive(orchestrate_chat(req)):
            if cancel_requested(task_id):
                update_task(task_id, status="cancelled", result=result)
                terminal = True
                break
            suppress_frame = False
            for event, data in _parse_sse_frame(raw_frame):
                if event == "ready" and isinstance(data, dict) and data.get("conversation_id"):
                    result["run_id"] = str(data["conversation_id"])
                elif event == "final" and isinstance(data, dict):
                    artifact = data.get("artifact") if isinstance(data.get("artifact"), dict) else {}
                    result["run_id"] = str(data.get("conversation_id") or artifact.get("conversation_id") or result.get("run_id") or "")
                    version_id = artifact.get("version_id") or artifact.get("version_run_id")
                    if version_id:
                        result["version_id"] = str(version_id)
                    outcome = update_task(task_id, status="completed", result={key: value for key, value in result.items() if value})
                    terminal = True
                    if outcome.get("status") == "cancelled":
                        suppress_frame = True
                        break
                elif event == "error":
                    outcome = update_task(task_id, status="failed", error={"category": "analysis", "code": "stream_error"}, result=result)
                    terminal = True
                    if outcome.get("status") == "cancelled":
                        suppress_frame = True
                        break
            if suppress_frame:
                break
            if cancel_requested(task_id):
                update_task(task_id, status="cancelled", result=result)
                terminal = True
                break
            yield raw_frame
            if terminal:
                break
        if cancel_requested(task_id):
            update_task(task_id, status="cancelled", result=result)
        elif not terminal:
            outcome = update_task(task_id, status="failed", error={"category": "analysis", "code": "stream_incomplete"}, result=result)
            terminal = outcome.get("status") in {"failed", "cancelled"}
    except asyncio.CancelledError:
        update_task(task_id, status="cancelled", result=result)
        raise
    except Exception:
        update_task(task_id, status="failed", error={"category": "analysis", "code": "stream_failed"}, result=result)
        raise


@app.post("/api/chat")
async def chat(req: ChatRequest, request: Request) -> StreamingResponse:
    actor = actor_from_request(request)
    _require_workspace_action(req.workspace_id, request, "analysis.run")
    _audit_required(request, req.workspace_id, "analysis.run", "analysis", "chat")
    _audit_required(request, req.workspace_id, "message.create", "message", "pending")
    req = req.model_copy(update={"ui_context": merge_actor_into_ui_context(req.ui_context, actor)})
    is_iteration = bool(req.ui_context.get("iteration_inputs")) if isinstance(req.ui_context, dict) else False
    task = create_task(
        {
            "workspace_id": req.workspace_id,
            "task_type": "analysis.iterate" if is_iteration else "analysis.run",
            "action": "analysis.run",
        },
        actor,
    )
    claimed = claim_task(str(task["task_id"]), "chat-stream")
    if claimed is None:
        raise HTTPException(status_code=503, detail="Unable to start durable analysis task")
    return StreamingResponse(
        _task_backed_chat_stream(req, str(task["task_id"])),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-DataForge-Task-Id": str(task["task_id"]),
        },
    )


def _parse_sse_frame(raw: str) -> list[tuple[str, Any]]:
    frames: list[tuple[str, Any]] = []
    for block in str(raw or "").split("\n\n"):
        event = ""
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data_lines.append(line.removeprefix("data:").strip())
        if not event:
            continue
        data_text = "\n".join(data_lines)
        try:
            data: Any = json.loads(data_text) if data_text else {}
        except json.JSONDecodeError:
            data = data_text
        frames.append((event, data))
    return frames


def _compact_event_data(data: Any) -> Any:
    if not isinstance(data, dict):
        return data
    compact = dict(data)
    if "delta" in compact:
        compact["delta"] = str(compact["delta"])[:120]
    if "artifact" in compact and isinstance(compact["artifact"], dict):
        compact["artifact"] = {
            "workspace_id": compact["artifact"].get("workspace_id"),
            "conversation_id": compact["artifact"].get("conversation_id"),
            "keys": sorted(str(key) for key in compact["artifact"].keys())[:30],
        }
    return compact


def _require_workspace_action(workspace_id: str, request: Request, action: str) -> str:
    try:
        return require_workspace_permission(workspace_id, actor_from_request(request), action)
    except PermissionError as exc:
        try:
            record_audit_event(
                actor_from_request(request),
                action,
                {"workspace_id": workspace_id, "resource_type": "workspace", "resource_id": workspace_id},
                result="denied",
                reason_code="permission_denied",
                correlation=_request_correlation(request),
            )
        except Exception:
            pass
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _audit_required(
    request: Request,
    workspace_id: str,
    action: str,
    resource_type: str,
    resource_id: str,
    *,
    correlation: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    try:
        return record_audit_event(
            actor_from_request(request),
            action,
            {
                "workspace_id": workspace_id,
                "resource_type": resource_type,
                "resource_id": _safe_audit_id(resource_id, "pending"),
            },
            result="allowed",
            reason_code="authorized",
            correlation={**_request_correlation(request), **dict(correlation or {})},
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Audit persistence is required") from exc


def _request_correlation(request: Request | None) -> dict[str, str]:
    if request is None:
        return {}
    value = str(request.headers.get("x-request-id") or request.headers.get("x-correlation-id") or request.headers.get("idempotency-key") or "").strip()
    return {"request_id": value} if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}", value) else {}


def _safe_audit_id(value: Any, fallback: str) -> str:
    clean = str(value or "").strip()
    return clean if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}", clean) else fallback


def _artifact_workspace_ids(safe_name: str) -> set[str]:
    workspace_ids: set[str] = set()
    for job in list_artifact_jobs():
        workspace_id = str(job.get("workspace_id") or "")
        if workspace_id and safe_name in _artifact_names(job.get("artifacts")):
            workspace_ids.add(workspace_id)
    for run in list_runs():
        workspace_id = str(run.get("workspace_id") or "")
        if workspace_id and safe_name in _artifact_names(run.get("artifact_urls")):
            workspace_ids.add(workspace_id)
    return workspace_ids


def _artifact_names(value: Any, *, depth: int = 0) -> set[str]:
    if depth > 4:
        return set()
    if isinstance(value, str):
        path = urlparse(value).path
        return {Path(path).name} if path else set()
    if isinstance(value, Mapping):
        names: set[str] = set()
        for item in list(value.values())[:50]:
            names.update(_artifact_names(item, depth=depth + 1))
        return names
    if isinstance(value, (list, tuple)):
        names = set()
        for item in value[:50]:
            names.update(_artifact_names(item, depth=depth + 1))
        return names
    return set()


async def _recover_stale_upload_ingest(workspace_id: str) -> None:
    try:
        jobs = await run_in_threadpool(workspace_pending_ingest_jobs, workspace_id, stale_only=True)
    except Exception:
        return
    for job in jobs:
        _schedule_upload_ingest(job, delay_seconds=0.0)


def _schedule_upload_ingest(
    result: dict[str, Any],
    *,
    actor: Mapping[str, Any] | None = None,
    delay_seconds: float | None = None,
) -> None:
    job_id = result.get("ingest_job_id") or result.get("job_id")
    workspace_id = result.get("workspace_id")
    if not job_id or not workspace_id:
        return
    task_id = _ensure_upload_ingest_task(result, actor)
    key = f"{workspace_id}:{job_id}"
    if key in _INGEST_KEYS:
        return
    _INGEST_KEYS.add(key)
    loop = asyncio.get_running_loop()
    delay = _INGEST_START_DELAY_SECONDS if delay_seconds is None else max(0.0, float(delay_seconds))

    def _start() -> None:
        task = asyncio.create_task(_run_upload_ingest_background(str(workspace_id), str(job_id), task_id))
        _INGEST_TASKS.add(task)
        def _done(done_task: asyncio.Task[Any]) -> None:
            _INGEST_TASKS.discard(done_task)
            _INGEST_KEYS.discard(key)

        task.add_done_callback(_done)

    loop.call_later(delay, _start)


def _ensure_upload_ingest_task(result: Mapping[str, Any], actor: Mapping[str, Any] | None) -> str | None:
    existing = str(result.get("_dataforge_task_id") or "")
    if existing:
        return existing
    task = create_task(
        {
            "workspace_id": str(result.get("workspace_id") or ""),
            "task_type": "workspace.ingest",
            "action": "file.create",
            "result": {"ingest_job_id": str(result.get("ingest_job_id") or result.get("job_id") or "")},
        },
        actor,
    )
    if isinstance(result, dict):
        result["_dataforge_task_id"] = task["task_id"]
    return str(task["task_id"])


async def _run_upload_ingest_background(workspace_id: str, job_id: str, task_id: str | None = None) -> None:
    async with _INGEST_SEMAPHORE:
        if task_id:
            claimed = await run_in_threadpool(claim_task, task_id, "upload-ingest-worker")
            if claimed is None:
                try:
                    task = await run_in_threadpool(get_task, task_id)
                    if task.get("cancel_requested") or task.get("status") == "cancel_requested":
                        await run_in_threadpool(update_task, task_id, status="cancelled")
                except (FileNotFoundError, ValueError):
                    pass
                return
        try:
            await run_in_threadpool(run_workspace_ingest_job, workspace_id, job_id)
        except Exception as exc:
            if task_id:
                await run_in_threadpool(
                    update_task,
                    task_id,
                    status="failed",
                    error={"message": "upload ingest worker failed", "error_type": type(exc).__name__},
                )
            # The per-file worker persists failures when possible; this guard keeps
            # an unhandled background exception from terminating the event loop.
            print(f"upload ingest job failed workspace={workspace_id} job={job_id}: {type(exc).__name__}", flush=True)
        else:
            if task_id:
                await run_in_threadpool(
                    update_task,
                    task_id,
                    status="completed",
                    result={"ingest_job_id": job_id},
                )
