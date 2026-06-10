from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "generated-outputs"


def _minimal_pdf(text: str) -> bytes:
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 11 Tf 50 780 Td 14 TL ({safe[:2600]}) Tj ET"
    objects = [
        "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj",
        "2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj",
        "3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj",
        "4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj",
        f"5 0 obj << /Length {len(stream.encode('utf-8'))} >> stream\n{stream}\nendstream endobj",
    ]
    body = "%PDF-1.4\n"
    offsets = [0]
    for obj in objects:
        offsets.append(len(body.encode("utf-8")))
        body += obj + "\n"
    xref_at = len(body.encode("utf-8"))
    body += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    for off in offsets[1:]:
        body += f"{off:010d} 00000 n \n"
    body += f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    return body.encode("utf-8")


def render_pdf_report(proposal: dict[str, Any], template: str = "project_proposal") -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = int(time.time())
    path = OUT_DIR / f"{proposal.get('opportunity_id', 'proposal')}-{stamp}.pdf"
    text = f"DataForge {template}\n\n{json.dumps(proposal, indent=2, ensure_ascii=False)}"
    path.write_bytes(_minimal_pdf(text))
    return {
        "pdf_blob_url": path.as_uri(),
        "artifact_name": path.name,
        "artifact_url": f"/api/artifacts/{path.name}",
        "bytes": path.stat().st_size,
        "local_path": str(path),
    }
