# Reuse Manifest

| Source | Target | Adaptation |
|---|---|---|
| `api/main.py` | `backend/app.py` | Preserve health, RAG, artifact, image, and SSE endpoint patterns; replace pack terminology with workspace terminology. |
| `api/chat_loop.py` | `backend/chat_loop_primitives.py` | Preserve SSE formatting, heartbeat, function-call loop summaries, and tool-output handling primitives. |
| `functions/agent/rag.py` | `backend/rag.py` | Adapt `pack_id` filter to `workspace_id` and add local corpus fallback for offline acceptance. |
| `api/image_generator.py` | `backend/tools/generate_image.py` | Preserve Azure token-based image generation shape with deterministic local fallback. |
| `scripts/build_index.py` | `ingest/build_index.py` | Rebuild for markdown workspace ingestion and `workspace_id` fields. |
| `web/*` | `web/*` | Rebuild into a three-column DataForge trace workbench. |

