from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

try:
    from .orchestrator import orchestrate_chat
    from .schemas import ChatRequest
except ImportError:
    from orchestrator import orchestrate_chat
    from schemas import ChatRequest


def _parse_sse(frame: str) -> tuple[str, Any] | None:
    event = None
    data = None
    for line in frame.splitlines():
        if line.startswith("event: "):
            event = line.removeprefix("event: ")
        elif line.startswith("data: "):
            raw = line.removeprefix("data: ")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = raw
    return (event, data) if event else None


async def main() -> int:
    req = ChatRequest(
        workspace_id="demo-corpus",
        message="Create a full package with PDF, concept image, and audio for a data product from this workspace.",
    )
    events: list[tuple[str, Any]] = []
    async for frame in orchestrate_chat(req):
        parsed = _parse_sse(frame)
        if parsed:
            events.append(parsed)

    final = next(data for event, data in events if event == "final")
    proposal = final["artifact"]["proposal"]
    pdf = Path(proposal["pdf"]["local_path"])
    image = Path(proposal["concept_image"]["local_path"])
    audio = Path(proposal["audio_summary"]["local_path"])

    assert proposal["artifact_urls"]["pdf"], "Missing PDF artifact URL"
    assert proposal["artifact_urls"]["concept_image"], "Missing image artifact URL"
    assert proposal["artifact_urls"]["audio_summary"], "Missing audio artifact URL"
    assert pdf.read_bytes().startswith(b"%PDF"), "PDF header mismatch"
    assert image.read_bytes().startswith(b"\x89PNG"), "PNG header mismatch"
    assert audio.read_bytes().startswith(b"RIFF"), "WAV header mismatch"
    assert proposal["audio_summary"]["bytes"] > 1000, "Audio too small"
    assert any(event == "tool_result" and data.get("name") == "render_pdf_report" for event, data in events)
    assert any(event == "tool_result" and data.get("name") == "generate_image" for event, data in events)
    assert any(event == "tool_result" and data.get("name") == "narrate_summary" for event, data in events)

    print(
        json.dumps(
            {
                "ok": True,
                "events": [event for event, _ in events],
                "artifact_urls": proposal["artifact_urls"],
                "bytes": {
                    "pdf": proposal["pdf"]["bytes"],
                    "concept_image": proposal["concept_image"]["bytes"],
                    "audio_summary": proposal["audio_summary"]["bytes"],
                },
                "audio_mode": proposal["audio_summary"]["mode"],
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
