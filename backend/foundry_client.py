from __future__ import annotations

import json
import os
import random
import re
import time
from pathlib import Path
from typing import Any

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from openai.types.responses.response_input_param import FunctionCallOutput


ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "agents" / "prompts"

PROMPT_FILES = {
    "df-feasibility-analyst": "feasibility_analyst.md",
    "df-auditor": "auditor.md",
}

_WEB_TOOL_CACHE: dict[str, Any] | None = None
_LLM_RETRY_DELAYS = (0.5, 1.0, 2.0)
_TRANSIENT_ERROR_TERMS = (
    "rate limit",
    "too many requests",
    "timeout",
    "timed out",
    "temporarily unavailable",
    "connection reset",
    "connection aborted",
    "connection error",
    "remote end closed",
    "server disconnected",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
)


CLARIFY_GUIDANCE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "question": {
            "type": "string",
            "description": "A concise Chinese onboarding guide and next-step question.",
        },
        "options": {
            "type": "array",
            "description": "Two to five concise Chinese next-step options.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "label": {"type": "string"},
                },
                "required": ["id", "label"],
            },
        },
    },
    "required": ["question", "options"],
}


COORDINATOR_ROUTE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "intent": {
            "type": "string",
            "enum": [
                "feasibility_analysis",
                "followup_edit",
                "smalltalk_or_meta",
                "clarify_needed",
                "corpus_qa",
            ],
        },
        "experts": {
            "type": "array",
            "items": {"type": "string"},
        },
        "output_mode": {
            "type": "string",
            "enum": ["chat", "report", "full_package"],
        },
        "needs_clarification": {"type": "boolean"},
        "reason": {"type": "string"},
        "clarifying_question": {"type": "string"},
    },
    "required": ["intent", "experts", "output_mode", "needs_clarification", "reason", "clarifying_question"],
}


COORDINATOR_REPLY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "text": {"type": "string"},
    },
    "required": ["text"],
}


FOLLOWUP_ASSESSMENT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "text": {"type": "string"},
        "assessment": {
            "type": "string",
            "enum": ["supported", "needs_more_evidence", "risky", "unclear"],
        },
        "gaps": {
            "type": "array",
            "items": {"type": "string"},
        },
        "clarify": {"type": "string"},
        "should_clarify": {"type": "boolean"},
    },
    "required": ["text", "assessment", "gaps", "clarify", "should_clarify"],
}


MARKET_WEB_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "external_findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "claim": {"type": "string"},
                    "source_title": {"type": "string"},
                    "source_url": {"type": "string"},
                    "confidence": {"type": "string"},
                },
                "required": ["claim", "source_title", "source_url", "confidence"],
            },
        },
        "positioning_note": {"type": "string"},
    },
    "required": ["external_findings", "positioning_note"],
}


ACTION_PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "recommendation": {"type": "string"},
        "steps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["recommendation", "steps"],
}

IMAGE_SUBJECT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "subject": {"type": "string"},
        "kind": {"type": "string", "enum": ["product_app", "product_physical", "service", "campaign"]},
    },
    "required": ["subject", "kind"],
}

EXEC_SUMMARY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "headline": {"type": "string"},
        "points": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["headline", "points"],
}


PLAYBOOK_DETAIL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "points": {"type": "array", "items": {"type": "string"}},
        "goal": {"type": "string"},
    },
    "required": ["summary", "points", "goal"],
}


def run_playbook_detail(payload: dict[str, Any]) -> dict[str, Any]:
    """用某个 PM 方法的视角，针对这批数据分析出的机会，生成【与数据挂钩】的具体内容（不是泛泛框架介绍）。"""
    client = _project_client()
    openai_client = client.get_openai_client()
    instructions = (
        "你是资深产品分析师。请用【method_name 指定的 PM 方法】的视角，针对这批数据分析出来的机会，"
        "给出【具体、与数据/证据挂钩】的内容——不要泛泛介绍这个方法本身，要落到这个机会、这批数据上。\n"
        "输出 JSON：\n"
        "1) summary：一句话，用该方法视角概括‘这个机会该怎么看 / 怎么做’，必须点到具体机会与数据信号。\n"
        "2) points：3-4 条，每条一句，按该方法的结构展开并紧扣真实数据/人群/缺口——"
        "如 opportunity-tree 给‘机会→方案→实验’三层、jtbd 给‘场景/任务/痛点’、pricing 给‘计费方式/价值锚点/市场缺口’、"
        "roadmap 给‘30/60/90 天’、prd 给‘目标用户/核心功能/验收指标’、experiment 给‘假设/门槛/样本周期’。\n"
        "3) goal：一句‘产品落地目标’，尽量可量化、和数据挂钩。\n"
        "只用提供的 feasibility / evidence / 人群信息，不编造数字；缺数据就说‘需补：…’。中文。只返回 JSON {summary, points, goal}。"
    )
    create_args: dict[str, Any] = {
        "model": os.environ.get("DF_CHAT_DEPLOYMENT", "gpt-5.1"),
        "instructions": instructions,
        "input": json.dumps(payload, ensure_ascii=False, indent=2),
        "max_output_tokens": 800,
        "text": _schema_format("df_playbook_detail", PLAYBOOK_DETAIL_SCHEMA),
    }
    try:
        response = _responses_create_with_retry(openai_client, **create_args)
    except Exception as exc:
        if not _can_retry_without_schema(exc):
            raise
        create_args.pop("text", None)
        response = _responses_create_with_retry(openai_client, **create_args)
    try:
        data = _extract_json(getattr(response, "output_text", "") or "")
    except Exception:
        data = {}
    return {
        "summary": str(data.get("summary") or "").strip(),
        "points": [str(p).strip() for p in (data.get("points") or []) if str(p).strip()],
        "goal": str(data.get("goal") or "").strip(),
    }


DATA_OVERVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "overview": {"type": "string"},
        "datasets": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"name": {"type": "string"}, "what": {"type": "string"}},
                "required": ["name", "what"],
            },
        },
        "usable_for": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["overview", "datasets", "usable_for"],
}


