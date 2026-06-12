from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import tempfile
import time
import zlib
from pathlib import Path
from typing import Any

import requests
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "batch8_reference_image_eval.json"
ENV_CANDIDATES = [
    Path(r"C:\Users\12140\.dataforge-codex.env"),
    ROOT.parent / "数据产品化Agent" / ".dataforge-codex.env",
]

sys.path.insert(0, str(ROOT))


class Harness:
    def __init__(self, api_base: str | None = None) -> None:
        self.api_base = api_base.rstrip("/") if api_base else None
        if self.api_base:
            self.client = None
        else:
            from backend.app import app

            self.client = TestClient(app)

    def upload_image(self, path: Path, *, name: str, asset_role: str = "logo", workspace_id: str | None = None) -> dict[str, Any]:
        data = {"name": name, "asset_role": asset_role}
        if workspace_id:
            data["workspace_id"] = workspace_id
        with path.open("rb") as handle:
            files = [("file", (path.name, handle, "image/png"))]
            if self.api_base:
                response = requests.post(f"{self.api_base}/api/upload", data=data, files=files, timeout=180)
            else:
                response = self.client.post("/api/upload", data=data, files=files)  # type: ignore[union-attr]
        response.raise_for_status()
        return response.json()

    def get(self, path: str) -> Any:
        if self.api_base:
            response = requests.get(f"{self.api_base}{path}", timeout=120)
        else:
            response = self.client.get(path)  # type: ignore[union-attr]
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            return response.json()
        return response.content

    def post_json(self, path: str, payload: dict[str, Any], timeout: int = 240) -> dict[str, Any]:
        if self.api_base:
            response = requests.post(f"{self.api_base}{path}", json=payload, timeout=timeout)
        else:
            response = self.client.post(path, json=payload)  # type: ignore[union-attr]
        response.raise_for_status()
        return response.json()

    def delete(self, workspace_id: str) -> dict[str, Any]:
        if self.api_base:
            response = requests.delete(f"{self.api_base}/api/workspaces/{workspace_id}", timeout=120)
        else:
            response = self.client.delete(f"/api/workspaces/{workspace_id}")  # type: ignore[union-attr]
        response.raise_for_status()
        return response.json()


def _load_env() -> list[str]:
    loaded: list[str] = []
    for path in ENV_CANDIDATES:
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line.removeprefix("export ").strip()
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        loaded.append(str(path))
    return loaded


def _prepare_local_env() -> None:
    os.environ.setdefault("DF_COORDINATOR_ALLOW_DETERMINISTIC_FALLBACK", "1")
    os.environ.setdefault("DF_DISABLE_VECTOR_SEARCH", "1")
    for key in ("SEARCH_ENDPOINT", "SEARCH_KEY", "DF_SEARCH_SERVICE", "AZURE_OPENAI_API_KEY", "OPENAI_API_KEY", "OPENAI_ENDPOINT"):
        os.environ.pop(key, None)


def _logo_png(path: Path, accent: tuple[int, int, int]) -> Path:
    width = height = 256
    paper = (250, 250, 248)
    dark = (24, 32, 44)
    pixels = [[paper for _ in range(width)] for _ in range(height)]
    for y in range(36, 220):
        for x in range(36, 220):
            if (x - 128) ** 2 + (y - 128) ** 2 < 86**2:
                pixels[y][x] = accent
    for y in range(108, 150):
        for x in range(70, 186):
            pixels[y][x] = dark
    rows = [b"".join(struct.pack("BBB", *pixel) for pixel in row) for row in pixels]
    raw = b"".join(b"\x00" + row for row in rows)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    payload = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(payload)
    return path


def _artifact_bytes(harness: Harness, artifact_url: str | None) -> bytes:
    if not artifact_url:
        return b""
    return harness.get(artifact_url if artifact_url.startswith("/") else "/" + artifact_url)


