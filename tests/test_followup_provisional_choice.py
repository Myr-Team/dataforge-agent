import os

import backend.orchestrator as orchestrator
from backend.customer_text import sanitize_customer_text
from backend.orchestrator import _followup_plan_draft, _followup_provisional_choice_assessment
from backend.schemas import ChatRequest, RoutingDecision


os.environ["DF_ANSWER_COMPOSER_ENABLED"] = "0"


DEMO_SITE_SELECTION_PROMPT = (
    "请基于这些楼层级人流信号和周边环境数据，判断我们是否可以把资产防丢硬件沉淀的数据，"
    "产品化成“快闪店/小店选址建议”服务。请给出优先候选点位、证据、风险和一个低成本试点方案。"
)


def test_structured_chat_fallback_uses_markdown_sections(monkeypatch) -> None:
    monkeypatch.setattr(orchestrator, "_llm_chat_answer", lambda *args, **kwargs: None)

    result = orchestrator._structured_chat_answer_v10(
        ChatRequest(workspace_id="demo-corpus", message="这个方向下一步怎么判断？"),
        RoutingDecision(
            workspace_id="demo-corpus",
            intent="corpus_qa",
            experts=[],
            output_mode="chat",
            needs_clarification=False,
            reason="test",
        ),
        {
            "workspace_id": "demo-corpus",
            "corpus": {
                "hits": [
                    {
                        "title": "候选点位信号",
                        "source_file": "candidate_sites.csv",
                        "content": "核心商圈人流高，停留时长更长，可用于优先筛选试点点位。",
                    }
                ]
            },
            "feasibility": {
                "verdict": "conditional",
                "opportunity_id": "小店选址建议服务",
                "gap_list": ["缺少真实转化率和单次交付成本。"],
            },
        },
    )

    markdown = result["markdown"]
    assert markdown.startswith("## 综合判断")
    assert "\n\n## 依据" in markdown
    assert "\n- " in markdown
    assert "\n\n## 下一步" in markdown
    assert "小店选址建议服务" in markdown


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


def test_lightweight_followup_plan_request_returns_offer(monkeypatch) -> None:
    last_analysis = {
        "verdict": "conditional",
        "overall_confidence": "market_inferred",
        "opportunity_id": "快闪店/小店选址建议服务",
        "recommendation": "先用楼层级人流和周边环境做小范围选址试点。",
        "gap_list": ["缺少真实转化率和单次交付成本。"],
        "action_plan": ["选 2 个候选点位做周末试点。", "记录咨询量、转化和执行成本。"],
        "dimensions": [{"name": "data_sufficiency", "score": 3, "confidence": "data_confirmed"}],
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
        lambda payload: (_ for _ in ()).throw(AssertionError("Plan draft should not call generic follow-up LLM")),
    )

    result = orchestrator._lightweight_reply(
        ChatRequest(workspace_id="demo-corpus", message="请把刚才的判断整理出一版方案看看"),
        RoutingDecision(
            workspace_id="demo-corpus",
            intent="followup_edit",
            experts=[],
            output_mode="chat",
            needs_clarification=False,
            reason="test",
        ),
        history=[{"role": "assistant", "text": "上一轮判断：有条件可行。"}],
    )

    assert result["mode"] == "followup_plan_draft"
    assert result["is_plan"] is True
    assert result["produce_offer"]["kind"] == "proposal"
    assert "## 一句话方案" in result["text"]
    assert "## 试点动作" in result["text"]
    assert "快闪店/小店选址建议服务" in result["text"]


