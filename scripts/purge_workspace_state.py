from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import cache_store  # noqa: E402
from backend.run_store import list_runs, purge_workspace_runs  # noqa: E402


def _redis_patterns(workspace_id: str, run_ids: list[str]) -> list[str]:
    patterns = [
        f"dataforge:analysis:v1:workspace={workspace_id}:*",
        f"data_overview:{workspace_id}:*",
    ]
    patterns.extend(f"plan_metrics:{run_id}" for run_id in run_ids)
    return patterns


def main() -> int:
    parser = argparse.ArgumentParser(description="Purge stale DataForge run/cache state for one workspace.")
    parser.add_argument("workspace_id", help="Workspace id to clear, for example an uploaded demo workspace id.")
    parser.add_argument("--execute", action="store_true", help="Actually delete run records and Redis keys. Omit for dry-run.")
    args = parser.parse_args()

    run_ids = sorted({str(item.get("run_id") or "") for item in list_runs(args.workspace_id) if item.get("run_id")})
    patterns = _redis_patterns(args.workspace_id, run_ids)
    result: dict[str, object] = {
        "workspace_id": args.workspace_id,
        "mode": "execute" if args.execute else "dry_run",
        "run_ids": run_ids,
        "redis_patterns": patterns,
        "notes": [
            "Feasibility cache is also invalidated by _FEASIBILITY_PROMPT_VERSION.",
            "After purge, rerun auto-analysis for this workspace so last_analysis and artifacts are regenerated.",
        ],
    }
    if args.execute:
        result["runs"] = purge_workspace_runs(args.workspace_id)
        result["redis"] = [cache_store.delete_matching(pattern) for pattern in patterns]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
