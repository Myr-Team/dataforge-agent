from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app import app
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
            "playbook": "Opportunity Solution Tree",
            "artifact_mode": "full_package",
            "ui_context": {"playbook_label": "机会树", "requested_output": "full_package"},
        }
    )

    assert req.playbook == "Opportunity Solution Tree"
    assert req.artifact_mode == "full_package"
    assert req.ui_context["playbook_label"] == "机会树"