def test_answer_composer_can_drive_plan_reply_and_offer(monkeypatch) -> None:
    monkeypatch.setenv("DF_ANSWER_COMPOSER_ENABLED", "1")
    last_analysis = {
        "verdict": "conditional",
        "overall_confidence": "market_inferred",
        "opportunity_id": "快闪店/小店选址建议服务",
        "recommendation": "先用楼层级人流和周边环境做小范围选址试点。",
        "gap_list": ["缺少真实转化率和单次交付成本。"],
        "action_plan": ["选 2 个候选点位做周末试点。"],
        "dimensions": [{"name": "data_sufficiency", "score": 3, "confidence": "data_confirmed"}],
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
        "run_answer_composer",
        lambda payload: {
            "text": "## 一句话方案\n先选两个高信号点位做周末低成本试点。\n\n## 风险与待验证\n- 需要补真实转化率。",
            "mode": "answer_composer",
            "response_id": "resp_test",
            "usage": {"total_tokens": 10},
            "assessment": "needs_more_evidence",
            "gaps": ["缺少真实转化率"],
            "clarify": "",
            "should_clarify": False,
            "is_plan": True,
            "needs_full_analysis": False,
            "route_hint": "plan_draft",
            "answer_type": "plan",
        },
    )
    monkeypatch.setattr(
        orchestrator,
        "run_followup_assessment",
        lambda payload: (_ for _ in ()).throw(AssertionError("composer should answer before generic follow-up")),
    )

    result = orchestrator._lightweight_reply(
        ChatRequest(workspace_id="demo-corpus", message="请基于刚才的结论生成一版方案"),
        RoutingDecision(
            workspace_id="demo-corpus",
            intent="followup_edit",
            experts=[],
            output_mode="chat",
            needs_clarification=False,
            reason="test",
        ),
        history=[{"role": "assistant", "text": "上一轮判断：有条件可行。"}],
    )

    assert result["mode"] == "answer_composer"
    assert result["is_plan"] is True
    assert result["produce_offer"]["kind"] == "proposal"
    assert result["route_hint"] == "plan_draft"
    assert "## 一句话方案" in result["text"]


def test_answer_composer_clarify_becomes_visible_reply(monkeypatch) -> None:
    monkeypatch.setenv("DF_ANSWER_COMPOSER_ENABLED", "1")
    last_analysis = {
        "verdict": "conditional",
        "overall_confidence": "market_inferred",
        "opportunity_id": "快闪店/小店选址建议服务",
        "gap_list": ["缺少明确比较对象。"],
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
        "run_answer_composer",
        lambda payload: {
            "text": "",
            "mode": "answer_composer",
            "response_id": "resp_clarify",
            "usage": {"total_tokens": 8},
            "assessment": "unclear",
            "gaps": ["缺少明确比较对象"],
            "clarify": "你想比较哪些候选点位，还是想先看当前数据里最值得试点的两个点位？",
            "should_clarify": True,
            "is_plan": False,
            "needs_full_analysis": False,
            "route_hint": "clarify",
            "answer_type": "clarify",
        },
    )

    result = orchestrator._lightweight_reply(
        ChatRequest(workspace_id="demo-corpus", message="那这个呢？"),
        RoutingDecision(
            workspace_id="demo-corpus",
            intent="followup_edit",
            experts=[],
            output_mode="chat",
            needs_clarification=False,
            reason="test",
        ),
        history=[{"role": "assistant", "text": "上一轮判断：有条件可行。"}],
    )

    assert result["mode"] == "answer_composer"
    assert result["should_clarify"] is True
    assert result["text"] == "你想比较哪些候选点位，还是想先看当前数据里最值得试点的两个点位？"


