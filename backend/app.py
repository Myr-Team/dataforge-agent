from __future__ import annotations

import json
import os
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
    from .workspace_store import create_workspace_from_uploads, delete_workspace, get_workspace_detail, list_workspaces
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
    from workspace_store import create_workspace_from_uploads, delete_workspace, get_workspace_detail, list_workspaces
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
    return await run_in_threadpool(generate_image, req.prompt, req.size)


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
