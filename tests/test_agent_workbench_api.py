from __future__ import annotations

import time

from fastapi.testclient import TestClient

import backend.app as app_module
import backend.workspace_store as workspace_store
from backend.app import app
from backend.customer_text import sanitize_customer_text
from backend.orchestrator import (
    _image_prompt_from_proposal,
    _mcp_tool_allowed,
    _proposal_image_kind,
    _request_with_history,
    _safe_chat_topic_label,
    _structured_answer_v10,
    _tool_provenance,
    _ui_context_lines,
)
from backend.pm_skills import playbook_suggestion
from backend.schemas import ChatRequest, RoutingDecision
from backend.tools.generate_image import _image_prompt


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
    monkeypatch.setattr(app_module, "_schedule_upload_ingest", lambda result: None)

    pdf_bytes = b"%PDF-1.4\n1 0 obj\n<<>>\nstream\n(climbing campaign conversion data and sponsor notes)\nendstream\nendobj\n%%EOF"
    response = client.post(
        "/api/upload",
        data={"name": "PDF smoke workspace"},
        files=[("file", ("climbing-demo.pdf", pdf_bytes, "application/pdf"))],
    )

    assert response.status_code == 200
    uploaded = response.json()
    assert uploaded["format"] == "pdf"
    assert uploaded["ingest_job_id"]
    assert uploaded["documents"][0]["format"] == "pdf"
    assert uploaded["documents"][0]["status"] == "解析中"

    manifest_path = tmp_path / "workspaces" / uploaded["workspace_id"] / "manifest.json"
    assert manifest_path.exists()

    status = workspace_store.run_workspace_ingest_job(uploaded["workspace_id"], uploaded["ingest_job_id"])
    assert status["state"] in {"ready", "partial"}

    detail = workspace_store.get_workspace_detail(uploaded["workspace_id"])
    assert detail["documents"][0]["status"] in {"已就绪", "部分字段"}
    assert detail["documents"][0]["format"] == "pdf"
    assert detail["manifest"]["metrics"]["field_count"] >= 1