def test_answer_composer_brief_reply_is_normalized_to_markdown(monkeypatch) -> None:
    monkeypatch.setenv("DF_ANSWER_COMPOSER_ENABLED", "1")
    last_analysis = {
        "verdict": "conditional",
        "overall_confidence": "market_inferred",
        "opportunity_id": "快闪店/小店选址建议服务",
        "gap_list": ["缺少真实转化率和试点成本。"],
        "action_plan": ["先选 2 个候选点位做小样本试点。"],
    }
    monkeypatch.setattr(
        orchestrator,
        "workspace_context",
        lambda workspace_id: {
            "workspace_id": workspace_id,
            "name": "演示工作区",
            "doc_count": 3,
            "profile_summary": "包含楼层级人流、停留和周边环境字段。",
            "documents": [{"name": "site_signals.csv", "format": "csv"}],
        },
    )
    monkeypatch.setattr(orchestrator, "_last_analysis_for_workspace", lambda workspace_id, context=None: last_analysis)
    monkeypatch.setattr(
        orchestrator,
        "run_answer_composer",
        lambda payload: {
            "text": "可以继续推进，但要先把试点范围收窄。依据是已有楼层级人流和周边环境信号可做初筛。下一步先验证转化率和试点成本。",
            "mode": "answer_composer",
            "response_id": "resp_brief",
            "usage": {"total_tokens": 12},
            "assessment": "supported_with_gaps",
            "gaps": ["缺少真实转化率和试点成本。"],
            "clarify": "",
            "should_clarify": False,
            "is_plan": False,
            "needs_full_analysis": False,
            "route_hint": "direct_answer",
            "answer_type": "brief_answer",
        },
    )

    result = orchestrator._lightweight_reply(
        ChatRequest(workspace_id="demo-corpus", message="这个方向靠谱吗？"),
        RoutingDecision(
            workspace_id="demo-corpus",
            intent="followup_edit",
            experts=[],
            output_mode="chat",
            needs_clarification=False,
            reason="test",
        ),
        history=[{"role": "assistant", "text": "上一轮判断：有条件可行。"}],
    )

    assert result["mode"] == "answer_composer"
    assert result["is_plan"] is False
    assert result["text"].startswith("## 综合判断")
    assert "\n\n## 依据" in result["text"]
    assert "\n\n## 下一步" in result["text"]


def test_plain_generate_plan_stays_chat_plan_not_artifact_request() -> None:
    assert orchestrator._plan_draft_requested("帮我生成一版方案看看")
    assert orchestrator._plan_draft_requested("请基于当前工作区已有分析，整理一版简短试点方案，包含候选点位、证据、风险和下一步验证。")
    assert not orchestrator._artifact_generation_requested("帮我生成一版方案看看")
    assert orchestrator._artifact_generation_requested("帮我生成 PDF 文档")


def test_preflight_defaults_empty_artifact_mode_to_chat_for_plan_request(monkeypatch) -> None:
    last_analysis = {
        "verdict": "conditional",
        "overall_confidence": "market_inferred",
        "opportunity_id": "快闪店/小店选址建议服务",
        "gap_list": ["缺少真实转化率和试点成本。"],
        "action_plan": ["先选择 2 个候选点位做周末试点。"],
    }
    monkeypatch.setattr(
        orchestrator,
        "workspace_context",
        lambda workspace_id: {
            "workspace_id": workspace_id,
            "name": "演示工作区",
            "doc_count": 3,
            "documents": [{"name": "site_signals.csv", "format": "csv"}],
        },
    )
    monkeypatch.setattr(orchestrator, "_last_analysis_for_workspace", lambda workspace_id, context=None: last_analysis)

    routed = orchestrator._preflight_fast_route(
        ChatRequest(workspace_id="demo-corpus", message="请基于当前工作区已有分析，整理一版简短试点方案，包含候选点位、证据、风险和下一步验证。"),
        [],
    )

    assert routed is not None
    decision, meta = routed
    assert decision.intent == "followup_edit"
    assert decision.output_mode == "chat"
    assert meta["fast_path"] == "lightweight_followup"
    assert meta["plan_draft_followup"] is True


def test_preflight_does_not_swallow_explicit_heavy_rerun_followup(monkeypatch) -> None:
    last_analysis = {
        "verdict": "conditional",
        "overall_confidence": "market_inferred",
        "opportunity_id": "快闪店/小店选址建议服务",
        "gap_list": ["缺少真实转化率和试点成本。"],
    }
    monkeypatch.setattr(
        orchestrator,
        "workspace_context",
        lambda workspace_id: {
            "workspace_id": workspace_id,
            "name": "演示工作区",
            "doc_count": 3,
            "documents": [{"name": "site_signals.csv", "format": "csv"}],
        },
    )
    monkeypatch.setattr(orchestrator, "_last_analysis_for_workspace", lambda workspace_id, context=None: last_analysis)

    routed = orchestrator._preflight_fast_route(
        ChatRequest(workspace_id="demo-corpus", message="请基于上一版重新做一次完整分析，并更新五维评分"),
        [],
    )

    assert routed is None


