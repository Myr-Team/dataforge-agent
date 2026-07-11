import asyncio
import json
from pathlib import Path
import subprocess

import backend.control_plane as control_plane
import backend.run_store as run_store


REPO_ROOT = Path(__file__).resolve().parents[1]
VIEW_MODEL_PROBE = """
import fs from "node:fs";
import { deriveMafViewModel } from "./web/src/mafViewModel.js";

const payload = JSON.parse(fs.readFileSync(0, "utf8"));
const model = deriveMafViewModel(payload.trace, payload.summary.maf);
process.stdout.write(JSON.stringify(model));
"""


def test_real_run_summary_endpoint_exposes_persisted_maf_to_view_model(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_store, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_store, "upload_blob_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_store, "download_blob_json", lambda *args, **kwargs: {})
    run_store._ACTIVE.clear()

    run_id = "run-maf-summary"
    selected = ["df-coordinator", "df-feasibility-analyst"]
    skipped = ["df-corpus-analyst", "df-market-researcher", "df-auditor", "df-producer"]
    run_store.start_run(run_id, "ws-maf-summary", "evaluate")
    run_store.record_event(
        run_id,
        "maf_plan",
        {
            "status": "completed",
            "mode": "specialist_handoff",
            "pattern": "specialist_handoff",
            "selected_agents": selected,
            "skipped_agents": skipped,
            "max_revisions": 2,
            "reason_codes": ["intent:feasibility_analysis"],
        },
    )
    run_store.record_event(
        run_id,
        "maf_agent_completed",
        {
            "status": "completed",
            "agent_id": "df-coordinator",
            "duration_ms": 17,
            "input_tokens": 11,
            "output_tokens": 7,
            "total_tokens": 18,
        },
    )
    run_store.complete_run(run_id, final={"text": "done"}, artifact={})

    persisted_detail = run_store.get_run(run_id)
    summary_payload = asyncio.run(control_plane.run_summary_endpoint(run_id))
    trace_payload = asyncio.run(control_plane.run_trace_endpoint(run_id))

    assert summary_payload["maf"] == persisted_detail["maf"]
    assert summary_payload["maf"]["mode"] == "specialist_handoff"
    assert summary_payload["maf"]["selected_agents"] == selected
    assert summary_payload["status"] == persisted_detail["status"]

    probe = subprocess.run(
        ["node", "--input-type=module", "--eval", VIEW_MODEL_PROBE],
        cwd=REPO_ROOT,
        input=json.dumps({"summary": summary_payload, "trace": trace_payload}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert probe.returncode == 0, f"{probe.stdout}\n{probe.stderr}"
    model = json.loads(probe.stdout)
    coordinator = next(agent for agent in model["agents"] if agent["id"] == "df-coordinator")

    assert model["mode"] == "specialist_handoff"
    assert model["selectedAgents"] == selected
    assert model["skippedAgents"] == skipped
    assert model["maxRevisions"] == 2
    assert coordinator["durationMs"] == 17
