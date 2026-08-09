from __future__ import annotations

import json
import os

from .sql_repository import SqlFinOpsRepository
from .job_status import JobRunService, SqlJobRunRepository


def main() -> int:
    status_service: JobRunService | None = None
    status_record = None
    try:
        status_service = JobRunService(_job_status_repository())
        status_record = status_service.start("finops_retention")
        repository = _repository()
        retention_days = _bounded_int(
            os.environ.get("DF_FINOPS_RETENTION_DAYS"),
            default=90,
            minimum=1,
            maximum=365,
        )
        repository.purge_expired_request_facts(retention_days=retention_days)
        status_service.succeed(status_record)
    except Exception as exc:
        if status_service is not None and status_record is not None:
            try:
                status_service.fail(status_record, error=exc)
            except Exception:
                pass
        print(
            json.dumps(
                {"status": "failed", "category": type(exc).__name__},
                separators=(",", ":"),
            )
        )
        return 1
    print(
        json.dumps(
            {"status": "completed", "retention_days": retention_days},
            separators=(",", ":"),
        )
    )
    return 0


def _repository() -> SqlFinOpsRepository:
    try:
        from ..lineage_sql import build_lineage_sql_connection_factory
    except ImportError:
        from lineage_sql import build_lineage_sql_connection_factory
    return SqlFinOpsRepository(
        connection_factory=build_lineage_sql_connection_factory()
    )


def _job_status_repository() -> SqlJobRunRepository:
    try:
        from ..lineage_sql import build_lineage_sql_connection_factory
    except ImportError:
        from lineage_sql import build_lineage_sql_connection_factory
    return SqlJobRunRepository(
        connection_factory=build_lineage_sql_connection_factory()
    )


def _bounded_int(
    value: str | None,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        parsed = int(value) if value is not None else default
    except ValueError:
        parsed = default
    return max(minimum, min(parsed, maximum))


if __name__ == "__main__":
    raise SystemExit(main())