def test_plan_request_with_candidate_points_still_returns_markdown(monkeypatch) -> None:
    last_analysis = {
        "verdict": "conditional",
        "overall_confidence": "market_inferred",
        "opportunity_id": "快闪店/小店选址建议服务",
        "recommendation": "先用楼层级人流和周边环境做小范围选址试点。",
        "gap_list": ["缺少真实转化率和单次交付成本。"],
        "action_plan": ["选 2 个候选点位做周末试点。", "记录咨询量、转化和执行成本。"],
        "dimensions": [{"name": "data_sufficiency", "score": 3, "confidence": "data_confirmed"}],
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
        lambda payload: (_ for _ in ()).throw(AssertionError("Plan draft should not fall through to generic follow-up")),
    )

    result = orchestrator._lightweight_reply(
        ChatRequest(workspace_id="demo-corpus", message="请整理出一版方案，包含优先候选点位、证据、风险和低成本试点"),
        RoutingDecision(
            workspace_id="demo-corpus",
            intent="followup_edit",
            experts=[],
            output_mode="chat",
            needs_clarification=False,
            reason="test",
        ),
        history=[{"role": "assistant", "text": "上一轮判断：有条件可行。"}],
    )

    assert result["mode"] == "followup_plan_draft"
    assert result["is_plan"] is True
    assert result["produce_offer"]["kind"] == "proposal"
    assert "## 一句话方案" in result["text"]
    assert "## 试点动作" in result["text"]


def test_followup_fallback_answers_current_question_not_static_template(monkeypatch) -> None:
    last_analysis = {
        "verdict": "conditional",
        "overall_confidence": "market_inferred",
        "opportunity_id": "快闪店/小店选址建议服务",
        "gap_list": ["缺少真实转化率和单次交付成本。"],
        "action_plan": ["选 2 个候选点位做周末试点。"],
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
        lambda payload: (_ for _ in ()).throw(RuntimeError("temporary model failure")),
    )

    result = orchestrator._lightweight_reply(
        ChatRequest(workspace_id="demo-corpus", message="请解释一下这个判断的依据"),
        RoutingDecision(
            workspace_id="demo-corpus",
            intent="followup_edit",
            experts=[],
            output_mode="chat",
            needs_clarification=False,
            reason="test",
        ),
        history=[{"role": "assistant", "text": "上一轮判断：有条件可行。"}],
    )

    assert result["mode"] == "followup_local_fallback"
    assert "可以基于上一轮结论继续判断" not in result["text"]
    assert "## 当前依据" in result["text"]
    assert "[D1]" in result["text"]


def test_lightweight_followup_red_team_question_uses_stable_gap_answer(monkeypatch) -> None:
    last_analysis = {
        "verdict": "conditional",
        "overall_confidence": "speculative",
        "opportunity_id": "   ",
        "title": "选址情报演示",
        "gap_list": [
            "需在现有 数据字段 与 数据字段 上完成至少一次周末样本切分和人流-环境相关性实算。",
            "需设计并实施至少 3–5 家商户访谈，收集真实兴趣和愿付费区间。",
        ],
        "action_plan": ["围绕 等 B1 foodcourt 周末时段，先做一轮相关性试点。"],
        "citations": [{"marker": "[D1]", "snippet": "楼层级人流和周边环境字段可用于候选点位初筛。"}],
    }
    monkeypatch.setattr(
        orchestrator,
        "workspace_context",
        lambda workspace_id: {
            "workspace_id": workspace_id,
            "name": "选址情报演示",
            "doc_count": 3,
            "profile_summary": "包含楼层级人流密度、停留时长、周边环境和信号强度字段。",
            "documents": [{"name": "site_signals.csv", "format": "csv"}],
        },
    )
    monkeypatch.setattr(orchestrator, "_last_analysis_for_workspace", lambda workspace_id, context=None: last_analysis)
    monkeypatch.setattr(
        orchestrator,
        "run_followup_assessment",
        lambda payload: (_ for _ in ()).throw(AssertionError("Red-team follow-up should not call generic LLM")),
    )

    result = orchestrator._lightweight_reply(
        ChatRequest(workspace_id="demo-corpus", message="最大的红队质疑是什么？"),
        RoutingDecision(
            workspace_id="demo-corpus",
            intent="followup_edit",
            experts=[],
            output_mode="chat",
            needs_clarification=False,
            reason="test",
        ),
        history=[
            {
                "role": "user",
                "text": "请判断能否产品化成“快闪店/小店选址建议”服务。",
            },
            {"role": "assistant", "text": "上一轮判断：有条件可行。"},
        ],
    )

    assert result["mode"] == "followup_red_team_assessment"
    assert "最大的红队质疑" in result["text"]
    assert "围绕“”" not in result["text"]
    assert "数据字段 与 数据字段" not in result["text"]
    assert "现有数据基础" in result["text"]
    assert "负一层 美食区" in result["text"]


