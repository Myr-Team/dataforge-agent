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
    result = create_workspace_upload_job(
        files=[
            {"filename": "device_events.csv", "content": csv_content, "content_type": "text/csv"},
            {
                "filename": "surrounding_env.xlsx",
                "content": _xlsx_bytes(),
                "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            },
            {"filename": "market_notes.md", "content": md_content, "content_type": "text/markdown"},
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
    finally:
        dw._blob_containers = original_blob
        dw._sql_tables = original_sql


def main() -> None:
    tests = [
        test_files_content_quality_edit_history,
        test_analyze_selected_files_contract,
        test_connector_credentials_are_session_scoped_and_not_echoed,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")


if __name__ == "__main__":
    main()
