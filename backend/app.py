from __future__ import annotations

import json
import os
from typing import Any

from fastapi import FastAPI
from fastapi import File
from fastapi import Form
from fastapi import Request
from fastapi import UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.responses import StreamingResponse
from pathlib import Path
from starlette.concurrency import run_in_threadpool

try:
    from .orchestrator import orchestrate_chat
    from .rag import search
    from .tracing import configure_monitoring
    from .workspace_store import create_workspace_from_upload, list_workspaces
    from .schemas import (
        ChatRequest,
        GenerateImageRequest,
        NarrateSummaryRequest,
        RenderPdfRequest,
        SearchPackContextRequest,
        SearchPackContextResponse,
        UploadResponse,
        WorkspacesResponse,
    )
    from .tools.generate_image import generate_image
    from .tools.narrate_summary import narrate_summary
    from .tools.render_pdf import render_pdf_report
except ImportError:
    from orchestrator import orchestrate_chat
    from rag import search
    from tracing import configure_monitoring
    from workspace_store import create_workspace_from_upload, list_workspaces
    from schemas import (
        ChatRequest,
        GenerateImageRequest,
        NarrateSummaryRequest,
        RenderPdfRequest,
        SearchPackContextRequest,
        SearchPackContextResponse,
        UploadResponse,
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
    return {
        "ok": True,
        "service": "dataforge-backend",
        "search_endpoint": bool(os.environ.get("SEARCH_ENDPOINT")),
        "workspace_default": "demo-corpus",
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
    file: UploadFile = File(...),
    name: str | None = Form(default=None),
    workspace_id: str | None = Form(default=None),
) -> UploadResponse:
    content = await file.read()
    result = await run_in_threadpool(
        create_workspace_from_upload,
        filename=file.filename or "upload",
        content=content,
        content_type=file.content_type,
        name=name,
        requested_workspace_id=workspace_id,
    )
    return UploadResponse.model_validate(result)


@app.get("/api/workspaces", response_model=WorkspacesResponse)
async def workspaces() -> WorkspacesResponse:
    return WorkspacesResponse(workspaces=await run_in_threadpool(list_workspaces))


@app.post("/api/render-pdf-report")
async def render_pdf(req: RenderPdfRequest) -> dict[str, Any]:
    return await run_in_threadpool(render_pdf_report, req.proposal, req.template)


@app.post("/api/generate-image")
async def image(req: GenerateImageRequest) -> dict[str, Any]:
    return await run_in_threadpool(generate_image, req.prompt, req.size)


@app.get("/api/artifacts/{name}")
def artifact(name: str) -> FileResponse:
    path = ARTIFACT_DIR / name
    return FileResponse(path)


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


@app.post("/api/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    return StreamingResponse(orchestrate_chat(req), media_type="text/event-stream")
