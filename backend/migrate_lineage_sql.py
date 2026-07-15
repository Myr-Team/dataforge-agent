from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any
from uuid import uuid4

try:
    from .experiment_store import (
        LegacyLineageHistory,
        analysis_lineage_fingerprints,
        inspect_legacy_lineage_history,
        lineage_workspace_exists,
    )
except ImportError:
    from experiment_store import (
        LegacyLineageHistory,
        analysis_lineage_fingerprints,
        inspect_legacy_lineage_history,
        lineage_workspace_exists,
    )


class _ExistingSqlLineage(RuntimeError):
    pass


def migrate_workspace(
    workspace_id: str,
    *,
    dry_run: bool,
    lineage_repository: Any | None = None,
    registry_state: Mapping[str, Any] | None = None,
    run_loader: Callable[[str], dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    normalized_workspace = str(workspace_id or "").strip()
    if not normalized_workspace:
        raise ValueError("workspace_id is required")
    repository = lineage_repository or _runtime_repository()
    try:
        if lineage_workspace_exists(repository, normalized_workspace):
            return _result(
                normalized_workspace,
                status="rejected",
                reason="sql_lineage_exists",
                dry_run=dry_run,
            )
    except Exception:
        return _result(
            normalized_workspace,
            status="unavailable",
            reason="lineage_unavailable",
            dry_run=dry_run,
        )
    if registry_state is None or run_loader is None:
        try:
            from .run_store import authoritative_run_registry, get_run
        except ImportError:
            from run_store import authoritative_run_registry, get_run
        registry_state = authoritative_run_registry(normalized_workspace)
        run_loader = get_run

    history = inspect_legacy_lineage_history(
        normalized_workspace,
        registry_state=registry_state,
        run_loader=run_loader,
    )
    if history.status != "ready":
        return _result(
            normalized_workspace,
            status="legacy_unavailable",
            reason=history.reason or "history_incomplete",
            dry_run=dry_run,
        )
    if not history.canonical_runs:
        return _result(
            normalized_workspace,
            status="legacy_unavailable",
            reason="history_incomplete",
            dry_run=dry_run,
        )

    if dry_run:
        return _ready_result(normalized_workspace, history, dry_run=True, status="ready")

    try:
        _import_history(repository, normalized_workspace, history.canonical_runs)
    except _ExistingSqlLineage:
        return _result(
            normalized_workspace,
            status="rejected",
            reason="sql_lineage_exists",
            dry_run=False,
        )
    except Exception:
        return _result(
            normalized_workspace,
            status="unavailable",
            reason="lineage_unavailable",
            dry_run=False,
        )
    return _ready_result(normalized_workspace, history, dry_run=False, status="migrated")


def _import_history(
    repository: Any,
    workspace_id: str,
    canonical_runs: Sequence[Mapping[str, Any]],
) -> None:
    transaction = getattr(repository, "_transaction", None)
    if not callable(transaction):
        raise RuntimeError("lineage repository does not support atomic migration")
    actor_metadata = json.dumps(
        {"source": "legacy_blob_migration"},
        sort_keys=True,
        separators=(",", ":"),
    )
    with transaction() as cursor:
        existing = cursor.execute(
            """/* lineage:migration-lock-workspace */
            SELECT workspace_id
            FROM df_lineage.workspace_lineage WITH (UPDLOCK, HOLDLOCK)
            WHERE workspace_id = ?""",
            workspace_id,
        ).fetchone()
        if existing is not None:
            raise _ExistingSqlLineage()
        cursor.execute(
            """/* lineage:migration-insert-workspace */
            INSERT INTO df_lineage.workspace_lineage (
                workspace_id,
                generation,
                lifecycle_state,
                next_version_ordinal,
                actor_metadata
            ) VALUES (?, ?, N'active', ?, ?)""",
            workspace_id,
            1,
            len(canonical_runs) + 1,
            actor_metadata,
        )
        for ordinal, run in enumerate(canonical_runs, start=1):
            decision_fingerprint, evidence_fingerprint = analysis_lineage_fingerprints(run)
            cursor.execute(
                """/* lineage:migration-insert-version */
                INSERT INTO df_lineage.experiment_version (
                    version_id,
                    workspace_id,
                    generation,
                    ordinal,
                    canonical_run_id,
                    decision_fingerprint,
                    evidence_fingerprint,
                    actor_metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                str(uuid4()),
                workspace_id,
                1,
                ordinal,
                str(run.get("run_id") or ""),
                decision_fingerprint,
                evidence_fingerprint,
                actor_metadata,
            )


def _runtime_repository() -> Any:
    try:
        from .app import get_lineage_repository
    except ImportError:
        from app import get_lineage_repository
    return get_lineage_repository()


def _result(
    workspace_id: str,
    *,
    status: str,
    reason: str,
    dry_run: bool,
) -> dict[str, Any]:
    return {
        "workspace_id": workspace_id,
        "status": status,
        "reason": reason,
        "dry_run": dry_run,
    }


def _ready_result(
    workspace_id: str,
    history: LegacyLineageHistory,
    *,
    dry_run: bool,
    status: str,
) -> dict[str, Any]:
    return {
        "workspace_id": workspace_id,
        "status": status,
        "dry_run": dry_run,
        "generation": 1,
        "version_count": len(history.canonical_runs),
        "legacy_analysis_count": len(history.analysis_runs),
        "legacy_attachment_count": history.attachment_count,
        "attachment_imported_count": 0,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Migrate one complete legacy Blob lineage to Azure SQL.")
    parser.add_argument("--workspace-id", required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = migrate_workspace(args.workspace_id, dry_run=not args.apply)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] in {"ready", "migrated"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
