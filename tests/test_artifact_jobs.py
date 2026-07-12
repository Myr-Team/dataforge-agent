from __future__ import annotations

from pathlib import Path

import backend.artifact_jobs as artifact_jobs
import backend.tools.render_pdf as render_pdf
from backend.app import app
from fastapi.testclient import TestClient


def _configure_store(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(artifact_jobs, "ARTIFACT_JOB_DIR", tmp_path / "artifact-jobs")
    monkeypatch.setattr(artifact_jobs, "download_blob_json", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(artifact_jobs, "upload_blob_json", lambda *_args, **_kwargs: {})


def _request(**overrides) -> dict:
    payload = {
        "workspace_id": "ws-artifacts",
        "conversation_id": "run-v1",
        "kinds": ["pdf", "concept_image"],
        "feasibility": {"verdict": "conditional", "dimensions": [{"name": "asset_data", "score": 3}]},
        "answer": {"text": "Sensitive analysis text should not be copied into the job record."},
    }
    payload.update(overrides)
    return payload


def test_job_state_survives_store_reload_without_copying_analysis_payload(tmp_path: Path, monkeypatch) -> None:
    _configure_store(tmp_path, monkeypatch)

    job = artifact_jobs.create_artifact_job(
        _request(),
        actor={"email": "owner@contoso.com", "actor_id": "oid-owner"},
        idempotency_key="request-1",
    )
    reloaded = artifact_jobs.get_artifact_job(job["job_id"])

    assert reloaded["status"] == "queued"
    assert reloaded["source_run_id"] == "run-v1"
    assert reloaded["requested_kinds"] == ["pdf", "concept_image"]
    assert reloaded["plan_version"].startswith("V")
    assert "feasibility" not in reloaded
    assert "answer" not in reloaded


def test_idempotency_key_reuses_non_terminal_job(tmp_path: Path, monkeypatch) -> None:
    _configure_store(tmp_path, monkeypatch)

    first = artifact_jobs.create_artifact_job(_request(), actor={}, idempotency_key="same-request")
    second = artifact_jobs.create_artifact_job(_request(), actor={}, idempotency_key="same-request")

    assert first["job_id"] == second["job_id"]


def test_partial_generation_keeps_completed_artifacts(tmp_path: Path, monkeypatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    job = artifact_jobs.create_artifact_job(_request(), actor={}, idempotency_key="partial-request")
    monkeypatch.setattr(
        artifact_jobs,
        "_producer_payload",
        lambda _job: _request(),
    )
    monkeypatch.setattr(
        artifact_jobs,
        "_produce",
        lambda _payload: {
            "artifact_urls": {"pdf": "/api/artifacts/project-v1.pdf"},
            "pdf": {"artifact_url": "/api/artifacts/project-v1.pdf", "bytes": 1200},
            "concept_image": {"mode": "concept_image_error", "error": "provider timeout"},
            "warnings": [
                {
                    "kind": "concept_image",
                    "message": "概念图生成失败，建议书已生成。",
                    "error": "provider timeout",
                }
            ],
        },
    )

    result = artifact_jobs.run_artifact_job(job["job_id"])

    assert result["status"] == "partial"
    assert result["artifacts"]["pdf"]["artifact_url"].endswith("project-v1.pdf")
    assert result["errors"]["concept_image"]["message"] == "概念图生成失败，建议书已生成。"


def test_terminal_job_is_not_reused_by_idempotency_key(tmp_path: Path, monkeypatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    first = artifact_jobs.create_artifact_job(_request(), actor={}, idempotency_key="repeat")
    artifact_jobs._update_job(first["job_id"], status="failed", errors={"pdf": {"message": "failed"}})

    second = artifact_jobs.create_artifact_job(_request(), actor={}, idempotency_key="repeat")

    assert first["job_id"] != second["job_id"]


def test_artifact_job_api_creates_and_exposes_persisted_status(tmp_path: Path, monkeypatch) -> None:
    _configure_store(tmp_path, monkeypatch)
    monkeypatch.setattr(artifact_jobs, "_producer_payload", lambda _job: _request())
    monkeypatch.setattr(
        artifact_jobs,
        "_produce",
        lambda _payload: {
            "artifact_urls": {
                "pdf": "/api/artifacts/project-v1.pdf",
                "concept_image": "/api/artifacts/project-v1.png",
            },
            "pdf": {"artifact_url": "/api/artifacts/project-v1.pdf"},
            "concept_image": {"artifact_url": "/api/artifacts/project-v1.png"},
        },
    )
    client = TestClient(app)

    created = client.post(
        "/api/artifact-jobs",
        json=_request(),
        headers={"Idempotency-Key": "api-request-1"},
    )

    assert created.status_code == 202
    job_id = created.json()["job_id"]
    detail = client.get(f"/api/artifact-jobs/{job_id}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "completed"

    listed = client.get("/api/workspaces/ws-artifacts/artifact-jobs")
    assert listed.status_code == 200
    assert listed.json()["jobs"][0]["job_id"] == job_id


def test_pdf_filename_contains_explicit_plan_version(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(render_pdf, "OUT_DIR", tmp_path)
    monkeypatch.setattr(render_pdf, "_html_pdf", lambda _proposal, _template: b"%PDF-1.4\n%%EOF")
    monkeypatch.setattr(render_pdf, "upload_artifact", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")))

    result = render_pdf.render_pdf_report(
        {"opportunity_id": "Pilot plan", "doc_meta": {"version": "V2"}},
        "project_proposal",
    )

    assert "-V2-" in result["artifact_name"]