def run_data_overview(payload: dict[str, Any]) -> dict[str, Any]:
    """客户刚上传数据后，用客户能懂的话解释【这批数据都是什么、能用来做什么】。"""
    client = _project_client()
    openai_client = client.get_openai_client()
    instructions = (
        "你是数据分析师。客户刚上传了一批数据，请用【客户能听懂的话】解释这批数据都是什么、能用来做什么，"
        "让客户一上来就明白自己手里有什么。\n"
        "输出 JSON：\n"
        "1) overview：2-3 句，概括这批数据整体是什么、规模、覆盖什么主题、适合回答什么问题。\n"
        "2) datasets：对每个上传文件给一条 {name, what}，what 用一句话说这个文件大概是什么内容、什么维度"
        "（结合文件名、格式、字段推断；别编造具体数值或不存在的字段）。\n"
        "3) usable_for：2-4 条，这批数据可以支撑的分析/用途（如‘按门店看到访与停留差异’）。\n"
        "中文、客户友好、不要堆术语、不编造。只返回 JSON。"
    )
    create_args: dict[str, Any] = {
        "model": os.environ.get("DF_CHAT_DEPLOYMENT", "gpt-5.1"),
        "instructions": instructions,
        "input": json.dumps(payload, ensure_ascii=False, indent=2),
        "max_output_tokens": 900,
        "text": _schema_format("df_data_overview", DATA_OVERVIEW_SCHEMA),
    }
    try:
        response = _responses_create_with_retry(openai_client, **create_args)
    except Exception as exc:
        if not _can_retry_without_schema(exc):
            raise
        create_args.pop("text", None)
        response = _responses_create_with_retry(openai_client, **create_args)
    try:
        data = _extract_json(getattr(response, "output_text", "") or "")
    except Exception:
        data = {}
    return {
        "overview": str(data.get("overview") or "").strip(),
        "datasets": [{"name": str(d.get("name") or ""), "what": str(d.get("what") or "")} for d in (data.get("datasets") or []) if isinstance(d, dict)],
        "usable_for": [str(u).strip() for u in (data.get("usable_for") or []) if str(u).strip()],
    }


PLAN_METRICS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "metrics": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "key": {"type": "string"},
                    "label": {"type": "string"},
                    "value": {"type": "string"},
                    "unit": {"type": "string"},
                    "kind": {"type": "string", "enum": ["assumption", "observed", "target"]},
                    "note": {"type": "string"},
                },
                "required": ["key", "label", "value", "unit", "kind", "note"],
            },
        },
    },
    "required": ["metrics"],
}


def run_plan_metrics_extract(payload: dict[str, Any]) -> dict[str, Any]:
    """从上一版方案里抽取【可回填迭代的关键指标】（如客获率/转换率/客单价/周期等）。

    抽出来的都是方案里【AI 预估/假设】的数字，因此 kind 默认 assumption；
    客户随后可在前端把它改成 observed（实测回填）或 target（目标值）。
    保持通用：抽方案里真实出现的量化杠杆，别硬塞固定那几个指标，也别编造数字。
    """
    client = _project_client()
    openai_client = client.get_openai_client()
    instructions = (
        "你是产品分析师。下面是上一版可行性方案的内容。请抽取其中【可以用于下一轮迭代的关键量化指标】，"
        "也就是方案里出现的、可以被回填实测值再优化的数字杠杆，例如客获率/转化率/客单价/续费率/获客成本/"
        "活动周期/样本量/预算等——只抽方案里真实出现或明确依据的，不要硬凑、不要编造数字。\n"
        "对每个指标输出 {key, label, value, unit, kind, note}：\n"
        "- key：英文短标识（如 conversion_rate）；label：中文名（如 转化率）。\n"
        "- value：方案里给出的数值（字符串，保留原值；没有明确数值就给区间或留空字符串）。\n"
        "- unit：单位（如 %、元、天、人；没有就空字符串）。\n"
        "- kind：一律填 assumption（这些是方案里的预估/假设值，客户之后会自行改成实测或目标）。\n"
        "- note：一句话说明这个指标在方案里是干嘛的/依据什么。\n"
        "抽 3-8 个最关键的即可。中文。只返回 JSON {metrics:[...]}。"
    )
    create_args: dict[str, Any] = {
        "model": os.environ.get("DF_CHAT_DEPLOYMENT", "gpt-5.1"),
        "instructions": instructions,
        "input": json.dumps(payload, ensure_ascii=False, indent=2),
        "max_output_tokens": 900,
        "text": _schema_format("df_plan_metrics", PLAN_METRICS_SCHEMA),
    }
    try:
        response = _responses_create_with_retry(openai_client, **create_args)
    except Exception as exc:
        if not _can_retry_without_schema(exc):
            raise
        create_args.pop("text", None)
        response = _responses_create_with_retry(openai_client, **create_args)
    try:
        data = _extract_json(getattr(response, "output_text", "") or "")
    except Exception:
        data = {}
    out: list[dict[str, Any]] = []
    for m in (data.get("metrics") or []):
        if not isinstance(m, dict):
            continue
        kind = str(m.get("kind") or "assumption")
        if kind not in ("assumption", "observed", "target"):
            kind = "assumption"
        out.append(
            {
                "key": str(m.get("key") or "").strip(),
                "label": str(m.get("label") or "").strip(),
                "value": str(m.get("value") or "").strip(),
                "unit": str(m.get("unit") or "").strip(),
                "kind": kind,
                "note": str(m.get("note") or "").strip(),
            }
        )
    return {"metrics": [m for m in out if m["label"]]}


def run_image_subject(payload: dict[str, Any]) -> dict[str, Any]:
    """让 agent 判断这份方案【实际要做的产品/交付物是什么】，给图像模型一个可作画的产品主体（英文）。"""
    client = _project_client()
    openai_client = client.get_openai_client()
    instructions = (
        "你是产品概念视觉的美术指导。根据这份可行性方案，判断【我们实际要做的产品 / 交付物是什么】，"
        "并把它描述成一个【可作画的具体视觉主体】，用于让图像模型画出这个产品本身——"
        "绝不要画成报告、数据看板、分析图表、会议室或办公室场景。\n"
        "kind 取值：product_app（App/软件界面）、product_physical（实体产品/包装/样机）、service（服务或门店体验场景）、campaign（活动主视觉）。\n"
        "subject：用一句【英文】描述要画的具体产品画面，越具体越好（如 'a clean mobile app dashboard screen showing the single core metric and a hero device mockup'）。示例仅示意，必须按 evidence 与机会本身生成，不得套用示例行业或模板。\n"
        "只返回 JSON：{subject, kind}。"
    )
    create_args: dict[str, Any] = {
        "model": os.environ.get("DF_CHAT_DEPLOYMENT", "gpt-5.1"),
        "instructions": instructions,
        "input": json.dumps(payload, ensure_ascii=False, indent=2),
        "max_output_tokens": 400,
        "text": _schema_format("df_image_subject", IMAGE_SUBJECT_SCHEMA),
    }
    # foundry 偶发超时会让 subject 判断失败 → 退回笼统兜底图。多试几次吸收瞬时抖动，
    # 先带 schema、失败再去掉 schema，共 3 次机会，尽量拿到具体产品主体。
    response = None
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            if attempt == 1:
                create_args.pop("text", None)  # 第 2 次去掉结构化约束，提高成功率
            response = _responses_create_with_retry(openai_client, **create_args)
            break
        except Exception as exc:  # 超时/限流等瞬时错误重试
            last_error = exc
            response = None
            if not (_is_transient_llm_error(exc) or _can_retry_without_schema(exc)):
                break
    if response is None:
        return {"subject": "", "kind": "", "error": f"{type(last_error).__name__}: {last_error}"[:200] if last_error else ""}
    try:
        data = _extract_json(getattr(response, "output_text", "") or "")
    except Exception:
        data = {}
    return {
        "subject": str(data.get("subject") or "").strip(),
        "kind": str(data.get("kind") or "").strip(),
    }