def test_followup_plan_draft_uses_analysis_title_when_opportunity_is_empty() -> None:
    result = _followup_plan_draft(
        ChatRequest(workspace_id="demo-corpus", message="请把上面的结论整理成一版可执行方案"),
        previous="上一轮结论：可以先小范围验证。",
        last_analysis={
            "title": "V2 周末快闪试摆回填 · 选址服务更接近重点方案",
            "summary": "回填后线索量和单线索成本边界更清楚。",
            "verdict": "conditional",
            "overall_confidence": "speculative",
            "opportunity_id": "   ",
            "recommendation": "围绕 等 B1 foodcourt 周末时段先做相关性试点。",
            "action_plan": ["围绕 等 B1 foodcourt 周末时段，先做一轮相关性试点。"],
            "gap_list": ["缺少真实成交数据。"],
        },
        context={"name": "选址情报演示(模拟)", "profile_summary": "包含楼层级人流和周边环境。"},
    )

    assert result is not None
    assert "围绕“”" not in result["text"]
    assert "V2周末快闪试摆回填" in result["text"]
    assert "围绕 等" not in result["text"]
    assert "负一层 美食区" in result["text"]


def test_followup_plan_draft_prefers_previous_user_quoted_topic() -> None:
    result = _followup_plan_draft(
        ChatRequest(workspace_id="demo-corpus", message="请把上面的结论整理成一版可执行方案"),
        previous="上一轮结论：有条件可行。",
        last_analysis={
            "title": "选址情报演示",
            "verdict": "conditional",
            "overall_confidence": "speculative",
            "opportunity_id": "围绕 等 B1 foodcourt 周末时段",
            "recommendation": "围绕 等 B1 foodcourt 周末时段先做相关性试点。",
            "action_plan": ["在 类楼层场景产出快闪适配评分。", "按周末时段切分 及 3 个类似点位样本。"],
        },
        context={"name": "选址情报演示(模拟)", "profile_summary": "包含楼层级人流和周边环境。"},
        history=[
            {
                "role": "user",
                "text": "请判断能否产品化成“快闪店/小店选址建议”服务，并给出试点方案。",
            }
        ],
    )

    assert result is not None
    assert "快闪店/小店选址建议" in result["text"]
    assert "围绕“围绕" not in result["text"]
    assert "在 类" not in result["text"]
    assert "切分 及" not in result["text"]


def test_sanitize_customer_text_removes_orphaned_evidence_marker_prefix() -> None:
    text = sanitize_customer_text("围绕 S05 等 B1 foodcourt 周末时段，先做相关性试点。")
    assert "S05" not in text
    assert "围绕 等" not in text
    assert "围绕负一层 美食区" in text
    followup = sanitize_customer_text("可以用低成本先做 S05 等 B1 foodcourt 周末场景试点，哪怕在 S05/B1 foodcourt 做出结果。")
    assert "做 等" not in followup
    assert "/B1" not in followup
    assert "负一层 美食区" in followup


