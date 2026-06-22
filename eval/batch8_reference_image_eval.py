from __future__ import annotations

import argparse
import hashlib
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


def _blob_backed_reference_regression() -> dict[str, Any]:
    """Prove reference images can load from Blob without local workspace state."""
    from backend import workspace_store

    workspace_id = "upload-batch8-blob-only-regression"
    filename = "banana_logo.png"
    blob_name = f"workspaces/{workspace_id}/reference_images/{filename}"
    with tempfile.TemporaryDirectory() as tmp:
        content = _logo_png(Path(tmp) / filename, (32, 145, 220)).read_bytes()
        meta = {
            "workspace_id": workspace_id,
            "name": "Batch8 blob-only regression",
            "reference_images": [
                {
                    "url": f"/api/workspaces/{workspace_id}/reference-images/{filename}",
                    "blob_url": f"https://yourstorageacct.blob.core.windows.net/dataforge-workspaces/{blob_name}",
                    "blob_name": blob_name,
                    "role": "logo",
                    "filename": filename,
                    "source_file": f"reference_images/{filename}",
                    "content_type": "image/png",
                    "bytes": len(content),
                }
            ],
        }
        calls: list[dict[str, str]] = []
        old_workspaces = workspace_store.WORKSPACES
        old_download_content = workspace_store.download_blob_content
        old_download_json = workspace_store.download_blob_json
        old_get_registry = workspace_store.get_registry_workspace
        old_load_bundle = workspace_store._load_workspace_bundle

        def fake_download_content(candidate: str) -> tuple[bytes, str] | None:
            calls.append({"download_blob_content": candidate})
            if candidate == blob_name:
                return content, "image/png"
            return None

        def fake_download_json(candidate: str) -> dict[str, Any] | None:
            calls.append({"download_blob_json": candidate})
            if candidate == f"workspaces/{workspace_id}/workspace.json":
                return meta
            return None

        def fake_get_registry(candidate: str) -> dict[str, Any] | None:
            calls.append({"get_registry_workspace": candidate})
            return meta if candidate == workspace_id else None

        def fail_local_bundle(candidate: str) -> None:
            raise AssertionError(f"local bundle should not be required: {candidate}")

        try:
            workspace_store.WORKSPACES = Path(tmp) / "missing-local-workspaces"
            workspace_store.download_blob_content = fake_download_content
            workspace_store.download_blob_json = fake_download_json
            workspace_store.get_registry_workspace = fake_get_registry
            workspace_store._load_workspace_bundle = fail_local_bundle
            listed = workspace_store.workspace_reference_images(workspace_id)
            downloaded = workspace_store.get_reference_image_content(workspace_id, filename)
        finally:
            workspace_store.WORKSPACES = old_workspaces
            workspace_store.download_blob_content = old_download_content
            workspace_store.download_blob_json = old_download_json
            workspace_store.get_registry_workspace = old_get_registry
            workspace_store._load_workspace_bundle = old_load_bundle

    content_bytes, content_type = downloaded or (b"", "")
    ok = bool(listed and content_bytes.startswith(b"\x89PNG") and content_type == "image/png")
    return {
        "ok": ok,
        "blob_name": blob_name,
        "listed_count": len(listed),
        "content_type": content_type,
        "bytes": len(content_bytes),
        "calls": calls,
    }


