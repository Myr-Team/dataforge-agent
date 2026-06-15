from __future__ import annotations

import base64
import hashlib
import json
import os
import urllib.error
import struct
import time
import uuid
import urllib.request
import zlib
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

try:
    from ..blob_store import download_blob_content, upload_artifact
except ImportError:
    from blob_store import download_blob_content, upload_artifact


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


def generate_image(
    prompt: str,
    size: str = "1024x1024",
    reference_image_urls: list[str] | None = None,
    overlay_title: str | None = None,
    logo_url: str | None = None,
) -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"concept-{int(time.time())}.png"
    width, height = (1024, 1024)
    if size == "1536x1024":
        width, height = (1536, 1024)
    elif size == "1024x1536":
        width, height = (1024, 1536)
    mode = "gpt-image-2"
    image_error = ""
    reference_image_urls = [str(item) for item in (reference_image_urls or []) if str(item or "").strip()]
    reference_inputs = _load_reference_images(reference_image_urls[:3])
    try:
        if reference_inputs:
            image_bytes = _edit_with_gpt_image_2(prompt, size, reference_inputs)
            mode = "gpt-image-2-edit"
        else:
            image_bytes = _generate_with_gpt_image_2(prompt, size)
    except Exception as exc:
        image_error = f"{type(exc).__name__}: {exc}"[:700]
        try:
            image_bytes = _generate_with_gpt_image_2(prompt, size)
            mode = "gpt-image-2"
        except Exception as gen_exc:
            mode = "deterministic-fallback"
            image_error = f"{image_error}; generation fallback failed: {type(gen_exc).__name__}: {gen_exc}"[:900]
            image_bytes = _concept_png(prompt, width, height)

    # 生成后合成 logo + 标题（成品封面感）；任何失败都回退原图，不影响产出
    overlay_mode = ""
    if overlay_title or logo_url:
        logo_bytes = None
        if logo_url:
            try:
                loaded = _load_reference_image(logo_url)
                if loaded:
                    logo_bytes = bytes(loaded.get("content") or b"")
            except Exception:
                logo_bytes = None
        composited, overlay_mode = _composite_overlay(image_bytes, overlay_title, logo_bytes)
        image_bytes = composited

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
        "reference_image_count": len(reference_inputs),
        "overlay": overlay_mode,
    }
    try:
        blob = upload_artifact(path.name, image_bytes, "image/png")
        result["concept_image_blob_url"] = str(blob.get("blob_url") or result["concept_image_blob_url"])
        result["blob_name"] = str(blob.get("blob_name") or "")
    except Exception as exc:
        result["blob_error"] = f"{type(exc).__name__}: {exc}"[:500]
    return result


def _load_reference_images(urls: list[str]) -> list[dict[str, Any]]:
    images: list[dict[str, Any]] = []
    errors: list[str] = []
    for url in urls:
        try:
            loaded = _load_reference_image(url)
            if loaded:
                images.append(loaded)
        except Exception as exc:
            errors.append(f"{url[:80]} => {type(exc).__name__}: {exc}")
    if urls and not images:
        raise RuntimeError("No reference image could be loaded: " + "; ".join(errors)[:600])
    return images


def _load_reference_image(url: str) -> dict[str, Any] | None:
    value = str(url or "").strip()
    if not value:
        return None
    if value.startswith("/api/workspaces/"):
        parts = [unquote(part) for part in value.split("/") if part]
        if len(parts) >= 5 and parts[0] == "api" and parts[1] == "workspaces" and parts[3] == "reference-images":
            try:
                from ..workspace_store import get_reference_image_content
            except ImportError:
                from workspace_store import get_reference_image_content

            downloaded = get_reference_image_content(parts[2], parts[4])
            if downloaded:
                content, content_type = downloaded
                return {"filename": parts[4], "content": content, "content_type": content_type}
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"}:
        blob_loaded = _load_azure_blob_url(parsed)
        if blob_loaded:
            return blob_loaded
        with urllib.request.urlopen(value, timeout=45) as resp:
            content = resp.read()
            content_type = resp.headers.get("Content-Type") or _guess_image_type(parsed.path)
            return {"filename": Path(parsed.path).name or "reference.png", "content": content, "content_type": content_type}
    if parsed.scheme == "file":
        path = Path(unquote(parsed.path))
        return {"filename": path.name, "content": path.read_bytes(), "content_type": _guess_image_type(path.name)}
    path = Path(value)
    if path.exists() and path.is_file():
        return {"filename": path.name, "content": path.read_bytes(), "content_type": _guess_image_type(path.name)}
    return None


