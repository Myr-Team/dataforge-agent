from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.finops.job_status import (
    InMemoryJobRunRepository,
    JobRunService,
    safe_job_error_category,
)


NOW = datetime(2026, 8, 9, 2, 0, tzinfo=timezone.utc)


def test_job_run_records_running_success_and_latest_status() -> None:
    repository = InMemoryJobRunRepository()
    service = JobRunService(repository)

    first = service.start("finops_rollup", now=NOW)
    assert first.status == "running"
    completed = service.succeed(
        first,
        rows_observed=2404,
        rows_written=72,
        source_freshness_at="2026-08-09T01:59:00Z",
        now=NOW + timedelta(seconds=8),
    )

    assert completed.status == "succeeded"
    assert completed.rows_observed == 2404
    assert completed.rows_written == 72
    assert repository.latest("finops_rollup") == completed


def test_job_failure_keeps_only_a_safe_category() -> None:
    repository = InMemoryJobRunRepository()
    service = JobRunService(repository)
    started = service.start("finops_retention", now=NOW)
    failed = service.fail(
        started,
        error=RuntimeError("server=tcp:secret-database;password=never-store"),
        now=NOW + timedelta(seconds=2),
    )

    assert failed.status == "failed"
    assert failed.safe_error_category == "runtime_error"
    assert "secret" not in failed.model_dump_json()
    assert safe_job_error_category(TimeoutError("private endpoint")) == "timeout"


def test_job_history_is_bounded_and_ordered() -> None:
    repository = InMemoryJobRunRepository()
    service = JobRunService(repository)
    for minute in range(3):
        value = service.start("finops_apim_reconciliation", now=NOW + timedelta(minutes=minute))
        service.succeed(value, now=NOW + timedelta(minutes=minute, seconds=3))

    history = repository.list("finops_apim_reconciliation", limit=2)
    assert len(history) == 2
    assert history[0].started_at > history[1].started_at
