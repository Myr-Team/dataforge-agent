from __future__ import annotations

from backend.capability_packs import select_capability_packs
from backend.evidence_bundle import build_evidence_bundle, bundle_for_agent
import backend.control_plane as control_plane
import backend.run_store as run_store


def _site_profile(*, workspace_name: str, file_name: str) -> dict[str, object]:
    return {
        "schema_roles": ["location", "candidate", "demand", "time"],
        "metric_families": ["footfall", "conversion", "cost"],
        "temporal_coverage": {"available": True, "periods": 8},
        "entity_relationships": ["location_to_demand"],
        "workspace_name": workspace_name,
        "file_name": file_name,
    }


def _quality() -> dict[str, float]:
    return {"completeness": 0.94, "duplicate_rate": 0.01}


def _corpus() -> dict[str, object]:
    return {
        "profile": {
            "workspace_id": "workspace-1",
            "asset_evidence": [
                {
                    "source_type": "corpus",
                    "ref": "source.csv#row-1",
                    "quote": "Observed demand and conversion measurements.",
                }
            ],
        },
        "hits": [],
    }


def test_selected_pack_changes_agent_guidance_without_becoming_evidence() -> None:
    selections = select_capability_packs(
        "choose channels for demand coverage",
        _site_profile(workspace_name="alpha", file_name="first.csv"),
        _quality(),
    )

    bundle = build_evidence_bundle(
        _corpus(),
        {"workspace_id": "workspace-1", "intent": "feasibility_analysis"},
        [selection.model_dump(mode="json") for selection in selections],
    )

    feasibility = bundle_for_agent(bundle, "df-feasibility-analyst")
    producer = bundle_for_agent(bundle, "df-producer")

    assert feasibility["capability_guidance"][0]["pack_id"] == "site_channel_selection"
    assert feasibility["capability_guidance"][0]["questions"]
    assert feasibility["capability_guidance"][0]["validation_methods"]
    assert "artifact_sections" not in feasibility["capability_guidance"][0]
    assert producer["capability_guidance"][0]["artifact_sections"]
    assert "questions" not in producer["capability_guidance"][0]
    assert feasibility["capability_guidance_is_observed_evidence"] is False
    assert all(
        "score" not in item and "verdict" not in item and "conclusion" not in item
        for item in feasibility["capability_guidance"]
    )


def test_workspace_and_file_renames_do_not_change_pack_contract() -> None:
    first = select_capability_packs(
        "choose channels for demand coverage",
        _site_profile(workspace_name="alpha", file_name="first.csv"),
        _quality(),
    )
    renamed = select_capability_packs(
        "choose channels for demand coverage",
        _site_profile(workspace_name="unrelated", file_name="different.xlsx"),
        _quality(),
    )

    assert first == renamed


def test_run_trace_and_summary_preserve_the_selected_pack_contract(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "upload_blob_json", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(run_store, "download_blob_json", lambda *_args, **_kwargs: {})
    run_store._ACTIVE.clear()
    selection = select_capability_packs(
        "choose channels for demand coverage",
        _site_profile(workspace_name="alpha", file_name="first.csv"),
        _quality(),
    )[0].model_dump(mode="json")

    run_store.start_run("run-capability", "workspace-1", "choose channels")
    run_store.record_event(
        "run-capability",
        "capability_pack_selection",
        {"source": "normalized_goal_schema_profile_quality", "capability_packs": [selection]},
    )
    run_store.complete_run(
        "run-capability",
        final={"text": "done"},
        artifact={"capability_packs": [selection], "verdict_source": "evidence_guard"},
    )

    summary = control_plane.run_summary("run-capability")
    trace = control_plane.run_trace("run-capability")

    assert summary["capability_packs"] == [selection]
    assert trace[0]["event"] == "capability_pack_selection"
    assert trace[0]["detail"]["capability_packs"] == [selection]
