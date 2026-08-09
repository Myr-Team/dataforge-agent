from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from .rollups import aggregate_rollups
from .sql_repository import SqlFinOpsRepository
from .sql_rollups import SqlFinOpsRollupRepository
from .job_status import JobRunService, SqlJobRunRepository


def refresh_rollups(
    *,
    event_repository: Any,
    rollup_repository: Any,
    scopes: Mapping[str, tuple[str, ...]],
    from_value: str,
    to_value: str,
    budget_evaluator: Any | None = None,
) -> dict[str, int]:
    totals = {
        "scope_count": 0,
        "event_count": 0,
        "hourly_rows": 0,
        "daily_rows": 0,
    }
    for tenant_ref, workspace_ids in scopes.items():
        if not tenant_ref or not workspace_ids:
            continue
        events = event_repository.list_events(
            tenant_ref=tenant_ref,
            workspace_ids=tuple(workspace_ids),
            from_value=from_value,
            to_value=to_value,
        )
        hourly, daily = aggregate_rollups(events)
        rollup_repository.replace(
            tenant_ref=tenant_ref,
            from_value=from_value,
            to_value=to_value,
            hourly=hourly,
            daily=daily,
        )
        totals["scope_count"] += 1
        totals["event_count"] += len(events)
        totals["hourly_rows"] += len(hourly)
        totals["daily_rows"] += len(daily)
        if budget_evaluator is not None:
            try:
                budget_evaluator.evaluate_tenant(tenant_ref, now=datetime.now(timezone.utc), workspace_ids=tuple(workspace_ids))
            except Exception:
                # Alert processing is explicitly isolated from truthful rollups.
                pass
    return totals


def main() -> int:
    status_service: JobRunService | None = None
    status_record = None
    try:
        status_service = JobRunService(_job_status_repository())
        status_record = status_service.start("finops_rollup")
        event_repository, rollup_repository = _repositories()
        now = datetime.now(timezone.utc).replace(microsecond=0)
        hours = _bounded_int(
            os.environ.get("DF_FINOPS_ROLLUP_REFRESH_HOURS"),
            default=48,
            minimum=1,
            maximum=24 * 90,
        )
        start = now - timedelta(hours=hours)
        from_value = _iso(start)
        to_value = _iso(now)
        scopes = event_repository.list_scopes(
            from_value=from_value,
            to_value=to_value,
        )
        result = refresh_rollups(
            event_repository=event_repository,
            rollup_repository=rollup_repository,
            scopes=scopes,
            from_value=from_value,
            to_value=to_value,
        )
        status_service.succeed(
            status_record,
            rows_observed=int(result.get("event_count") or 0),
            rows_written=int(result.get("hourly_rows") or 0)
            + int(result.get("daily_rows") or 0),
            source_freshness_at=to_value,
        )
    except Exception as exc:
        if status_service is not None and status_record is not None:
            try:
                status_service.fail(status_record, error=exc)
            except Exception:
                pass
        print(json.dumps({"status": "failed", "category": type(exc).__name__}, separators=(",", ":")))
        return 1
    print(json.dumps({"status": "completed", **result}, separators=(",", ":")))
    return 0


def _repositories() -> tuple[SqlFinOpsRepository, SqlFinOpsRollupRepository]:
    try:
        from ..lineage_sql import build_lineage_sql_connection_factory
    except ImportError:
        from lineage_sql import build_lineage_sql_connection_factory
    factory = build_lineage_sql_connection_factory()
    return (
        SqlFinOpsRepository(connection_factory=factory),
        SqlFinOpsRollupRepository(connection_factory=factory),
    )


def _job_status_repository() -> SqlJobRunRepository:
    try:
        from ..lineage_sql import build_lineage_sql_connection_factory
    except ImportError:
        from lineage_sql import build_lineage_sql_connection_factory
    return SqlJobRunRepository(
        connection_factory=build_lineage_sql_connection_factory()
    )


def _bounded_int(value: str | None, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value) if value is not None else default
    except ValueError:
        parsed = default
    return max(minimum, min(parsed, maximum))


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
