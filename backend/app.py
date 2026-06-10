from __future__ import annotations

import json
import os
import uuid
from typing import Any

from fastapi import FastAPI
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.responses import StreamingResponse
from pathlib import Path

try:
    from .chat_loop_primitives import sse
    from .rag import search
    from .schemas import (
        ChatRequest,
        GenerateImageRequest,
        NarrateSummaryRequest,
        RenderPdfRequest,
        SearchPackContextRequest,
        SearchPackContextResponse,
    )
    from .tools.generate_image import generate_image
    from .tools.narrate_summary import narrate_summary
    from .tools.render_pdf import render_pdf_report
except ImportError:
    from chat_loop_primitives import sse
    from rag import search
    from schemas import (
        ChatRequest,
        GenerateImageRequest,
        NarrateSummaryRequest,
        RenderPdfRequest,
        SearchPackContextRequest,
        SearchPackContextResponse,
    )
    from tools.generate_image import generate_image
    from tools.narrate_summary import narrate_summary
    from tools.render_pdf import render_pdf_report


app = FastAPI(title="DataForge Tool Backend", version="0.3.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ARTIFACT_DIR = Path(__file__).resolve().parents[1] / "generated-outputs"


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "dataforge-backend",
        "search_endpoint": bool(os.environ.get("SEARCH_ENDPOINT")),
        "workspace_default": "demo-corpus",
    }


@app.post("/api/search-pack-context", response_model=SearchPackContextResponse)
def search_pack_context(req: SearchPackContextRequest) -> SearchPackContextResponse:
    hits = search(req.workspace_id, req.query, req.top_k)
    return SearchPackContextResponse(
        workspace_id=req.workspace_id,
        query=req.query,
        hits=hits,
        count=len(hits),
        source_index=os.environ.get("SEARCH_INDEX_NAME", "dataforge-workspaces"),
    )


@app.post("/api/render-pdf-report")
def render_pdf(req: RenderPdfRequest) -> dict[str, Any]:
    return render_pdf_report(req.proposal, req.template)


@app.post("/api/generate-image")
def image(req: GenerateImageRequest) -> dict[str, Any]:
    return generate_image(req.prompt, req.size)


@app.get("/api/artifacts/{name}")
def artifact(name: str) -> FileResponse:
    path = ARTIFACT_DIR / name
    return FileResponse(path)


@app.post("/api/narrate-summary")
def narrate(req: NarrateSummaryRequest, request: Request) -> dict[str, Any]:
    result = narrate_summary(req.text, req.voice)
    local_path = result.get("local_path")
    if local_path:
        artifact_url = str(request.url_for("artifact", name=Path(str(local_path)).name))
        if artifact_url.startswith("http://"):
            artifact_url = "https://" + artifact_url.removeprefix("http://")
        result["audio_blob_url"] = artifact_url
    return result


async def _mock_chat(req: ChatRequest):
    conv_id = req.conversation_id or str(uuid.uuid4())
    yield sse("ready", {"conversation_id": conv_id, "workspace_id": req.workspace_id})
    yield sse("user", {"text": req.message})
    yield sse(
        "plan",
        {
            "intent": "product_feasibility",
            "experts": ["corpus-analyst", "feasibility-analyst", "market-researcher", "producer", "auditor"],
            "output_mode": "report",
        },
    )
    yield sse("role_change", {"agent": "corpus-analyst"})
    hits = search(req.workspace_id, req.message, 3)
    yield sse("tool_call", {"name": "search_pack_context", "args": {"workspace_id": req.workspace_id, "query": req.message}})
    yield sse("tool_result", {"name": "search_pack_context", "count": len(hits), "sources": [h["source_file"] for h in hits]})
    yield sse("role_change", {"agent": "auditor"})
    yield sse("audit", {"verdict": "pass" if hits else "revise", "issues": [] if hits else ["No corpus evidence found"]})
    yield sse(
        "final",
        {
            "text": "DataForge found grounded signals for outdoor analytics and hard gaps for medical diagnosis claims.",
            "hits": hits,
        },
    )


@app.post("/api/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    return StreamingResponse(_mock_chat(req), media_type="text/event-stream")