def test_sanitize_customer_text_hides_internal_followup_and_placeholder_fields() -> None:
    text = sanitize_customer_text(
        "这是轻量跟进判断，不会重跑完整多智能体链。需在现有 数据字段 与 数据字段 上完成试算。"
    )
    assert "轻量跟进判断" not in text
    assert "不会重跑完整多智能体链" not in text
    assert "数据字段 与 数据字段" not in text
    assert "现有数据基础" in text


def test_sanitize_customer_text_keeps_existing_environment_fields_from_being_called_missing() -> None:
    text = sanitize_customer_text("暂时看不到真实**租金、业态、成交/转化**等字段，所以只能先试点。")
    assert "租金、业态、成交/转化" not in text
    assert "成交/转化、商户反馈或付费意愿" in text
    text = sanitize_customer_text("没有真实的 **租金/坪效/业态匹配/转化率** 数据，周边环境信号只在摘要中提到，并未看到可用结构化字段（如餐饮/服饰比例、品牌档次、客群画像）。")
    assert "租金/坪效/业态匹配/转化率" not in text
    assert "并未看到可用结构化字段" not in text
    assert "坪效/转化率、商户反馈或付费意愿" in text
    assert "周边环境已有初步字段" in text


def test_sanitize_customer_text_removes_stray_followup_filler() -> None:
    text = sanitize_customer_text("已在上一版方案中拆成 等 B1 foodcourt 场景的试点路径，按 等 B1 foodcourt 做，字段结构 里已有 floor、zone，同设备在该楼层/zone多次出现。")
    assert "拆成 等" not in text
    assert "拆成负一层 美食区" in text
    assert "按 等" not in text
    assert "按负一层 美食区" in text
    assert "字段结构 里" not in text
    assert "floor" not in text
    assert "zone" not in text
    assert "楼层/区域" in text


def test_conversation_markdown_removes_orphaned_closing_quote_prefix() -> None:
    markdown = orchestrator._format_conversation_markdown(
        "”，可以先用一轮小样本验证。",
        ["”，工作区里已有时段与区域信号。"],
        ["”，先明确试点的转化口径。"],
        {},
    )

    assert "”，" not in markdown
    assert "可以先用一轮小样本验证。" in markdown
    assert "工作区里已有时段与区域信号。" in markdown
    assert "先明确试点的转化口径。" in markdown


def test_sanitize_customer_text_removes_unverified_demo_threshold_and_internal_summary_name() -> None:
    text = sanitize_customer_text(
        "周边环境字段（如周边业态、竞品、交通等，workspace_summary已体现为选址情报演示数据集）。"
        "主指标：**客流量/进店人数提升 ≥ 20%** 相比客户自选点位（需客户提供对照店数据）。"
        "预算可以压在几万级以内。"
    )
    assert "workspace_summary" not in text
    assert "提升 ≥ 20%" not in text
    assert "是否优于对照点位" in text
    assert "预算需要在试点前明确上限" in text


def test_safe_chat_topic_label_skips_generic_followup_questions() -> None:
    assert orchestrator._safe_chat_topic_label("这个方向靠谱吗") == "当前工作区机会"
    assert orchestrator._safe_chat_topic_label("最大的红队质疑") == "当前工作区机会"
    assert orchestrator._safe_chat_topic_label("当前问题") == "当前工作区机会"


def test_last_history_user_topic_skips_generic_followup_and_keeps_business_topic() -> None:
    topic = orchestrator._last_history_user_topic(
        {
            "workspace_id": "demo-corpus",
            "_conversation_history": [
                {"role": "user", "text": "请判断能否产品化成“快闪店/小店选址建议”服务。"},
                {"role": "assistant", "text": "上一轮判断：有条件可行。"},
                {"role": "user", "text": "请把上面的结论整理成一版可执行方案。"},
                {"role": "assistant", "text": "上一轮方案：可以试点。"},
                {"role": "user", "text": "这个方向靠谱吗？"},
            ],
        }
    )
    assert topic == "快闪店/小店选址建议"
