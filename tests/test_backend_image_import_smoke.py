from __future__ import annotations

import importlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "backend" / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"


def test_backend_image_copies_agent_registry_before_import_smoke() -> None:
    lines = [line.strip() for line in DOCKERFILE.read_text(encoding="utf-8").splitlines()]

    build_agents_copy = next(
        index for index, line in enumerate(lines) if line.startswith("COPY agents/build_agents.py ")
    )
    tool_schemas_copy = next(
        index for index, line in enumerate(lines) if line.startswith("COPY agents/tool_schemas.json ")
    )
    import_smoke = next(
        index for index, line in enumerate(lines) if "python -m backend.import_smoke" in line
    )

    assert build_agents_copy < import_smoke
    assert tool_schemas_copy < import_smoke


def test_backend_image_packages_only_sql_lineage_verifier() -> None:
    lines = [line.strip() for line in DOCKERFILE.read_text(encoding="utf-8").splitlines()]
    verifier_copy_instruction = (
        "COPY scripts/verify_lineage_sql.py ./scripts/verify_lineage_sql.py"
    )

    verifier_copy = lines.index(verifier_copy_instruction)
    verifier_smoke = next(
        index
        for index, line in enumerate(lines)
        if "python scripts/verify_lineage_sql.py --check-prerequisites" in line
    )
    import_smoke = next(
        index for index, line in enumerate(lines) if "python -m backend.import_smoke" in line
    )

    assert verifier_copy < verifier_smoke < import_smoke
    assert all(
        not line.startswith("COPY scripts/") or line == verifier_copy_instruction
        for line in lines
    )


def test_backend_image_build_context_includes_sql_lineage_verifier() -> None:
    ignored_paths = set(DOCKERIGNORE.read_text(encoding="utf-8").splitlines())

    assert "scripts/*" in ignored_paths
    assert "!scripts/verify_lineage_sql.py" in ignored_paths


def test_backend_image_import_smoke_imports_runtime_entrypoints() -> None:
    smoke = importlib.import_module("backend.import_smoke")

    imported = smoke.import_backend_image_modules()

    assert imported == (
        "agents.build_agents",
        "backend.maf_agents",
        "backend.orchestrator",
        "backend.app",
    )
