from __future__ import annotations

import json

import backend.finops.retention as retention


class _Repository:
    def __init__(self) -> None:
        self.retention_days: int | None = None

    def purge_expired_request_facts(self, *, retention_days: int = 90) -> None:
        self.retention_days = retention_days


def test_retention_job_defaults_to_ninety_days_and_emits_bounded_status(
    monkeypatch,
    capsys,
) -> None:
    repository = _Repository()
    monkeypatch.delenv("DF_FINOPS_RETENTION_DAYS", raising=False)
    monkeypatch.setattr(retention, "_repository", lambda: repository)

    assert retention.main() == 0

    assert repository.retention_days == 90
    assert json.loads(capsys.readouterr().out) == {
        "status": "completed",
        "retention_days": 90,
    }


def test_retention_job_bounds_configuration_without_leaking_error_detail(
    monkeypatch,
    capsys,
) -> None:
    class FailingRepository:
        def purge_expired_request_facts(self, *, retention_days: int = 90) -> None:
            raise RuntimeError("server=tcp:secret-database")

    monkeypatch.setenv("DF_FINOPS_RETENTION_DAYS", "9999")
    monkeypatch.setattr(retention, "_repository", FailingRepository)

    assert retention.main() == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload == {"status": "failed", "category": "RuntimeError"}
