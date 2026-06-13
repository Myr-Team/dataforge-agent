from __future__ import annotations

from fastapi.testclient import TestClient

import backend.app as app_module
import backend.workspace_store as workspace_store
from backend.app import app
from backend.customer_text import sanitize_customer_text
from backend.orchestrator import _mcp_tool_allowed, _tool_provenance, _ui_context_lines
from backend.pm_skills import playbook_suggestion
from backend.schemas import ChatRequest


client = TestClient(app)


def test_workspace_dashboard_contract() -> None:
    response = client.get("/api/workspaces/demo-corpus/dashboard")

    assert response.status_code == 200
    data = response.json()
    assert data["workspace_id"] == "demo-corpus"
    assert data["workspace"]["workspace_id"] == "demo-corpus"
    assert isinstance(data["workspaces"], list)
    assert isinstance(data["runs"], list)
    assert isinstance(data["conversations"], list)
    assert data["health"]["service"] == "dataforge-backend"
    assert "dependencies" in data["health"]
    assert "search" in data["health"]["dependencies"]
    workspace = data["workspace"]
    for key in ("row_count", "field_count", "indexed_count", "fill_rate", "signal_score", "signal_distribution", "manifest"):
        assert key in workspace
    assert isinstance(workspace["documents"], list)
    for column in workspace["columns"]:
        assert column["signal"] in {"strong", "mid", "noise"}
        assert 0 <= column["signal_score"] <= 1


def test_workspace_manifest_endpoint() -> None:
    response = client.get("/api/workspaces/demo-corpus/manifest")

    assert response.status_code == 200
    data = response.json()
    assert data["version"] == "dataforge.canonical_manifest.v1"
    assert data["workspace_id"] == "demo-corpus"
    assert "metrics" in data
    assert isinstance(data["documents"], list)


def test_auto_analyze_wraps_chat_stream(monkeypatch) -> None:
    captured: dict[str, ChatRequest] = {}

    async def fake_orchestrate(req: ChatRequest):
        captured["req"] = req
        yield 'event: ready\ndata: {"conversation_id":"conv-test","workspace_id":"demo-corpus"}\n\n'
        yield 'event: role_change\ndata: {"agent":"df-feasibility-analyst"}\n\n'
        yield 'event: answer_delta\ndata: {"delta":"分析"}\n\n'
        yield 'event: answer_delta\ndata: {"delta":"完成"}\n\n'
        yield (
            'event: final\n'
            'data: {"text":"分析完成","artifact":{"workspace_id":"demo-corpus","feasibility":{"dimensions":[]}}}\n\n'
        )

    monkeypatch.setattr(app_module, "orchestrate_chat", fake_orchestrate)

    response = client.post("/api/workspaces/demo-corpus/auto-analyze", json={"playbook": "pricing"})

    assert response.status_code == 200
    data = response.json()
    assert data["conversation_id"] == "conv-test"
    assert data["text"] == "分析完成"
    assert data["answer_delta_chars"] == len("分析完成")
    assert any(item["event"] == "role_change" for item in data["trace"])
    assert captured["req"].playbook == "pricing"
    assert captured["req"].ui_context["auto_analyze"] is True
    assert captured["req"].ui_context["cache_bust"]


def test_pdf_upload_creates_profile_and_manifest(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(workspace_store, "WORKSPACES", tmp_path / "workspaces")
    monkeypatch.setattr(workspace_store, "_index_documents", lambda docs: len(docs))
    monkeypatch.setattr(workspace_store, "_persist_workspace_bundle", lambda **kwargs: {"mode": "test"})
    monkeypatch.setattr(workspace_store, "_persist_workspace_manifest", lambda workspace_id, manifest: None)
    monkeypatch.setattr(workspace_store, "load_workspace_registry", lambda: [])
    monkeypatch.setattr(workspace_store, "workspace_deleted", lambda workspace_id: False)

    pdf_bytes = b"%PDF-1.4\n1 0 obj\n<<>>\nstream\n(climbing campaign conversion data and sponsor notes)\nendstream\nendobj\n%%EOF"
    response = client.post(
        "/api/upload",
        data={"name": "PDF smoke workspace"},
        files=[("file", ("climbing-demo.pdf", pdf_bytes, "application/pdf"))],
    )

    assert response.status_code == 200
    uploaded = response.json()
    assert uploaded["format"] == "pdf"
    assert uploaded["documents"][0]["format"] == "pdf"
    assert uploaded["documents"][0]["status"] in {"已就绪", "部分字段"}

    manifest_path = tmp_path / "workspaces" / uploaded["workspace_id"] / "manifest.json"
    assert manifest_path.exists()


def test_chat_request_keeps_legacy_payload_compatible() -> None:
    req = ChatRequest.model_validate({"workspace_id": "demo-corpus", "message": "你好"})

    assert req.workspace_id == "demo-corpus"
    assert req.message == "你好"
    assert req.conversation_id is None
    assert req.playbook is None
    assert req.artifact_mode is None
    assert req.ui_context == {}


def test_chat_request_accepts_workbench_context() -> None:
    req = ChatRequest.model_validate(
        {
            "workspace_id": "demo-corpus",
            "message": "分析产品机会",
            "playbook": "opportunity_tree",
            "artifact_mode": "full_package",
            "ui_context": {"playbook_label": "机会树", "requested_output": "full_package"},
        }
    )

    assert req.playbook == "opportunity_tree"
    assert req.artifact_mode == "full_package"
    assert req.ui_context["playbook_label"] == "机会树"
    assert any("Playbook guardrail" in line for line in _ui_context_lines(req))


def test_pm_skill_runner_is_method_only() -> None:
    suggestion = playbook_suggestion(
        "pricing",
        {
            "tables": [
                {
                    "row_count": 3,
                    "columns": [{"name": "segment"}, {"name": "revenue"}],
                }
            ]
        },
    )

    assert suggestion["label"] == "定价"
    assert "结论必须" in suggestion["guardrail"]
    assert suggestion["workspace_context"]["row_count"] == 3
    assert suggestion["workspace_context"]["sample_columns"] == ["segment", "revenue"]


def test_mcp_allowlist_and_provenance_contract() -> None:
    assert _mcp_tool_allowed("market_lookup") is True
    assert _mcp_tool_allowed("unknown_write_tool") is False

    provenance = _tool_provenance(
        "market_lookup",
        "会员运营 SaaS; keywords=retention",
        sources=["https://example.com"],
        latency_ms=42,
    )
    assert provenance["tool_name"] == "market_lookup"
    assert provenance["source_type"] == "market_mcp"
    assert provenance["confidence"] == "market_inferred"
    assert provenance["allowed"] is True
    assert provenance["latency_ms"] == 42


def test_customer_text_sanitizer_hides_internal_terms() -> None:
    text = sanitize_customer_text(
        "raw_docs/orders.csv#orders-row-2 shows source_file and market_inferred evidence. data_confirmed chunk_id"
    )

    assert "raw_docs" not in text
    assert "source_file" not in text
    assert "chunk_id" not in text
    assert "市场推断" in text
    assert "工作区证据" in text
