"""Import the backend modules required by the production image entrypoint."""

from __future__ import annotations

import importlib


IMAGE_IMPORTS = (
    "agents.build_agents",
    "backend.maf_agents",
    "backend.orchestrator",
    "backend.app",
)


def import_backend_image_modules() -> tuple[str, ...]:
    for module_name in IMAGE_IMPORTS:
        importlib.import_module(module_name)
    return IMAGE_IMPORTS


def main() -> int:
    import_backend_image_modules()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
