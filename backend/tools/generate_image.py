from __future__ import annotations

import base64
import os
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "generated-outputs"

_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def generate_image(prompt: str, size: str = "1024x1024") -> dict[str, str | int]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"concept-{int(time.time())}.png"
    path.write_bytes(_ONE_PIXEL_PNG)
    return {
        "concept_image_blob_url": path.as_uri(),
        "local_path": str(path),
        "bytes": len(_ONE_PIXEL_PNG),
        "size": size,
        "mode": "local-placeholder",
    }