def _load_azure_blob_url(parsed: Any) -> dict[str, Any] | None:
    if ".blob.core.windows.net" not in str(parsed.netloc):
        return None
    parts = [unquote(part) for part in str(parsed.path).lstrip("/").split("/") if part]
    if len(parts) < 2:
        return None
    blob_name = "/".join(parts[1:])
    downloaded = download_blob_content(blob_name)
    if not downloaded:
        return None
    content, content_type = downloaded
    return {"filename": Path(blob_name).name, "content": content, "content_type": content_type}


def _guess_image_type(name: str) -> str:
    suffix = Path(name).suffix.lower()
    if suffix == ".png":
        return "image/png"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    return "image/png"


def _edit_with_gpt_image_2(prompt: str, size: str, images: list[dict[str, Any]]) -> bytes:
    endpoint = os.environ.get("OPENAI_ENDPOINT")
    if not endpoint:
        raise RuntimeError("Missing OPENAI_ENDPOINT for gpt-image-2 edits")
    deployment = os.environ.get("DF_IMAGE_DEPLOYMENT", "gpt-image-2")
    fields: dict[str, str] = {
        "model": deployment,
        "prompt": _image_prompt(prompt),
        "size": size,
        "n": "1",
    }
    quality = os.environ.get("DF_IMAGE_QUALITY")
    if quality:
        fields["quality"] = quality
    files = [
        (
            "image",
            str(item.get("filename") or f"reference-{idx}.png"),
            bytes(item.get("content") or b""),
            str(item.get("content_type") or _guess_image_type(str(item.get("filename") or ""))),
        )
        for idx, item in enumerate(images, start=1)
        if item.get("content")
    ]
    if not files:
        raise RuntimeError("No reference image bytes available for gpt-image-2 edits")
    body, content_type = _multipart_body(fields, files)
    url = endpoint.rstrip("/") + "/openai/v1/images/edits?api-version=preview"
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", content_type)
    _add_openai_auth(req)
    try:
        with urllib.request.urlopen(req, timeout=float(os.environ.get("DF_IMAGE_TIMEOUT_SECONDS", "180"))) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(f"gpt-image-2 edits HTTP {exc.code}: {body_text}") from exc
    return _decode_image_response(data, "gpt-image-2 edits")


def _multipart_body(fields: dict[str, str], files: list[tuple[str, str, bytes, str]]) -> tuple[bytes, str]:
    boundary = f"----dataforge-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
                str(value).encode("utf-8"),
                b"\r\n",
            ]
        )
    for field, filename, content, content_type in files:
        safe_name = Path(filename or "reference.png").name
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{field}"; filename="{safe_name}"\r\n'.encode("utf-8"),
                f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"),
                content,
                b"\r\n",
            ]
        )
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


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
    _add_openai_auth(req)
    try:
        with urllib.request.urlopen(req, timeout=float(os.environ.get("DF_IMAGE_TIMEOUT_SECONDS", "180"))) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(f"gpt-image-2 HTTP {exc.code}: {body}") from exc
    return _decode_image_response(data, "gpt-image-2")


