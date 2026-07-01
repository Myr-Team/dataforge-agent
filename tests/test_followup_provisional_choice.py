import backend.orchestrator as orchestrator
from backend.orchestrator import _followup_provisional_choice_assessment
from backend.schemas import ChatRequest, RoutingDecision


DEMO_SITE_SELECTION_PROMPT = (
    "请基于这些楼层级人流信号和周边环境数据，判断我们是否可以把资产防丢硬件沉淀的数据，"
    "产品化成“快闪店/小店选址建议”服务。请给出优先候选点位、证据、风险和一个低成本试点方案。"
)


def test_site_selection_followup_gives_provisional_answer_instead_of_blocking() -> None:
    result = _followup_provisional_choice_assessment(
        ChatRequest(workspace_id="demo-corpus", message=DEMO_SITE_SELECTION_PROMPT),
        {
            "verdict": "conditional",
            "overall_confidence": "market_inferred",
            "opportunity_id": "快闪店/小店选址建议服务",
            "gap_list": ["缺少成本/预算边界，暂不能支撑规模化定价。"],
            "action_plan": ["先选择 2 个楼层级候选点位做一周试点，记录咨询量、转化和单次交付成本。"],
            "citations": [
                {"marker": "[D1]", "snippet": "楼层级人流、停留时长和周边环境字段可用于候选点位初筛。"},
            ],
        },
        {
            "profile_summary": "包含楼层级人流密度、停留时长、周边环境和信号强度字段。",
            "documents": [{"name": "site_signals.csv", "format": "csv"}],
        },
    )

    assert result is not None
    assert result["should_clarify"] is False
    assert result["clarify"] == ""
    assert result["assessment"] == "provisional_supported_with_gaps"
    assert "不能直接给确定建议" not in result["text"]
    assert "优先候选点位" in result["text"]
    assert "低成本试点" in result["text"]


def test_lightweight_followup_routes_choice_question_to_provisional_answer(monkeypatch) -> None:
    last_analysis = {
        "verdict": "conditional",
        "overall_confidence": "market_inferred",
        "opportunity_id": "快闪店/小店选址建议服务",
        "gap_list": ["缺少成本/预算边界，暂不能支撑规模化定价。"],
        "action_plan": ["先选择 2 个楼层级候选点位做一周试点，记录咨询量、转化和单次交付成本。"],
        "citations": [{"marker": "[D1]", "snippet": "楼层级人流和周边环境字段可用于候选点位初筛。"}],
    }
    monkeypatch.setattr(
        orchestrator,
        "workspace_context",
        lambda workspace_id: {
            "workspace_id": workspace_id,
            "name": "演示工作区",
            "doc_count": 3,
            "profile_summary": "包含楼层级人流密度、停留时长、周边环境和信号强度字段。",
            "documents": [{"name": "site_signals.csv", "format": "csv"}],
        },
    )
    monkeypatch.setattr(orchestrator, "_last_analysis_for_workspace", lambda workspace_id, context=None: last_analysis)
    monkeypatch.setattr(
        orchestrator,
        "run_followup_assessment",
        lambda payload: (_ for _ in ()).throw(AssertionError("LLM follow-up should not be called for this guard path")),
    )

    result = orchestrator._lightweight_reply(
        ChatRequest(workspace_id="demo-corpus", message=DEMO_SITE_SELECTION_PROMPT),
        RoutingDecision(
            workspace_id="demo-corpus",
            intent="followup_edit",
            experts=[],
            output_mode="chat",
            needs_clarification=False,
            reason="test",
        ),
        history=[],
    )

    assert result["mode"] == "followup_provisional_choice"
    assert result["should_clarify"] is False
    assert "不能直接给确定建议" not in result["text"]
    assert "优先候选点位" in result["text"]