def run(api_base: str | None = None, cleanup: bool = True, full_produce: bool = False) -> dict[str, Any]:
    loaded_env = _load_env() if api_base else []
    if not api_base:
        _prepare_local_env()
    blob_regression = _blob_backed_reference_regression()
    harness = Harness(api_base)
    workspace_ids: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        logo = _logo_png(Path(tmp) / "batch8-logo.png", (32, 145, 220))
        upload = harness.upload_image(logo, name=f"batch8 logo workspace {int(time.time())}", asset_role="logo")
        workspace_id = upload["workspace_id"]
        workspace_ids.append(workspace_id)
        detail = harness.get(f"/api/workspaces/{workspace_id}")
        reference_images = detail.get("reference_images") or []
        reference_proxy_reads: list[dict[str, Any]] = []
        image_bytes = b""
        if reference_images:
            for _ in range(5 if api_base else 1):
                payload = harness.get(reference_images[0]["url"])
                image_bytes = bytes(payload)
                reference_proxy_reads.append(
                    {
                        "status": 200,
                        "bytes": len(image_bytes),
                        "png": image_bytes.startswith(b"\x89PNG"),
                    }
                )

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
        alternate_reference_images: list[dict[str, Any]] = []
        alternate_image_result: dict[str, Any] = {}
        alternate_concept_bytes = b""
        no_reference_image_result: dict[str, Any] = {}
        no_reference_bytes = b""
        if api_base:
            alternate_logo = _logo_png(Path(tmp) / "batch8-logo-alt.png", (225, 70, 44))
            alternate_upload = harness.upload_image(
                alternate_logo,
                name=f"batch8 alternate logo workspace {int(time.time())}",
                asset_role="logo",
            )
            alternate_workspace_id = alternate_upload["workspace_id"]
            workspace_ids.append(alternate_workspace_id)
            alternate_detail = harness.get(f"/api/workspaces/{alternate_workspace_id}")
            alternate_reference_images = alternate_detail.get("reference_images") or []
            alternate_image_result = harness.post_json(
                "/api/generate-image",
                {
                    "prompt": "Generate a data product campaign concept using the provided logo.",
                    "size": "1024x1024",
                    "reference_image_urls": [alternate_reference_images[0]["url"]] if alternate_reference_images else [],
                },
                timeout=300,
            )
            alternate_concept_bytes = _artifact_bytes(harness, alternate_image_result.get("artifact_url"))
            no_reference_image_result = harness.post_json(
                "/api/generate-image",
                {
                    "prompt": "Generate a data product concept image without reference assets.",
                    "size": "1024x1024",
                    "reference_image_urls": [],
                },
                timeout=300,
            )
            no_reference_bytes = _artifact_bytes(harness, no_reference_image_result.get("artifact_url"))

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
        "reference_proxy_png": bool(reference_proxy_reads and all(item.get("png") for item in reference_proxy_reads)),
        "blob_backed_reference_without_local_meta": bool(blob_regression.get("ok")),
        "generate_image_returned_png": bool(concept_bytes.startswith(b"\x89PNG")),
        "generate_image_reference_count": int(image_result.get("reference_image_count") or 0) >= 1,
        "generate_image_mode_valid": image_result.get("mode") in {"gpt-image-2-edit", "gpt-image-2", "deterministic-fallback"},
        "produce_reference_edit_mode": (not full_produce)
        or ((produce_result.get("concept_image") or {}).get("mode") in {"gpt-image-2-edit", "gpt-image-2", "deterministic-fallback"}),
        "produce_reference_count": (not full_produce)
        or (int((produce_result.get("concept_image") or {}).get("reference_image_count") or 0) >= 1),
        "produce_concept_png": (not full_produce) or bool(produce_bytes.startswith(b"\x89PNG")),
    }
    if api_base:
        checks["generate_image_used_edit_mode"] = image_result.get("mode") == "gpt-image-2-edit"
        checks["alternate_logo_used_edit_mode"] = alternate_image_result.get("mode") == "gpt-image-2-edit"
        checks["alternate_logo_reference_count"] = int(alternate_image_result.get("reference_image_count") or 0) >= 1
        checks["alternate_logo_changed_output"] = bool(
            concept_bytes
            and alternate_concept_bytes
            and hashlib.sha256(concept_bytes).hexdigest() != hashlib.sha256(alternate_concept_bytes).hexdigest()
        )
        checks["no_reference_mode"] = no_reference_image_result.get("mode") == "gpt-image-2"
        checks["no_reference_count"] = int(no_reference_image_result.get("reference_image_count") or 0) == 0
        checks["no_reference_png"] = bool(no_reference_bytes.startswith(b"\x89PNG"))
        if full_produce:
            checks["produce_used_edit_mode"] = (produce_result.get("concept_image") or {}).get("mode") == "gpt-image-2-edit"

    result = {
        "ok": all(checks.values()),
        "api_base": api_base,
        "loaded_env": loaded_env,
        "checks": checks,
        "workspace_id": workspace_ids[0] if workspace_ids else None,
        "blob_regression": blob_regression,
        "reference_proxy_reads": reference_proxy_reads,
        "reference_images": reference_images,
        "image_result": {
            "mode": image_result.get("mode"),
            "bytes": image_result.get("bytes"),
            "reference_image_count": image_result.get("reference_image_count"),
            "image_error": image_result.get("image_error"),
            "sha256": hashlib.sha256(concept_bytes).hexdigest() if concept_bytes else "",
        },
        "alternate_image_result": {
            "mode": alternate_image_result.get("mode"),
            "bytes": alternate_image_result.get("bytes"),
            "reference_image_count": alternate_image_result.get("reference_image_count"),
            "image_error": alternate_image_result.get("image_error"),
            "sha256": hashlib.sha256(alternate_concept_bytes).hexdigest() if alternate_concept_bytes else "",
            "reference_images": alternate_reference_images,
        },
        "no_reference_image_result": {
            "mode": no_reference_image_result.get("mode"),
            "bytes": no_reference_image_result.get("bytes"),
            "reference_image_count": no_reference_image_result.get("reference_image_count"),
            "image_error": no_reference_image_result.get("image_error"),
            "sha256": hashlib.sha256(no_reference_bytes).hexdigest() if no_reference_bytes else "",
        },
        "produce_result": {
            "mode": (produce_result.get("concept_image") or {}).get("mode"),
            "bytes": (produce_result.get("concept_image") or {}).get("bytes"),
            "reference_image_count": (produce_result.get("concept_image") or {}).get("reference_image_count"),
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