def run_executive_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """为正式 PDF 报告生成干净、条目式的执行摘要（去对话腔、去 markdown）。"""
    client = _project_client()
    openai_client = client.get_openai_client()
    instructions = (
        "你是为【客户决策】撰写正式执行摘要的分析师，输出将放进一份正式 PDF 报告。\n"
        "硬性要求：\n"
        "1) 绝不要任何对话腔/寒暄（禁止出现“先给你一版”“方便你扫一眼”“我先”“下面给你”“整体扫一眼”之类）。\n"
        "2) 不要任何 markdown 符号（不要 * # ` 等），纯文本。\n"
        "3) headline：一句话点出机会方向与可行性结论（客户视角、专业克制）。\n"
        "4) points：3-5 条要点，每条一句话，覆盖：机会/产品方向、关键依据、主要缺口或风险、建议的下一步；可执行、不空话。\n"
        "5) 中文，正式、简洁。只返回 JSON：{headline, points}。"
    )
    create_args: dict[str, Any] = {
        "model": os.environ.get("DF_CHAT_DEPLOYMENT", "gpt-5.1"),
        "instructions": instructions,
        "input": json.dumps(payload, ensure_ascii=False, indent=2),
        "max_output_tokens": 700,
        "text": _schema_format("df_exec_summary", EXEC_SUMMARY_SCHEMA),
    }
    try:
        response = _responses_create_with_retry(openai_client, **create_args)
    except Exception as exc:
        if not _can_retry_without_schema(exc):
            raise
        create_args.pop("text", None)
        response = _responses_create_with_retry(openai_client, **create_args)
    try:
        data = _extract_json(getattr(response, "output_text", "") or "")
    except Exception:
        data = {}
    points = [str(p).strip() for p in (data.get("points") or []) if str(p).strip()]
    return {
        "headline": str(data.get("headline") or "").strip(),
        "points": points,
    }


def run_action_plan(payload: dict[str, Any]) -> dict[str, Any]:
    """用 LLM 根据可行性判定 + 维度评分 + 缺口 + 证据，生成【这批数据专属】的行动方案（替代写死模板）。"""
    client = _project_client()
    openai_client = client.get_openai_client()
    instructions = (
        "你是 DataForge 的可行性分析师。系统已经对这个工作区机会给出了判定(verdict)、各维度评分与理由(dimensions)、"
        "关键缺口(gap_list) 和带 marker 的证据(evidence)。请据此生成【针对这个具体机会、这批数据】的下一步行动方案。\n"
        "硬性规则：\n"
        "1) 绝不套用通用模板，绝不假设行业（不要默认是攀岩/会员/活动/门店；完全按 evidence 与机会本身来）。\n"
        "2) recommendation：一句话推荐，说清‘建议做什么、为什么、先验证什么’，紧扣这个机会与证据，40-90 字。\n"
        "3) steps：4-5 条具体、可执行、互不重复的下一步。每条要落地（做什么、看哪个指标/产出什么），并紧扣具体证据、评分或缺口；"
        "需要引用证据时在该条末尾用 [n]（n=evidence 的 marker 数字）。\n"
        "4) 必须贴合 evidence 和 gap_list 的真实内容：缺数据就把‘补齐某项数据/做某项统计’写成一步；"
        "禁止写‘先定一个主指标再小样本验证’‘把证据整理成2-3个假设’这种放之四海皆准的空话。\n"
        "5) 如果 payload.playbook 存在，必须按该产品方法组织步骤：JTBD 区分任务/触发/替代方案，机会树区分目标/机会/方案/实验，PRD 区分用户/问题/MVP/验收，路线图区分阶段闸口，定价验证计费单位/价值/成本，实验验证写清假设/样本/通过标准；但不能为了方法而改写证据或结论。\n"
        "6) 不同工作区/不同数据必须给出明显不同的方案。中文。只返回 JSON：{recommendation, steps}。"
    )
    create_args: dict[str, Any] = {
        "model": os.environ.get("DF_CHAT_DEPLOYMENT", "gpt-5.1"),
        "instructions": instructions,
        "input": json.dumps(payload, ensure_ascii=False, indent=2),
        "max_output_tokens": 1300,
        "text": _schema_format("df_action_plan", ACTION_PLAN_SCHEMA),
    }
    try:
        response = _responses_create_with_retry(openai_client, **create_args)
    except Exception as exc:
        if not _can_retry_without_schema(exc):
            raise
        create_args.pop("text", None)
        response = _responses_create_with_retry(openai_client, **create_args)
    text = getattr(response, "output_text", "") or ""
    try:
        data = _extract_json(text)
    except Exception:
        data = {}
    steps = [str(s).strip() for s in (data.get("steps") or []) if str(s).strip()]
    return {
        "recommendation": str(data.get("recommendation") or "").strip(),
        "steps": steps,
        "response_id": getattr(response, "id", None),
        "usage": _usage_dict(getattr(response, "usage", None)),
        "mode": "llm_action_plan",
    }


def _project_client() -> AIProjectClient:
    return AIProjectClient(
        endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        credential=DefaultAzureCredential(),
        allow_preview=True,
    )


def _status_code_from_exception(exc: Exception) -> int | None:
    for attr in ("status_code", "status", "code"):
        value = getattr(exc, attr, None)
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            pass
    response = getattr(exc, "response", None)
    for attr in ("status_code", "status", "code"):
        value = getattr(response, attr, None)
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            pass
    return None


def _is_transient_llm_error(exc: Exception) -> bool:
    status = _status_code_from_exception(exc)
    if status is not None:
        return status in {408, 409, 425, 429} or status >= 500
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    message = f"{type(exc).__name__}: {exc}".lower()
    return any(term in message for term in _TRANSIENT_ERROR_TERMS)


def _can_retry_without_schema(exc: Exception) -> bool:
    if _is_transient_llm_error(exc):
        return False
    message = f"{type(exc).__name__}: {exc}".lower()
    schema_terms = (
        "json_schema",
        "response_format",
        "structured output",
        "schema",
        "text.format",
        "unsupported parameter",
        "unknown parameter",
        "invalid parameter",
        "invalid request",
    )
    return any(term in message for term in schema_terms)


def _llm_retry_delay(attempt: int) -> float:
    base = _LLM_RETRY_DELAYS[min(attempt, len(_LLM_RETRY_DELAYS) - 1)]
    return base + random.uniform(0.0, 0.2)


