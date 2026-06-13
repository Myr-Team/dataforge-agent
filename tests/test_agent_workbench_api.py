from __future__ import annotations

from fastapi.testclient import TestClient

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