def _add_openai_auth(req: urllib.request.Request) -> None:
    api_key = os.environ.get("AZURE_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if api_key:
        req.add_header("api-key", api_key)
    else:
        from azure.identity import DefaultAzureCredential

        token = DefaultAzureCredential().get_token("https://cognitiveservices.azure.com/.default")
        req.add_header("Authorization", f"Bearer {token.token}")


def _decode_image_response(data: dict[str, Any], label: str) -> bytes:
    b64 = ((data.get("data") or [{}])[0] or {}).get("b64_json")
    if not b64:
        raise RuntimeError(f"{label} response did not include b64_json")
    image_bytes = base64.b64decode(b64)
    if not image_bytes.startswith(b"\x89PNG"):
        raise RuntimeError(f"{label} output was not PNG")
    return image_bytes


_CJK_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
]


def _cjk_font_path() -> str | None:
    for candidate in _CJK_FONT_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    return None


def _wrap_text(draw: Any, text: str, font: Any, max_w: float) -> list[str]:
    lines: list[str] = []
    cur = ""
    for ch in str(text or ""):
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        if draw.textlength(cur + ch, font=font) > max_w and cur:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def _composite_overlay(image_bytes: bytes, title: str | None, logo_bytes: bytes | None) -> tuple[bytes, str]:
    """在生成图上合成 logo + 标题 + DataForge 字标。失败回退原图。返回 (bytes, overlay_mode)。"""
    try:
        from io import BytesIO

        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return image_bytes, "no_pillow"
    try:
        base = Image.open(BytesIO(image_bytes)).convert("RGBA")
        W, H = base.size
        font_path = _cjk_font_path()
        if not font_path:
            return image_bytes, "no_cjk_font"
        t_size = max(34, int(W * 0.050))
        s_size = max(15, int(W * 0.019))
        title_font = ImageFont.truetype(font_path, t_size)
        sub_font = ImageFont.truetype(font_path, s_size)

        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        # 底部渐变压暗带，保证文字可读
        band_h = int(H * 0.32)
        for i in range(band_h):
            alpha = int(205 * (i / band_h) ** 1.25)
            draw.line([(0, H - band_h + i), (W, H - band_h + i)], fill=(8, 14, 24, alpha))
        # 顶部细蓝条
        draw.rectangle([0, 0, W, max(4, int(H * 0.008))], fill=(0, 113, 227, 255))

        pad = int(W * 0.05)
        mode = "title"
        title_clean = str(title or "").strip()
        lines = _wrap_text(draw, title_clean, title_font, W - 2 * pad)[:2] if title_clean else []
        y = H - pad - len(lines) * (t_size + 8)
        draw.text((pad, y - s_size - 12), "DataForge · 数据产品可行性建议书", font=sub_font, fill=(206, 226, 255, 240))
        for line in lines:
            draw.text((pad, y), line, font=title_font, fill=(255, 255, 255, 255))
            y += t_size + 8

        if logo_bytes:
            try:
                logo = Image.open(BytesIO(logo_bytes)).convert("RGBA")
                lh = max(56, int(H * 0.10))
                lw = max(1, int(logo.width * lh / max(1, logo.height)))
                logo = logo.resize((lw, lh))
                chip_pad = int(lh * 0.20)
                chip = Image.new("RGBA", (lw + 2 * chip_pad, lh + 2 * chip_pad), (255, 255, 255, 235))
                overlay.alpha_composite(chip, (pad, pad))
                overlay.alpha_composite(logo, (pad + chip_pad, pad + chip_pad))
                mode = "title+logo"
            except Exception:
                pass

        out = Image.alpha_composite(base, overlay).convert("RGB")
        buffer = BytesIO()
        out.save(buffer, format="PNG")
        return buffer.getvalue(), mode
    except Exception as exc:
        return image_bytes, f"error:{type(exc).__name__}"


def _image_prompt(prompt: str) -> str:
    return (
        "Create the requested proposal concept image exactly following the deliverable type and reference asset instructions. "
        "If a reference image or logo is provided, integrate it visibly while preserving brand shape and color. "
        "Use polished commercial art direction suitable for a demo proposal cover. "
        "Avoid conference tables, generic office review scenes, people gathered around screens, handshakes, and unrelated corporate stock imagery. "
        "Avoid readable UI microtext. Brief: "
        + str(prompt or "")[:2600]
    )