def _responses_create_with_retry(openai_client: Any, **create_args: Any) -> Any:
    retry_count = 0
    while True:
        try:
            response = openai_client.responses.create(**create_args)
            if retry_count:
                try:
                    setattr(response, "_dataforge_retry_attempts", retry_count)
                except Exception:
                    pass
            return response
        except Exception as exc:
            if retry_count >= len(_LLM_RETRY_DELAYS) or not _is_transient_llm_error(exc):
                raise
            time.sleep(_llm_retry_delay(retry_count))
            retry_count += 1


def _stream_response_events_with_retry(openai_client: Any, create_args: dict[str, Any]) -> Any:
    retry_count = 0
    emitted_token = False
    while True:
        try:
            stream = openai_client.responses.create(**create_args)
            for event in stream:
                if _stream_delta(event):
                    emitted_token = True
                yield event
            return
        except Exception as exc:
            if emitted_token or retry_count >= len(_LLM_RETRY_DELAYS) or not _is_transient_llm_error(exc):
                raise
            time.sleep(_llm_retry_delay(retry_count))
            retry_count += 1


def _usage_dict(usage: Any) -> dict[str, Any]:
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        return usage.model_dump()
    if isinstance(usage, dict):
        return usage
    return {
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }


def _stream_delta(event: Any) -> str:
    event_type = str(getattr(event, "type", "") or "")
    delta = getattr(event, "delta", None)
    if isinstance(delta, str) and delta:
        return delta
    if event_type.endswith(".delta") and delta is not None:
        return str(delta)
    return ""


def _response_meta(response: Any, mode: str) -> dict[str, Any]:
    meta = {
        "mode": mode,
        "response_id": getattr(response, "id", None),
        "usage": _usage_dict(getattr(response, "usage", None)),
    }
    retry_attempts = getattr(response, "_dataforge_retry_attempts", 0)
    if retry_attempts:
        meta["retry_attempts"] = retry_attempts
    return meta


