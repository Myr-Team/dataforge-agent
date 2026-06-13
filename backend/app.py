from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI
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
    from .blob_store import download_artifact
    from .conversation_store import get_conversation, list_conversations
    from .dependency_health import health_dependencies, health_dependency_details
    from .orchestrator import orchestrate_chat, produce_from_existing_report
    from .rag import search
    from .run_store import get_run, list_runs
    from .tracing import configure_monitoring
    from .workspace_store import create_workspace_from_uploads, delete_workspace, get_reference_image_content, get_workspace_detail, list_workspaces
    from .schemas import (
        ChatRequest,
        ConversationDetailResponse,
        ConversationsResponse,
        GenerateImageRequest,
        NarrateSummaryRequest,
        ProduceRequest,
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
    from blob_store import download_artifact
    from conversation_store import get_conversation, list_conversations
    from dependency_health import health_dependencies, health_dependency_details
    from orchestrator import orchestrate_chat, produce_from_existing_report
    from rag import search
    from run_store import get_run, list_runs
    from tracing import configure_monitoring
    from workspace_store import create_workspace_from_uploads, delete_workspace, get_reference_image_content, get_workspace_detail, list_workspaces
    from schemas import (
        ChatRequest,
        ConversationDetailResponse,
        ConversationsResponse,
        GenerateImageRequest,
        NarrateSummaryRequest,
        ProduceRequest,
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

ARTIFACT_DIR = Path(__file__).resolve().parents[1] / "generated-outputs"


@app.get("/api/health")
async def health() -> dict[str, Any]:
    dependencies = health_dependencies()
    return {
        "ok": True,
        "service": "dataforge-backend",
        "search_endpoint": bool(os.environ.get("SEARCH_ENDPOINT")),
        "workspace_default": "demo-corpus",
        "dependencies": dependencies,
        "dependency_details": health_dependency_details(),
    }


@app.post("/api/search-pack-context", response_model=SearchPackContextResponse)
async def search_pack_context(req: SearchPackContextRequest) -> SearchPackContextResponse:
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
    file: list[UploadFile] = File(...),
    name: str | None = Form(default=None),
    description: str | None = Form(default=None),
    workspace_id: str | None = Form(default=None),
    asset_role: str | None = Form(default=None),
) -> UploadResponse:
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
        result = await run_in_threadpool(
            create_workspace_from_uploads,
            files=files,
            name=name,
            description=description,
            requested_workspace_id=workspace_id,
            asset_role=asset_role,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Workspace not found: {workspace_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return UploadResponse.model_validate(result)


@app.get("/api/workspaces", response_model=WorkspacesResponse)
async def workspaces() -> WorkspacesResponse:
    return WorkspacesResponse(workspaces=await run_in_threadpool(list_workspaces))


@app.get("/api/workspaces/{workspace_id}", response_model=WorkspaceDetailResponse)
async def workspace_detail(workspace_id: str) -> WorkspaceDetailResponse:
    try:
        result = await run_in_threadpool(get_workspace_detail, workspace_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Workspace not found: {workspace_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return WorkspaceDetailResponse.model_validate(result)


@app.get("/api/workspaces/{workspace_id}/dashboard", response_model=WorkspaceDashboardResponse)
async def workspace_dashboard(workspace_id: str) -> WorkspaceDashboardResponse:
    def _load() -> dict[str, Any]:
        dependencies = health_dependencies()
        return {
            "workspace_id": workspace_id,
            "workspace": get_workspace_detail(workspace_id),
            "workspaces": list_workspaces(),
            "runs": list_runs(workspace_id)[:12],
            "conversations": list_conversations(workspace_id)[:12],
            "health": {
                "ok": True,
                "service": "dataforge-backend",
                "search_endpoint": bool(os.environ.get("SEARCH_ENDPOINT")),
                "workspace_default": "demo-corpus",
                "dependencies": dependencies,
            },
            "dependency_details": health_dependency_details(),
        }

    try:
        result = await run_in_threadpool(_load)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Workspace not found: {workspace_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return WorkspaceDashboardResponse.model_validate(result)


@app.get("/api/workspaces/{workspace_id}/manifest")
async def workspace_manifest(workspace_id: str) -> dict[str, Any]:
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
        ui_context={
            **(body.get("ui_context") if isinstance(body.get("ui_context"), dict) else {}),
            "entrypoint": "workspace_dashboard",
            "auto_analyze": True,
            "cache_bust": cache_bust,
        },
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
async def workspace_reference_image(workspace_id: str, filename: str) -> Response:
    result = await run_in_threadpool(get_reference_image_content, workspace_id, filename)
    if not result:
        raise HTTPException(status_code=404, detail=f"Reference image not found: {filename}")
    content, content_type = result
    return Response(content=content, media_type=content_type)


@app.delete("/api/workspaces/{workspace_id}", response_model=WorkspaceDeleteResponse)
async def remove_workspace(workspace_id: str) -> WorkspaceDeleteResponse:
    try:
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
def artifact(name: str) -> Response:
    safe_name = Path(name).name
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


@app.post("/api/produce")
async def produce(req: ProduceRequest) -> dict[str, Any]:
    return await run_in_threadpool(produce_from_existing_report, req.model_dump())


@app.get("/api/runs", response_model=RunsResponse)
async def runs(workspace_id: str | None = None) -> RunsResponse:
    return RunsResponse(runs=await run_in_threadpool(list_runs, workspace_id))


@app.get("/api/runs/{run_id}", response_model=RunDetailResponse)
async def run_detail(run_id: str) -> RunDetailResponse:
    try:
        result = await run_in_threadpool(get_run, run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}") from exc
    return RunDetailResponse.model_validate(result)


@app.get("/api/conversations", response_model=ConversationsResponse)
async def conversations(workspace_id: str | None = None) -> ConversationsResponse:
    return ConversationsResponse(conversations=await run_in_threadpool(list_conversations, workspace_id))


@app.get("/api/conversations/{conversation_id}", response_model=ConversationDetailResponse)
async def conversation_detail(conversation_id: str) -> ConversationDetailResponse:
    try:
        result = await run_in_threadpool(get_conversation, conversation_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Conversation not found: {conversation_id}") from exc
    return ConversationDetailResponse.model_validate(result)


@app.post("/api/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    return StreamingResponse(
        orchestrate_chat(req),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
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
