from __future__ import annotations

import base64
import hashlib
import json
import os
import urllib.error
import struct
import time
import urllib.request
import zlib
from pathlib import Path
from typing import Any

try:
    from ..blob_store import upload_artifact
except ImportError:
    from blob_store import upload_artifact


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "generated-outputs"


def _png(width: int, height: int, rows: list[bytes]) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + row for row in rows)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def _rect(pixels: list[list[tuple[int, int, int]]], x0: int, y0: int, x1: int, y1: int, color: tuple[int, int, int]) -> None:
    width = len(pixels[0])
    height = len(pixels)
    for y in range(max(0, y0), min(height, y1)):
        row = pixels[y]
        for x in range(max(0, x0), min(width, x1)):
            row[x] = color


def _concept_png(prompt: str, width: int = 1024, height: int = 1024) -> bytes:
    digest = hashlib.sha256(prompt.encode("utf-8")).digest()
    accent = (50 + digest[0] % 150, 80 + digest[1] % 120, 90 + digest[2] % 110)
    warm = (220, 197, 132)
    ink = (20, 32, 42)
    paper = (246, 247, 242)
    pixels = [[paper for _ in range(width)] for _ in range(height)]

    for y in range(height):
        blend = y / max(1, height - 1)
        base = (
            int(paper[0] * (1 - blend) + 228 * blend),
            int(paper[1] * (1 - blend) + 236 * blend),
            int(paper[2] * (1 - blend) + 232 * blend),
        )
        for x in range(width):
            pixels[y][x] = base

    _rect(pixels, 74, 92, 950, 226, ink)
    _rect(pixels, 104, 270, 450, 866, (255, 255, 255))
    _rect(pixels, 502, 270, 920, 866, (255, 255, 255))
    _rect(pixels, 126, 304, 428, 334, accent)
    _rect(pixels, 524, 304, 898, 334, warm)

    for i in range(9):
        y = 380 + i * 44
        length = 180 + digest[i] % 130
        _rect(pixels, 128, y, 128 + length, y + 16, (91, 111, 126))
        _rect(pixels, 128, y + 22, 386, y + 30, (205, 214, 215))

    points = [(548, 738), (610, 620), (676, 666), (740, 518), (810, 548), (882, 416)]
    for x, y in points:
        _rect(pixels, x - 9, y - 9, x + 9, y + 9, accent)
    for idx in range(len(points) - 1):
        x0, y0 = points[idx]
        x1, y1 = points[idx + 1]
        steps = max(abs(x1 - x0), abs(y1 - y0))
        for step in range(steps + 1):
            t = step / max(1, steps)
            x = int(x0 * (1 - t) + x1 * t)
            y = int(y0 * (1 - t) + y1 * t)
            _rect(pixels, x - 3, y - 3, x + 3, y + 3, accent)

    for i, height_bar in enumerate([180, 240, 156, 306, 212]):
        x = 552 + i * 64
        _rect(pixels, x, 806 - height_bar, x + 34, 806, (44, 77, 91))
        _rect(pixels, x, 806 - height_bar, x + 34, 806 - height_bar + 14, warm)

    rows = [b"".join(struct.pack("BBB", *pixel) for pixel in row) for row in pixels]
    return _png(width, height, rows)


def generate_image(prompt: str, size: str = "1024x1024") -> dict[str, str | int]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"concept-{int(time.time())}.png"
    width, height = (1024, 1024)
    if size == "1536x1024":
        width, height = (1536, 1024)
    elif size == "1024x1536":
        width, height = (1024, 1536)
    mode = "gpt-image-2"
    image_error = ""
    try:
        image_bytes = _generate_with_gpt_image_2(prompt, size)
    except Exception as exc:
        mode = "deterministic-fallback"
        image_error = f"{type(exc).__name__}: {exc}"[:700]
        image_bytes = _concept_png(prompt, width, height)

    path.write_bytes(image_bytes)
    result: dict[str, str | int] = {
        "concept_image_blob_url": path.as_uri(),
        "artifact_name": path.name,
        "artifact_url": f"/api/artifacts/{path.name}",
        "local_path": str(path),
        "bytes": path.stat().st_size,
        "size": size,
        "mode": mode,
        "image_error": image_error,
    }
    try:
        blob = upload_artifact(path.name, image_bytes, "image/png")
        result["concept_image_blob_url"] = str(blob.get("blob_url") or result["concept_image_blob_url"])
        result["blob_name"] = str(blob.get("blob_name") or "")
    except Exception as exc:
        result["blob_error"] = f"{type(exc).__name__}: {exc}"[:500]
    return result


def _generate_with_gpt_image_2(prompt: str, size: str) -> bytes:
    endpoint = os.environ.get("OPENAI_ENDPOINT")
    if not endpoint:
        raise RuntimeError("Missing OPENAI_ENDPOINT for gpt-image-2")
    deployment = os.environ.get("DF_IMAGE_DEPLOYMENT", "gpt-image-2")
    payload: dict[str, Any] = {
        "model": deployment,
        "prompt": _image_prompt(prompt),
        "size": size,
        "n": 1,
        "quality": os.environ.get("DF_IMAGE_QUALITY", "medium"),
        "output_format": "png",
    }
    url = endpoint.rstrip("/") + "/openai/v1/images/generations?api-version=preview"
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    api_key = os.environ.get("AZURE_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if api_key:
        req.add_header("api-key", api_key)
    else:
        from azure.identity import DefaultAzureCredential

        token = DefaultAzureCredential().get_token("https://cognitiveservices.azure.com/.default")
        req.add_header("Authorization", f"Bearer {token.token}")
    try:
        with urllib.request.urlopen(req, timeout=float(os.environ.get("DF_IMAGE_TIMEOUT_SECONDS", "180"))) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(f"gpt-image-2 HTTP {exc.code}: {body}") from exc
    b64 = ((data.get("data") or [{}])[0] or {}).get("b64_json")
    if not b64:
        raise RuntimeError("gpt-image-2 response did not include b64_json")
    image_bytes = base64.b64decode(b64)
    if not image_bytes.startswith(b"\x89PNG"):
        raise RuntimeError("gpt-image-2 output was not PNG")
    return image_bytes


def _image_prompt(prompt: str) -> str:
    return (
        "Create a polished enterprise product concept image for a data product proposal. "
        "Show a realistic dashboard/workflow scene with evidence cards, confidence labels, "
        "market signals, and business stakeholders reviewing the result. Avoid readable UI microtext; "
        "make it suitable as a proposal cover image. Context: "
        + str(prompt or "")[:2600]
    )
