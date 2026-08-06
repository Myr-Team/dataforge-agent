from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import backend.app as app_module
import backend.artifact_registry as artifact_registry
import backend.control_plane as control_plane
import backend.maf_agents as maf_agents
import backend.tools.generate_image as generate_image
import backend.tools.narrate_summary as narrate_summary
import backend.tools.render_pdf as render_pdf
from backend.app import app


def _configure_artifact_storage(tmp_path, monkeypatch):
    output_dir = tmp_path / "generated-outputs"
    monkeypatch.setattr(artifact_registry, "ARTIFACT_RECORD_DIR", tmp_path / "artifact-registry")
    monkeypatch.setattr(artifact_registry, "blob_configured", lambda: False)
    monkeypatch.setattr(app_module, "ARTIFACT_DIR", output_dir)
    monkeypatch.setattr(generate_image, "OUT_DIR", output_dir)
    monkeypatch.setattr(render_pdf, "OUT_DIR", output_dir)
    monkeypatch.setattr(narrate_summary, "OUT_DIR", output_dir)
    return output_dir


def _write_artifact(workspace_id: str, kind: str, content_type: str, suffix: str, content: bytes, output_dir):
    reservation = artifact_registry.reserve_artifact(
        workspace_id=workspace_id,
        kind=kind,
        content_type=content_type,
        suffix=suffix,
    )
    return artifact_registry.write_artifact(reservation, content, output_dir)


def test_opaque_artifacts_are_workspace_bound_and_never_cross_serve(tmp_path, monkeypatch) -> None:
    output_dir = _configure_artifact_storage(tmp_path, monkeypatch)
    artifact_a = _write_artifact("ws-a", "pdf", "application/pdf", ".pdf", b"workspace-a", output_dir)
    artifact_b = _write_artifact("ws-b", "pdf", "application/pdf", ".pdf", b"workspace-b", output_dir)

    assert artifact_a["artifact_name"] != artifact_b["artifact_name"]
    assert "ws-a" not in artifact_a["artifact_name"]
    assert "ws-b" not in artifact_b["artifact_name"]

    def require_workspace(workspace_id, _request, _action):
        if workspace_id != "ws-a":
            raise HTTPException(status_code=403, detail="workspace permission denied")
        return "viewer"

    monkeypatch.setattr(app_module, "_require_workspace_action", require_workspace)
    client = TestClient(app)

    allowed = client.get(f"/api/artifacts/{artifact_a['artifact_name']}")
    denied = client.get(f"/api/artifacts/{artifact_b['artifact_name']}")

    assert allowed.status_code == 200
    assert allowed.content == b"workspace-a"
    assert denied.status_code == 403
    assert b"workspace-b" not in denied.content


def test_download_rejects_legacy_file_even_when_old_metadata_names_it(tmp_path, monkeypatch) -> None:
    output_dir = _configure_artifact_storage(tmp_path, monkeypatch)
    (output_dir / "legacy.pdf").parent.mkdir(parents=True, exist_ok=True)
    (output_dir / "legacy.pdf").write_bytes(b"legacy bytes")
    monkeypatch.setattr(
        app_module,
        "list_artifact_jobs",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy artifact job scan")),
    )
    monkeypatch.setattr(
        app_module,
        "list_runs",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy run scan")),
    )
    monkeypatch.setattr(app_module, "download_artifact", lambda _name: (b"legacy blob", "application/pdf"))

    response = TestClient(app).get("/api/artifacts/legacy.pdf")

    assert response.status_code == 404
    assert b"legacy bytes" not in response.content
    assert b"legacy blob" not in response.content


def test_download_rejects_reserved_artifact_before_bytes_are_ready(tmp_path, monkeypatch) -> None:
    _configure_artifact_storage(tmp_path, monkeypatch)
    reservation = artifact_registry.reserve_artifact(
        workspace_id="ws-a",
        kind="pdf",
        content_type="application/pdf",
        suffix=".pdf",
    )
    monkeypatch.setattr(app_module, "_require_workspace_action", lambda *_args: (_ for _ in ()).throw(AssertionError("must not authorize incomplete artifact")))

    response = TestClient(app).get(f"/api/artifacts/{reservation['artifact_name']}")

    assert response.status_code == 404


