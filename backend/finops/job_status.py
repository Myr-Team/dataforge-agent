from __future__ import annotations

import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .sql_repository import ConnectionFactory, FinOpsPersistenceError


JobName = Literal[
    "finops_apim_reconciliation",
    "finops_rollup",
    "finops_retention",
]
JobStatus = Literal["running", "succeeded", "failed"]


class JobRunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_ref: str = Field(pattern=r"^jobrun_[0-9a-f]{32}$")
    job_name: JobName
    status: JobStatus
    started_at: str
    completed_at: str | None = None
    safe_error_category: str | None = Field(default=None, max_length=64)
    rows_observed: int = Field(default=0, ge=0)
    rows_written: int = Field(default=0, ge=0)
    source_freshness_at: str | None = None


class JobRunRepository(Protocol):
    def save(self, value: JobRunRecord) -> JobRunRecord: ...

    def latest(self, job_name: JobName) -> JobRunRecord | None: ...

    def list(self, job_name: JobName, limit: int = 10) -> list[JobRunRecord]: ...


class InMemoryJobRunRepository:
    def __init__(self) -> None:
        self._lock = RLock()
        self._items: dict[tuple[str, str], JobRunRecord] = {}

    def save(self, value: JobRunRecord) -> JobRunRecord:
        with self._lock:
            self._items[(value.job_name, value.execution_ref)] = value.model_copy(deep=True)
        return value.model_copy(deep=True)

    def latest(self, job_name: JobName) -> JobRunRecord | None:
        items = self.list(job_name, limit=1)
        return items[0] if items else None

    def list(self, job_name: JobName, limit: int = 10) -> list[JobRunRecord]:
        with self._lock:
            items = [
                item.model_copy(deep=True)
                for (name, _), item in self._items.items()
                if name == job_name
            ]
        items.sort(key=lambda item: (item.started_at, item.execution_ref), reverse=True)
        return items[: max(1, min(int(limit), 50))]


class SqlJobRunRepository:
    def __init__(self, *, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def save(self, value: JobRunRecord) -> JobRunRecord:
        with self._transaction() as cursor:
            cursor.execute(
                """/* finops:save-job-run-status */
                MERGE df_finops.job_run_status WITH (HOLDLOCK) AS target
                USING (SELECT ? AS job_name, ? AS execution_ref) AS source
                ON target.job_name = source.job_name
                   AND target.execution_ref = source.execution_ref
                WHEN MATCHED THEN UPDATE SET
                    run_status = ?, completed_at = ?, safe_error_category = ?,
                    rows_observed = ?, rows_written = ?, source_freshness_at = ?
                WHEN NOT MATCHED THEN INSERT (
                    job_name, execution_ref, run_status, started_at,
                    completed_at, safe_error_category, rows_observed,
                    rows_written, source_freshness_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);""",
                value.job_name,
                value.execution_ref,
                value.status,
                value.completed_at,
                value.safe_error_category,
                value.rows_observed,
                value.rows_written,
                value.source_freshness_at,
                value.job_name,
                value.execution_ref,
                value.status,
                value.started_at,
                value.completed_at,
                value.safe_error_category,
                value.rows_observed,
                value.rows_written,
                value.source_freshness_at,
            )
        return value.model_copy(deep=True)

    def latest(self, job_name: JobName) -> JobRunRecord | None:
        items = self.list(job_name, limit=1)
        return items[0] if items else None

    def list(self, job_name: JobName, limit: int = 10) -> list[JobRunRecord]:
        bounded = max(1, min(int(limit), 50))
        with self._transaction() as cursor:
            rows = cursor.execute(
                f"""/* finops:list-job-run-status */
                SELECT TOP ({bounded}) execution_ref, job_name, run_status,
                    started_at, completed_at, safe_error_category,
                    rows_observed, rows_written, source_freshness_at
                FROM df_finops.job_run_status
                WHERE job_name = ?
                ORDER BY started_at DESC, execution_ref DESC""",
                job_name,
            ).fetchall()
        return [_decode(row) for row in rows]

    @contextmanager
    def _transaction(self):
        connection = None
        try:
            connection = self._connection_factory()
            connection.autocommit = False
            cursor = connection.cursor()
            yield cursor
            connection.commit()
        except Exception as exc:
            if connection is not None:
                try:
                    connection.rollback()
                except Exception:
                    pass
            raise FinOpsPersistenceError("FinOps job status SQL operation failed") from exc
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass


class JobRunService:
    def __init__(self, repository: JobRunRepository) -> None:
        self._repository = repository

    def start(self, job_name: JobName, *, now: datetime | None = None) -> JobRunRecord:
        value = JobRunRecord(
            execution_ref=f"jobrun_{uuid.uuid4().hex}",
            job_name=job_name,
            status="running",
            started_at=_iso(now or datetime.now(timezone.utc)),
        )
        return self._repository.save(value)

    def succeed(
        self,
        value: JobRunRecord,
        *,
        rows_observed: int = 0,
        rows_written: int = 0,
        source_freshness_at: str | None = None,
        now: datetime | None = None,
    ) -> JobRunRecord:
        completed = value.model_copy(
            update={
                "status": "succeeded",
                "completed_at": _iso(now or datetime.now(timezone.utc)),
                "safe_error_category": None,
                "rows_observed": max(0, int(rows_observed)),
                "rows_written": max(0, int(rows_written)),
                "source_freshness_at": source_freshness_at,
            }
        )
        return self._repository.save(completed)

    def fail(
        self,
        value: JobRunRecord,
        *,
        error: BaseException,
        now: datetime | None = None,
    ) -> JobRunRecord:
        failed = value.model_copy(
            update={
                "status": "failed",
                "completed_at": _iso(now or datetime.now(timezone.utc)),
                "safe_error_category": safe_job_error_category(error),
            }
        )
        return self._repository.save(failed)


def safe_job_error_category(error: BaseException) -> str:
    if isinstance(error, TimeoutError):
        return "timeout"
    name = type(error).__name__
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
    safe = re.sub(r"[^a-z0-9_]", "", snake).strip("_")
    return (safe or "job_failed")[:64]


def _decode(row: Any) -> JobRunRecord:
    return JobRunRecord(
        execution_ref=str(row[0]),
        job_name=str(row[1]),
        status=str(row[2]),
        started_at=_db_time(row[3]),
        completed_at=_db_time(row[4]) if row[4] is not None else None,
        safe_error_category=str(row[5]) if row[5] is not None else None,
        rows_observed=int(row[6] or 0),
        rows_written=int(row[7] or 0),
        source_freshness_at=_db_time(row[8]) if row[8] is not None else None,
    )


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _db_time(value: Any) -> str:
    return value.isoformat().replace("+00:00", "Z") if hasattr(value, "isoformat") else str(value)


__all__ = [
    "InMemoryJobRunRepository",
    "JobRunRecord",
    "JobRunService",
    "SqlJobRunRepository",
    "safe_job_error_category",
]
