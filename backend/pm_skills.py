from __future__ import annotations

from typing import Any


PLAYBOOKS: dict[str, dict[str, Any]] = {
    "jtbd": {
        "label": "JTBD",
        "focus": "把客户任务、触发场景和现有替代方案分开验证。",
        "questions": ["谁在什么场景下需要它？", "当前替代做法是什么？", "成功标准能否被数据观测？"],
        "artifact_sections": ["目标用户", "关键任务", "触发场景", "替代方案", "验证指标"],
    },
    "opportunity_tree": {
        "label": "机会树",
        "focus": "从业务目标拆到机会、方案和实验，不直接跳到功能清单。",
        "questions": ["顶层业务目标是什么？", "资料里支持哪些机会分支？", "最小可验证方案是什么？"],
        "artifact_sections": ["业务目标", "机会分支", "证据强度", "方案假设", "验证动作"],
    },
    "prd": {
        "label": "PRD",
        "focus": "把证据约束、用户价值、功能边界和验收指标写清楚。",
        "questions": ["核心用户和问题是什么？", "MVP 必须解决哪一段流程？", "验收指标来自哪些证据？"],
        "artifact_sections": ["背景", "目标用户", "问题陈述", "MVP 范围", "指标与风险"],
    },
    "roadmap": {
        "label": "路线图",
        "focus": "按验证顺序安排阶段，不把未经验证的功能提前承诺。",
        "questions": ["先验证数据价值还是市场需求？", "哪些能力依赖前置数据治理？", "每阶段退出条件是什么？"],
        "artifact_sections": ["0-30 天", "31-60 天", "61-90 天", "依赖项", "决策点"],
    },
    "pricing": {
        "label": "定价",
        "focus": "把价值锚点、使用量口径和市场参考分开标注。",
        "questions": ["客户为哪类结果付费？", "计费单位是否能被系统稳定计量？", "市场参考来自哪里？"],
        "artifact_sections": ["价值锚点", "计费单位", "价格假设", "市场参考", "验证实验"],
    },
    "experiment": {
        "label": "实验验证",
        "focus": "用最小样本验证最关键风险，明确通过/失败标准。",
        "questions": ["最大不确定性是什么？", "最小实验样本是什么？", "什么结果才算通过？"],
        "artifact_sections": ["假设", "样本", "动作", "指标", "判断标准"],
    },
}


def normalize_playbook(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    aliases = {
        "机会树": "opportunity_tree",
        "opportunity": "opportunity_tree",
        "prd": "prd",
        "路线图": "roadmap",
        "roadmap": "roadmap",
        "定价": "pricing",
        "pricing": "pricing",
        "实验": "experiment",
        "experiment": "experiment",
        "jtbd": "jtbd",
    }
    return aliases.get(text, text if text in PLAYBOOKS else None)


def playbook_suggestion(playbook: Any, workspace_profile: dict[str, Any] | None = None) -> dict[str, Any]:
    key = normalize_playbook(playbook) or "opportunity_tree"
    spec = PLAYBOOKS[key]
    context = _profile_context(workspace_profile or {})
    return {
        "playbook": key,
        "label": spec["label"],
        "focus": spec["focus"],
        "questions": list(spec["questions"]),
        "artifact_sections": list(spec["artifact_sections"]),
        "workspace_context": context,
        "guardrail": "该方法只提供分析结构，结论必须由工作区证据、市场来源和审计共同约束。",
    }


def _profile_context(profile: dict[str, Any]) -> dict[str, Any]:
    tables = [item for item in profile.get("tables") or [] if isinstance(item, dict)]
    row_count = sum(int(item.get("row_count") or 0) for item in tables)
    column_names: list[str] = []
    for table in tables:
        for column in table.get("columns") or []:
            if isinstance(column, dict) and column.get("name"):
                column_names.append(str(column["name"]))
            if len(column_names) >= 8:
                break
        if len(column_names) >= 8:
            break
    return {
        "table_count": len(tables),
        "row_count": row_count,
        "sample_columns": column_names,
    }