def test_workspace_artifact_catalog_hides_foreign_registered_artifacts(tmp_path, monkeypatch) -> None:
    output_dir = _configure_artifact_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(control_plane, "ARTIFACT_DIR", output_dir)
    foreign = _write_artifact("ws-b", "pdf", "application/pdf", ".pdf", b"workspace-b", output_dir)
    monkeypatch.setattr(control_plane, "list_runs", lambda _workspace_id: [{"run_id": "run-a"}])
    monkeypatch.setattr(
        control_plane,
        "get_run",
        lambda _run_id: {
            "run_id": "run-a",
            "workspace_id": "ws-a",
            "artifact": {"proposal": {"artifact_urls": {"pdf": f"/api/artifacts/{foreign['artifact_name']}"}}},
        },
    )
    monkeypatch.setattr(control_plane, "list_artifact_jobs", lambda _workspace_id: [])
    monkeypatch.setattr(control_plane, "list_tasks", lambda _workspace_id: [])
    catalog = control_plane.list_workspace_artifacts("ws-a")

    assert catalog["artifacts"] == []


def test_workspace_artifact_catalog_projects_workspace_id_for_lineage(tmp_path, monkeypatch) -> None:
    output_dir = _configure_artifact_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(control_plane, "ARTIFACT_DIR", output_dir)
    owned = _write_artifact("ws-a", "pdf", "application/pdf", ".pdf", b"workspace-a", output_dir)
    monkeypatch.setattr(control_plane, "list_runs", lambda _workspace_id: [{"run_id": "run-a"}])
    monkeypatch.setattr(
        control_plane,
        "get_run",
        lambda _run_id: {
            "run_id": "run-a",
            "workspace_id": "ws-a",
            "completed_at": "2026-07-24T04:05:00Z",
            "artifact": {
                "proposal": {
                    "artifact_urls": {"pdf": f"/api/artifacts/{owned['artifact_name']}"}
                }
            },
        },
    )
    monkeypatch.setattr(control_plane, "list_artifact_jobs", lambda _workspace_id: [])
    monkeypatch.setattr(control_plane, "list_tasks", lambda _workspace_id: [])

    catalog = control_plane.list_workspace_artifacts("ws-a")

    assert len(catalog["artifacts"]) == 1
    assert catalog["artifacts"][0]["workspace_id"] == "ws-a"
    assert catalog["artifacts"][0]["run_id"] == "run-a"


def test_blob_registry_persistence_failure_blocks_artifact_bytes(tmp_path, monkeypatch) -> None:
    output_dir = _configure_artifact_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(artifact_registry, "blob_configured", lambda: True)
    monkeypatch.setattr(
        artifact_registry,
        "upload_blob_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("Blob unavailable")),
    )

    with pytest.raises(artifact_registry.ArtifactPersistenceError, match="durable artifact record persistence"):
        _write_artifact("ws-a", "pdf", "application/pdf", ".pdf", b"must not write", output_dir)

    assert not output_dir.exists()


def test_direct_generation_routes_register_ready_workspace_artifacts(tmp_path, monkeypatch) -> None:
    _configure_artifact_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(app_module, "_require_authenticated_workspace_action", lambda *_args: "editor")
    monkeypatch.setattr(app_module, "_require_workspace_action", lambda *_args: "viewer")
    monkeypatch.setattr(app_module, "_audit_required", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(generate_image, "_generate_with_gpt_image_2", lambda *_args: b"image-bytes")
    monkeypatch.setattr(render_pdf, "_html_pdf", lambda *_args: b"%PDF-1.4\n%%EOF")
    monkeypatch.setenv("SPEECH_KEY", "test-key")
    monkeypatch.setattr(
        narrate_summary.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline speech")),
    )
    client = TestClient(app)

    generated = {
        "image": client.post("/api/generate-image", json={"workspace_id": "ws-direct", "prompt": "chart"}),
        "pdf": client.post(
            "/api/render-pdf-report",
            json={"workspace_id": "ws-direct", "proposal": {"opportunity_id": "direct"}},
        ),
        "audio": client.post("/api/narrate-summary", json={"workspace_id": "ws-direct", "text": "summary"}),
    }

    for response in generated.values():
        assert response.status_code == 200
        payload = response.json()
        record = artifact_registry.get_artifact(payload["artifact_name"])
        assert record is not None
        assert record["workspace_id"] == "ws-direct"
        assert record["status"] == "ready"
        download = client.get(payload["artifact_url"])
        assert download.status_code == 200


@pytest.mark.asyncio
async def test_maf_local_tool_registers_workspace_bound_artifact(tmp_path, monkeypatch) -> None:
    _configure_artifact_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(maf_agents, "workspace_reference_images", lambda _workspace_id: [])
    monkeypatch.setattr(render_pdf, "_html_pdf", lambda *_args: b"%PDF-1.4\n%%EOF")

    pdf_tool = maf_agents._local_tools("ws-maf")["render_pdf_report"]
    result = await pdf_tool.invoke(
        arguments={"proposal": {"opportunity_id": "maf"}, "template": "project_proposal"},
        skip_parsing=True,
    )

    record = artifact_registry.get_artifact(result["artifact_name"])
    assert record["workspace_id"] == "ws-maf"
    assert record["status"] == "ready"
