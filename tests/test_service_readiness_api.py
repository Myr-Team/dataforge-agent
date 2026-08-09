from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

import backend.service_readiness_router as readiness
from auth_fixtures import trusted_headers
from backend.app import app
from backend.finops.job_status import InMemoryJobRunRepository, JobRunService


def _client(monkeypatch, *, role: str = "owner") -> TestClient:
    monkeypatch.setenv("DF_WEB_PROXY_SECRET", "test-proxy-secret")
    monkeypatch.setenv("DF_FINOPS_HMAC_SECRET", "finops-test-secret")
    monkeypatch.setenv("DF_FINOPS_READ_ENABLED", "1")
    monkeypatch.setattr(readiness, "active_workspace_role", lambda _workspace, _actor: role)
    monkeypatch.setattr(
        readiness,
        "dependency_status",
        lambda: {
            "dependencies": {"foundry": True, "blob": True, "search": True},
            "details": {
                "foundry": {"ok": True, "state": "ok", "latency_ms": 42},
                "blob": {"ok": True, "state": "ok", "latency_ms": 12},
                "search": {"ok": True, "state": "ok", "latency_ms": 18},
            },
        },
    )
    monkeypatch.setattr(readiness, "cache_probe", lambda: {"status": "ok", "elapsed_ms": 3})
    monkeypatch.setattr(
        readiness,
        "pricing_status",
        lambda _tenant_ref: {"status": "ready", "catalog_revision": "official-v1", "mapping_count": 3},
    )
    monkeypatch.setattr(
        readiness,
        "provider_status",
        lambda _tenant_ref: {"status": "ready", "configured": 1, "connected": 1, "governed": 1},
    )
    monkeypatch.setattr(
        readiness,
        "latest_risk_status",
        lambda _tenant_ref, _workspace_id: {"status": "ready", "scan_status": "completed", "rules_evaluated": 7},
    )
    job_repository = InMemoryJobRunRepository()
    job_service = JobRunService(job_repository)
    for job_name in readiness.EXPECTED_JOBS:
        started = job_service.start(job_name, now=datetime(2026, 8, 9, 1, 55, tzinfo=timezone.utc))
        job_service.succeed(started, now=datetime(2026, 8, 9, 1, 56, tzinfo=timezone.utc))
    monkeypatch.setattr(readiness, "get_job_run_repository", lambda: job_repository)
    monkeypatch.setattr(
        readiness,
        "_now",
        lambda: datetime(2026, 8, 9, 2, 0, tzinfo=timezone.utc),
    )
    return TestClient(app)


def test_service_readiness_returns_safe_grouped_real_states(monkeypatch) -> None:
    client = _client(monkeypatch)
    response = client.get(
        "/api/service-readiness",
        params={"workspace_id": "ws-a"},
        headers=trusted_headers(actor_id="owner-a", tenant_id="tenant-a"),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body["groups"]) == {"identity", "data", "ai", "finops", "background_jobs"}
    assert body["groups"]["identity"]["items"][0]["status"] == "ready"
    assert body["groups"]["finops"]["items"][1]["details"]["mapping_count"] == 3
    assert all(item["status"] == "ready" for item in body["groups"]["background_jobs"]["items"])
    serialized = response.text.lower()
    for forbidden in ("subscription", "tenant_id", "endpoint", "password", "secret", "resource_id"):
        assert forbidden not in serialized


def test_service_readiness_requires_selected_workspace_admin(monkeypatch) -> None:
    client = _client(monkeypatch, role="member")
    response = client.get(
        "/api/service-readiness",
        params={"workspace_id": "ws-a"},
        headers=trusted_headers(actor_id="member-a", tenant_id="tenant-a"),
    )

    assert response.status_code == 403


def test_service_readiness_marks_never_run_jobs_without_fabrication(monkeypatch) -> None:
    client = _client(monkeypatch)
    monkeypatch.setattr(readiness, "get_job_run_repository", InMemoryJobRunRepository)
    response = client.get(
        "/api/service-readiness",
        params={"workspace_id": "ws-a"},
        headers=trusted_headers(actor_id="owner-a", tenant_id="tenant-a"),
    )

    jobs = response.json()["groups"]["background_jobs"]["items"]
    assert all(item["status"] == "not_run" for item in jobs)
    assert all(item["last_completed_at"] is None for item in jobs)