def run(api_base: str | None = None, cleanup: bool = True, full_produce: bool = False) -> dict[str, Any]:
    loaded_env = _load_env() if api_base else []
    if not api_base:
        _prepare_local_env()
    harness = Harness(api_base)
    workspace_ids: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        logo = _logo_png(Path(tmp) / "batch8-logo.png", (32, 145, 220))
        upload = harness.upload_image(logo, name=f"batch8 logo workspace {int(time.time())}", asset_role="logo")
        workspace_id = upload["workspace_id"]
        workspace_ids.append(workspace_id)
        detail = harness.get(f"/api/workspaces/{workspace_id}")
        reference_images = detail.get("reference_images") or []
        image_bytes = harness.get(reference_images[0]["url"]) if reference_images else b""

        image_result = harness.post_json(
            "/api/generate-image",
            {
                "prompt": "生成一张带有上传 logo 视觉元素的数据产品活动概念图",
                "size": "1024x1024",
                "reference_image_urls": [reference_images[0]["url"]] if reference_images else [],
            },
            timeout=300,
        )
        concept_bytes = _artifact_bytes(harness, image_result.get("artifact_url"))

        produce_result: dict[str, Any] = {}
        produce_bytes = b""
        if full_produce:
            produce_result = harness.post_json(
                "/api/produce",
                {
                    "workspace_id": workspace_id,
                    "feasibility": {
                        "opportunity_id": "Batch8 Logo Concept",
                        "dimensions": [],
                        "verdict": "conditional",
                        "overall_confidence": "data_confirmed",
                        "gap_list": ["需要继续验证活动转化。"],
                    },
                    "corpus": {"hits": []},
                    "market": {},
                    "audit": {"verdict": "pass", "issues": [], "target_expert": None},
                    "answer": {"text": "基于参考 logo 生成概念图。"},
                },
                timeout=420,
            )
            produce_bytes = _artifact_bytes(harness, (produce_result.get("artifact_urls") or {}).get("concept_image"))

    delete_results: dict[str, Any] = {}
    if cleanup:
        for workspace_id in workspace_ids:
            try:
                delete_results[workspace_id] = harness.delete(workspace_id)
            except Exception as exc:
                delete_results[workspace_id] = {"error": f"{type(exc).__name__}: {exc}"}

    checks = {
        "reference_registered": bool(reference_images and reference_images[0].get("role") == "logo"),
        "reference_proxy_png": bool(image_bytes.startswith(b"\x89PNG")),
        "generate_image_returned_png": bool(concept_bytes.startswith(b"\x89PNG")),
        "generate_image_reference_count": int(image_result.get("reference_image_count") or 0) >= 1,
        "generate_image_mode_valid": image_result.get("mode") in {"gpt-image-2-edit", "gpt-image-2", "deterministic-fallback"},
        "produce_reference_edit_mode": (not full_produce)
        or ((produce_result.get("concept_image") or {}).get("mode") in {"gpt-image-2-edit", "gpt-image-2", "deterministic-fallback"}),
        "produce_concept_png": (not full_produce) or bool(produce_bytes.startswith(b"\x89PNG")),
    }
    if api_base:
        checks["generate_image_used_edit_mode"] = image_result.get("mode") == "gpt-image-2-edit"
        if full_produce:
            checks["produce_used_edit_mode"] = (produce_result.get("concept_image") or {}).get("mode") == "gpt-image-2-edit"

    result = {
        "ok": all(checks.values()),
        "api_base": api_base,
        "loaded_env": loaded_env,
        "checks": checks,
        "workspace_id": workspace_ids[0] if workspace_ids else None,
        "reference_images": reference_images,
        "image_result": {
            "mode": image_result.get("mode"),
            "bytes": image_result.get("bytes"),
            "reference_image_count": image_result.get("reference_image_count"),
            "image_error": image_result.get("image_error"),
        },
        "produce_result": {
            "mode": (produce_result.get("concept_image") or {}).get("mode"),
            "bytes": (produce_result.get("concept_image") or {}).get("bytes"),
            "image_error": (produce_result.get("concept_image") or {}).get("image_error"),
        }
        if produce_result
        else {},
        "delete_results": delete_results,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-base")
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--full-produce", action="store_true")
    args = parser.parse_args()
    result = run(args.api_base, cleanup=not args.keep, full_produce=args.full_produce)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