def _to_plain_data(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return {key: _to_plain_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_plain_data(item) for item in value]
    return value


def _response_sources(response: Any) -> list[dict[str, str]]:
    data = _to_plain_data(getattr(response, "output", []))
    sources: list[dict[str, str]] = []
    seen: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            url = value.get("url") or value.get("source_url")
            title = value.get("title") or value.get("source_title") or value.get("text")
            if isinstance(url, str) and url.startswith(("http://", "https://")) and url not in seen:
                seen.add(url)
                sources.append({"title": str(title or url), "url": url, "confidence": "market_inferred"})
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(data)
    return sources[:8]


def _response_tool_trace(response: Any) -> list[dict[str, Any]]:
    data = _to_plain_data(getattr(response, "output", []))
    calls: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            item_type = str(value.get("type") or "")
            if any(token in item_type for token in ("web_search", "bing_grounding", "search_call")):
                calls.append(
                    {
                        "type": item_type,
                        "status": value.get("status"),
                        "id": value.get("id"),
                    }
                )
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(data)
    return calls


def _response_mcp_trace(response: Any) -> list[dict[str, Any]]:
    data = _to_plain_data(getattr(response, "output", []))
    calls: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            item_type = str(value.get("type") or "")
            if item_type.startswith("mcp_"):
                calls.append(
                    {
                        "type": item_type,
                        "name": value.get("name"),
                        "server_label": value.get("server_label"),
                        "status": value.get("status"),
                        "id": value.get("id"),
                        "error": value.get("error"),
                        "arguments": value.get("arguments"),
                        "output": value.get("output"),
                        "agent_reference": value.get("agent_reference"),
                    }
                )
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(data)
    return calls


def _market_results_from_mcp_trace(tool_calls: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[Any]]:
    competitors: list[dict[str, Any]] = []
    sources: list[Any] = []
    for call in tool_calls:
        if call.get("type") != "mcp_call" or call.get("name") != "market_lookup":
            continue
        if call.get("error"):
            raise RuntimeError(f"MCP market_lookup failed: {call.get('error')}")
        output = call.get("output")
        if not output:
            continue
        data = json.loads(str(output))
        raw_items = data.get("results") or data.get("competitors") or (data if isinstance(data, list) else [])
        if not isinstance(raw_items, list):
            continue
        for item in raw_items[:6]:
            if not isinstance(item, dict):
                continue
            enriched = dict(item)
            enriched.setdefault("confidence", "market_inferred")
            enriched.setdefault("source_type", "market_mcp")
            enriched.setdefault("tool", "foundry_agent_mcp.market_lookup")
            competitors.append(enriched)
            if enriched.get("url"):
                sources.append(enriched["url"])
        if competitors:
            return competitors, sources
    return competitors, sources


def _foundry_web_tool_candidates() -> list[tuple[str, dict[str, Any]]]:
    candidates: list[tuple[str, dict[str, Any]]] = []
    bing_connection_id = (
        os.environ.get("DF_BING_CONNECTION_ID")
        or os.environ.get("BING_CONNECTION_ID")
        or os.environ.get("AZURE_BING_CONNECTION_ID")
    )
    if bing_connection_id:
        candidates.append(
            (
                "bing_grounding",
                {
                    "type": "bing_grounding",
                    "bing_grounding": {
                        "search_configurations": [
                            {
                                "project_connection_id": bing_connection_id,
                                "market": os.environ.get("DF_WEB_MARKET", "zh-CN"),
                                "count": 5,
                            }
                        ]
                    },
                },
            )
        )
    candidates.append(("web_search_preview", {"type": "web_search_preview", "search_context_size": "medium"}))
    candidates.append(("web_search", {"type": "web_search", "search_context_size": "medium"}))
    return candidates


def _verify_foundry_web_tool(openai_client: Any) -> dict[str, Any]:
    global _WEB_TOOL_CACHE
    if _WEB_TOOL_CACHE is not None:
        return _WEB_TOOL_CACHE

    failures: list[dict[str, str]] = []
    instructions = (
        "Use the provided Foundry web search tool for this availability check. "
        "Return a compact JSON object with a brief claim and source_url."
    )
    for name, tool in _foundry_web_tool_candidates():
        try:
            response = _responses_create_with_retry(
                openai_client,
                model=os.environ.get("DF_CHAT_DEPLOYMENT", "gpt-5.1"),
                instructions=instructions,
                input="Check current public information about enterprise data analytics platforms.",
                tools=[tool],
                max_output_tokens=500,
            )
            text = getattr(response, "output_text", "") or ""
            sources = _response_sources(response)
            tool_calls = _response_tool_trace(response)
            if text.strip() and (sources or tool_calls):
                _WEB_TOOL_CACHE = {
                    "available": True,
                    "name": name,
                    "tool": tool,
                    "sources": sources,
                    "tool_calls": tool_calls,
                    "response_id": getattr(response, "id", None),
                    "usage": _usage_dict(getattr(response, "usage", None)),
                }
                return _WEB_TOOL_CACHE
            failures.append({"tool": name, "error": "no_web_call_or_source_detected"})
        except Exception as exc:
            failures.append({"tool": name, "error": str(exc)[:500]})
    _WEB_TOOL_CACHE = {"available": False, "failures": failures}
    return _WEB_TOOL_CACHE


def _extract_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, flags=re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def _schema_format(agent_name: str, response_schema: dict[str, Any] | None) -> dict[str, Any] | None:
    if not response_schema:
        return None
    return {
        "format": {
            "type": "json_schema",
            "name": agent_name.replace("-", "_"),
            "schema": response_schema,
            "strict": False,
        }
    }


def _agent_reference(agent_name: str) -> dict[str, Any]:
    return {"agent_reference": {"name": agent_name, "type": "agent_reference"}}


def _execute_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name != "search_pack_context":
        raise ValueError(f"Unsupported Foundry agent tool call: {name}")
    try:
        from .rag import search
    except ImportError:
        from rag import search

    workspace_id = str(arguments.get("workspace_id") or "demo-corpus")
    query = str(arguments.get("query") or "")
    top_k = int(arguments.get("top_k") or 5)
    top_k = max(1, min(top_k, 20))
    hits = search(workspace_id, query, top_k)
    return {
        "workspace_id": workspace_id,
        "query": query,
        "hits": hits,
        "count": len(hits),
        "source_index": os.environ.get("SEARCH_INDEX_NAME", "dataforge-workspaces"),
    }


def _function_calls(response: Any) -> list[Any]:
    return [item for item in getattr(response, "output", []) if getattr(item, "type", None) == "function_call"]


def _run_prompt_agent(
    agent_name: str,
    input_text: str,
    *,
    response_schema: dict[str, Any] | None,
    max_output_tokens: int,
    max_tool_rounds: int = 4,
) -> dict[str, Any]:
    client = _project_client()
    openai_client = client.get_openai_client()
    conversation = openai_client.conversations.create()
    text_format = _schema_format(agent_name, response_schema)
    tool_trace: list[dict[str, Any]] = []

    create_args: dict[str, Any] = {
        "input": input_text,
        "conversation": conversation.id,
        "max_output_tokens": max_output_tokens,
        "extra_body": _agent_reference(agent_name),
    }
    if text_format:
        create_args["text"] = text_format
    try:
        response = _responses_create_with_retry(openai_client, **create_args)
    except Exception as exc:
        if "text" not in create_args or not _can_retry_without_schema(exc):
            raise
        create_args.pop("text")
        response = _responses_create_with_retry(openai_client, **create_args)

    for _ in range(max_tool_rounds):
        calls = _function_calls(response)
        if not calls:
            break
        tool_outputs: list[FunctionCallOutput] = []
        for call in calls:
            name = str(getattr(call, "name", ""))
            raw_args = getattr(call, "arguments", "{}") or "{}"
            arguments = json.loads(raw_args)
            result = _execute_tool(name, arguments)
            tool_trace.append(
                {
                    "name": name,
                    "args": arguments,
                    "count": result.get("count"),
                    "call_id": getattr(call, "call_id", None),
                }
            )
            tool_outputs.append(
                FunctionCallOutput(
                    type="function_call_output",
                    call_id=getattr(call, "call_id"),
                    output=json.dumps(result, ensure_ascii=False),
                )
            )
        next_args: dict[str, Any] = {
            "input": tool_outputs,
            "conversation": conversation.id,
            "max_output_tokens": max_output_tokens,
            "extra_body": _agent_reference(agent_name),
        }
        if text_format:
            next_args["text"] = text_format
        try:
            response = _responses_create_with_retry(openai_client, **next_args)
        except Exception as exc:
            if "text" not in next_args or not _can_retry_without_schema(exc):
                raise
            next_args.pop("text")
            response = _responses_create_with_retry(openai_client, **next_args)
    else:
        raise RuntimeError(f"{agent_name} exceeded {max_tool_rounds} tool rounds")

    text = getattr(response, "output_text", "") or ""
    if not text.strip():
        raise RuntimeError(f"{agent_name} returned no final text after tool loop")
    try:
        openai_client.conversations.delete(conversation_id=conversation.id)
    except Exception:
        pass
    return {
        "text": text,
        "structured": _extract_json(text),
        "response_id": getattr(response, "id", None),
        "usage": _usage_dict(getattr(response, "usage", None)),
        "mode": "foundry_agent_service",
        "tool_calls": tool_trace,
    }


def run_agent(
    agent_name: str,
    input_text: str,
    *,
    response_schema: dict[str, Any] | None = None,
    max_output_tokens: int = 3200,
) -> dict[str, Any]:
    prompt_file = PROMPT_FILES.get(agent_name)
    if not prompt_file:
        raise ValueError(f"Unsupported agent: {agent_name}")
    instructions = (PROMPTS / prompt_file).read_text(encoding="utf-8")
    if response_schema:
        instructions += (
            "\n\nReturn only valid JSON. The JSON must conform to this schema:\n"
            + json.dumps(response_schema, ensure_ascii=False)
        )

    if os.environ.get("DF_AGENT_RUNTIME", "responses") == "foundry_agent_service":
        agent_payload = input_text
        if response_schema:
            agent_payload += (
                "\n\nReturn only valid JSON. The JSON must conform to this schema:\n"
                + json.dumps(response_schema, ensure_ascii=False)
            )
        return _run_prompt_agent(
            agent_name,
            agent_payload,
            response_schema=response_schema,
            max_output_tokens=max_output_tokens,
        )

    client = _project_client()
    openai_client = client.get_openai_client()
    instructions += "\nKeep the answer concise: 2 to 3 short paragraphs, no repeated schema restatement. Target 450 to 700 Chinese characters."
    create_args = {
        "model": os.environ.get("DF_CHAT_DEPLOYMENT", "gpt-5.1"),
        "instructions": instructions,
        "input": input_text,
        "max_output_tokens": max_output_tokens,
    }
    text_format = _schema_format(agent_name, response_schema)
    if text_format:
        create_args["text"] = text_format
    try:
        response = _responses_create_with_retry(openai_client, **create_args)
    except Exception as exc:
        if "text" not in create_args or not _can_retry_without_schema(exc):
            raise
        create_args.pop("text")
        response = _responses_create_with_retry(openai_client, **create_args)
    text = getattr(response, "output_text", "") or ""
    return {
        "text": text,
        "structured": _extract_json(text),
        "response_id": getattr(response, "id", None),
        "usage": _usage_dict(getattr(response, "usage", None)),
        "mode": "responses_schema",
    }


def run_market_mcp_research(payload: dict[str, Any]) -> dict[str, Any]:
    client = _project_client()
    openai_client = client.get_openai_client()
    compact_payload = {
        "category": payload.get("category"),
        "keywords": payload.get("keywords") or [],
        "limit": payload.get("limit") or 5,
        "opportunity_id": payload.get("opportunity_id"),
        "research_goal": "Call the MCP market_lookup tool and use its returned competitors as the source of truth.",
        "response_requirements": [
            "Use the MCP tool market_lookup before answering.",
            "Do not invent competitors.",
            "Return compact JSON with competitors and positioning_note.",
            "All MCP results are external market context and must remain market_inferred.",
        ],
    }
    response = _responses_create_with_retry(
        openai_client,
        input=json.dumps(compact_payload, ensure_ascii=False, indent=2),
        max_output_tokens=1200,
        extra_body=_agent_reference("df-market-researcher"),
    )
    tool_calls = _response_mcp_trace(response)
    competitors, sources = _market_results_from_mcp_trace(tool_calls)
    if not competitors:
        raise RuntimeError("df-market-researcher did not return competitors from MCP market_lookup")
    return {
        "competitors": competitors,
        "sources": sources,
        "positioning_note": "Competitor context was retrieved through Foundry Agent Service MCP market_lookup.",
        "_llm": {
            "mode": "foundry_agent_mcp",
            "response_id": getattr(response, "id", None),
            "usage": _usage_dict(getattr(response, "usage", None)),
            "tool_calls": [{key: value for key, value in call.items() if key != "output"} for call in tool_calls],
        },
    }


def run_coordinator_guidance(payload: dict[str, Any]) -> dict[str, Any]:
    client = _project_client()
    openai_client = client.get_openai_client()
    instructions = (
        "你是 DataForge 的协调器。用户的输入过短、过泛或当前工作区证据不足时，"
        "你要生成中文上下文引导，而不是固定模板。question 必须很短：一句话说明还缺目标、范围或约束，"
        "不要自我介绍，不要展开说明工作区详情；具体下一步方向放到 options。"
        "同时生成 2 到 5 个中文选项，每个选项要短、可点击、面向业务用户。"
        "不要输出数据库字段名、schema key、文件路径或 raw_docs 引用；需要提到字段含义时改写成自然中文。"
        "语气自然，避免每次复用同一句话。不要编造 profile_summary 之外的具体事实。"
        "只返回 JSON。"
    )
    create_args: dict[str, Any] = {
        "model": os.environ.get("DF_CHAT_DEPLOYMENT", "gpt-5.1"),
        "instructions": instructions,
        "input": json.dumps(payload, ensure_ascii=False, indent=2),
        "max_output_tokens": 700,
        "text": _schema_format("df_coordinator_guidance", CLARIFY_GUIDANCE_SCHEMA),
    }
    try:
        response = _responses_create_with_retry(openai_client, **create_args)
    except Exception as exc:
        if not _can_retry_without_schema(exc):
            raise
        create_args.pop("text", None)
        response = _responses_create_with_retry(openai_client, **create_args)
    text = getattr(response, "output_text", "") or ""
    data = _extract_json(text)
    return {
        "question": str(data.get("question") or "").strip(),
        "options": data.get("options") if isinstance(data.get("options"), list) else [],
        "response_id": getattr(response, "id", None),
        "usage": _usage_dict(getattr(response, "usage", None)),
        "mode": "coordinator_llm",
    }


def run_coordinator_route(payload: dict[str, Any]) -> dict[str, Any]:
    client = _project_client()
    openai_client = client.get_openai_client()
    instructions = (
        "You are the DataForge coordinator. Classify the current user message by intent using the "
        "message, workspace context, and recent conversation history. Do not use a keyword checklist. "
        "Choose exactly one intent: feasibility_analysis, followup_edit, smalltalk_or_meta, "
        "clarify_needed, or corpus_qa. Schedule only the agents that are actually needed. "
        "Use df-corpus-analyst for workspace retrieval, df-feasibility-analyst for product feasibility, "
        "df-market-researcher only when external market/competitor context is needed, df-auditor only "
        "when there is a feasibility report to audit, and df-producer only for requested artifacts. "
        "When the user is asking for advice, a plan, a recommendation, or how to act and the workspace "
        "contains relevant data, prefer answering with corpus_qa or feasibility_analysis instead of asking "
        "for clarification; ask clarification only when the workspace lacks enough evidence or the request "
        "depends on missing target/scope constraints. "
        "For followup_edit, schedule no analyst agents; the rewrite will use the prior assistant answer. "
        "For smalltalk_or_meta, schedule no analyst agents and answer directly. "
        "Return concise JSON only. Write reason and any clarifying question in Chinese."
    )
    create_args: dict[str, Any] = {
        "model": os.environ.get("DF_CHAT_DEPLOYMENT", "gpt-5.1"),
        "instructions": instructions,
        "input": json.dumps(payload, ensure_ascii=False, indent=2),
        "max_output_tokens": 650,
        "text": _schema_format("df_coordinator_route", COORDINATOR_ROUTE_SCHEMA),
    }
    try:
        response = _responses_create_with_retry(openai_client, **create_args)
    except Exception as exc:
        if not _can_retry_without_schema(exc):
            raise
        create_args.pop("text", None)
        response = _responses_create_with_retry(openai_client, **create_args)
    text = getattr(response, "output_text", "") or ""
    data = _extract_json(text)
    data["_llm"] = _response_meta(response, "coordinator_route")
    return data


def run_coordinator_direct_reply(payload: dict[str, Any]) -> dict[str, Any]:
    client = _project_client()
    openai_client = client.get_openai_client()
    instructions = (
        "You are the DataForge coordinator. Reply directly to smalltalk, capability, or meta questions. "
        "Do not invent workspace facts beyond the provided workspace context. Keep it concise, natural, "
        "and in Chinese unless the user explicitly asks for another language. Return JSON only."
    )
    return _coordinator_text_response(openai_client, instructions, payload, "coordinator_direct_reply")


# 共享指令体（run_grounded_chat_answer 与 stream_grounded_chat_answer 共用，仅结尾输出格式不同）。
_GROUNDED_CHAT_BODY = (
    "你是 DataForge 的数据分析助手，正在和客户多轮对话。请针对用户【当前这一条】消息作答，绝不套固定句式模板。\n"
    "\n"
    "先判断用户这条是【普通问答/判断】还是【要你出方案/策划/活动/计划/落地步骤】：\n"
    "\n"
    "A) 普通问答/判断（如“会员数据怎样”“值得办吗”“证据有多强”“缺什么数据”）：\n"
    "  - 先一句话给结论，开头可用一个贴切的 emoji 点题（✅ 可行 / ⚠️ 谨慎 / ❌ 不建议 / 📊 数据情况 / 💡 建议 / 🔍 发现），再结合证据解释。一般 120-280 字。\n"
    "  - 把【关键结论、数字、指标、人群】用 **加粗** 突出；有【多个并列要点】时【换行】用 `- ` 列点，必要时每点前加一个贴切 emoji（如 📈 增长、💰 成本、👥 人群、⚠️ 风险、🧩 缺口），让回答更易读。\n"
    "  - 但这种问答【不要】加 `## 大标题`、不要套方案那套小节骨架。\n"
    "\n"
    "B) 要你出方案/策划/活动/计划（如“做一场拉新活动”“帮我策划…”“这个怎么落地”“给个推广方案”）：\n"
    "  - 按下面模板输出【真正可执行的方案】，用 Markdown：每个小节用 `## 标题` 单独成行，要点用 `- ` 或 `1.` 列表，小节之间空一行。直接照这个骨架填，缺的小节可省略：\n"
    "    ## 一句话方案\n"
    "    （定位+主线，一句话）\n"
    "    ## 🎯 目标与主指标\n"
    "    - 目标：… / 主指标（北极星）：…\n"
    "    ## 👥 目标人群\n"
    "    - 结合资料里的真实人群/痛点\n"
    "    ## 🎬 活动机制（主线玩法）\n"
    "    1. …\n"
    "    2. …\n"
    "    ## 📅 节奏（时间线）\n"
    "    - 第1周：… / 第2-3周：… / 第4周：复盘\n"
    "    ## 📊 漏斗指标\n"
    "    - 曝光 → 报名 → 到店 → 转化/复购，每段给一个可量化阈值\n"
    "    ## ⚠️ 风险与先验证\n"
    "    - 最大风险 + 先用小样本验证什么\n"
    "  - 方案必须结合 evidence 里的真实信号；没有数据支撑的地方写“需补：…”，绝不编造数字。\n"
    "\n"
    "通用规则：\n"
    "1) 只用 evidence 提供的证据，不编造工作区事实；需要引用时在句末用 [n]（n=evidence 的 marker 数字）。\n"
    "2) 绝不整段照抄证据原文，不输出字段名或 '会员编号为\"Mxxxx\"'、'数值为\"…\"' 这类原始值；综合成人话。\n"
    "3) 结合 conversation_history 记住上文，理解指代与新增约束（如“预算减半”“只看旗舰店”）。\n"
    "4) 不同问题给明显不同的答案；证据不足就直说缺什么。\n"
    "5) 排版友好：适度用 emoji 与 **加粗** 让回答更生动、易扫读，但保持专业、克制——别每句都加、别堆砌。"
)


def run_grounded_chat_answer(payload: dict[str, Any]) -> dict[str, Any]:
    """用 LLM 针对用户【当前这条问题】、结合工作区证据与对话历史作答（替代写死的模板）。"""
    client = _project_client()
    openai_client = client.get_openai_client()
    instructions = _GROUNDED_CHAT_BODY + "\n只返回 JSON，字段 text 为最终回答（方案模板里的换行 \\n 必须保留）。"
    return _coordinator_text_response(openai_client, instructions, payload, "grounded_chat_answer", max_output_tokens=1700)


def stream_grounded_chat_answer(payload: dict[str, Any]) -> Any:
    """与 run_grounded_chat_answer 同一套自适应逻辑，但【真 token 流式】输出纯文本（不套 JSON）。
    逐 token 产出 {type:'delta', delta}；结束产出 {type:'meta', ...}。"""
    client = _project_client()
    openai_client = client.get_openai_client()
    instructions = _GROUNDED_CHAT_BODY + "\n直接输出最终回答正文（方案模板里的换行必须保留），不要输出 JSON、不要加任何前后缀说明。"
    create_args = {
        "model": os.environ.get("DF_CHAT_DEPLOYMENT", "gpt-5.1"),
        "instructions": instructions,
        "input": json.dumps(payload, ensure_ascii=False, indent=2),
        "max_output_tokens": 1700,
        "stream": True,
    }
    completed_meta: dict[str, Any] | None = None
    for event in _stream_response_events_with_retry(openai_client, create_args):
        delta = _stream_delta(event)
        if delta:
            yield {"type": "delta", "delta": delta}
            continue
        event_type = str(getattr(event, "type", "") or "")
        if event_type in {"response.completed", "response.done"}:
            response = getattr(event, "response", None)
            if response is not None:
                completed_meta = _response_meta(response, "grounded_chat_stream")
    yield {"type": "meta", **(completed_meta or {"mode": "grounded_chat_stream", "usage": {}})}


def run_followup_rewrite(payload: dict[str, Any]) -> dict[str, Any]:
    client = _project_client()
    openai_client = client.get_openai_client()
    instructions = (
        "You rewrite the previous DataForge answer according to the current user instruction. "
        "Use only the provided previous assistant answer and conversation history. Do not rerun or invent "
        "analysis. Preserve the substantive conclusion unless the user asks for a format or language change. "
        "Keep the rewrite lightweight: do not expand evidence panels or add new analysis; target 250-450 words. "
        "If the user asks for English, write English; otherwise follow the user's language. Return JSON only."
    )
    return _coordinator_text_response(openai_client, instructions, payload, "followup_rewrite", max_output_tokens=750)


def run_followup_assessment(payload: dict[str, Any]) -> dict[str, Any]:
    client = _project_client()
    openai_client = client.get_openai_client()
    instructions = (
        "You are the lightweight DataForge follow-up evaluator. Use only the provided last_analysis, "
        "previous assistant answer, workspace summary, and current user message. Do not rerun retrieval, "
        "market research, feasibility scoring, or audit. Judge whether the user's new idea or constraint "
        "is supported by the previous evidence, identify the most important missing information, and ask "
        "one concise clarifying question when the user intent is ambiguous or the decision depends on a "
        "missing scope, target user, metric, budget, timing, or data source. Do not use a keyword checklist "
        "or scenario-specific templates. Never invent workspace facts. Return JSON only, in the user's language."
    )
    create_args: dict[str, Any] = {
        "model": os.environ.get("DF_CHAT_DEPLOYMENT", "gpt-5.1"),
        "instructions": instructions,
        "input": json.dumps(payload, ensure_ascii=False, indent=2),
        "max_output_tokens": 850,
        "text": _schema_format("followup_assessment", FOLLOWUP_ASSESSMENT_SCHEMA),
    }
    try:
        response = _responses_create_with_retry(openai_client, **create_args)
    except Exception as exc:
        if not _can_retry_without_schema(exc):
            raise
        create_args.pop("text", None)
        response = _responses_create_with_retry(openai_client, **create_args)
    text = getattr(response, "output_text", "") or ""
    data = _extract_json(text)
    gaps = [str(item).strip() for item in (data.get("gaps") or []) if str(item).strip()]
    return {
        "text": str(data.get("text") or "").strip(),
        "assessment": str(data.get("assessment") or "unclear"),
        "gaps": gaps[:6],
        "clarify": str(data.get("clarify") or "").strip(),
        "should_clarify": bool(data.get("should_clarify")),
        "response_id": getattr(response, "id", None),
        "usage": _usage_dict(getattr(response, "usage", None)),
        "mode": "followup_assessment",
    }


def _coordinator_text_response(
    openai_client: Any,
    instructions: str,
    payload: dict[str, Any],
    mode: str,
    *,
    max_output_tokens: int = 900,
) -> dict[str, Any]:
    create_args: dict[str, Any] = {
        "model": os.environ.get("DF_CHAT_DEPLOYMENT", "gpt-5.1"),
        "instructions": instructions,
        "input": json.dumps(payload, ensure_ascii=False, indent=2),
        "max_output_tokens": max_output_tokens,
        "text": _schema_format(mode, COORDINATOR_REPLY_SCHEMA),
    }
    try:
        response = _responses_create_with_retry(openai_client, **create_args)
    except Exception as exc:
        if not _can_retry_without_schema(exc):
            raise
        create_args.pop("text", None)
        response = _responses_create_with_retry(openai_client, **create_args)
    text = getattr(response, "output_text", "") or ""
    try:
        data = _extract_json(text)
        output_text = str(data.get("text") or "").strip()
    except Exception:
        output_text = _best_effort_text_field(text) or text.strip()
    return {
        "text": output_text,
        "response_id": getattr(response, "id", None),
        "usage": _usage_dict(getattr(response, "usage", None)),
        "mode": mode,
    }


def _best_effort_text_field(text: str) -> str:
    stripped = str(text or "").strip()
    match = re.search(r'"text"\s*:\s*"', stripped)
    if not match:
        return ""
    raw = stripped[match.end() :]
    raw = re.sub(r'"\s*}\s*$', "", raw, flags=re.S)
    for end in range(len(raw), max(0, len(raw) - 500), -1):
        candidate = raw[:end]
        try:
            return json.loads('"' + candidate + '"').strip()
        except Exception:
            continue
    return raw.replace("\\n", "\n").replace('\\"', '"').strip()


def run_market_web_research(payload: dict[str, Any]) -> dict[str, Any]:
    client = _project_client()
    openai_client = client.get_openai_client()
    verification = _verify_foundry_web_tool(openai_client)
    if not verification.get("available"):
        return {
            "external_findings": [],
            "sources": [],
            "positioning_note": "Foundry native web search is unavailable for this deployment; market analysis is limited to internal competitor lookup.",
            "_llm": {
                "mode": "foundry_web_unavailable",
                "response_id": None,
                "usage": {},
                "verification": verification,
            },
        }

    instructions = (
        "你是 DataForge 的市场研究员。请使用已经验证可用的 Foundry 原生 web 搜索工具查询公开市场信息，"
        "只返回 JSON。所有外部网页信息都必须标记为 market_inferred，不能说成工作区数据已确认。"
        "主动搜索同类产品、竞品、替代方案、价格/套餐、活动玩法或增长机制，并给出我们与它们的差异点。"
        "输出 2 到 4 条外部竞品/同类机会对比结论，每条必须尽量带 source_url；positioning_note 要短，说明竞品在做什么、价格/玩法、我们的差异点。"
    )
    create_args: dict[str, Any] = {
        "model": os.environ.get("DF_CHAT_DEPLOYMENT", "gpt-5.1"),
        "instructions": instructions,
        "input": json.dumps(payload, ensure_ascii=False, indent=2),
        "tools": [verification["tool"]],
        "max_output_tokens": 1200,
        "text": _schema_format("df_market_web_research", MARKET_WEB_SCHEMA),
    }
    try:
        response = _responses_create_with_retry(openai_client, **create_args)
    except Exception as exc:
        if not _can_retry_without_schema(exc):
            raise
        create_args.pop("text", None)
        response = _responses_create_with_retry(openai_client, **create_args)
    text = getattr(response, "output_text", "") or ""
    sources = _response_sources(response)
    tool_calls = _response_tool_trace(response)
    try:
        data = _extract_json(text)
    except Exception:
        data = {"external_findings": [], "positioning_note": text.strip()}
    findings = data.get("external_findings") or []
    clean_findings: list[dict[str, Any]] = []
    for idx, finding in enumerate(findings[:4]):
        if not isinstance(finding, dict):
            continue
        source = sources[idx] if idx < len(sources) else {}
        item = {
            "claim": str(finding.get("claim") or "").strip(),
            "source_title": str(finding.get("source_title") or source.get("title") or "").strip(),
            "source_url": str(finding.get("source_url") or source.get("url") or "").strip(),
            "confidence": "market_inferred",
            "source_type": "market",
        }
        if item["claim"]:
            clean_findings.append(item)
            if item["source_url"] and all(source.get("url") != item["source_url"] for source in sources):
                sources.append(
                    {
                        "title": item["source_title"] or item["source_url"],
                        "url": item["source_url"],
                        "confidence": "market_inferred",
                    }
                )
    meta = _response_meta(response, f"foundry_native_{verification['name']}")
    meta["verification"] = {
        "tool": verification.get("name"),
        "response_id": verification.get("response_id"),
        "sources": verification.get("sources", []),
        "tool_calls": verification.get("tool_calls", []),
    }
    meta["tool_calls"] = tool_calls
    return {
        "external_findings": clean_findings,
        "sources": sources,
        "positioning_note": str(data.get("positioning_note") or "").strip(),
        "_llm": meta,
    }


def stream_grounded_answer(payload: dict[str, Any]) -> Any:
    client = _project_client()
    openai_client = client.get_openai_client()
    instructions = (
        "你是 DataForge 的最终回答输出器。请把结构化 FeasibilityReport、审计结论、"
        "工作区证据和市场补充信息整理成中文分析叙述。要求："
        "1) 只基于输入中的事实与证据，不编造；"
        "2) 每个主要判断都写明证据 ref 或来源类型，并标注 data_confirmed、market_inferred 或 speculative；"
        "3) 工作区证据与外部市场推断必须区分，market_inferred 不能写成工作区已确认事实；"
        "4) 如果审计指出问题，要自然说明修订或保守处理；"
        "5) 不要只输出模板句，要结合本次数据给出 3 到 6 段可读分析。"
    )
    create_args = {
        "model": os.environ.get("DF_CHAT_DEPLOYMENT", "gpt-5.1"),
        "instructions": instructions,
        "input": json.dumps(payload, ensure_ascii=False, indent=2),
        "max_output_tokens": 520,
        "stream": True,
    }
    completed_meta: dict[str, Any] | None = None
    for event in _stream_response_events_with_retry(openai_client, create_args):
        delta = _stream_delta(event)
        if delta:
            yield {"type": "delta", "delta": delta}
            continue
        event_type = str(getattr(event, "type", "") or "")
        if event_type in {"response.completed", "response.done"}:
            response = getattr(event, "response", None)
            if response is not None:
                completed_meta = _response_meta(response, "responses_stream")
    yield {"type": "meta", **(completed_meta or {"mode": "responses_stream", "usage": {}})}
