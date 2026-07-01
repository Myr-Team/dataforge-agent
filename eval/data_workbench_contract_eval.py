from __future__ import annotations

import asyncio
import io
import json
import sys
import uuid
from pathlib import Path
from typing import Any

from openpyxl import Workbook


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import data_workbench as dw  # noqa: E402
from backend import orchestrator  # noqa: E402
from backend.schemas import ChatRequest  # noqa: E402
from backend.workspace_store import create_workspace_upload_job, delete_workspace, run_workspace_ingest_job  # noqa: E402


def _xlsx_bytes() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "events"
    sheet.append(["event_id", "region", "latency_ms"])
    sheet.append(["e1", "north", 12])
    sheet.append(["e2", "south", 18])
    sheet.append(["e3", "south", 900])
    out = io.BytesIO()
    workbook.save(out)
    workbook.close()
    return out.getvalue()


def _create_workspace() -> dict[str, Any]:
    csv_content = (
        "id,region,score,note\n"
        "1,north,10,ok\n"
        "2,south,,missing\n"
        "2,south,,missing\n"
        "3,west,999,outlier\n"
    ).encode("utf-8")
    md_content = "# Market notes\n\nCustomers mention installation friction and missing parts.\n".encode("utf-8")
    json_content = json.dumps(
        [
            {"region": "north", "demand_score": 8, "pilot_cost": 1200},
            {"region": "south", "demand_score": 3, "pilot_cost": None},
        ],
        ensure_ascii=False,
    ).encode("utf-8")
    result = create_workspace_upload_job(
        files=[
            {"filename": "device_events.csv", "content": csv_content, "content_type": "text/csv"},
            {
                "filename": "surrounding_env.xlsx",
                "content": _xlsx_bytes(),
                "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            },
            {"filename": "market_notes.md", "content": md_content, "content_type": "text/markdown"},
            {"filename": "pilot_regions.json", "content": json_content, "content_type": "application/json"},
        ],
        name=f"Round3 Workbench Contract {uuid.uuid4().hex[:6]}",
    )
    if result.get("ingest_job_id"):
        run_workspace_ingest_job(result["workspace_id"], result["ingest_job_id"])
    return result


def test_files_content_quality_edit_history() -> None:
    workspace = _create_workspace()
    workspace_id = workspace["workspace_id"]
    try:
        files = dw.list_workspace_files(workspace_id)
        assert files["storage"]["used_bytes"] > 0
        data_files = files["groups"][0]["files"]
        doc_files = files["groups"][1]["files"]
        assert {item["type"] for item in data_files} >= {"csv", "xlsx"}
        assert any(item["type"] == "md" for item in doc_files)

        csv_file = next(item for item in data_files if item["type"] == "csv")
        xlsx_file = next(item for item in data_files if item["type"] == "xlsx")
        json_file = next(item for item in data_files if item["type"] == "json")
        md_file = next(item for item in doc_files if item["type"] == "md")

        csv_preview = dw.preview_file_content(workspace_id, csv_file["id"], limit=2, offset=1)
        assert csv_preview["kind"] == "table"
        assert csv_preview["total_rows"] == 4
        assert [col["name"] for col in csv_preview["columns"]] == ["id", "region", "score", "note"]
        assert csv_preview["rows"][0][1] == "south"

        xlsx_preview = dw.preview_file_content(workspace_id, xlsx_file["id"], limit=2, offset=0)
        assert xlsx_preview["kind"] == "table"
        assert xlsx_preview["total_rows"] == 3
        assert [col["name"] for col in xlsx_preview["columns"]] == ["event_id", "region", "latency_ms"]

        quality = dw.file_quality(workspace_id, csv_file["id"])
        assert quality["field_mapping"]["mapped"] == 4
        assert quality["quality"]["missing_pct"] > 0
        assert quality["quality"]["duplicate_pct"] > 0
        assert quality["validation"]["status"] in {"warn", "failed"}

        json_preview = dw.preview_file_content(workspace_id, json_file["id"], limit=2, offset=0)
        assert json_preview["kind"] == "json"
        assert json_preview["total_chars"] > 20
        assert "pilot_cost" in json_preview["text"]
        json_quality = dw.file_quality(workspace_id, json_file["id"])
        assert json_quality["field_mapping"]["total"] == 3
        assert json_quality["quality"]["missing_pct"] > 0

        saved = dw.save_table_cells(workspace_id, csv_file["id"], {"edits": [{"row": 0, "col": "score", "value": "11"}]})
        assert saved["saved_at"]
        changed = dw.preview_file_content(workspace_id, csv_file["id"], limit=1, offset=0)
        assert changed["rows"][0][2] == "11"
        history = dw.file_history(workspace_id, csv_file["id"])
        assert history and "单元格" in history[0]["change_summary"]

        md_saved = dw.save_markdown_content(workspace_id, md_file["id"], {"text": "# Updated\n\nEvidence note."})
        assert md_saved["saved_at"]
        md_preview = dw.preview_file_content(workspace_id, md_file["id"], limit=1, offset=0)
        assert md_preview["kind"] == "markdown"
        assert "Updated" in md_preview["text"]

        created_md = dw.create_workspace_file(workspace_id, {"name": "pilot_notes", "type": "md", "text": "# Pilot notes\n\nNew demand signal."})
        assert created_md["file"]["type"] == "md"
        created_md_preview = dw.preview_file_content(workspace_id, created_md["file"]["id"], limit=1, offset=0)
        assert created_md_preview["kind"] == "markdown"
        assert "Pilot notes" in created_md_preview["text"]

        created_csv = dw.create_workspace_file(
            workspace_id,
            {
                "name": "validation_metrics",
                "kind": "table",
                "columns": ["metric", "value"],
                "rows": [["trial_signup", "12"], ["pilot_cost", "3400"]],
            },
        )
        assert created_csv["file"]["type"] == "csv"
        created_csv_preview = dw.preview_file_content(workspace_id, created_csv["file"]["id"], limit=10, offset=0)
        assert created_csv_preview["rows"][0] == ["trial_signup", "12"]
        assert created_csv_preview["total_cols"] == 2

        structured = dw.save_table_cells(
            workspace_id,
            created_csv["file"]["id"],
            {
                "add_rows": [{"values": ["pilot_retention", "0.62"]}],
                "add_cols": [{"name": "owner", "values": ["growth", "finance", "ops"]}],
                "delete_rows": [1],
            },
        )
        assert structured["changes"]["rows_added"] == 1
        assert structured["changes"]["rows_deleted"] == 1
        assert structured["changes"]["cols_added"] == 1
        structured_preview = dw.preview_file_content(workspace_id, created_csv["file"]["id"], limit=10, offset=0)
        assert [col["name"] for col in structured_preview["columns"]] == ["metric", "value", "owner"]
        assert structured_preview["total_rows"] == 2

        initial_mapping = dw.file_field_mapping(workspace_id, created_csv["file"]["id"])
        assert initial_mapping["field_mapping"]["total"] == 3
        saved_mapping = dw.save_file_field_mapping(
            workspace_id,
            created_csv["file"]["id"],
            {"mapping": {"metric": {"target": "business_metric", "notes": "Used to compare pilot outcomes"}}},
        )
        assert saved_mapping["overrides"]["metric"]["target"] == "business_metric"
        mapped_quality = dw.file_quality(workspace_id, created_csv["file"]["id"])
        metric_field = next(item for item in mapped_quality["field_mapping"]["fields"] if item["name"] == "metric")
        assert metric_field["target"] == "business_metric"
        assert metric_field["mapping_source"] == "user"

        deleted = dw.delete_workspace_file(workspace_id, created_md["file"]["id"])
        assert deleted["deleted"] is True
        after_delete = dw.list_workspace_files(workspace_id)
        assert all(created_md["file"]["id"] != item["id"] for group in after_delete["groups"] for item in group["files"])
    finally:
        delete_workspace(workspace_id)


def test_analyze_selected_files_contract() -> None:
    workspace = _create_workspace()
    workspace_id = workspace["workspace_id"]
    original = dw.orchestrate_chat

    async def fake_orchestrate_chat(req: Any):
        yield f"event: ready\ndata: {json.dumps({'conversation_id': 'conv-test'})}\n\n"
        final = {
            "text": "analysis complete",
            "mode": "analysis",
            "artifact": {"workspace_id": req.workspace_id, "conversation_id": "conv-test"},
        }
        yield f"event: final\ndata: {json.dumps(final)}\n\n"

    try:
        files = dw.list_workspace_files(workspace_id)
        file_id = files["groups"][0]["files"][0]["id"]
        dw.orchestrate_chat = fake_orchestrate_chat
        result = asyncio.run(dw.analyze_selected_files(workspace_id, {"file_ids": [file_id]}))
        assert result["status"] == "started"
        assert result["mode"] == "analysis"
        assert result["conversation_id"] == "conv-test"
        assert result["jump"] == {"view": "agent_flow", "conversation_id": "conv-test"}
        assert result["selected_files"][0]["id"] == file_id
    finally:
        dw.orchestrate_chat = original
        delete_workspace(workspace_id)


def test_connector_credentials_are_session_scoped_and_not_echoed() -> None:
    workspace_id = "demo-contract"
    original_blob = dw._blob_containers
    original_sql = dw._sql_tables
    try:
        dw._blob_containers = lambda payload: [{"name": "container-a", "updated_at": None}]
        fake_blob_secret = "not-a-real-key"
        fake_connection_string = "DefaultEndpointsProtocol=https;AccountName=demo;" + "Account" + f"Key={fake_blob_secret};EndpointSuffix=core.windows.net"
        blob = dw.connect_blob(
            workspace_id,
            {"connection_string": fake_connection_string},
        )
        assert blob["status"] == "connected"
        assert blob["credential_echo"] is None
        assert fake_blob_secret not in json.dumps(blob)
        assert dw._CONNECTORS.get(blob["connection_id"], "blob", workspace_id)["connection_string"].startswith("DefaultEndpointsProtocol")

        dw._sql_tables = lambda payload: [{"schema": "dbo", "name": "Events", "id": "dbo.Events"}]
        sql = dw.connect_sql(workspace_id, {"server": "server.example", "database": "db", "username": "user", "password": "secret"})
        assert sql["status"] == "connected"
        assert sql["tables"][0]["id"] == "dbo.Events"
        assert sql["credential_echo"] is None
        assert "secret" not in json.dumps(sql)
        sql_from_string = dw.connect_sql(
            workspace_id,
            {
                "connection_string": (
                    "Server=tcp:server.example,1433;Initial Catalog=db;"
                    "Persist Security Info=False;User ID=user;Password=secret;"
                    "MultipleActiveResultSets=False;Encrypt=True;"
                    "TrustServerCertificate=False;Connection Timeout=30;"
                )
            },
        )
        assert sql_from_string["status"] == "connected"
        assert sql_from_string["tables"][0]["id"] == "dbo.Events"
        stored_sql = dw._CONNECTORS.get(sql_from_string["connection_id"], "sql", workspace_id)
        assert stored_sql["server"] == "tcp:server.example,1433"
        assert stored_sql["database"] == "db"
        assert stored_sql["username"] == "user"
        assert stored_sql["password"] == "secret"
        assert "connection_string" not in stored_sql
        assert "secret" not in json.dumps(sql_from_string)
        status = dw.connector_status(workspace_id, "sql", sql["connection_id"])
        assert status["status"] == "connected"
        assert status["expires_at"]
        disconnected = dw.disconnect_connector(workspace_id, {"kind": "sql", "connection_id": sql["connection_id"]})
        assert disconnected["disconnected"] is True
        try:
            dw.connector_status(workspace_id, "sql", sql["connection_id"])
            raise AssertionError("disconnected connector should not validate")
        except ValueError as exc:
            assert "not found or expired" in str(exc)
    finally:
        dw._blob_containers = original_blob
        dw._sql_tables = original_sql


def test_followup_clarifies_when_requested_evidence_is_missing() -> None:
    req = ChatRequest(workspace_id="demo-contract", message="应该在哪个区域先做试点？")
    result = orchestrator._followup_evidence_clarification(
        req,
        {"gap_list": ["上一轮分析缺少区域、门店或城市层面的对比证据。"]},
        {"profile_summary": "工作区只有总体需求和成本摘要。", "customer_summary": "", "documents": []},
    )
    assert result is not None
    assert result["should_clarify"] is True
    assert result["assessment"] == "needs_clarification"
    assert "区域" in result["clarify"]


def test_produce_roadmap_and_validation_plan_contract() -> None:
    original_summary = orchestrator.run_executive_summary
    original_references = orchestrator.workspace_reference_images
    created_paths: list[Path] = []
    try:
        orchestrator.run_executive_summary = lambda payload: {"headline": "Pilot validation", "points": ["Evidence-gated rollout"]}
        orchestrator.workspace_reference_images = lambda workspace_id: []
        result = orchestrator.produce_from_existing_report(
            {
                "workspace_id": "demo-contract",
                "kinds": ["roadmap", "validation_plan"],
                "feasibility": {
                    "opportunity_id": "pilot-validation",
                    "verdict": "conditional",
                    "overall_confidence": "medium",
                    "recommendation": "Run a limited pilot before expansion.",
                    "gap_list": ["补齐转化证据", "核算单位交付成本"],
                    "action_plan": ["定义试点样本", "记录报名到付费转化", "复核交付成本"],
                    "dimensions": [
                        {"name": "demand", "score": 3, "rationale": "Uploaded notes show repeated requests."},
                        {"name": "cost", "score": 2, "rationale": "Cost data is incomplete."},
                    ],
                },
                "answer": {"text": "Use evidence-gated rollout."},
            }
        )
        assert result["artifact_urls"]["roadmap"].endswith(".md")
        assert result["artifact_urls"]["validation_plan"].endswith(".md")
        assert "补齐转化证据" in result["roadmap"]["markdown"]
        assert "核算单位交付成本" in result["validation_plan"]["markdown"]
        created_paths.extend(Path(result[key]["local_path"]) for key in ("roadmap", "validation_plan"))
    finally:
        orchestrator.run_executive_summary = original_summary
        orchestrator.workspace_reference_images = original_references
        for path in created_paths:
            try:
                path.unlink()
            except OSError:
                pass


def main() -> None:
    tests = [
        test_files_content_quality_edit_history,
        test_analyze_selected_files_contract,
        test_connector_credentials_are_session_scoped_and_not_echoed,
        test_followup_clarifies_when_requested_evidence_is_missing,
        test_produce_roadmap_and_validation_plan_contract,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")


if __name__ == "__main__":
    main()