def test_async_upload_keeps_file_failures_isolated_and_idempotent(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(workspace_store, "WORKSPACES", tmp_path / "workspaces")
    monkeypatch.setattr(workspace_store, "_index_documents", lambda docs: len(docs))
    monkeypatch.setattr(workspace_store, "_persist_workspace_bundle", lambda **kwargs: {"mode": "test"})
    monkeypatch.setattr(workspace_store, "_persist_workspace_manifest", lambda workspace_id, manifest: None)
    monkeypatch.setattr(workspace_store, "load_workspace_registry", lambda: [])
    monkeypatch.setattr(workspace_store, "workspace_deleted", lambda workspace_id: False)
    monkeypatch.setattr(app_module, "_schedule_upload_ingest", lambda result: None)

    csv_bytes = b"segment,revenue\nA,100\nB,30\n"
    bad_bytes = b"\x00\x01not supported"
    start = time.perf_counter()
    response = client.post(
        "/api/upload",
        data={"name": "Async mixed workspace"},
        files=[
            ("file", ("metrics.csv", csv_bytes, "text/csv")),
            ("file", ("broken.bin", bad_bytes, "application/octet-stream")),
        ],
    )
    elapsed = time.perf_counter() - start
    assert response.status_code == 200
    uploaded = response.json()
    assert elapsed < 3
    assert [doc["status"] for doc in uploaded["documents"]] == ["解析中", "解析中"]

    status = workspace_store.run_workspace_ingest_job(uploaded["workspace_id"], uploaded["ingest_job_id"])
    assert status["state"] == "partial"
    files = {item["name"]: item for item in status["files"]}
    assert files["metrics.csv"]["status"] == "已就绪"
    assert files["broken.bin"]["status"] == "失败"
    assert files["broken.bin"]["error"]

    second = client.post(
        "/api/upload",
        data={"name": "Async mixed workspace", "workspace_id": uploaded["workspace_id"]},
        files=[("file", ("metrics.csv", csv_bytes, "text/csv"))],
    )
    assert second.status_code == 200
    repeated = second.json()
    assert repeated["ingest_job_id"] is None
    assert len([doc for doc in repeated["documents"] if doc["name"] == "metrics.csv"]) == 1
    repeated_detail = workspace_store.get_workspace_detail(uploaded["workspace_id"])
    assert " | " not in repeated_detail["profile_summary"]


def test_upload_workspace_prefers_blob_state_over_stale_local(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(workspace_store, "WORKSPACES", tmp_path / "workspaces")
    workspace_id = "upload-shared-state"
    local_dir = workspace_store.WORKSPACES / workspace_id
    local_dir.mkdir(parents=True)
    (local_dir / "workspace.json").write_text(
        '{"workspace_id":"upload-shared-state","name":"Local stale","format":"csv","documents":[{"name":"one.csv","status":"解析中"}],"profile_file":"profile.json","profile_summary":"local"}',
        encoding="utf-8",
    )
    (local_dir / "profile.json").write_text(
        '{"workspace_id":"upload-shared-state","name":"Local stale","tables":[]}',
        encoding="utf-8",
    )
    blob_meta = {
        "workspace_id": workspace_id,
        "name": "Blob current",
        "format": "mixed",
        "documents": [
            {"name": "one.csv", "source_file": "raw_docs/one.csv", "status": "已就绪"},
            {"name": "two.json", "source_file": "raw_docs/two.json", "status": "已就绪"},
        ],
        "profile_file": "profile.json",
        "profile_summary": "blob current",
        "indexed_count": 2,
    }
    blob_profile = {
        "workspace_id": workspace_id,
        "name": "Blob current",
        "format": "mixed",
        "tables": [],
        "documents": blob_meta["documents"],
        "profile_summary": "blob current",
    }

    monkeypatch.setattr(workspace_store, "_blob_configured_for_workspace", lambda: True)
    monkeypatch.setattr(workspace_store, "workspace_deleted", lambda value: False)
    monkeypatch.setattr(
        workspace_store,
        "download_blob_json",
        lambda blob_name: blob_meta if blob_name.endswith("/workspace.json") else blob_profile if blob_name.endswith("/profile.json") else None,
    )

    detail = workspace_store.get_workspace_detail(workspace_id)

    assert detail["name"] == "Blob current"
    assert [item["name"] for item in detail["documents"]] == ["one.csv", "two.json"]
    assert all(item["status"] == "已就绪" for item in detail["documents"])


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


def test_feasibility_renderer_rewrites_raw_replay_action_plan() -> None:
    req = ChatRequest(workspace_id="demo-corpus", message="请自动分析这批攀岩馆数据能产品化成什么")
    decision = RoutingDecision(
        workspace_id="demo-corpus",
        intent="feasibility",
        experts=["df-feasibility-analyst"],
        output_mode="report",
        needs_clarification=False,
        reason="test",
    )
    evidence = {
        "source_type": "corpus",
        "ref": "raw_docs/operations_metrics.xlsx#operations_metrics-PilotMetrics-row-2",
        "quote": "深圳一家连锁攀岩馆会员到访、活动报名、复购和异业合作线索。",
    }
    artifact = {
        "workspace_id": "demo-corpus",
        "corpus": {
            "hits": [
                {
                    "source_file": "raw_docs/operations_metrics.xlsx",
                    "chunk_id": "operations_metrics-PilotMetrics-row-2",
                    "content": evidence["quote"],
                    "title": "攀岩馆会员数据",
                }
            ],
        },
        "feasibility": {
            "opportunity_id": "攀岩馆会员增长机会",
            "recommendation": "先用深圳一家连锁攀岩馆品牌，本工作区为演示用合成数据确定试点。",
            "action_plan": [
                "先用深圳一家连锁攀岩馆品牌，本工作区为演示用合成数据确定首轮试点。",
                "把业务类别为“L03”，资料分组为“岩点装备”转成具体活动钩子。",
                "用业务类别为“L08”，资料分组为“悦动健身”设定复盘口径。",
            ],
            "dimensions": [
                {"name": "market", "score": 3, "rationale": "有需求线索", "evidence": [evidence], "confidence": "data_confirmed"},
                {"name": "technical", "score": 3, "rationale": "可执行", "evidence": [evidence], "confidence": "data_confirmed"},
                {"name": "asset_data", "score": 3, "rationale": "数据可用", "evidence": [evidence], "confidence": "data_confirmed"},
                {"name": "resource_cost", "score": 3, "rationale": "成本待验证", "evidence": [evidence], "confidence": "data_confirmed"},
                {"name": "differentiation_risk", "score": 3, "rationale": "差异待验证", "evidence": [evidence], "confidence": "data_confirmed"},
            ],
            "verdict": "conditional",
            "overall_confidence": "data_confirmed",
            "gap_list": ["缺少真实投放成本和转化阈值。"],
        },
        "audit": {"verdict": "pass"},
    }

    answer = _structured_answer_v10(req, decision, artifact)
    markdown = answer["markdown"]
    scores = [item["score"] for item in artifact["feasibility"]["dimensions"]]

    assert "业务类别" not in markdown
    assert "资料分组" not in markdown
    assert "L03" not in markdown
    assert "本工作区为演示用" not in markdown
    assert "先把" in markdown
    assert len(set(scores)) > 1


def test_chat_mode_renderer_is_concise_and_not_report_like() -> None:
    req = ChatRequest(
        workspace_id="demo-corpus",
        message="圣诞跨馆联赛值得再办吗？",
        artifact_mode="chat",
        ui_context={"mode": "conversation"},
    )
    decision = RoutingDecision(
        workspace_id="demo-corpus",
        intent="feasibility_analysis",
        experts=["df-corpus-analyst", "df-feasibility-analyst"],
        output_mode="chat",
        needs_clarification=False,
        reason="test",
    )
    evidence = {
        "source_type": "corpus",
        "ref": "raw_docs/climb.xlsx#climb-row-2",
        "quote": "资料分组: 圣诞跨馆联赛; 时间维度: 2024-12; 新客报名: 38; 到店转化: 21%; 复购: 有提升",
    }
    artifact = {
        "workspace_id": "demo-corpus",
        "corpus": {
            "hits": [
                {
                    "source_file": "raw_docs/climb.xlsx",
                    "chunk_id": "climb-row-2",
                    "content": evidence["quote"],
                    "title": "圣诞跨馆联赛复盘",
                }
            ]
        },
        "feasibility": {
            "opportunity_id": "圣诞跨馆联赛",
            "verdict": "conditional",
            "overall_confidence": "data_confirmed",
            "dimensions": [
                {"name": "market", "score": 4, "rationale": "有活动报名与到店信号", "evidence": [evidence], "confidence": "data_confirmed"}
            ],
            "gap_list": ["缺少真实预算、单次获客成本和复购阈值。"],
        },
    }

    answer = _structured_answer_v10(req, decision, artifact)
    markdown = answer["markdown"]

    assert 60 <= len(markdown) <= 340
    assert "行动方案" not in markdown
    assert "评分" not in markdown
    assert "\n1." not in markdown
    assert "资料分组" not in markdown
    assert "时间维度" not in markdown
    assert "L03" not in markdown
    assert answer["output_contract"]["answer_style"] == "concise_conversation"
    assert answer["output_contract"]["no_dimension_scores"] is True
    assert _safe_chat_topic_label("Required") == "当前工作区机会"


def test_conversation_history_is_used_for_budget_followup() -> None:
    history = [
        {"role": "user", "text": "圣诞跨馆联赛值得再办吗？"},
        {"role": "assistant", "text": "可以继续，但建议缩小成一轮可复盘试点。"},
    ]
    req = ChatRequest(
        workspace_id="demo-corpus",
        conversation_id="conv-budget",
        message="那如果预算只有一半呢？",
        artifact_mode="chat",
        ui_context={"mode": "conversation"},
    )
    working_req = _request_with_history(req, history)

    assert "Conversation history for continuity" in working_req.message
    assert "圣诞跨馆联赛值得再办吗" in working_req.message
    assert "Current user message" in working_req.message
    assert "预算只有一半" in working_req.message

    decision = RoutingDecision(
        workspace_id="demo-corpus",
        intent="corpus_qa",
        experts=["df-corpus-analyst"],
        output_mode="chat",
        needs_clarification=False,
        reason="test",
    )
    artifact = {
        "workspace_id": "demo-corpus",
        "_conversation_history": history,
        "corpus": {
            "hits": [
                {
                    "source_file": "raw_docs/climb.xlsx",
                    "chunk_id": "climb-row-5",
                    "content": "活动名称: 圣诞跨馆联赛; 报名人数: 56; 到店转化: 28%; 预算: 中等; 复购: 有提升",
                    "title": "圣诞跨馆联赛复盘",
                }
            ]
        },
        "feasibility": {"opportunity_id": "圣诞跨馆联赛", "verdict": "conditional", "gap_list": []},
    }

    markdown = _structured_answer_v10(working_req, decision, artifact)["markdown"]

    assert "预算减半" in markdown
    assert "圣诞跨馆联赛" in markdown
    assert "行动方案" not in markdown
    assert "\n1." not in markdown


def test_producer_image_prompt_uses_deliverable_type_and_logo_instruction() -> None:
    product_proposal = {
        "title": "会员续费提醒产品",
        "feasibility": {"opportunity_id": "会员续费提醒产品", "recommendation": "做一个会员续费提醒小程序产品", "dimensions": []},
        "market": {},
        "reference_images": [],
    }
    event_proposal = {
        "title": "圣诞跨馆联赛活动",
        "feasibility": {"opportunity_id": "圣诞跨馆联赛活动", "recommendation": "生成活动海报和报名转化企划", "dimensions": []},
        "market": {},
        "reference_images": [{"role": "logo", "blob_url": "https://example.test/logo.png"}],
    }
    dashboard_proposal = {
        "title": "门店经营分析看板",
        "feasibility": {"opportunity_id": "门店经营分析看板", "recommendation": "输出报告和 dashboard 概念", "dimensions": []},
        "market": {},
        "reference_images": [],
    }

    assert _proposal_image_kind(product_proposal)[0] == "product_concept"
    assert _proposal_image_kind(event_proposal)[0] == "event_poster"
    assert _proposal_image_kind(dashboard_proposal)[0] == "analytics_board"

    product_prompt = _image_prompt_from_proposal(product_proposal)
    event_prompt = _image_prompt_from_proposal(event_proposal)
    wrapped_prompt = _image_prompt(event_prompt)

    assert "产品概念" in product_prompt or "product UI" in product_prompt
    assert "活动海报" in event_prompt
    assert "logo" in event_prompt.lower()
    assert "visibly" in event_prompt
    assert "business stakeholders" not in wrapped_prompt
    assert "meeting discussion" not in wrapped_prompt.lower()
