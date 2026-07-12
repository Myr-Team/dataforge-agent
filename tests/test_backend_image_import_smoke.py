from __future__ import annotations

import importlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "backend" / "Dockerfile"


def test_backend_image_copies_agent_registry_before_import_smoke() -> None:
    lines = [line.strip() for line in DOCKERFILE.read_text(encoding="utf-8").splitlines()]

    build_agents_copy = next(
        index for index, line in enumerate(lines) if line.startswith("COPY agents/build_agents.py ")
    )
    tool_schemas_copy = next(
        index for index, line in enumerate(lines) if line.startswith("COPY agents/tool_schemas.json ")
    )
    import_smoke = lines.index("RUN python -m backend.import_smoke")

    assert build_agents_copy < import_smoke
    assert tool_schemas_copy < import_smoke


def test_backend_image_import_smoke_imports_runtime_entrypoints() -> None:
    smoke = importlib.import_module("backend.import_smoke")

    imported = smoke.import_backend_image_modules()

    assert imported == (
        "agents.build_agents",
        "backend.maf_agents",
        "backend.orchestrator",
        "backend.app",
    )
