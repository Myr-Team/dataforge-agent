from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import os
import re
import threading
import time
import urllib.request
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from starlette.concurrency import run_in_threadpool

try:
    from . import cache_store
    from . import content_safety
    from .chat_loop_primitives import sse
    from .conversation_store import append_message, conversation_context
    from .customer_text import (
        clarify_options_from_context,
        customer_hit_title,
        field_label_map_from_hits,
        friendly_label,
        normalize_clarify_options,
        record_pairs,
        sanitize_citations,
        sanitize_customer_text,
    )
    from .feasibility_rubric import (
        apply_post_audit_guardrails,
        apply_pre_audit_guardrails,
        attach_rubric_metadata,
        audit_label,
        confidence_label,
        dimension_label,
        finalize_verdict_contract,
        load_rubric,
        make_blind_verdict,
        rubric_version,
        verdict_label,
    )
    from .foundry_client import (
        run_agent,
        run_coordinator_direct_reply,
        run_coordinator_guidance,
        run_coordinator_route,
        run_followup_rewrite,
        run_action_plan,
        run_executive_summary,
        run_image_subject,
        run_playbook_detail,
        run_grounded_chat_answer,
        run_market_mcp_research,
        run_market_web_research,
        stream_grounded_answer,
        stream_grounded_chat_answer,
    )
    from .pm_skills import playbook_suggestion
    from .rag import search
    from .router import deterministic_route
    from .run_store import complete_run, get_run, record_event, start_run
    from .schemas import AuditVerdict, ChatRequest, Evidence, FeasibilityReport, RoutingDecision
    from .tracing import trace_event
    from .tools.generate_image import generate_image
    from .tools.narrate_summary import narrate_summary
    from .tools.render_pdf import render_pdf_report
    from .workspace_store import get_workspace_detail, save_workspace_last_analysis, workspace_context, workspace_reference_images
except ImportError:
    import cache_store
    import content_safety
    from chat_loop_primitives import sse
    from conversation_store import append_message, conversation_context
    from customer_text import (
        clarify_options_from_context,
        customer_hit_title,
        field_label_map_from_hits,
        friendly_label,
        normalize_clarify_options,
        record_pairs,
        sanitize_citations,
        sanitize_customer_text,
    )
    from feasibility_rubric import (
        apply_post_audit_guardrails,
        apply_pre_audit_guardrails,
        attach_rubric_metadata,
        audit_label,
        confidence_label,
        dimension_label,
        finalize_verdict_contract,
        load_rubric,
        make_blind_verdict,
        rubric_version,
        verdict_label,
    )
    from foundry_client import (
        run_agent,
        run_coordinator_direct_reply,
        run_coordinator_guidance,
        run_coordinator_route,
        run_followup_rewrite,
        run_action_plan,
        run_executive_summary,
        run_image_subject,
        run_playbook_detail,
        run_grounded_chat_answer,
        run_market_mcp_research,
        run_market_web_research,
        stream_grounded_answer,
        stream_grounded_chat_answer,
    )
    from pm_skills import playbook_suggestion
    from rag import search
    from router import deterministic_route
    from run_store import complete_run, get_run, record_event, start_run
    from schemas import AuditVerdict, ChatRequest, Evidence, FeasibilityReport, RoutingDecision
    from tracing import trace_event
    from tools.generate_image import generate_image
    from tools.narrate_summary import narrate_summary
    from tools.render_pdf import render_pdf_report
    from workspace_store import get_workspace_detail, save_workspace_last_analysis, workspace_context, workspace_reference_images


PRODUCT_TERMS = (
    "product",
    "saas",
    "business",
    "proposal",
    "package",
    "\u4ea7\u54c1",
    "\u5546\u4e1a",
    "\u9879\u76ee\u4e66",
    "\u65b9\u6848",
    "\u53d8\u73b0",
)
_MARKET_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_MARKET_CACHE_SECONDS = float(os.environ.get("DF_MARKET_CACHE_SECONDS", "600"))
MCP_TOOL_ALLOWLIST: dict[str, dict[str, Any]] = {
    "market_lookup": {
        "source_type": "market_mcp",
        "confidence": "market_inferred",
        "risk": "read",
        "require_approval": "never",
        "implemented": True,
    },
    "pricing_benchmark_lookup": {
        "source_type": "market_mcp",
        "confidence": "market_inferred",
        "risk": "read",
        "require_approval": "never",
        "implemented": False,
    },
    "pm_playbook_suggest": {
        "source_type": "pm_skill",
        "confidence": "speculative",
        "risk": "read",
        "require_approval": "never",
        "implemented": True,
    },
    "data_quality_diagnose": {
        "source_type": "workspace_computed",
        "confidence": "data_confirmed",
        "risk": "read",
        "require_approval": "never",
        "implemented": False,
    },
}


def _contains(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in terms)


def _clean_text(value: Any, limit: int | None = None) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit] if limit else text


def _intent_message(message: Any) -> str:
    text = str(message or "").strip()
    match = re.search(r"(?:^|\n)Current user message:\s*(.+)\s*$", text, re.S)
    if match:
        return match.group(1).strip()
    return text


def _looks_like_solution_request(message: str) -> bool:
    text = _intent_message(message).lower()
    if not text:
        return False
    cjk_action = re.search(
        r"(怎么|如何|怎样|方案|建议|推荐|策略|计划|活动|推广|落地|执行|设计|产品化|路线图|定价|实验|"
        r"prd|项目书|生成|输出|证据|最强|最弱|试点|客群|只看|工作区数据|当前数据|这批数据|"
        r"能做什么|先做什么|产品方向|企划|拉新|新客|转化|宣传|曝光|名声)",
        text,
    )
    latin_action = re.search(r"\b(how|plan|strategy|recommend|recommendation|advice|campaign|promote|proposal|approach)\b", text)
    return bool(cjk_action or latin_action)


def _artifact_generation_requested(message: str) -> bool:
    return bool(re.search(r"(生成|输出|制作|产出).{0,16}(项目书|prd|路线图|实验计划|方案|报告|文档)", _intent_message(message), re.I))


def _data_only_requested(message: str) -> bool:
    text = _intent_message(message)
    lowered = text.lower()
    if re.search(r"\b(only|exclude|without|no)\b.{0,24}\b(external|market|competitor|competition|web)\b", lowered):
        return True
    if re.search(r"(只看|仅看|不要|不需要|排除|不看).{0,16}(工作区|当前数据|这批数据|外部|市场|竞品|竞对)", text, re.I):
        return True
    return bool(re.search(r"(只看|仅看|不要|不需要|排除).{0,16}(工作区|当前数据|这批数据|外部|市场|竞品)", text, re.I))


NEXT_STEP_HINT_RE = re.compile(r"(下一步|建议|可以先做|验证|试点|路线图|PRD|实验)", re.I)


def _ensure_next_step_hint(text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned or NEXT_STEP_HINT_RE.search(cleaned):
        return cleaned
    separator = "" if re.search(r"[。.!！？?]$", cleaned) else "。"
    return cleaned + separator + "下一步建议：告诉我你想先看产品机会、目标客群、证据强弱，还是直接生成 PRD 或路线图。"


def _market_context_requested(message: str) -> bool:
    text = _intent_message(message).lower()
    if not text:
        return False
    direct_terms = (
        "竞品",
        "竞对",
        "竞争对手",
        "替代方案",
        "替代品",
        "外部市场",
        "市场行情",
        "外部行情",
        "行业对比",
        "同类产品",
        "差异化",
        "定价基准",
        "价格对比",
        "标杆产品",
        "benchmark",
        "competitor",
        "competition",
        "alternative",
        "market research",
        "pricing benchmark",
    )
    if _contains(text, direct_terms):
        return True
    return bool(
        re.search(r"(外部|市场|行业|定价|价格).{0,10}(对比|基准|标杆|竞品|竞对|替代)", text)
        or re.search(r"(对比|基准|标杆|竞品|竞对|替代).{0,10}(外部|市场|行业|定价|价格)", text)
    )


def _preset_outcome_requested(message: str) -> bool:
    return bool(
        re.search(
            r"(无论如何|不管证据|不管资料|一定|必须|直接).{0,12}(可行|高分|通过)|打高分|always say feasible|force feasible",
            _intent_message(message),
            re.I,
        )
    )


def _explicit_heavy_analysis_requested(message: str) -> bool:
    text = _intent_message(message)
    if not text:
        return False
    if _artifact_generation_requested(text) or _preset_outcome_requested(text):
        return True
    return bool(
        re.search(
            r"(自动分析|完整分析|深度分析|系统分析|可行性|五维|评分|报告|项目书|prd|路线图|实验计划|"
            r"机会树|jtbd|定价策略|商业模式|产品化.{0,12}(方向|机会|方案|什么)|"
            r"能.{0,8}产品化|能做成什么|做成.{0,8}产品|输出.{0,12}(分析|报告|方案)|"
            r"生成.{0,12}(分析|报告|方案)|full package|feasibility|roadmap|experiment plan)",
            text,
            re.I,
        )
    )


def _ordinary_workspace_qa_requested(message: str) -> bool:
    text = _intent_message(message)
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return False
    if _market_context_requested(text) or _artifact_generation_requested(text) or _preset_outcome_requested(text):
        return False
    if _explicit_heavy_analysis_requested(text):
        return False
    if len(compact) <= 90:
        return bool(
            re.search(
                r"(吗|呢|嘛|？|\?|怎么看|是否|能不能|有没有|值不值得|值得|好不好|该不该|要不要|"
                r"为什么|怎么|如何|哪|多少|证据|依据|只看|当前资料|工作区|这批|还有|注意|建议)",
                text,
                re.I,
            )
        )
    return False


def _looks_like_context_followup(message: str) -> bool:
    """只识别【纯改写/排版】类追问（说短点/翻译/列表化…）——这种才走快速改写。
    像“预算减半先砍哪部分”“为什么”“哪个”这类【分析型追问】不算，放它去 corpus_qa 走真 LLM 作答，
    否则会落到写死的 _fast_followup_reply 模板，答非所问。"""
    text = _intent_message(message)
    compact = re.sub(r"\s+", "", text)
    if not compact or len(compact) > 60:
        return False
    if _market_context_requested(text) or _artifact_generation_requested(text) or _explicit_heavy_analysis_requested(text):
        return False
    return bool(
        re.search(
            r"(说.{0,4}(短|简短|简洁|精简)|短一点|简短点|精简|换个?(说法|表述|角度说)|换种说法|重写|改写|"
            r"再(写|说|表述|组织)一?(遍|次|下)|翻译|英文|中文|用表格|列成表|做成表格|分点列|列表化|"
            r"加(个)?标题|换行|排版|正式(一点|些)|口语化|更(专业|正式|简单))",
            text,
            re.I,
        )
    )


def _request_with_history(req: ChatRequest, history: list[dict[str, Any]]) -> ChatRequest:
    context_blocks: list[str] = []
    if history:
        turns = []
        for item in history[-20:]:
            role = str(item.get("role") or "message")
            text = _clean_text(item.get("text"), 900)
            if text:
                turns.append(f"{role}: {text}")
        if turns:
            context_blocks.append("Conversation history for continuity:\n" + "\n".join(turns))
    ui_lines = _ui_context_lines(req)
    if ui_lines:
        context_blocks.append("UI-selected analysis context:\n" + "\n".join(ui_lines))
    if not context_blocks:
        return req
    contextual_message = (
        "\n\n".join(context_blocks)
        + "\n\nCurrent user message:\n"
        + req.message
    )
    return req.model_copy(update={"message": contextual_message})


def _ui_context_lines(req: ChatRequest) -> list[str]:
    lines: list[str] = []
    playbook = _clean_text(getattr(req, "playbook", None), 120)
    if playbook:
        lines.append(f"- Product playbook: {playbook}")
        try:
            suggestion = playbook_suggestion(playbook)
            lines.append(
                "- Playbook guardrail: "
                + _clean_text(
                    f"{suggestion['label']} focuses on {suggestion['focus']} {suggestion['guardrail']}",
                    300,
                )
            )
        except Exception:
            pass
    artifact_mode = _clean_text(getattr(req, "artifact_mode", None), 120)
    if artifact_mode:
        lines.append(f"- Requested artifact mode: {artifact_mode}")
    ui_context = getattr(req, "ui_context", None)
    if isinstance(ui_context, dict):
        for key, value in list(ui_context.items())[:8]:
            label = _clean_text(key, 80)
            if not label:
                continue
            if isinstance(value, (dict, list)):
                text = _clean_text(json.dumps(value, ensure_ascii=False), 240)
            else:
                text = _clean_text(value, 240)
            if text:
                lines.append(f"- {label}: {text}")
    return lines[:10]


def _compact_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for item in history[-20:]:
        text = _clean_text(item.get("text"), 900)
        if text:
            compact.append({"role": item.get("role"), "text": text, "time": item.get("time")})
    return compact


def _persist_user_message(conversation_id: str, workspace_id: str, text: str, assume_new: bool = False) -> None:
    try:
        append_message(conversation_id, workspace_id=workspace_id, role="user", text=text, remote_load=not assume_new)
    except Exception:
        pass


def _persist_assistant_message(conversation_id: str, workspace_id: str, text: str, verdict: str | None = None, citations: list[dict[str, Any]] | None = None) -> None:
    try:
        append_message(conversation_id, workspace_id=workspace_id, role="assistant", text=text, verdict=verdict, citations=citations)
    except Exception:
        pass


def _persist_last_analysis(workspace_id: str, final_payload: dict[str, Any]) -> None:
    try:
        save_workspace_last_analysis(workspace_id, final_payload)
    except Exception:
        pass


def _artifact_verdict(artifact: dict[str, Any], fallback: str | None = None) -> str | None:
    feasibility = artifact.get("feasibility") or {}
    if isinstance(feasibility, dict) and feasibility.get("verdict"):
        return str(feasibility.get("verdict"))
    return fallback


def _slug(value: str, fallback: str = "generated-data-product") -> str:
    compact = _collapse_repeated_slug(value)
    slug = re.sub(r"[^a-z0-9]+", "-", compact.lower()).strip("-")
    tokens = [token for token in slug.split("-") if token]
    if len(tokens) > 8:
        slug = "-".join(tokens[:8])
    return slug[:72].strip("-") or fallback


def _collapse_repeated_slug(value: Any) -> str:
    raw = str(value or "").strip()
    tokens = [token for token in re.split(r"[-_\s]+", raw) if token]
    if not tokens:
        return raw
    collapsed: list[str] = []
    idx = 0
    while idx < len(tokens):
        best_size = 0
        best_end = idx
        max_size = (len(tokens) - idx) // 2
        for size in range(1, max_size + 1):
            seq = tokens[idx : idx + size]
            end = idx + size
            repeats = 1
            while end + size <= len(tokens) and tokens[end : end + size] == seq:
                repeats += 1
                end += size
            if repeats > 1 and size > best_size:
                best_size = size
                best_end = end
        if best_size:
            collapsed.extend(tokens[idx : idx + best_size])
            idx = best_end
        else:
            if not collapsed or collapsed[-1].lower() != tokens[idx].lower():
                collapsed.append(tokens[idx])
            idx += 1
    for size in range(1, len(collapsed) // 2 + 1):
        prefix = collapsed[:size]
        tail = collapsed[size:]
        if tail and len(tail) <= size and all(
            prefix[pos].lower().startswith(tail[pos].lower()) or tail[pos].lower().startswith(prefix[pos].lower())
            for pos in range(len(tail))
        ):
            collapsed = prefix
            break
    return " ".join(collapsed)


def _human_title_from_opportunity(value: Any) -> str:
    text = _collapse_repeated_slug(value)
    text = re.sub(r"[\\/#.]+", " ", text)
    text = re.sub(r"[_-]+", " ", text)
    text = re.sub(r"\b(raw|docs|json|csv|xlsx|xlsm|profile)\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" -_")
    if not text:
        return "当前工作区机会"
    if re.search(r"[\u4e00-\u9fff]", text):
        return _clip_title_clause(text, 32)
    words = text.split()
    return " ".join(word.capitalize() if word.isascii() else word for word in words[:8])


def _query_action_title(message: str) -> str:
    raw = str(message or "").strip()
    text = re.sub(r"\s+", "", raw)
    text = re.sub(r"[，。！？!?；;：:、,.]", "", text)
    text = re.sub(r"^(我想|请|帮我|能不能|可以|想要|需要|基于|围绕)", "", text)
    text = re.sub(r"(请)?(基于|根据).*$", "", text)
    text = text.replace("该怎么做", "怎么做")
    if re.search(r"[\u4e00-\u9fff]", text):
        for marker in ("怎么做", "如何做", "怎么", "如何", "有什么建议", "给建议", "推荐", "帮我分析一下", "分析一下"):
            text = text.replace(marker, "")
        text = re.sub(r"(数据|资料|工作区|方案|建议|策略|计划|可行性|评估|分析)$", "", text)
        return _complete_short_phrase(text, fallback="当前问题")
    words = [word for word in re.findall(r"[A-Za-z0-9]+", str(message or "")) if len(word) > 2]
    return " ".join(words[:5]).title()


_TITLE_SKIP_FIELDS = {
    "collection",
    "id",
    "row",
    "source",
    "source_file",
    "chunk_id",
    "workspace_id",
    "document_type",
}
_TITLE_SCENE_FIELDS = ("store", "branch", "location", "region", "city", "venue", "门店", "区域", "城市", "地点")
_TITLE_ACTION_FIELDS = (
    "topic",
    "activity",
    "campaign",
    "event",
    "product",
    "plan",
    "theme",
    "pain",
    "signal",
    "sponsor",
    "conversion",
    "主题",
    "活动",
    "产品",
    "痛点",
    "赞助",
    "转化",
)
_QUERY_FILLERS = (
    "请",
    "帮我",
    "我想",
    "能不能",
    "可以",
    "评估",
    "分析",
    "这些",
    "资料",
    "数据",
    "工作区",
    "做成",
    "做一个",
    "给",
    "一版",
    "一下",
    "怎么",
    "如何",
    "可行性",
    "方案",
    "建议",
)


def _complete_short_phrase(text: Any, fallback: str = "当前资料机会", limit: int = 28) -> str:
    clean = sanitize_customer_text(str(text or ""))
    clean = re.sub(r"\s+", "", clean).strip(" ，。；;：:-_\"'“”")
    clean = re.sub(r"[。；;]+$", "", clean)
    clean = clean.replace("……", "").replace("...", "").replace("…", "")
    if not clean:
        return fallback
    if len(clean) <= limit:
        return clean
    for sep in ("，", "。", "；", "、", "：", "/", "／", "|"):
        pos = clean[:limit].rfind(sep)
        if pos >= int(limit * 0.5):
            candidate = clean[:pos].rstrip("，。；、：/／| ")
            if candidate:
                return candidate
    return fallback


def _title_value(value: Any, limit: int = 18) -> str:
    text = sanitize_customer_text(str(value or ""))
    text = re.sub(r"\s+", " ", text).strip(" ，。；;：:-_\"'“”")
    text = re.sub(r"\b(raw_docs|profile|chunk|row-\d+)\b", "", text, flags=re.I).strip()
    if not text or re.fullmatch(r"[-+]?\d+(?:\.\d+)?%?", text):
        return ""
    first_clause = re.split(r"[。；;\n]", text, maxsplit=1)[0].strip(" ，、")
    if not first_clause:
        return ""
    if len(first_clause) > limit:
        for sep in ("，", "、", "/", "／", "|"):
            pos = first_clause[:limit].rfind(sep)
            if pos >= int(limit * 0.45):
                first_clause = first_clause[:pos].strip(" ，、/／|")
                break
        else:
            return ""
    if len(first_clause) < 2:
        return ""
    return first_clause


def _field_group(name: Any) -> str:
    lowered = str(name or "").lower()
    if any(term in lowered for term in _TITLE_ACTION_FIELDS):
        return "action"
    if any(term in lowered for term in _TITLE_SCENE_FIELDS):
        return "scene"
    return "other"


def _evidence_topic_from_hits(hits: list[dict[str, Any]]) -> str:
    candidates: list[tuple[int, str, str]] = []
    seen: set[str] = set()
    for hit in hits[:10]:
        content = str(hit.get("content") or "")
        for name, value in record_pairs(content):
            lowered = str(name or "").lower().strip()
            if lowered in _TITLE_SKIP_FIELDS:
                continue
            clean_value = _title_value(value)
            if not clean_value:
                continue
            key = re.sub(r"\W+", "", clean_value.lower())
            if not key or key in seen:
                continue
            seen.add(key)
            group = _field_group(name)
            score = 30 if group == "action" else 22 if group == "scene" else 10
            if re.search(r"(活动|推广|赞助|会员|转化|复购|品牌|痛点|权益|产品)", clean_value):
                score += 8
            candidates.append((score, group, clean_value))
    if not candidates:
        return ""
    candidates.sort(key=lambda item: item[0], reverse=True)
    action = next((value for _, group, value in candidates if group == "action"), "")
    scene = next((value for _, group, value in candidates if group == "scene" and value != action), "")
    other = next((value for _, group, value in candidates if group == "other" and value not in {action, scene}), "")
    if action and scene:
        return _complete_short_phrase(f"{scene}×{action}验证")
    if action and other:
        return _complete_short_phrase(f"{action}×{other}验证")
    if action:
        suffix = "" if re.search(r"(活动|推广|验证|产品|赞助|转化)$", action) else "验证"
        return _complete_short_phrase(f"{action}{suffix}")
    if scene and other:
        return _complete_short_phrase(f"{scene}×{other}机会验证")
    if scene:
        return _complete_short_phrase(f"{scene}机会验证")
    return _complete_short_phrase(candidates[0][2])


def _normalize_for_echo(value: Any) -> str:
    text = re.sub(r"\s+", "", str(value or "")).lower()
    text = re.sub(r"[，。！？!?；;：:、,.\-_/\\#（）()\"'“”]", "", text)
    for filler in _QUERY_FILLERS:
        text = text.replace(filler, "")
    return text


def _looks_like_query_echo(title: Any, message: Any) -> bool:
    title_norm = _normalize_for_echo(title)
    message_norm = _normalize_for_echo(message)
    if not title_norm or not message_norm:
        return False
    if title_norm in message_norm or message_norm in title_norm:
        return True
    common = sum(1 for char in set(title_norm) if char in message_norm)
    return common / max(len(set(title_norm)), 1) >= 0.78 and len(title_norm) >= 6


def _clean_opportunity_label(raw: Any, req: ChatRequest, artifact: dict[str, Any]) -> str:
    evidence_title = _evidence_topic_from_hits(artifact.get("corpus", {}).get("hits", []))
    collapsed = _human_title_from_opportunity(raw)
    raw_text = str(raw or "")
    raw_tokens = [token for token in re.split(r"[-_\s]+", raw_text.lower()) if token]
    collapsed_tokens = [token for token in re.split(r"\s+", collapsed.lower()) if token]
    source_like = bool(re.search(r"\b(raw_docs|upload|workspace|profile|json|csv|xlsx|batch\d+)\b", raw_text, re.I))
    repeated_or_truncated = source_like or len(raw_tokens) > len(collapsed_tokens) + 2 or len(raw_text) > 72 or "…" in collapsed
    if evidence_title and (
        repeated_or_truncated
        or _looks_like_query_echo(collapsed, req.message)
        or re.search(r"(方案|可行性|评估)$", collapsed)
    ):
        return evidence_title
    return _complete_short_phrase(collapsed, fallback=evidence_title or "当前工作区机会")


def _customer_opportunity_title(req: ChatRequest, artifact: dict[str, Any], raw: Any) -> str:
    evidence_title = _evidence_topic_from_hits(artifact.get("corpus", {}).get("hits", []))
    cleaned = _clean_opportunity_label(raw, req, artifact)
    if evidence_title and (
        not cleaned
        or not re.search(r"[\u4e00-\u9fff]", cleaned)
        or re.search(r"\b(raw|docs|upload|workspace|profile|batch\d+)\b", cleaned, re.I)
        or "…" in cleaned
        or _looks_like_query_echo(cleaned, req.message)
    ):
        return evidence_title
    return cleaned or evidence_title or "当前工作区机会"


def _clip_title_clause(text: Any, limit: int = 32) -> str:
    clean = re.sub(r"\s+", "", str(text or "")).strip(" ，。；;：:-_")
    if len(clean) <= limit:
        return clean
    head = clean[:limit]
    for sep in ("，", "。", "；", "、", "："):
        pos = head.rfind(sep)
        if pos >= int(limit * 0.55):
            return head[:pos].rstrip("，。；、： ")
    return head.rstrip("，。；、： ") + "…"


def _normalize_feasibility_opportunity(report: FeasibilityReport, req: ChatRequest, artifact: dict[str, Any]) -> FeasibilityReport:
    data = report.model_dump()
    data["opportunity_id"] = _clean_opportunity_label(data.get("opportunity_id"), req, artifact)
    return FeasibilityReport.model_validate(data)


def _evidence_from_hit(hit: dict[str, Any]) -> Evidence:
    quote = _clean_text(hit.get("content", ""), 360)
    ref_parts = [
        str(hit.get("source_file") or "unknown"),
        str(hit.get("sheet") or ""),
        str(hit.get("row") or ""),
        str(hit.get("chunk_id") or hit.get("id") or "chunk"),
    ]
    ref = "#".join(part for part in ref_parts if part)
    return Evidence(source_type="corpus", ref=ref, quote=quote or None)


def _frame(event: str, data: Any, conversation_id: str | None = None) -> str:
    if event != "answer_delta" or os.environ.get("DF_TRACE_DELTAS") == "1":
        trace_event(event, data, conversation_id)
    try:
        record_event(conversation_id, event, data)
    except Exception:
        pass
    return sse(event, data)


def _agent_tool_events(agent: str, meta: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for call in meta.get("tool_calls", []):
        events.append(
            (
                "tool_call",
                {
                    "agent": agent,
                    "name": call.get("name"),
                    "args": call.get("args") or {},
                    "autonomous": True,
                },
            )
        )
        events.append(
            (
                "tool_result",
                {
                    "agent": agent,
                    "name": call.get("name"),
                    "count": call.get("count"),
                    "autonomous": True,
                },
            )
        )
    return events


def _coordinator(req: ChatRequest, history: list[dict[str, Any]]) -> tuple[RoutingDecision, dict[str, Any]]:
    context = workspace_context(req.workspace_id)
    profile = {}
    if isinstance(context.get("profile"), dict):
        profile = context["profile"]
    elif isinstance(context.get("detail"), dict) and isinstance(context["detail"].get("profile"), dict):
        profile = context["detail"]["profile"]
    pm_skill = playbook_suggestion(getattr(req, "playbook", None), profile)
    payload = {
        "workspace_id": req.workspace_id,
        "workspace_context": context,
        "current_message": req.message,
        "ui_context": {
            "playbook": getattr(req, "playbook", None),
            "pm_skill": pm_skill,
            "artifact_mode": getattr(req, "artifact_mode", None),
            "details": getattr(req, "ui_context", {}),
        },
        "conversation_history": _compact_history(history),
        "has_previous_assistant_answer": any(item.get("role") == "assistant" for item in history),
        "allowed_agents": [
            "df-corpus-analyst",
            "df-feasibility-analyst",
            "df-market-researcher",
            "df-auditor",
            "df-producer",
        ],
        "style_nonce": uuid.uuid4().hex[:8],
    }
    try:
        raw = run_coordinator_route(payload)
        decision = _routing_decision_from_llm(req, raw)
        return decision, raw.get("_llm") or {"mode": "coordinator_route"}
    except Exception as exc:
        if os.environ.get("DF_COORDINATOR_ALLOW_DETERMINISTIC_FALLBACK") == "1":
            decision = deterministic_route(req.message, req.workspace_id, {"doc_count": context.get("doc_count", 0)})
            _apply_requested_output_mode(req, decision)
            decision.reason = f"Coordinator LLM failed; deterministic development fallback used: {_clean_text(exc, 240)}"
            return decision, {"mode": "deterministic_fallback", "error": _clean_text(exc, 300)}
        return (
            RoutingDecision(
                workspace_id=req.workspace_id,
                intent="clarify_needed",
                experts=[],
                output_mode="chat",
                needs_clarification=True,
                clarifying_question=None,
                reason=f"Coordinator LLM unavailable, so DataForge needs clarification instead of guessing: {_clean_text(exc, 240)}",
            ),
            {"mode": "coordinator_route_unavailable", "error": _clean_text(exc, 300)},
        )


def _preflight_fast_route(req: ChatRequest, history: list[dict[str, Any]]) -> tuple[RoutingDecision, dict[str, Any]] | None:
    context = workspace_context(req.workspace_id)
    doc_count = int(context.get("doc_count") or 0)
    if doc_count <= 0:
        return None
    current_message = _intent_message(req.message)
    requested_mode = str(getattr(req, "artifact_mode", "") or "").strip()
    requested_analysis_mode = requested_mode in {"report", "full_package", "proposal"}
    wants_artifact = requested_mode in {"full_package", "proposal"} or _artifact_generation_requested(current_message)
    market_context = _market_context_requested(current_message)
    auto_analyze = _is_auto_analyze_request(req)
    heavy = _explicit_heavy_analysis_requested(current_message) or auto_analyze or requested_analysis_mode
    if (
        req.conversation_id
        and history
        and _looks_like_context_followup(current_message)
        and not heavy
        and not wants_artifact
        and not market_context
    ):
        decision = RoutingDecision(
            workspace_id=req.workspace_id,
            intent="followup_edit",
            experts=[],
            output_mode="chat",
            needs_clarification=False,
            clarifying_question=None,
            reason="同会话短追问命中快速路径，复用上一轮上下文，跳过 coordinator、检索和市场工具。",
        )
        return decision, {
            "mode": "preflight_fast_route",
            "fast_path": "followup_edit",
            "market_tools_allowed": False,
        }
    if (
        _ordinary_workspace_qa_requested(current_message)
        and not heavy
        and not wants_artifact
        and not market_context
    ):
        decision = RoutingDecision(
            workspace_id=req.workspace_id,
            intent="corpus_qa",
            experts=["df-corpus-analyst"],
            output_mode="chat",
            needs_clarification=False,
            clarifying_question=None,
            reason="普通工作区问答命中快速路径，直接检索当前资料并简答，跳过 coordinator、市场联网和完整多 Agent 链。",
        )
        return decision, {
            "mode": "preflight_fast_route",
            "fast_path": "corpus_qa",
            "market_tools_allowed": False,
        }
    return None


def _routing_decision_from_llm(req: ChatRequest, raw: dict[str, Any]) -> RoutingDecision:
    intent = str(raw.get("intent") or "clarify_needed").strip()
    allowed_intents = {"feasibility_analysis", "followup_edit", "smalltalk_or_meta", "clarify_needed", "corpus_qa"}
    if intent not in allowed_intents:
        intent = "clarify_needed"
    context = workspace_context(req.workspace_id)
    doc_count = int(context.get("doc_count") or 0)
    current_message = _intent_message(req.message)
    requested_mode = str(getattr(req, "artifact_mode", "") or "").strip()
    requested_analysis_mode = requested_mode in {"report", "full_package", "proposal"}
    wants_artifact = requested_mode in {"full_package", "proposal"} or _artifact_generation_requested(current_message)
    data_only = _data_only_requested(current_message)
    market_context = _market_context_requested(current_message)
    auto_analyze = _is_auto_analyze_request(req)
    explicit_heavy = _explicit_heavy_analysis_requested(current_message) or auto_analyze or requested_analysis_mode
    ordinary_qa = _ordinary_workspace_qa_requested(current_message)
    followup_context = bool(req.conversation_id) and _looks_like_context_followup(current_message)
    allow_market_tools = bool((market_context or auto_analyze) and not data_only)
    forced_grounded_answer = False
    forced_fast_path = False
    if doc_count > 0 and followup_context and not explicit_heavy and not wants_artifact and not market_context:
        intent = "followup_edit"
        raw["needs_clarification"] = False
        raw["output_mode"] = "chat"
        forced_fast_path = True
    elif doc_count > 0 and (_preset_outcome_requested(current_message) or wants_artifact or auto_analyze):
        intent = "feasibility_analysis"
        raw["needs_clarification"] = False
        forced_grounded_answer = True
        if wants_artifact and not raw.get("output_mode"):
            raw["output_mode"] = "full_package"
    elif doc_count > 0 and ordinary_qa and not requested_analysis_mode:
        intent = "corpus_qa"
        raw["needs_clarification"] = False
        raw["output_mode"] = "chat"
        forced_fast_path = True
    elif intent == "clarify_needed" and _looks_like_solution_request(current_message) and explicit_heavy and doc_count > 0:
        intent = "feasibility_analysis"
        raw["needs_clarification"] = False
        forced_grounded_answer = True
    elif intent in {"clarify_needed", "corpus_qa"} and market_context and doc_count > 0 and not data_only:
        intent = "feasibility_analysis"
        raw["needs_clarification"] = False
        forced_grounded_answer = True
    elif intent == "feasibility_analysis" and doc_count > 0 and ordinary_qa and not requested_analysis_mode and not wants_artifact and not market_context and not auto_analyze:
        intent = "corpus_qa"
        raw["needs_clarification"] = False
        raw["output_mode"] = "chat"
        forced_fast_path = True
    experts = [str(item) for item in (raw.get("experts") or []) if isinstance(item, str)]
    allowed_agents = {
        "df-corpus-analyst",
        "df-feasibility-analyst",
        "df-market-researcher",
        "df-auditor",
        "df-producer",
    }
    experts = [agent for agent in experts if agent in allowed_agents]
    if data_only or not allow_market_tools:
        experts = [agent for agent in experts if agent != "df-market-researcher"]
    if intent in {"followup_edit", "smalltalk_or_meta", "clarify_needed"}:
        experts = []
    elif intent == "corpus_qa":
        experts = ["df-corpus-analyst"]
    elif intent == "feasibility_analysis":
        if "df-corpus-analyst" not in experts:
            experts.insert(0, "df-corpus-analyst")
        if "df-feasibility-analyst" not in experts:
            experts.append("df-feasibility-analyst")
        if market_context and allow_market_tools and "df-market-researcher" not in experts:
            if "df-auditor" in experts:
                experts.insert(experts.index("df-auditor"), "df-market-researcher")
            else:
                experts.append("df-market-researcher")
        if "df-auditor" not in experts:
            experts.append("df-auditor")
    output_mode = str(raw.get("output_mode") or ("report" if intent == "feasibility_analysis" else "chat"))
    if requested_mode in {"chat", "report", "full_package"}:
        output_mode = requested_mode
    elif requested_mode == "proposal":
        output_mode = "full_package"
    elif wants_artifact:
        output_mode = "full_package"
    if output_mode not in {"chat", "report", "full_package"}:
        output_mode = "report" if intent == "feasibility_analysis" else "chat"
    if intent in {"corpus_qa", "followup_edit", "smalltalk_or_meta", "clarify_needed"}:
        output_mode = "chat"
    if output_mode != "full_package":
        experts = [agent for agent in experts if agent != "df-producer"]
    if output_mode == "full_package" and "df-producer" not in experts and intent == "feasibility_analysis":
        experts.append("df-producer")
    return RoutingDecision(
        workspace_id=req.workspace_id,
        intent=intent,
        experts=experts,
        output_mode=output_mode,  # type: ignore[arg-type]
        needs_clarification=bool(raw.get("needs_clarification")) or intent == "clarify_needed",
        clarifying_question=str(raw.get("clarifying_question") or "").strip() or None,
        reason=(
            (_clean_text(raw.get("reason"), 800) + "；用户是在请求可基于当前资料回答的行动方案，已稳定路由到 grounded answer。")
            if forced_grounded_answer
            else (_clean_text(raw.get("reason"), 800) + "；普通问答/追问已走快速路径，跳过市场联网和完整多 Agent 链。")
            if forced_fast_path
            else (_clean_text(raw.get("reason"), 900) or "Coordinator routed the request by intent.")
        ),
    )


def _apply_requested_output_mode(req: ChatRequest, decision: RoutingDecision) -> None:
    requested_mode = str(getattr(req, "artifact_mode", "") or "").strip()
    if requested_mode == "proposal":
        requested_mode = "full_package"
    if requested_mode not in {"chat", "report", "full_package"}:
        return
    decision.output_mode = requested_mode  # type: ignore[assignment]
    if decision.intent == "feasibility_analysis" and requested_mode == "full_package" and "df-producer" not in decision.experts:
        decision.experts.append("df-producer")


def _is_auto_analyze_request(req: ChatRequest) -> bool:
    ui_context = getattr(req, "ui_context", None)
    if not isinstance(ui_context, dict):
        return False
    return bool(ui_context.get("auto_analyze")) or str(ui_context.get("entrypoint") or "") == "workspace_dashboard"


def _suppress_auto_analyze_producer(req: ChatRequest, decision: RoutingDecision) -> bool:
    if not _is_auto_analyze_request(req):
        return False
    before = list(decision.experts)
    decision.experts = [agent for agent in decision.experts if agent != "df-producer"]
    if decision.output_mode == "full_package":
        decision.output_mode = "report"  # type: ignore[assignment]
    return before != decision.experts


def _clarify_guidance(req: ChatRequest, decision: RoutingDecision, conversation_id: str) -> dict[str, Any]:
    context = workspace_context(req.workspace_id)
    payload = {
        "conversation_id": conversation_id,
        "workspace_context": context,
        "user_message": req.message,
        "routing_reason": decision.reason,
        "style_nonce": uuid.uuid4().hex[:8],
        "requirements": [
            "中文输出",
            "只输出一句短问题",
            "点明缺少目标、范围或约束",
            "选项里承载下一步方向",
            "避免固定模板和逐字复用",
        ],
    }
    try:
        result = run_coordinator_guidance(payload)
        field_labels = _workspace_field_labels(req.workspace_id)
        question = sanitize_customer_text(_clean_text(result.get("question"), 1200), field_labels)
        options = [
            {"id": item["id"], "label": sanitize_customer_text(item["label"], field_labels)}
            for item in normalize_clarify_options(result.get("options"), context, req.message)
        ]
        if question:
            return result | {"question": question, "options": options}
    except Exception as exc:
        return {
            "question": _fallback_clarify_question(context),
            "options": clarify_options_from_context(context, req.message),
            "mode": "coordinator_fallback",
            "error": _clean_text(exc, 300),
        }
    return {
        "question": _fallback_clarify_question(context),
        "options": clarify_options_from_context(context, req.message),
        "mode": "coordinator_fallback",
    }


def _structured_clarify(req: ChatRequest, guidance: dict[str, Any]) -> dict[str, Any]:
    context = workspace_context(req.workspace_id)
    field_labels = _workspace_field_labels(req.workspace_id)
    options = [
        {"id": item["id"], "label": sanitize_customer_text(item["label"], field_labels)}
        for item in normalize_clarify_options(guidance.get("options"), context, req.message)
    ]
    question = sanitize_customer_text(
        _clean_text(guidance.get("question"), 1200) or _fallback_clarify_question(context),
        field_labels,
    )
    question = _short_clarify_question(question, options, context)
    return {
        "question": question,
        "options": options,
        "allow_multi": True,
        "allow_freeform": True,
    }


def _fallback_clarify_question(context: dict[str, Any]) -> str:
    name = context.get("name") or context.get("workspace_id") or "当前工作区"
    return f"还缺一个目标：你想基于「{name}」先做资料问答、可行性评估，还是项目方案包？"


def _short_clarify_question(question: str, options: list[dict[str, str]], context: dict[str, Any]) -> str:
    cleaned = re.sub(r"\s+", " ", sanitize_customer_text(question or "")).strip()
    cleaned = re.sub(r"^(你好[，,。 ]*)?(我是\s*DataForge[^。！？!?]*[。！？!?]?)", "", cleaned).strip()
    sentences = [item.strip(" ，。；;") for item in re.split(r"[。！？!?]\s*", cleaned) if item.strip()]
    preferred = next((item for item in sentences if re.search(r"(缺|需要|想|选择|目标|范围|约束|先做|下一步)", item)), "")
    labels = [str(item.get("label") or "").strip() for item in options if str(item.get("label") or "").strip()]
    if not preferred:
        preferred = _fallback_clarify_question(context).rstrip("？")
    if len(preferred) > 90:
        if labels:
            preferred = "还缺一个目标：请选择「" + " / ".join(labels[:3]) + "」，或直接补充你的业务约束"
        else:
            preferred = _fallback_clarify_question(context).rstrip("？")
    preferred = preferred.rstrip("。！？!?；; ")
    return preferred + "？"


def _run_corpus_analyst(req: ChatRequest, top_k: int = 8, use_vector: bool = True) -> dict[str, Any]:
    hits = search(req.workspace_id, req.message, top_k, use_vector=use_vector, prefer_local=not use_vector)
    if not hits and not use_vector:
        hits = _workspace_profile_fallback_hits(req.workspace_id)
    for hit in hits:
        hit["raw_title"] = hit.get("raw_title") or hit.get("title")
        hit["title"] = customer_hit_title(hit)
    evidence = [_evidence_from_hit(hit).model_dump() for hit in hits[:5]]
    title = _infer_title(req.message, hits)
    opportunities: list[dict[str, Any]] = []
    if evidence:
        opportunities.append(
            {
                "id": _slug(title),
                "title": title,
                "description": "A product opportunity inferred from the current workspace evidence.",
                "supporting_evidence": evidence[:3],
            }
        )
    return {
        "profile": {
            "workspace_id": req.workspace_id,
            "assets": sorted({hit.get("title", "Untitled") for hit in hits}),
            "asset_evidence": evidence,
            "gaps_observed": [] if hits else ["No workspace evidence matched the request."],
        },
        "hits": hits,
        "opportunities": opportunities,
    }


def _workspace_profile_fallback_hits(workspace_id: str) -> list[dict[str, Any]]:
    context = workspace_context(workspace_id)
    summary = _clean_text(context.get("profile_summary"), 1200)
    documents = [item for item in (context.get("documents") or []) if isinstance(item, dict)]
    doc_lines = []
    for item in documents[:5]:
        name = _clean_text(item.get("name") or item.get("source_file") or "工作区资料", 80)
        status = _clean_text(item.get("status") or item.get("format") or "", 40)
        if name:
            doc_lines.append(f"{name}: {status}".strip(": "))
    content_parts = []
    if summary:
        content_parts.append(f"数据画像摘要: {summary}")
    if doc_lines:
        content_parts.append("已上传资料: " + "；".join(doc_lines))
    if not content_parts:
        name = _clean_text(context.get("name") or workspace_id, 80)
        doc_count = int(context.get("doc_count") or 0)
        if doc_count <= 0:
            return []
        content_parts.append(f"工作区 {name} 已有 {doc_count} 份资料，可先用于方向性问答，但需要更具体的问题来命中细节证据。")
    return [
        {
            "id": f"{workspace_id}-profile-fast-fallback",
            "workspace_id": workspace_id,
            "title": "工作区数据画像摘要",
            "raw_title": "Workspace profile summary",
            "content": "\n".join(content_parts),
            "source_file": "profile.json",
            "chunk_id": "profile-fast-fallback",
            "document_type": "profile",
            "language": "zh-Hans",
            "sheet": None,
            "row": None,
            "score": 1.0,
            "retrieval_mode": "profile_fallback",
        }
    ]


def _infer_title(message: str, hits: list[dict[str, Any]]) -> str:
    evidence_title = _evidence_topic_from_hits(hits)
    if evidence_title:
        return evidence_title
    titles = [str(hit.get("title") or "").strip() for hit in hits if hit.get("title")]
    if titles:
        first = _human_title_from_opportunity(titles[0])
        return _complete_short_phrase(first)
    words = [word for word in re.findall(r"[A-Za-z0-9]+", message) if len(word) > 2]
    return " ".join(words[:5]).title() or "Workspace Product Opportunity"


def _evidence_catalog(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for evidence in artifact.get("corpus", {}).get("profile", {}).get("asset_evidence", []):
        ref = str(evidence.get("ref") or "")
        if ref and ref not in seen:
            seen.add(ref)
            items.append(
                {
                    "source_type": evidence.get("source_type", "corpus"),
                    "ref": ref,
                    "quote": _clean_text(evidence.get("quote"), 420),
                }
            )
    for hit in artifact.get("corpus", {}).get("hits", []):
        evidence = _evidence_from_hit(hit).model_dump()
        ref = str(evidence.get("ref") or "")
        if ref and ref not in seen:
            seen.add(ref)
            items.append(
                {
                    "source_type": "corpus",
                    "ref": ref,
                    "id": hit.get("id"),
                    "quote": _clean_text(evidence.get("quote"), 420),
                    "metadata": {
                        "title": hit.get("title"),
                        "source_file": hit.get("source_file"),
                        "sheet": hit.get("sheet"),
                        "row": hit.get("row"),
                    },
                }
            )
    return items


def _normalize_ref(value: Any) -> str:
    text = str(value or "").replace("\\", "/").strip().lower()
    text = re.sub(r"/+", "/", text)
    text = re.sub(r"(^|#)(?:raw_docs|external)/", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip("# ")


def _ref_tail(ref: str) -> str:
    return _normalize_ref(ref).split("#")[-1]


def _ref_source(ref: str) -> str:
    return _normalize_ref(ref).split("#")[0]


def _ref_tokens(ref: str) -> set[str]:
    normalized = _normalize_ref(ref)
    parts = [part for part in normalized.split("#") if part]
    tokens = set(parts[1:])
    tail = parts[-1] if parts else ""
    if tail:
        tokens.add(tail)
        tokens.update(re.findall(r"[a-z0-9_-]+-row-\d+", tail))
        tokens.update(re.findall(r"row-\d+", tail))
    return {token for token in tokens if len(token) > 1}


def _refs_match(candidate_ref: str, catalog_item: dict[str, Any]) -> bool:
    allowed_ref = str(catalog_item.get("ref") or "")
    candidate = _normalize_ref(candidate_ref)
    allowed = _normalize_ref(allowed_ref)
    if not candidate or not allowed:
        return False
    if candidate == allowed:
        return True
    catalog_id = _normalize_ref(catalog_item.get("id"))
    if catalog_id and candidate == catalog_id:
        return True

    candidate_source = _ref_source(candidate)
    allowed_source = _ref_source(allowed)
    source_matches = candidate_source == allowed_source or Path(candidate_source).name == Path(allowed_source).name
    if not source_matches:
        return False

    candidate_tail = _ref_tail(candidate)
    allowed_tail = _ref_tail(allowed)
    if candidate_tail and allowed_tail and candidate_tail == allowed_tail:
        return True

    candidate_tokens = _ref_tokens(candidate)
    allowed_tokens = _ref_tokens(allowed)
    if candidate_tokens & allowed_tokens:
        return True

    metadata = catalog_item.get("metadata") or {}
    sheet = _normalize_ref(metadata.get("sheet"))
    row = _normalize_ref(metadata.get("row"))
    if sheet and row and f"#{sheet}#{row}#" in f"#{candidate}#":
        return True
    return False


def _quote_matches(candidate_quote: str | None, catalog_item: dict[str, Any]) -> bool:
    quote = _clean_text(candidate_quote)
    allowed = _clean_text(catalog_item.get("quote"))
    return bool(quote and allowed and (quote in allowed or allowed in quote))


def _verify_evidence(report: FeasibilityReport, catalog: list[dict[str, Any]]) -> tuple[FeasibilityReport, list[str]]:
    warnings: list[str] = []
    cleaned_dimensions = []
    for dimension in report.dimensions:
        cleaned_evidence = []
        for evidence in dimension.evidence:
            matched = next(
                (
                    item
                    for item in catalog
                    if _refs_match(evidence.ref, item) or _quote_matches(evidence.quote, item)
                ),
                None,
            )
            if not matched:
                warnings.append(f"{dimension.name}:{evidence.ref}")
                continue
            cleaned_evidence.append(evidence)
        if cleaned_evidence:
            data = dimension.model_dump()
            data["evidence"] = [item.model_dump() for item in cleaned_evidence]
            cleaned_dimensions.append(data)
        else:
            warnings.append(f"{dimension.name}:dimension_dropped_no_verified_evidence")
    data = report.model_dump()
    data["dimensions"] = cleaned_dimensions
    return FeasibilityReport.model_validate(data), warnings


def _normalize_feasibility_confidence(report: FeasibilityReport) -> FeasibilityReport:
    rank = {"speculative": 0, "market_inferred": 1, "data_confirmed": 2}
    labels = {value: key for key, value in rank.items()}
    data = report.model_dump()
    normalized_dimensions = []
    confidences: list[str] = []
    for dimension in data.get("dimensions", []):
        evidence = dimension.get("evidence") or []
        source_types = {str(item.get("source_type") or "") for item in evidence}
        if not evidence:
            confidence = "speculative"
        elif "market" in source_types:
            confidence = "market_inferred"
        elif source_types & {"corpus", "computed"}:
            confidence = "data_confirmed"
        else:
            confidence = "speculative"
        model_confidence = str(dimension.get("confidence") or confidence)
        confidence = labels[min(rank.get(model_confidence, 0), rank.get(confidence, 0))]
        dimension["confidence"] = confidence
        confidences.append(confidence)
        normalized_dimensions.append(dimension)
    data["dimensions"] = normalized_dimensions
    if not confidences:
        data["overall_confidence"] = "speculative"
    else:
        model_overall = str(data.get("overall_confidence") or "speculative")
        weakest_dimension = labels[min(rank.get(item, 0) for item in confidences)]
        data["overall_confidence"] = labels[min(rank.get(model_overall, 0), rank.get(weakest_dimension, 0))]
    return FeasibilityReport.model_validate(data)


def _fallback_feasibility(req: ChatRequest, artifact: dict[str, Any], catalog: list[dict[str, Any]], reason: str) -> dict[str, Any]:
    first = catalog[0] if catalog else {}
    evidence = []
    if first:
        evidence.append(
            Evidence(
                source_type=first.get("source_type", "corpus"),
                ref=str(first.get("ref") or "unknown"),
                quote=_clean_text(first.get("quote"), 300) or None,
            )
        )
    report = FeasibilityReport(
        opportunity_id=str(
            (artifact.get("corpus", {}).get("opportunities") or [{}])[0].get("id")
            or "fallback-evidence-review"
        ),
        dimensions=[
            {
                "name": "asset_data",
                "score": 1,
                "rationale": "模型结构化分析未能稳定完成，系统已保留检索证据并给出保守降级结论，避免因单次证据引用或模型输出问题中断整条运行。",
                "evidence": [item.model_dump() for item in evidence],
                "confidence": "speculative",
            }
        ]
        if evidence
        else [],
        verdict="_".join(["not", "yet", "feasible"]),
        overall_confidence="speculative",
        gap_list=[
            "模型结构化可行性分析暂不可用；当前结果为基于已检索证据的保守降级结论。",
            "请复核证据引用与模型输出格式后再提升结论强度。",
        ],
    )
    report = _normalize_feasibility_opportunity(report, req, artifact)
    data = apply_pre_audit_guardrails(report.model_dump(), catalog, req.message)
    data["_llm"] = {
        "mode": "fallback_after_agent_error",
        "response_id": None,
        "usage": {},
        "error": _clean_text(reason, 500),
    }
    return data


def _model_meta(result: dict[str, Any]) -> dict[str, Any]:
    meta = {
        "response_id": result.get("response_id"),
        "usage": result.get("usage") or {},
    }
    if result.get("mode"):
        meta["mode"] = result["mode"]
    if result.get("tool_calls") is not None:
        meta["tool_calls"] = result["tool_calls"]
    return meta


def _clean_sentence_end(text: str) -> str:
    return str(text or "").rstrip("\u3002. ")


_RAW_ACTION_PLAN_PATTERN = re.compile(
    r"(raw_docs|source_file|chunk_id|profile\.json|schema|字段|业务类别|资料分组|本工作区为演示用|合成数据|"
    r"\bL\d{2}\b|row-\d+|#[^ \n，。；、]{4,})",
    re.I,
)


def _action_plan_needs_rewrite(items: list[Any]) -> bool:
    clean_items = [str(item or "").strip() for item in items if str(item or "").strip()]
    if not clean_items:
        return True
    joined = "\n".join(clean_items)
    if _RAW_ACTION_PLAN_PATTERN.search(joined):
        return True
    if re.search(r"先用.{18,}(确定|设定|转成|做分层|做对照)", joined):
        return True
    if re.search(r"把.{18,}(转成|作为|用于)", joined):
        return True
    return any(len(item) > 180 for item in clean_items)


def _citation_marker(citations: list[dict[str, Any]], index: int) -> str:
    if index < len(citations):
        marker = citations[index].get("marker")
        if marker:
            return f"[{marker}]"
    return ""


def _citation_marker_group(citations: list[dict[str, Any]], start: int, limit: int = 2) -> str:
    markers = []
    for item in citations[start : start + limit]:
        marker = item.get("marker")
        if marker:
            markers.append(f"[{marker}]")
    return " ".join(dict.fromkeys(markers))


def _synthesized_feasibility_steps(
    title: str,
    citations: list[dict[str, Any]],
    feasibility: dict[str, Any],
) -> list[str]:
    first = _citation_marker_group(citations, 0, 2)
    second = _citation_marker_group(citations, 2, 2) or first
    third = _citation_marker_group(citations, 4, 2) or _citation_marker(citations, 0)
    fourth = _citation_marker_group(citations, 6, 2) or _citation_marker(citations, 1)
    steps = [
        f"先把“{title}”压缩成一个小试点：限定一个优先客群、一个触达场景和一组可复盘指标，避免一开始铺太宽。 {first}".rstrip(),
        f"把工作区证据整理成 2-3 个产品假设：分别写清目标用户、核心痛点、触发机制和预期转化，不把单条记录或编号当成结论。 {second}".rstrip(),
        f"设计两周验证节奏：第一周做触达和报名，第二周看参与、到访、转化、复购、成本和用户反馈，低于阈值就停止扩展。 {third}".rstrip(),
        f"如果涉及合作方或市场信息，只把它作为补充假设；先验证用户参与和转化，再讨论赞助、联名或规模化投入。 {fourth}".rstrip(),
    ]
    gaps = [sanitize_customer_text(str(item)) for item in (feasibility.get("gap_list") or []) if str(item).strip()]
    clean_gap = ""
    for gap in gaps:
        if not _RAW_ACTION_PLAN_PATTERN.search(gap):
            clean_gap = _clean_sentence_end(gap)
            break
    if clean_gap:
        steps.append(f"优先补齐影响判断的最大缺口：{clean_gap}。")
    else:
        steps.append("每轮复盘只保留能被数据或客户反馈支持的结论；证据不足的维度要降级为待验证假设。")
    return steps[:5]


def _diversify_feasibility_scores_data(data: dict[str, Any]) -> dict[str, Any]:
    dimensions = [item for item in data.get("dimensions") or [] if isinstance(item, dict)]
    scores = [int(item.get("score") or 0) for item in dimensions]
    if len(scores) < 3 or len(set(scores)) > 1:
        return data
    baseline = {
        "asset_data": 4,
        "technical": 3,
        "market": 3,
        "resource_cost": 2,
        "differentiation_risk": 2,
    }
    for dimension in dimensions:
        name = str(dimension.get("name") or "")
        evidence = [item for item in dimension.get("evidence") or [] if isinstance(item, dict)]
        confidence = str(dimension.get("confidence") or "speculative")
        source_types = {str(item.get("source_type") or "") for item in evidence}
        score = baseline.get(name, 3)
        if len(evidence) >= 2 and source_types & {"corpus", "computed"}:
            score += 1
        if confidence == "market_inferred":
            score = min(score, 3)
        if confidence == "speculative" or not evidence:
            score = min(score, 2)
        dimension["score"] = max(1, min(5, score))
        rationale = str(dimension.get("rationale") or "").strip()
        if rationale and "同分" not in rationale and "证据强弱" not in rationale:
            dimension["rationale"] = f"{rationale}（该分数已按证据强弱做保守区分。）"
    data["dimensions"] = dimensions
    return data


def _diversify_feasibility_scores(report: FeasibilityReport) -> FeasibilityReport:
    return FeasibilityReport.model_validate(_diversify_feasibility_scores_data(report.model_dump()))


_FEASIBILITY_PROMPT_VERSION = "df-feasibility-analyst:batch11-p0-fix-action-first"


def _corpus_fingerprint(artifact: dict[str, Any]) -> tuple[str, str]:
    hits = artifact.get("corpus", {}).get("hits", [])
    retrieval_modes = sorted({str(hit.get("retrieval_mode") or "unknown") for hit in hits}) or ["none"]
    parts = []
    for hit in hits[:12]:
        parts.append(
            {
                "id": hit.get("id"),
                "source_file": hit.get("source_file"),
                "chunk_id": hit.get("chunk_id"),
                "retrieval_mode": hit.get("retrieval_mode"),
                "content": _clean_text(hit.get("content"), 1600),
            }
        )
    raw = json.dumps(parts, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24], "+".join(retrieval_modes)


def _feasibility_cache_key(req: ChatRequest, artifact: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    fingerprint, retrieval_mode = _corpus_fingerprint(artifact)
    active_rubric_version = rubric_version()
    query_hash = hashlib.sha256(req.message.encode("utf-8")).hexdigest()[:16]
    ui_context = getattr(req, "ui_context", {}) or {}
    cache_bust = str(ui_context.get("cache_bust") or "").strip() if isinstance(ui_context, dict) else ""
    cache_bust_hash = hashlib.sha256(cache_bust.encode("utf-8")).hexdigest()[:10] if cache_bust else ""
    key = (
        "dataforge:analysis:v1"
        f":workspace={req.workspace_id}"
        f":fingerprint={fingerprint}"
        f":prompt={_FEASIBILITY_PROMPT_VERSION}"
        f":rubric={active_rubric_version}"
        f":retrieval={retrieval_mode}"
        f":query={query_hash}"
    )
    if cache_bust_hash:
        key = f"{key}:run={cache_bust_hash}"
    return key, {
        "workspace_id": req.workspace_id,
        "chunk_fingerprint": fingerprint,
        "prompt_version": _FEASIBILITY_PROMPT_VERSION,
        "rubric_version": active_rubric_version,
        "retrieval_mode": retrieval_mode,
        "query_hash": query_hash,
        "cache_bust_hash": cache_bust_hash or None,
        "key_sample": key,
    }


def _run_feasibility_analyst(
    req: ChatRequest,
    artifact: dict[str, Any],
    audit_feedback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    catalog = _evidence_catalog(artifact)
    if not catalog:
        report = FeasibilityReport(
            opportunity_id="insufficient-evidence",
            dimensions=[],
            verdict="_".join(["not", "yet", "feasible"]),
            overall_confidence="speculative",
            gap_list=["\u5de5\u4f5c\u533a\u4e2d\u672a\u68c0\u7d22\u5230\u4e0e\u8be5\u8bf7\u6c42\u76f8\u5173\u7684\u8bc1\u636e\u3002"],
        )
        report = _normalize_feasibility_opportunity(report, req, artifact)
        data = apply_pre_audit_guardrails(report.model_dump(), catalog, req.message)
        data["_llm"] = {"mode": "empty_evidence_deterministic", "response_id": None, "usage": {}}
        return data
    cache_key, cache_meta = _feasibility_cache_key(req, artifact)
    if not audit_feedback and os.environ.get("DF_DISABLE_REDIS_CACHE") != "1":
        cached, get_meta = cache_store.get_json(cache_key)
        if cached:
            cached["_llm"] = {
                **(cached.get("_llm") or {}),
                "mode": "redis_cached_feasibility",
                "cache": get_meta | cache_meta,
                "response_id": None,
                "usage": {},
            }
            return cached
        artifact["_feasibility_cache"] = {"get": get_meta | cache_meta}
    payload = {
        "workspace_id": req.workspace_id,
        "user_request": req.message,
        "candidate_opportunities": artifact.get("corpus", {}).get("opportunities", []),
        "evidence_catalog": catalog,
        "audit_feedback": audit_feedback,
        "rubric": load_rubric(),
        "rubric_version": rubric_version(),
    }
    try:
        result = run_agent(
            "df-feasibility-analyst",
            json.dumps(payload, ensure_ascii=False, indent=2),
            response_schema=FeasibilityReport.model_json_schema(),
            max_output_tokens=2800,
        )
        report = FeasibilityReport.model_validate(result["structured"])
        report, evidence_warnings = _verify_evidence(report, catalog)
        report = _normalize_feasibility_confidence(report)
        report = _normalize_feasibility_opportunity(report, req, artifact)
        report = _diversify_feasibility_scores(report)
        data = apply_pre_audit_guardrails(report.model_dump(), catalog, req.message)
        data["_llm"] = _model_meta(result)
        data["_llm"]["evidence_warnings"] = evidence_warnings
        if not audit_feedback and os.environ.get("DF_DISABLE_REDIS_CACHE") != "1":
            set_meta = cache_store.set_json(cache_key, {key: value for key, value in data.items() if key != "_llm"})
            data["_llm"]["cache"] = (artifact.get("_feasibility_cache") or {}).get("get", {}) | {"set": set_meta, **cache_meta}
        return data
    except Exception as exc:
        return _fallback_feasibility(req, artifact, catalog, str(exc))


def _market_lookup(category: str, keywords: list[str]) -> dict[str, Any]:
    if not _mcp_tool_allowed("market_lookup"):
        raise PermissionError("MCP tool market_lookup is not allow-listed")
    base = os.environ.get("MCP_MARKET_URL", "https://ca-dataforge-mcp.thankfultree-c0fc8321.eastus2.azurecontainerapps.io")
    url = base.rstrip("/")
    if url.endswith("/mcp"):
        url = url.removesuffix("/mcp")
    payload = json.dumps({"category": category, "keywords": keywords}).encode("utf-8")
    req = urllib.request.Request(f"{url}/tools/market_lookup", data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _mcp_tool_allowed(tool_name: str) -> bool:
    spec = MCP_TOOL_ALLOWLIST.get(tool_name)
    return bool(spec and spec.get("implemented") and spec.get("require_approval") != "always")


def _tool_provenance(
    tool_name: str,
    input_summary: str,
    *,
    source_type: str | None = None,
    confidence: str | None = None,
    citations: list[Any] | None = None,
    sources: list[Any] | None = None,
    latency_ms: int | None = None,
    error: str | None = None,
    fallback: str | None = None,
) -> dict[str, Any]:
    spec = MCP_TOOL_ALLOWLIST.get(tool_name, {})
    return {
        "tool_name": tool_name,
        "input_summary": _clean_text(input_summary, 260),
        "source_type": source_type or spec.get("source_type") or "tool",
        "confidence": confidence or spec.get("confidence") or "speculative",
        "citations": citations or [],
        "sources": sources or [],
        "latency_ms": latency_ms,
        "error": _clean_text(error, 300) if error else None,
        "fallback": _clean_text(fallback, 220) if fallback else None,
        "allowed": bool(spec),
        "require_approval": spec.get("require_approval", "unknown"),
        "risk": spec.get("risk", "unknown"),
    }


def _run_market_researcher(artifact: dict[str, Any]) -> dict[str, Any]:
    feasibility = artifact.get("feasibility", {})
    corpus_opportunity = ((artifact.get("corpus", {}).get("opportunities") or [{}])[0] or {})
    opportunity = str(
        feasibility.get("opportunity_id")
        or corpus_opportunity.get("id")
        or corpus_opportunity.get("title")
        or "workspace-product"
    )
    category = opportunity.replace("-", " ")
    keywords = [word for word in category.split() if len(word) > 2][:4] or ["analytics", "workflow"]
    cache_key = f"{artifact.get('workspace_id')}|{opportunity}|{' '.join(keywords)}"
    cached = _MARKET_CACHE.get(cache_key)
    now = time.monotonic()
    if cached and now < cached[0]:
        data = json.loads(json.dumps(cached[1], ensure_ascii=False))
        data.setdefault("_llm", {})["cache"] = "memory"
        return data
    competitors: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    tool_provenance: dict[str, dict[str, Any]] = {}

    def lookup_competitors() -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
        started = time.perf_counter()
        mcp_payload = {
            "opportunity_id": opportunity,
            "category": category,
            "keywords": keywords,
            "limit": 5,
            "evidence_catalog": _evidence_catalog(artifact)[:4],
        }
        meta: dict[str, Any] = {}
        try:
            data = run_market_mcp_research(mcp_payload)
            raw_competitors = data.get("competitors") or data.get("results") or []
            items = [_market_inferred_item(item) for item in raw_competitors[:5] if isinstance(item, dict)]
            sources = [item for item in (data.get("sources") or []) if isinstance(item, (str, dict))]
            if not sources:
                sources = [item.get("url") for item in items if item.get("url")]
            meta = data.get("_llm", {}) if isinstance(data.get("_llm"), dict) else {}
            source_label = "Foundry Agent Service MCP market_lookup"
            fallback = None
        except Exception as exc:
            fallback_data = _market_lookup(category, keywords)
            raw_competitors = fallback_data.get("competitors") or fallback_data.get("results") or (fallback_data if isinstance(fallback_data, list) else [])
            items = [_market_inferred_item(item) for item in raw_competitors[:5] if isinstance(item, dict)]
            sources = [item for item in (fallback_data.get("sources") or []) if isinstance(item, (str, dict))]
            if not sources:
                sources = [item.get("url") for item in items if item.get("url")]
            meta = {"mode": "local_http_fallback", "error": _clean_text(exc, 300)}
            source_label = "Local HTTP fallback after Foundry Agent MCP failure"
            fallback = "Foundry Agent MCP market_lookup failed; used local HTTP fallback to avoid dropping market context."
        provenance = _tool_provenance(
            "market_lookup",
            f"{category}; keywords={', '.join(keywords)}",
            sources=sources[:8],
            citations=sources[:8],
            latency_ms=int((time.perf_counter() - started) * 1000),
            fallback=fallback,
        )
        provenance["protocol"] = "foundry_agent_mcp" if meta.get("mode") == "foundry_agent_mcp" else "local_http_fallback"
        provenance["source_label"] = source_label
        provenance["agent_response_id"] = meta.get("response_id")
        provenance["tool_calls"] = meta.get("tool_calls", [])
        return items, provenance, meta

    web_payload = {
        "opportunity_id": opportunity,
        "category": category,
        "keywords": keywords,
        "research_goal": "Search comparable products, competitors, pricing/packaging, campaign mechanics, and differentiation points for this opportunity.",
        "required_comparison_fields": ["competitor_or_alternative", "pricing_or_playbook", "what_they_do", "our_differentiation"],
        "feasibility": {key: value for key, value in feasibility.items() if key != "_llm"},
        "evidence_catalog": _evidence_catalog(artifact)[:6],
    }
    with concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="dataforge-market") as pool:
        competitor_future = pool.submit(lookup_competitors)
        web_future = pool.submit(run_market_web_research, web_payload)
        mcp_llm: dict[str, Any] = {}
        try:
            competitors, tool_provenance["market_lookup"], mcp_llm = competitor_future.result()
        except Exception as exc:
            errors["market_lookup"] = _clean_text(exc, 300)
            tool_provenance["market_lookup"] = _tool_provenance(
                "market_lookup",
                f"{category}; keywords={', '.join(keywords)}",
                error=str(exc),
                fallback="MCP market lookup failed; report continues with workspace evidence and any available Foundry web sources.",
            )
        try:
            web_result = web_future.result()
        except Exception as exc:
            web_result = {
                "external_findings": [],
                "sources": [],
                "positioning_note": "",
                "_llm": {"mode": "web_search_unavailable", "response_id": None, "usage": {}, "error": _clean_text(exc, 300)},
            }
    web_llm = web_result.get("_llm", {}) if isinstance(web_result, dict) else {}
    tool_provenance["foundry_native_web_search"] = _tool_provenance(
        "foundry_native_web_search",
        f"{category}; keywords={', '.join(keywords)}",
        source_type="foundry_web",
        confidence="market_inferred",
        sources=web_result.get("sources", []) if isinstance(web_result, dict) else [],
        citations=web_result.get("sources", []) if isinstance(web_result, dict) else [],
        latency_ms=web_llm.get("latency_ms") if isinstance(web_llm, dict) else None,
        error=web_llm.get("error") if isinstance(web_llm, dict) else None,
        fallback="Foundry web search unavailable; market claims must remain limited." if isinstance(web_llm, dict) and web_llm.get("error") else None,
    )
    result = {
        "opportunity_id": opportunity,
        "competitors": competitors,
        "external_findings": web_result.get("external_findings", []),
        "sources": web_result.get("sources", []),
        "positioning_note": web_result.get("positioning_note")
        or "Compare the workspace-backed concept against adjacent commercial offerings before packaging.",
        "confidence": "market_inferred",
        "_llm": web_result.get("_llm", {}),
        "errors": errors,
        "tool_provenance": tool_provenance,
    }
    result["_llm"]["mcp"] = mcp_llm
    _MARKET_CACHE[cache_key] = (now + _MARKET_CACHE_SECONDS, json.loads(json.dumps(result, ensure_ascii=False)))
    return result


def _market_inferred_item(item: dict[str, Any]) -> dict[str, Any]:
    data = dict(item)
    data.setdefault("confidence", "market_inferred")
    data.setdefault("source_type", "market_mcp")
    return data


def _proposal_payload(artifact: dict[str, Any]) -> dict[str, Any]:
    feasibility = artifact.get("feasibility", {})
    corpus = artifact.get("corpus", {})
    market = artifact.get("market", {})
    opportunity_id = str(feasibility.get("opportunity_id") or "workspace-product")
    title = opportunity_id.replace("-", " ").title()
    narrative = (
        artifact.get("answer", {}).get("text")
        or artifact.get("narrative")
        or artifact.get("executive_summary")
        or _final_text(
            RoutingDecision(
                workspace_id=artifact.get("workspace_id", "demo-corpus"),
                intent="product_feasibility",
                experts=[],
                output_mode="report",
                needs_clarification=False,
                reason="producer payload",
            ),
            artifact,
        )
    )
    # 为正式 PDF 生成干净、条目式的执行摘要（去对话腔/markdown）；失败回退清洗版叙述
    exec_headline, exec_points = "", []
    try:
        es = run_executive_summary({
            "opportunity": title,
            "verdict": feasibility.get("verdict"),
            "overall_confidence": feasibility.get("overall_confidence"),
            "recommendation": feasibility.get("recommendation"),
            "action_plan": [str(s) for s in (feasibility.get("action_plan") or [])[:5]],
            "gap_list": [str(g) for g in (feasibility.get("gap_list") or [])[:4]],
            "dimensions": [{"name": d.get("name"), "score": d.get("score")} for d in (feasibility.get("dimensions") or [])[:5] if isinstance(d, dict)],
            "audience": (corpus.get("profile", {}) or {}).get("customer_summary"),
            "market": (market or {}).get("positioning_note"),
        })
        exec_headline = str(es.get("headline") or "").strip()
        exec_points = [str(p).strip() for p in (es.get("points") or []) if str(p).strip()]
    except Exception:
        exec_headline, exec_points = "", []
    if exec_headline or exec_points:
        clean_summary = (exec_headline + ("\n" + "\n".join(f"- {p}" for p in exec_points) if exec_points else "")).strip()
    else:
        clean_summary = _clean_exec_summary_fallback(narrative)
    return {
        "opportunity_id": opportunity_id,
        "title": title,
        "executive_summary": clean_summary,
        "executive_headline": exec_headline,
        "executive_points": exec_points,
        "feasibility": {key: value for key, value in feasibility.items() if key != "_llm"},
        "corpus_profile": corpus.get("profile", {}),
        "opportunities": corpus.get("opportunities", []),
        "market": market,
        "audit": artifact.get("audit", {}),
        "workspace_id": artifact.get("workspace_id"),
        "reference_images": artifact.get("reference_images") or [],
    }


def _clean_exec_summary_fallback(text: Any) -> str:
    """LLM 摘要失败时的兜底：去掉对话腔开场 + markdown 符号。"""
    cleaned = str(text or "")
    cleaned = re.sub(r"[#*`_]+", "", cleaned)
    cleaned = re.sub(r"(?m)^\s*(先给你[^。\n]*。|下面给你[^。\n]*。|我先[^。\n]*。|这里给你[^。\n]*。)", "", cleaned)
    cleaned = re.sub(r"(方便你[^。\n]*扫一眼[^。\n]*。|方便你整体[^。\n]*。)", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()[:1600]


_PRODUCE_KINDS = ("pdf", "concept_image", "audio")


def _run_producer(artifact: dict[str, Any], kinds: list[str] | None = None) -> dict[str, Any]:
    # 默认只生成项目文档(PDF)+概念图，语音摘要按需（非必要产物）
    wanted = [k for k in (kinds or ["pdf", "concept_image"]) if k in _PRODUCE_KINDS]
    if not wanted:
        wanted = ["pdf", "concept_image"]
    if not artifact.get("reference_images"):
        artifact["reference_images"] = workspace_reference_images(str(artifact.get("workspace_id") or ""))
    proposal = _proposal_payload(artifact)
    result: dict[str, Any] = {
        "opportunity_id": proposal["opportunity_id"],
        "proposal": proposal,
        "kinds": wanted,
        "artifact_urls": {},
    }
    with concurrent.futures.ThreadPoolExecutor(max_workers=3, thread_name_prefix="dataforge-producer") as pool:
        pdf_future = pool.submit(render_pdf_report, proposal, "project_proposal") if "pdf" in wanted else None
        image_future = None
        if "concept_image" in wanted:
            image_prompt = _image_prompt_from_proposal(proposal)
            result["image_kind"] = _proposal_image_kind(proposal)[0]
            result["image_prompt"] = image_prompt
            image_future = pool.submit(
                generate_image,
                image_prompt,
                "1024x1024",
                _reference_image_urls(proposal.get("reference_images") or []),
                str(proposal.get("title") or proposal.get("opportunity_id") or "").strip(),
                _logo_reference_url(proposal.get("reference_images") or []),
            )
        audio_future = pool.submit(narrate_summary, _concise_narration_from_proposal(proposal), "zh-CN-XiaoxiaoNeural") if "audio" in wanted else None
        if pdf_future:
            pdf = pdf_future.result()
            result["pdf"] = pdf
            result["artifact_urls"]["pdf"] = pdf.get("artifact_url")
        if image_future:
            image = image_future.result()
            result["concept_image"] = image
            result["artifact_urls"]["concept_image"] = image.get("artifact_url")
        if audio_future:
            audio = audio_future.result()
            result["audio_summary"] = audio
            result["artifact_urls"]["audio_summary"] = audio.get("artifact_url")
    return result


def _logo_reference_url(reference_images: list[dict[str, Any]]) -> str | None:
    for item in reference_images:
        if isinstance(item, dict) and str(item.get("role") or "") == "logo":
            value = item.get("blob_url") or item.get("url")
            if value:
                return str(value)
    return None


def _reference_image_urls(reference_images: list[dict[str, Any]]) -> list[str]:
    ordered = sorted(
        [item for item in reference_images if isinstance(item, dict)],
        key=lambda item: {"logo": 0, "activity": 1, "reference": 2}.get(str(item.get("role") or "reference"), 3),
    )
    urls: list[str] = []
    for item in ordered[:3]:
        value = item.get("blob_url") or item.get("url")
        if value:
            urls.append(str(value))
    return urls


def _proposal_image_kind(proposal: dict[str, Any]) -> tuple[str, str, str]:
    feasibility = proposal.get("feasibility") or {}
    market = proposal.get("market") or {}
    dimensions = feasibility.get("dimensions") or []
    text = " ".join(
        str(part or "")
        for part in (
            proposal.get("title"),
            proposal.get("opportunity_id"),
            proposal.get("executive_summary"),
            feasibility.get("opportunity_id"),
            feasibility.get("recommendation"),
            " ".join(str(item) for item in feasibility.get("action_plan") or []),
            " ".join(str(item.get("rationale") or "") for item in dimensions if isinstance(item, dict)),
            market.get("positioning_note"),
        )
    ).lower()
    if re.search(r"(活动|运营|赛事|联赛|比赛|挑战|打卡|拉新|推广|宣传|海报|campaign|event|competition|poster|festival)", text, re.I):
        return (
            "event_poster",
            "活动海报 / campaign key visual",
            "Create a campaign key visual: one bold hero scene and branded atmosphere conveying the campaign energy through imagery only.",
        )
    if re.search(r"(报告|分析|画像|看板|仪表盘|dashboard|analytics|report|insight|bi|scorecard|board)", text, re.I):
        return (
            "analytics_board",
            "分析看板 / dashboard concept",
            "Create a clean analytics/product board concept: abstract evidence, trend and score modules and decision panels rendered as shapes and iconography (not real text).",
        )
    return (
        "product_concept",
        "产品概念设计 / product UI or physical mockup",
        "Create a product concept design, app UI mockup, service screen, packaging, or physical prototype that makes the proposed product tangible.",
    )


def generate_playbook_detail(payload: dict[str, Any]) -> dict[str, Any]:
    """为行动计划某个 PM 方法生成与数据挂钩的内容；失败回退（前端用静态框架兜底）。"""
    method = str(payload.get("method") or "").strip()
    method_name = str(payload.get("method_name") or method or "PM 方法").strip()
    feasibility = payload.get("feasibility") or {}
    llm_payload = {
        "method_id": method,
        "method_name": method_name,
        "framework": payload.get("framework") or {},
        "opportunity": payload.get("opportunity") or feasibility.get("opportunity_id"),
        "verdict": feasibility.get("verdict"),
        "recommendation": feasibility.get("recommendation"),
        "action_plan": [str(s) for s in (feasibility.get("action_plan") or [])[:5]],
        "gap_list": [str(g) for g in (feasibility.get("gap_list") or [])[:4]],
        "dimensions": [{"name": d.get("name"), "score": d.get("score"), "rationale": str(d.get("rationale") or "")[:120]} for d in (feasibility.get("dimensions") or [])[:5] if isinstance(d, dict)],
        "audience": payload.get("audience"),
    }
    try:
        result = run_playbook_detail(llm_payload)
        if result.get("summary") or result.get("points"):
            return {"method": method, **result, "mode": "llm"}
    except Exception as exc:
        return {"method": method, "summary": "", "points": [], "goal": "", "mode": "error", "error": str(exc)[:200]}
    return {"method": method, "summary": "", "points": [], "goal": "", "mode": "empty"}


def produce_from_existing_report(payload: dict[str, Any]) -> dict[str, Any]:
    kinds = payload.get("kinds") or ["pdf", "concept_image"]
    artifact = {
        "workspace_id": payload.get("workspace_id") or "demo-corpus",
        "conversation_id": payload.get("conversation_id"),
        "feasibility": payload.get("feasibility") or {},
        "corpus": payload.get("corpus") or {},
        "market": payload.get("market") or {},
        "audit": payload.get("audit") or {},
        "answer": payload.get("answer") or {},
        "reference_images": payload.get("reference_images") or [],
        "narrative": payload.get("narrative") or payload.get("text"),
    }
    return _run_producer(artifact, kinds)


def _existing_proposal(payload: dict[str, Any]) -> dict[str, Any] | None:
    direct = payload.get("proposal")
    if _complete_proposal(direct):
        return {**direct, "reused": True}
    run_id = payload.get("conversation_id") or payload.get("run_id")
    if not run_id:
        return None
    try:
        run = get_run(str(run_id))
    except FileNotFoundError:
        return None
    for source in (run.get("artifact"), (run.get("final") or {}).get("artifact")):
        proposal = (source or {}).get("proposal") if isinstance(source, dict) else None
        if _complete_proposal(proposal):
            return {**proposal, "reused": True, "reused_from_run": str(run_id)}
    return None


def _complete_proposal(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    urls = value.get("artifact_urls")
    if not isinstance(urls, dict):
        return False
    return all(urls.get(key) for key in ("pdf", "concept_image", "audio_summary"))


def _image_prompt_from_proposal(proposal: dict[str, Any]) -> str:
    feasibility = proposal.get("feasibility") or {}
    image_kind, image_label, image_direction = _proposal_image_kind(proposal)

    # 把"大概的方案内容"提炼成一段供作画的 brief（意译成画面，不直接印字）
    title = _clean_text(proposal.get("title") or proposal.get("opportunity_id"), 80)
    recommendation = _clean_text(feasibility.get("recommendation"), 200)
    steps = [_clean_text(step, 80) for step in (feasibility.get("action_plan") or [])[:3] if str(step).strip()]
    corpus_profile = proposal.get("corpus_profile") or {}
    audience = _clean_text(corpus_profile.get("customer_summary") or corpus_profile.get("profile_summary") or proposal.get("executive_summary"), 180)
    market_note = _clean_text((proposal.get("market") or {}).get("positioning_note"), 140)

    brief_bits: list[str] = []
    if recommendation:
        brief_bits.append(f"核心建议：{recommendation}")
    if steps:
        brief_bits.append("关键动作：" + "；".join(steps))
    if audience:
        brief_bits.append(f"对象与背景：{audience}")
    if market_note:
        brief_bits.append(f"市场定位：{market_note}")
    scene_brief = " ".join(brief_bits)[:900]

    # 让 agent 判断"实际要做的产品是什么"，画产品本身（不是报告/看板）
    subject = ""
    kind = ""
    try:
        decided = run_image_subject({
            "title": title,
            "verdict": feasibility.get("verdict"),
            "recommendation": recommendation,
            "action_plan": steps,
            "audience": audience,
            "market": market_note,
        })
        subject = str(decided.get("subject") or "").strip()
        kind = str(decided.get("kind") or "").strip()
    except Exception:
        subject, kind = "", ""

    kind_dir = {
        "product_app": ("product app UI concept", "Depict the actual product as a clean modern app/software UI mockup on a device."),
        "product_physical": ("physical product / packaging concept", "Depict the tangible physical product, its packaging or a prototype."),
        "service": ("service / in-store experience concept", "Depict the branded service experience or in-store scene that delivers the product."),
        "campaign": ("campaign key visual", "Depict a bold campaign key visual with branded atmosphere."),
    }
    if subject and kind in kind_dir:
        image_label, image_direction = kind_dir[kind]
        image_kind = kind
        focal = f"The product to depict: {subject}."
    else:
        focal = f"Translate this opportunity and plan into a product/concept visual (do NOT print this text): 《{title}》。{scene_brief}"

    return (
        "Design ONE polished product concept key visual for a business proposal cover. "
        f"Deliverable type: {image_label} ({image_kind}). {image_direction} "
        "Show the actual PRODUCT / deliverable — NOT a report, dashboard, analytics chart, slide, meeting room or office scene. "
        f"{focal} Context (do NOT print as text): {scene_brief} "
        "Composition (important): one clear focal subject in the upper two-thirds; "
        "keep the BOTTOM ~28% as calm low-detail negative space or a soft gradient so a title caption can be overlaid later; "
        "keep the TOP-LEFT corner relatively clean for a small logo. "
        "Text: do NOT render paragraphs, headlines, or any Chinese characters; at most one or two very short English label marks. "
        "Style: modern, premium, uncluttered, a confident blue accent. "
        "Avoid: conference tables, office review scenes, people around screens, handshakes, stock-photo clichés, dense fake UI microtext."
    )


def _narration_from_proposal(proposal: dict[str, Any]) -> str:
    summary = _clean_text(proposal.get("executive_summary"), 1600)
    feasibility = proposal.get("feasibility") or {}
    if not summary:
        summary = (
            f"DataForge 已完成项目建议书。结论是 {verdict_label(feasibility.get('verdict', 'unknown'))}，"
            f"整体置信度为 {confidence_label(feasibility.get('overall_confidence', 'unknown'))}。"
        )
    dimensions = feasibility.get("dimensions") or []
    scores = "；".join(
        f"{_friendly_dimension_name(item.get('name'))} {item.get('score')}分 {confidence_label(item.get('confidence'))}" for item in dimensions[:5]
    )
    gaps = "；".join(_clean_sentence_end(gap) for gap in (feasibility.get("gap_list") or [])[:3])
    tail = f"\n\n维度评分：{scores or '暂无可评分维度'}。主要缺口：{gaps or '暂无明确缺口'}。"
    return _clean_text(summary + tail, 2400)


def _concise_narration_from_proposal(proposal: dict[str, Any]) -> str:
    feasibility = proposal.get("feasibility") or {}
    summary = _clean_text(proposal.get("executive_summary"), 620)
    if not summary:
        summary = (
            f"DataForge 已生成一版有证据支撑的项目建议。"
            f"结论：{verdict_label(feasibility.get('verdict', 'unknown'))}。"
            f"整体置信度：{confidence_label(feasibility.get('overall_confidence', 'unknown'))}。"
        )
    dimensions = feasibility.get("dimensions") or []
    scores = "; ".join(
        f"{_friendly_dimension_name(item.get('name'))} {item.get('score')}/5 {confidence_label(item.get('confidence'))}"
        for item in dimensions[:3]
    )
    gaps = "; ".join(_clean_sentence_end(gap) for gap in (feasibility.get("gap_list") or [])[:2])
    tail = (
        f"\n\n维度评分：{scores or '暂无可评分维度'}。"
        f"主要缺口：{gaps or '暂无明确缺口'}。"
    )
    return _clean_text(summary + tail, 950)


def _audit_artifact(req: ChatRequest, artifact: dict[str, Any]) -> tuple[AuditVerdict, dict[str, Any]]:
    if not _evidence_catalog(artifact):
        return (
            AuditVerdict(
                verdict="pass",
                issues=[],
                target_expert=None,
            ),
            {"mode": "empty_evidence_deterministic", "response_id": None, "usage": {}},
        )
    payload = {
        "workspace_id": req.workspace_id,
        "user_request": req.message,
        "feasibility": {key: value for key, value in artifact.get("feasibility", {}).items() if key != "_llm"},
        "evidence_verification": {
            "mode": "code_prevalidated",
            "warnings": (artifact.get("feasibility", {}).get("_llm") or {}).get("evidence_warnings", []),
        },
        "evidence_catalog": _evidence_catalog(artifact),
        "market": artifact.get("market", {}),
        "pm_skill": artifact.get("pm_skill", {}),
        "tool_provenance": (artifact.get("market") or {}).get("tool_provenance", {}),
        "market_provenance_policy": "External market claims must remain market_inferred and must not be treated as workspace data_confirmed facts.",
    }
    try:
        result = run_agent(
            "df-auditor",
            json.dumps(payload, ensure_ascii=False, indent=2),
            response_schema=AuditVerdict.model_json_schema(),
            max_output_tokens=700,
        )
    except Exception as exc:
        return (
            AuditVerdict(verdict="pass", issues=[], target_expert=None),
            {
                "mode": "audit_llm_unavailable_deterministic",
                "response_id": None,
                "usage": {},
                "error": str(exc)[:500],
            },
        )
    audit = AuditVerdict.model_validate(result["structured"])
    audit, adjustment = _normalize_audit_revision_gate(audit)
    meta = _model_meta(result)
    if adjustment:
        meta["audit_adjustment"] = adjustment
    return audit, meta


def _normalize_audit_revision_gate(audit: AuditVerdict) -> tuple[AuditVerdict, str | None]:
    if audit.verdict != "revise":
        return audit, None
    blocking_terms = (
        "ref outside",
        "outside the catalog",
        "not in the catalog",
        "cannot be traced",
        "lacks evidence",
        "missing evidence",
        "no evidence item",
        "empty evidence",
        "clinical diagnosis",
        "medical decision",
        "safety-critical",
        "marked feasible",
        "too strongly",
        "invent",
        "fabricat",
    )
    joined = " ".join(audit.issues).lower()
    if any(term in joined for term in blocking_terms):
        return audit, None
    adjusted = AuditVerdict(verdict="pass", issues=audit.issues, target_expert=None)
    return adjusted, "nonblocking_audit_warnings_no_revision"


def _apply_audit_and_verdict_contract(artifact: dict[str, Any], audit: AuditVerdict) -> dict[str, Any]:
    audit_data = audit.model_dump()
    artifact["audit"] = audit_data
    if not artifact.get("feasibility"):
        return {}
    artifact["feasibility"] = apply_post_audit_guardrails(
        artifact.get("feasibility") or {},
        artifact.get("_blind_feasibility") or artifact.get("feasibility") or {},
        _evidence_catalog(artifact),
        audit_data,
    )
    return finalize_verdict_contract(artifact, audit_data)


def _compact_hits(hits: list[dict[str, Any]], limit: int = 6) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for hit in hits[:limit]:
        compact.append(
            {
                "id": hit.get("id"),
                "title": hit.get("title"),
                "source_file": hit.get("source_file"),
                "sheet": hit.get("sheet"),
                "row": hit.get("row"),
                "chunk_id": hit.get("chunk_id"),
                "content": _clean_text(hit.get("content"), 700),
            }
        )
    return compact


def _answer_payload(req: ChatRequest, decision: RoutingDecision, artifact: dict[str, Any]) -> dict[str, Any]:
    corpus = artifact.get("corpus", {})
    return {
        "workspace_id": req.workspace_id,
        "user_request": req.message,
        "routing": decision.model_dump(),
        "feasibility": {
            key: value
            for key, value in artifact.get("feasibility", {}).items()
            if key != "_llm"
        },
        "audit": artifact.get("audit", {}),
        "market": artifact.get("market", {}),
        "evidence_catalog": _evidence_catalog(artifact)[:10],
        "retrieved_hits": _compact_hits(corpus.get("hits", [])),
        "corpus_profile": corpus.get("profile", {}),
        "conversation_history": artifact.get("_conversation_history", []),
        "style_nonce": uuid.uuid4().hex[:8],
    }


def _chunk_text(text: str, size: int = 28) -> list[str]:
    return [text[index : index + size] for index in range(0, len(text), size)] or [""]


def _strip_inline_refs(text: Any) -> str:
    value = _clean_text(text, 1200)
    value = re.sub(r"\[(?:raw_docs|external|profile\.json)[^\]]*\]", "", value)
    value = re.sub(r"\[[^\]]*#(?:profile|[^\]]*row[^\]]*)[^\]]*\]", "", value)
    value = re.sub(r"\b(?:data_confirmed|market_inferred|speculative)\b:?", "", value)
    value = re.sub(r"\s{2,}", " ", value)
    return value.strip()


def _build_citations(artifact: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    citations: list[dict[str, Any]] = []
    marker_by_ref: dict[str, int] = {}

    catalog = _evidence_catalog(artifact)

    def add_citation(
        ref: str,
        *,
        confidence: str = "data_confirmed",
        source_type: str = "corpus",
        matched_override: dict[str, Any] | None = None,
    ) -> int | None:
        matched = matched_override or next((item for item in catalog if _refs_match(ref, item)), None)
        if not matched and ref:
            matched = {"ref": ref, "quote": ""}
        if not matched:
            return None
        canonical_ref = str(matched.get("ref") or ref)
        normalized = _normalize_ref(canonical_ref)
        if normalized in marker_by_ref:
            return marker_by_ref[normalized]
        metadata = matched.get("metadata") or {}
        source_file = metadata.get("source_file") or _ref_source(canonical_ref)
        chunk_id = _ref_tail(canonical_ref) or matched.get("id") or canonical_ref
        marker = len(citations) + 1
        citations.append(
            {
                "marker": marker,
                "source_file": source_file,
                "chunk_id": chunk_id,
                "confidence": confidence,
                "confidence_label": confidence_label(confidence),
                "source_type": source_type,
                "snippet": _clean_text(matched.get("quote"), 420),
            }
        )
        marker_by_ref[normalized] = marker
        return marker

    feasibility = artifact.get("feasibility") or {}
    for dimension in feasibility.get("dimensions") or []:
        confidence = str(dimension.get("confidence") or "speculative")
        for evidence in dimension.get("evidence") or []:
            ref = str(evidence.get("ref") or "")
            if ref:
                add_citation(ref, confidence=confidence, source_type=str(evidence.get("source_type") or "corpus"))

    if not citations:
        for hit in artifact.get("corpus", {}).get("hits", [])[:8]:
            evidence = _evidence_from_hit(hit)
            add_citation(
                evidence.ref,
                confidence="data_confirmed",
                source_type="corpus",
                matched_override={
                    "ref": evidence.ref,
                    "quote": evidence.quote,
                    "metadata": {
                        "title": hit.get("title"),
                        "source_file": hit.get("source_file"),
                        "sheet": hit.get("sheet"),
                        "row": hit.get("row"),
                    },
                },
            )

    for finding in artifact.get("market", {}).get("external_findings", [])[:4]:
        if not isinstance(finding, dict):
            continue
        url = str(finding.get("source_url") or "")
        claim = str(finding.get("claim") or "")
        if not url and not claim:
            continue
        key = _normalize_ref(url or claim)
        if key in marker_by_ref:
            continue
        marker = len(citations) + 1
        citations.append(
            {
                "marker": marker,
                "source_file": url or finding.get("source_title") or "market",
                "chunk_id": f"market-{marker}",
                "confidence": "market_inferred",
                "confidence_label": confidence_label("market_inferred"),
                "source_type": "market",
                "snippet": _clean_text(claim, 420),
                "source_url": url,
            }
        )
        marker_by_ref[key] = marker
    return citations, marker_by_ref


def _evidence_markers(evidence_items: list[dict[str, Any]], citations: list[dict[str, Any]]) -> str:
    markers: list[str] = []
    for evidence in evidence_items:
        ref = str(evidence.get("ref") or "")
        tail = _ref_tail(ref)
        source = _ref_source(ref)
        matched = next(
            (
                item
                for item in citations
                if _normalize_ref(ref)
                and (
                    (tail and _normalize_ref(item.get("chunk_id")) == tail)
                    or (tail and tail in _normalize_ref(item.get("chunk_id")))
                )
            ),
            None,
        )
        if not matched and source and not tail:
            matched = next((item for item in citations if _normalize_ref(item.get("source_file")) == source), None)
        if matched:
            marker = f"[{matched['marker']}]"
            if marker not in markers:
                markers.append(marker)
    return " ".join(markers[:3])


def _structured_answer(req: ChatRequest, decision: RoutingDecision, artifact: dict[str, Any]) -> dict[str, Any]:
    if decision.intent == "corpus_qa":
        return _structured_corpus_answer(req, artifact)
    citations, _ = _build_citations(artifact)
    feasibility = artifact.get("feasibility") or {}
    corpus = artifact.get("corpus") or {}
    market = artifact.get("market") or {}
    audit = artifact.get("audit") or {}
    opportunity = feasibility.get("opportunity_id") or ((corpus.get("opportunities") or [{}])[0] or {}).get("title") or "workspace opportunity"
    opportunity_title = _human_title_from_opportunity(opportunity)
    verdict = feasibility.get("verdict") or "unknown"
    overall = feasibility.get("overall_confidence") or "speculative"
    lines: list[str] = [
        "## 执行摘要",
        f"- 结论：**{verdict}**；整体置信度：**{overall}**。",
        f"- 本次回答基于当前工作区检索、可行性分析和审计结果生成；审计结论：{audit.get('verdict', 'not_run')}。",
        "",
        "## 机会",
        f"- 机会方向：{_strip_inline_refs(opportunity_title)}。",
    ]
    if market.get("positioning_note"):
        market_markers = " ".join(f"[{item['marker']}]" for item in citations if item.get("source_type") == "market")[:24]
        lines.append(f"- 市场补充：{_strip_inline_refs(market.get('positioning_note'))} {market_markers}".rstrip())
    lines.extend(["", "## 各维度评分与判断"])
    dimensions = feasibility.get("dimensions") or []
    if dimensions:
        for dimension in dimensions:
            markers = _evidence_markers(dimension.get("evidence") or [], citations)
            name = dimension.get("name") or "dimension"
            score = dimension.get("score", "n/a")
            confidence = dimension.get("confidence") or "speculative"
            rationale = _strip_inline_refs(dimension.get("rationale")) or "证据不足，需要补充。"
            lines.append(f"- **{name}**：{score}/5，置信度 `{confidence}`。{rationale} {markers}".rstrip())
    else:
        lines.append("- 当前没有足够的已验证证据形成维度评分。")
    lines.extend(["", "## 关键缺口"])
    gaps = feasibility.get("gap_list") or []
    if gaps:
        for gap in gaps[:5]:
            lines.append(f"- {_strip_inline_refs(gap)}")
    else:
        lines.append("- 暂无额外缺口；建议继续用新数据验证关键假设。")
    lines.extend(["", "## 建议下一步"])
    for item in _feasibility_next_steps(req, artifact, citations):
        lines.append(f"- {item}")
    if citations:
        lines.append(f"- 前端可在证据面板中查看 {len(citations)} 条结构化 citation。")
    markdown = "\n".join(lines).strip()
    markdown = _strip_raw_ref_leaks(markdown)
    return {
        "markdown": markdown,
        "citations": citations,
        "_llm": {"mode": "structured_answer_renderer", "response_id": None, "usage": {}},
    }


def _feasibility_next_steps(req: ChatRequest, artifact: dict[str, Any], citations: list[dict[str, Any]]) -> list[str]:
    dimensions = artifact.get("feasibility", {}).get("dimensions") or []
    low_dimensions = [item for item in dimensions if int(item.get("score") or 0) <= 2]
    hits = artifact.get("corpus", {}).get("hits", [])
    signals = _evidence_signals(hits, citations)
    steps: list[str] = []
    if signals:
        first = signals[0]
        second = signals[1] if len(signals) > 1 else first
        third = signals[2] if len(signals) > 2 else second
        steps.append(f"把第一轮验证场景锁定在：{first['text']}，先做小范围触达和参与记录 {first['markers']}".rstrip())
        steps.append(f"把活动、联名、赞助或权益设计绑定到：{second['text']}，避免脱离资料里的真实兴趣/痛点 {second['markers']}".rstrip())
        steps.append(f"复盘指标直接围绕：{third['text']}，记录转化、复购、成本、参与反馈和渠道来源 {third['markers']}".rstrip())
    for dimension in low_dimensions[:2]:
        name = _friendly_dimension_name(dimension.get("name") or "低分维度")
        rationale = _strip_inline_refs(dimension.get("rationale")) or "当前证据不足"
        steps.append(f"补强{name}：针对“{rationale[:72]}”补一组可量化样本或真实成本数据。")
    if not steps:
        topic = _query_action_title(req.message) or _human_title_from_opportunity(artifact.get("feasibility", {}).get("opportunity_id"))
        steps.append(f"围绕“{topic}”跑一个 1 周最小验证，至少记录用户触达、参与、转化、成本和复盘反馈。")
    return steps[:5]


def _structured_corpus_answer(req: ChatRequest, artifact: dict[str, Any]) -> dict[str, Any]:
    citations, _ = _build_citations(artifact)
    hits = artifact.get("corpus", {}).get("hits", [])
    signals = _evidence_signals(hits, citations)
    topic = _query_action_title(req.message) or "当前问题"
    lines: list[str] = [
        "## 综合回答",
        f"- 可以先围绕“{topic}”给出一版有据方案；以下内容只基于当前工作区命中的 {len(hits)} 条上传资料。",
        "",
        "## 资料里浮现的信号",
    ]
    if signals:
        for signal in signals[:6]:
            lines.append(f"- {signal['text']} {signal['markers']}".rstrip())
    else:
        lines.append("- 当前问题没有命中足够具体的工作区记录。")
    lines.extend(["", "## 建议方案"])
    if signals:
        lines.extend(_corpus_action_lines(signals, citations))
    else:
        lines.append("- 先上传或补充与该问题直接相关的记录，再让系统生成有据建议。")
    if citations:
        lines.append(f"- 本回答返回 {len(citations)} 条结构化 citation，前端可打开证据面板核验。")
    markdown = _strip_raw_ref_leaks("\n".join(lines).strip())
    return {
        "markdown": markdown,
        "citations": citations,
        "_llm": {"mode": "structured_corpus_answer_renderer", "response_id": None, "usage": {}},
    }


def _evidence_signals(hits: list[dict[str, Any]], citations: list[dict[str, Any]]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    ordered_hits = sorted(
        hits[:8],
        key=lambda item: 1 if str(item.get("document_type") or "") == "profile" else 0,
    )
    for hit in ordered_hits:
        text = _compact_hit_signal_customer(hit)
        if not text:
            continue
        key = re.sub(r"\W+", "", text.lower())[:80]
        if key in seen:
            continue
        seen.add(key)
        markers = _evidence_markers([_evidence_from_hit(hit).model_dump()], citations)
        items.append({"text": text, "markers": markers})
    return items


def _compact_hit_signal(hit: dict[str, Any]) -> str:
    content = _clean_text(hit.get("content"), 900)
    if not content:
        return ""
    parts = [part.strip() for part in re.split(r";|\n", content) if part.strip()]
    selected: list[str] = []
    skip_names = {"collection", "id", "row", "source", "source_file", "chunk_id"}
    for part in parts:
        if ":" in part:
            name, value = part.split(":", 1)
            name = name.strip()
            value = value.strip()
            if not value or name.lower() in skip_names:
                continue
            if len(value) < 2:
                continue
            selected.append(f"{name} 是“{value[:44]}”")
        else:
            selected.append(part[:70])
        if len(selected) >= 2:
            break
    if not selected:
        return content[:100]
    text = "；".join(selected)
    return _strip_raw_ref_leaks(_strip_inline_refs(text))[:140]


def _compact_hit_signal_customer(hit: dict[str, Any]) -> str:
    content = _clean_text(hit.get("content"), 900)
    if not content:
        return ""
    field_labels = field_label_map_from_hits([hit])
    if str(hit.get("document_type") or "") == "profile":
        match = re.search(r"(?:高信号|交叉信号)[：:]\s*([^。\n]+)", content)
        if match:
            return _clip_customer_signal(_profile_signal_sentence(match.group(1), field_labels))
    selected: list[tuple[str, str]] = []
    skip_names = {"collection", "id", "row", "source", "source_file", "chunk_id", "workspace_id", "document_type"}
    for index, (name, value) in enumerate(record_pairs(content), start=1):
        if not value or name.lower() in skip_names or len(value) < 2:
            continue
        selected.append((friendly_label(name, value=value, index=index), value[:44]))
        if len(selected) >= 2:
            break
    if not selected:
        parts = [part.strip() for part in re.split(r";|\n", content) if part.strip()]
        text = "。".join(parts[:2]) if parts else content[:100]
        return _clip_customer_signal(sanitize_customer_text(_strip_inline_refs(text), field_labels))
    if len(selected) == 1:
        label, value = selected[0]
        text = f"{label}为“{value}”"
    else:
        (label_a, value_a), (label_b, value_b) = selected[0], selected[1]
        text = f"{label_a}为“{value_a}”，{label_b}为“{value_b}”"
    return _clip_customer_signal(sanitize_customer_text(_strip_inline_refs(text), field_labels))


def _profile_signal_sentence(signal: str, field_labels: dict[str, str]) -> str:
    cleaned = sanitize_customer_text(signal, field_labels)
    first = re.split(r"[；。]", cleaned, maxsplit=1)[0].strip(" ，。；")
    match = re.search(r"(.+?)\s*按\s*(.+?)\s*分组均值差异明显[：:]\s*(.+)", first)
    if match:
        metric = _clean_sentence_end(match.group(1).strip())
        group = _clean_sentence_end(match.group(2).strip())
        detail = re.sub(r"\(n=\d+\)", "", match.group(3)).strip(" ，。；")
        return f"{metric}在不同{group}之间差异明显（{detail}）"
    match = re.search(r"(.+?)\s+有\s+\d+\s+个不同取值", first)
    if match:
        return f"{match.group(1).strip()}有可用于分层观察的差异"
    return first or "数据画像中存在可用于验证的差异信号"


def _clip_customer_signal(text: str, limit: int = 140) -> str:
    if len(text) <= limit:
        return text
    head = text[:limit]
    for sep in ("；", "。", "，", "、", " "):
        pos = head.rfind(sep)
        if pos >= int(limit * 0.55):
            return head[:pos].rstrip("；。，、 ") + "…"
    return head.rstrip() + "…"


def _corpus_action_lines(signals: list[dict[str, str]], citations: list[dict[str, Any]]) -> list[str]:
    first = signals[0]
    second = signals[1] if len(signals) > 1 else first
    third = signals[2] if len(signals) > 2 else second
    fourth = signals[3] if len(signals) > 3 else third
    citation_total = len(citations)
    return [
        f"- 先把方案主题绑定到最强信号：{first['text']}，不要只做泛泛传播 {first['markers']}".rstrip(),
        f"- 把第二类信号转成参与钩子或合作权益：{second['text']}，用于设计触达文案、活动机制或会员权益 {second['markers']}".rstrip(),
        f"- 用第三类信号定义最小验证指标：{third['text']}，建议记录触达人数、参与率、转化/复购、单次成本和用户反馈 {third['markers']}".rstrip(),
        f"- 复盘时对照另一条命中信号做分层：{fourth['text']}，看哪些人群、渠道或内容真正拉动结果 {fourth['markers']}".rstrip(),
        f"- 本次证据引用共 {citation_total} 条；结论应随后续上传数据变化而变化。",
    ]


def _strip_raw_ref_leaks(text: str) -> str:
    text = re.sub(r"\[raw_docs/[^\]]+\]", "", text)
    text = re.sub(r"\[external/[^\]]+\]", "", text)
    text = re.sub(r"\[profile\.json[^\]]*\]", "", text)
    return text


DIMENSION_LABELS = {
    "market": "市场信号",
    "technical": "可交付性",
    "asset_data": "数据充分度",
    "resource_cost": "成本与规模",
    "differentiation_risk": "差异化",
}


def _customer_field_labels(artifact: dict[str, Any]) -> dict[str, str]:
    return field_label_map_from_hits(artifact.get("corpus", {}).get("hits", []))


def _workspace_field_labels(workspace_id: str) -> dict[str, str]:
    try:
        detail = get_workspace_detail(workspace_id)
    except Exception:
        return {}
    labels: dict[str, str] = {}
    for index, column in enumerate(detail.get("columns") or [], start=1):
        if not isinstance(column, dict):
            continue
        name = str(column.get("name") or "").strip()
        label = str(column.get("friendly_label") or friendly_label(name, role=column.get("role"), index=index)).strip()
        if name and label:
            labels[name] = label
    return labels


def _customer_text(text: Any, artifact: dict[str, Any]) -> str:
    return sanitize_customer_text(_strip_raw_ref_leaks(str(text or "")), _customer_field_labels(artifact))


def _friendly_dimension_name(name: Any) -> str:
    value = str(name or "").strip()
    return DIMENSION_LABELS.get(value, dimension_label(value) if value else friendly_label(value))


def _variant(artifact: dict[str, Any], choices: list[str]) -> str:
    if not choices:
        return ""
    seed = str(artifact.get("conversation_id") or uuid.uuid4().hex)
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return choices[digest[0] % len(choices)]


def _output_contract(intent: str) -> dict[str, Any]:
    if intent == "corpus_qa":
        sections = ["综合判断", "建议动作"]
    elif intent == "clarify_needed":
        sections = ["question", "options", "allow_multi", "allow_freeform", "confidence"]
    else:
        sections = ["行动方案", "评分", "风险/缺口", "依据"]
    return {
        "version": "batch11.customer_text.rubric.v1",
        "intent": intent,
        "sections": sections,
        "citation_style": "[n]",
        "customer_text": True,
        "raw_field_names_allowed": False,
    }


def _chat_output_contract(intent: str) -> dict[str, Any]:
    contract = _output_contract(intent)
    contract.update(
        {
            "version": "batch12.conversation_chat.v1",
            "sections": ["direct_answer", "brief_reasoning", "next_step"],
            "answer_style": "concise_conversation",
            "max_target_chars": 300,
            "no_numbered_action_plan": True,
            "no_dimension_scores": True,
        }
    )
    return contract


def _is_conversation_answer(req: ChatRequest, decision: RoutingDecision) -> bool:
    artifact_mode = str(getattr(req, "artifact_mode", "") or "").strip().lower()
    ui_context = getattr(req, "ui_context", None)
    ui_mode = str((ui_context or {}).get("mode") or "").strip().lower() if isinstance(ui_context, dict) else ""
    return (
        decision.intent == "corpus_qa"
        or artifact_mode == "chat"
        or ui_mode == "conversation"
        or str(getattr(decision, "output_mode", "") or "").strip().lower() == "chat"
    )


def _current_user_message(req: ChatRequest) -> str:
    return _intent_message(req.message)


def _last_history_user_topic(artifact: dict[str, Any]) -> str:
    history = artifact.get("_conversation_history") or []
    if not isinstance(history, list):
        return ""
    for item in reversed(history):
        if not isinstance(item, dict) or item.get("role") != "user":
            continue
        text = _current_user_message(ChatRequest(workspace_id=str(artifact.get("workspace_id") or "demo-corpus"), message=str(item.get("text") or "")))
        text = re.sub(r"(值得|能不能|是否|可以|怎么|如何|那如果|如果|呢|吗|？|\?)", "", text).strip(" ，。；;：:-_")
        candidate = _complete_short_phrase(text, fallback="", limit=26)
        if candidate:
            return candidate
    return ""


def _chat_constraint_phrase(current: str) -> str:
    text = str(current or "")
    constraints: list[str] = []
    if re.search(r"(预算.{0,8}(一半|减半|砍半)|只有一半|减半.{0,8}预算|half.{0,8}budget)", text, re.I):
        constraints.append("预算减半")
    if re.search(r"(只看|仅看).{0,12}(旗舰店|旗舰|主店|核心门店)", text):
        constraints.append("只看旗舰店")
    if re.search(r"(只看|仅看).{0,12}(数据|工作区|内部资料)", text):
        constraints.append("只按工作区数据")
    if re.search(r"(先不|不要|不看).{0,12}(市场|外部)", text):
        constraints.append("暂不纳入外部市场信息")
    if not constraints:
        return ""
    return "，".join(constraints)


def _sanitize_chat_sentence(text: Any, field_labels: dict[str, str]) -> str:
    cleaned = sanitize_customer_text(_strip_raw_ref_leaks(str(text or "")), field_labels)
    cleaned = re.sub(r"(?:资料分组|时间维度|业务类别|本工作区为演示用|合成数据)为[“\"']?[^，。；、\n]+[”\"']?[，；、]?", "", cleaned)
    cleaned = re.sub(r"\bL\d{2}\b", "", cleaned)
    cleaned = re.sub(r"(?m)^\s*\d+[.)、]\s*", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"([。！？!?])\1+", r"\1", cleaned)
    return cleaned.strip(" ，；;")


def _chat_topic(req: ChatRequest, artifact: dict[str, Any], feasibility: dict[str, Any]) -> str:
    current = _current_user_message(req)
    if re.search(r"(那|如果|这个|它|继续|预算|一半|减半|只看)", current) and artifact.get("_conversation_history"):
        topic = _last_history_user_topic(artifact)
        if topic:
            return _safe_chat_topic_label(topic)
    title_req = req.model_copy(update={"message": current})
    topic = _customer_opportunity_title(
        title_req,
        artifact,
        feasibility.get("opportunity_id") or _evidence_topic_from_hits(artifact.get("corpus", {}).get("hits", [])) or "当前机会",
    )
    return _safe_chat_topic_label(topic)


def _safe_chat_topic_label(topic: Any) -> str:
    cleaned = _complete_short_phrase(topic, fallback="", limit=28)
    if not cleaned:
        return "当前工作区机会"
    if re.fullmatch(r"(required|optional|true|false|yes|no|none|null|unknown|n/?a|id|row|profile)", cleaned, re.I):
        return "当前工作区机会"
    if re.fullmatch(r"[-+]?\d+(?:\.\d+)?%?", cleaned):
        return "当前工作区机会"
    return cleaned


def _marker_hint_from_citations(citations: list[dict[str, Any]], limit: int = 2) -> str:
    markers = [f"[{item.get('marker')}]" for item in citations[:limit] if item.get("marker")]
    return " ".join(markers)


def _trim_conversation_answer(text: str, *, limit: int = 320) -> str:
    cleaned = re.sub(r"\n{2,}", "\n", text).strip()
    cleaned = re.sub(r"(?m)^\s*\d+[.)、]\s*", "", cleaned)
    if len(cleaned) <= limit:
        return cleaned
    head = cleaned[:limit]
    for sep in ("。", "；", "，", "\n"):
        pos = head.rfind(sep)
        if pos >= int(limit * 0.72):
            return head[: pos + (1 if sep in "。；，" else 0)].rstrip()
    return head.rstrip(" ，；、") + "。"


def _looks_like_plan_markdown(text: str) -> bool:
    """LLM 是否按方案模板输出了结构化内容（有 ## 小节标题且多行）。"""
    if not text:
        return False
    return bool(re.search(r"(?m)^\s*#{2,4}\s+\S", text)) and text.count("\n") >= 2


def _sanitize_plan_markdown(text: str, field_labels: dict[str, str]) -> str:
    """方案专用清洗：逐行清洗但【保留换行、列表序号、## 标题】，不压平结构。"""
    out: list[str] = []
    for raw in str(text or "").split("\n"):
        stripped = raw.rstrip()
        if not stripped.strip():
            out.append("")
            continue
        m = re.match(r"^(\s*(?:#{2,4}\s+|[-*]\s+|\d+[.)、]\s+)?)(.*)$", stripped)
        prefix, body = (m.group(1), m.group(2)) if m else ("", stripped)
        body = sanitize_customer_text(_strip_raw_ref_leaks(body), field_labels)
        body = re.sub(r"(?:资料分组|时间维度|业务类别|本工作区为演示用|合成数据)为[“\"'][^，。；、\n]+[”\"']?[，；、]?", "", body)
        body = re.sub(r"\bL\d{2}\b", "", body)
        body = re.sub(r"[ \t]{2,}", " ", body).strip()
        out.append((prefix + body).rstrip() if body else prefix.rstrip())
    md = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    return md


def _trim_plan_answer(md: str, *, limit: int = 1500) -> str:
    """按整行截断方案，避免把某个小节截一半。"""
    if len(md) <= limit:
        return md
    kept: list[str] = []
    total = 0
    for line in md.split("\n"):
        if total + len(line) + 1 > limit and kept:
            break
        kept.append(line)
        total += len(line) + 1
    return "\n".join(kept).rstrip()


def _produce_offer_for(
    req: ChatRequest,
    feasibility: dict[str, Any],
    *,
    plan: bool = False,
) -> dict[str, Any] | None:
    """对话里识别出客户想要产物 → 给前端一个“确认生成”的信号；plan=刚生成了方案。"""
    msg = _current_user_message(req)
    has_analysis = bool(
        (feasibility or {}).get("verdict")
        or (feasibility or {}).get("dimensions")
        or (feasibility or {}).get("scores")
    )
    if re.search(r"(生成|制作|做|出|产出|导出|给我|来个|要个|帮我做).{0,8}(海报|配图|概念图|key ?visual|poster|宣传图)", msg, re.I) or re.search(r"(海报|poster|宣传图|概念图)", msg, re.I):
        return {"kind": "poster", "label": "确认生成活动海报 / 概念图", "ready": has_analysis}
    if re.search(r"(生成|制作|写|出|产出|导出|给我|帮我做|要个).{0,8}(方案|项目书|prd|proposal|报告|文档|计划书|pdf)", msg, re.I):
        return {"kind": "proposal", "label": "确认生成完整方案（PDF / 概念图 / 语音）", "ready": has_analysis}
    if plan:
        return {"kind": "proposal", "label": "把这个方案生成完整产物（PDF / 概念图 / 语音）", "ready": has_analysis}
    return None


def _grounded_chat_payload(
    req: ChatRequest,
    artifact: dict[str, Any],
    citations: list[dict[str, Any]],
    feasibility: dict[str, Any],
    field_labels: Any,
) -> dict[str, Any]:
    """构建 grounded 会话作答的 LLM 输入（run_ 与 stream_ 两条路径共用）。"""
    evidence: list[dict[str, Any]] = []
    for item in citations[:8]:
        marker = item.get("marker")
        snippet = sanitize_customer_text(str(item.get("snippet") or "")) if "sanitize_customer_text" in globals() else str(item.get("snippet") or "")
        snippet = _sanitize_chat_sentence(snippet, field_labels)
        if marker and snippet:
            evidence.append({"marker": marker, "evidence": _clip_customer_signal(snippet, 160)})
    history: list[dict[str, Any]] = []
    conv_id = getattr(req, "conversation_id", None)
    if conv_id:
        try:
            history = _compact_history(conversation_context(conv_id))
        except Exception:
            history = []
    corpus_profile = (artifact.get("corpus", {}) or {}).get("profile", {}) or {}
    return {
        "current_question": _current_user_message(req),
        "conversation_history": history[-8:],
        "evidence": evidence,
        "workspace_summary": _clip_customer_signal(str(corpus_profile.get("customer_summary") or corpus_profile.get("profile_summary") or ""), 220),
        "feasibility_hint": {
            "verdict": feasibility.get("verdict"),
            "opportunity": feasibility.get("opportunity_id"),
        },
    }


def _finalize_grounded_chat_text(raw: str, field_labels: Any) -> tuple[str, bool] | None:
    """把（流式或一次性拿到的）原始答案做保留结构的清洗。返回 (markdown, is_plan)，无效返回 None。"""
    raw = str(raw or "").strip()
    if not raw or len(raw) < 12:
        return None
    is_plan = _looks_like_plan_markdown(raw)
    md = _trim_plan_answer(_sanitize_plan_markdown(raw, field_labels), limit=1900 if is_plan else 820)
    if md and len(md) >= 12:
        return md, is_plan
    return None


def _llm_chat_answer(
    req: ChatRequest,
    artifact: dict[str, Any],
    citations: list[dict[str, Any]],
    feasibility: dict[str, Any],
    field_labels: Any,
) -> dict[str, Any] | None:
    """用 LLM 针对当前问题作答（替代模板）。失败返回 None，由调用方回退到模板。"""
    try:
        payload = _grounded_chat_payload(req, artifact, citations, feasibility, field_labels)
        result = run_grounded_chat_answer(payload)
        raw = str((result or {}).get("text") or "").strip()
        # 统一用「保留结构」的清洗：不再把换行/列表压平。方案给更大额度，普通问答短一些，
        # 但都保留 LLM 用的换行和 `- ` 列点，多点答案才有排版（修复追问一堆字没换行的问题）。
        finalized = _finalize_grounded_chat_text(raw, field_labels)
        if finalized:
            md, is_plan = finalized
            return {
                "markdown": md,
                "is_plan": is_plan,
                "response_id": (result or {}).get("response_id"),
                "usage": (result or {}).get("usage", {}),
            }
    except Exception:
        return None
    return None


def _structured_chat_answer_v10(req: ChatRequest, decision: RoutingDecision, artifact: dict[str, Any]) -> dict[str, Any]:
    citations, _ = _build_citations(artifact)
    field_labels = _customer_field_labels(artifact)
    citations = sanitize_citations(citations, field_labels)
    feasibility = artifact.get("feasibility") or {}
    if not isinstance(feasibility, dict):
        feasibility = {}
    # 先用 LLM 针对当前问题作答；失败再回退到下面的模板拼接。
    _llm_ans = _llm_chat_answer(req, artifact, citations, feasibility, field_labels)
    if _llm_ans:
        offer = _produce_offer_for(req, feasibility, plan=bool(_llm_ans.get("is_plan")))
        if offer:
            artifact["produce_offer"] = offer
        return {
            "markdown": _llm_ans["markdown"],
            "citations": citations,
            "is_plan": bool(_llm_ans.get("is_plan")),
            "_llm": {"mode": "grounded_chat_plan_llm" if _llm_ans.get("is_plan") else "grounded_chat_llm", "response_id": _llm_ans.get("response_id"), "usage": _llm_ans.get("usage", {})},
            "output_contract": _chat_output_contract(decision.intent),
        }
    hits = artifact.get("corpus", {}).get("hits", [])
    signals = _evidence_signals(hits, citations)
    current = _current_user_message(req)
    constraint = _chat_constraint_phrase(current)
    topic = _sanitize_chat_sentence(_chat_topic(req, artifact, feasibility), field_labels) or "当前机会"
    marker_hint = _combined_markers(signals[:3], 2) or _marker_hint_from_citations(citations)
    verdict = str(feasibility.get("verdict") or "").lower()

    if constraint:
        lead = f"如果继续围绕“{topic}”，在{constraint}的前提下，不建议照原规模复刻，应该缩成一轮更小的验证。"
    elif verdict in {"feasible", "recommended"}:
        lead = f"直接看，“{topic}”可以继续推进，但仍建议先做可复盘的小范围验证。"
    elif verdict in {"not_yet_feasible", "not_feasible", "rejected"}:
        lead = f"直接看，“{topic}”现在不适合直接放大，证据还不足以支撑高投入。"
    else:
        lead = f"直接看，“{topic}”可以继续讨论，但要按证据强弱先收窄试点范围。"

    lines = [lead]
    if signals:
        first = _sanitize_chat_sentence(signals[0].get("text"), field_labels)
        second = _sanitize_chat_sentence(signals[1].get("text"), field_labels) if len(signals) > 1 else ""
        if second and second != first:
            lines.append(f"目前较强的依据是{first}，同时{second}。{marker_hint}".strip())
        else:
            lines.append(f"目前较强的依据是{first}。{marker_hint}".strip())
    elif citations:
        lines.append(f"当前只命中少量工作区证据，结论应保守处理。{marker_hint}".strip())
    else:
        lines.append("当前工作区还没有足够证据支撑明确判断，需要先补充可复盘的数据。")

    gaps = [_sanitize_chat_sentence(item, field_labels) for item in (feasibility.get("gap_list") or []) if str(item).strip()]
    if gaps:
        lines.append(f"最大的缺口是{_clip_customer_signal(gaps[0], 58)}，所以先验证成本、参与或转化阈值。")
    elif constraint:
        lines.append("下一步只保留最能证明效果的门店、人群或渠道，其他动作先暂停。")
    else:
        lines.append("下一步建议先定一个主指标，再用一轮小样本验证决定是否生成完整项目书。")

    markdown = _trim_conversation_answer("".join(line if line.endswith(("。", "！", "？", ".", "!", "?")) else line + "。" for line in lines))
    markdown = _sanitize_chat_sentence(markdown, field_labels)
    return {
        "markdown": markdown,
        "citations": citations,
        "_llm": {"mode": "batch12_conversation_chat_renderer", "response_id": None, "usage": {}},
        "output_contract": _chat_output_contract(decision.intent),
    }


def _llm_feasibility_action_plan(
    req: ChatRequest,
    artifact: dict[str, Any],
    citations: list[dict[str, Any]],
    feasibility: dict[str, Any],
    title: str,
) -> tuple[str, list[str]] | None:
    """用 LLM 根据 LLM 自己产出的判定/维度/缺口/证据，生成这批数据专属的行动方案。失败返回 None。"""
    try:
        field_labels = _customer_field_labels(artifact)
        evidence: list[dict[str, Any]] = []
        for item in citations[:8]:
            marker = item.get("marker")
            snippet = _sanitize_chat_sentence(str(item.get("snippet") or ""), field_labels)
            if marker and snippet:
                evidence.append({"marker": marker, "evidence": _clip_customer_signal(snippet, 160)})
        dims: list[dict[str, Any]] = []
        for d in (feasibility.get("dimensions") or [])[:6]:
            dims.append({
                "name": _friendly_dimension_name(d.get("name")),
                "score": d.get("score"),
                "rationale": _clip_customer_signal(_sanitize_chat_sentence(str(d.get("rationale") or ""), field_labels), 130),
            })
        gaps = [_sanitize_chat_sentence(str(g), field_labels) for g in (feasibility.get("gap_list") or []) if str(g).strip()][:5]
        corpus_profile = (artifact.get("corpus", {}) or {}).get("profile", {}) or {}
        payload = {
            "opportunity": title,
            "verdict": feasibility.get("verdict"),
            "overall_confidence": feasibility.get("overall_confidence"),
            "dimensions": dims,
            "gap_list": gaps,
            "evidence": evidence,
            "user_request": _current_user_message(req),
            "workspace_summary": _clip_customer_signal(str(corpus_profile.get("customer_summary") or corpus_profile.get("profile_summary") or ""), 220),
        }
        result = run_action_plan(payload)
        rec = _sanitize_chat_sentence(str((result or {}).get("recommendation") or ""), field_labels)
        steps: list[str] = []
        for s in (result or {}).get("steps") or []:
            s2 = _sanitize_chat_sentence(str(s), field_labels)
            if s2 and len(s2) >= 8:
                steps.append(s2)
        if rec and len(rec) >= 8 and len(steps) >= 3:
            return rec, steps[:5]
    except Exception:
        return None
    return None


def _ensure_feasibility_action_plan(
    req: ChatRequest,
    artifact: dict[str, Any],
    citations: list[dict[str, Any]],
) -> tuple[str, list[str]]:
    feasibility = artifact.setdefault("feasibility", {})
    title = _customer_opportunity_title(req, artifact, feasibility.get("opportunity_id") or "当前资料机会")

    # 修订循环里第二次渲染时，复用第一次的 LLM 方案，避免重复调用模型。
    if feasibility.get("_action_plan_llm"):
        rec0 = str(feasibility.get("recommendation") or "").strip()
        plan0 = [str(s).strip() for s in (feasibility.get("action_plan") or []) if str(s).strip()]
        if rec0 and len(plan0) >= 3:
            return rec0, plan0[:5]

    # 1) 优先让 LLM 基于真实判定/维度/缺口/证据生成【这批数据专属】的行动方案（泛化、不套攀岩模板）。
    llm_plan = _llm_feasibility_action_plan(req, artifact, citations, feasibility, title)
    if llm_plan:
        recommendation, steps = llm_plan
        feasibility["opportunity_id"] = title
        feasibility["recommendation"] = recommendation
        feasibility["action_plan"] = steps[:5]
        feasibility["_action_plan_llm"] = True
        artifact["recommendation"] = recommendation
        artifact["action_plan"] = steps[:5]
        return recommendation, steps[:5]

    # 2) LLM 失败才回退到合成兜底（不再用写死的攀岩/活动模板覆盖）。
    existing_recommendation = str(feasibility.get("recommendation") or "").strip()
    existing_plan = [str(item).strip() for item in (feasibility.get("action_plan") or []) if str(item).strip()]
    if existing_recommendation and len(existing_plan) >= 3 and not _action_plan_needs_rewrite([existing_recommendation, *existing_plan]):
        return existing_recommendation, existing_plan[:5]

    signals = _evidence_signals(artifact.get("corpus", {}).get("hits", []), citations)
    steps: list[str] = _synthesized_feasibility_steps(title, citations, feasibility) if signals or citations else []
    gaps = [sanitize_customer_text(str(item)) for item in (feasibility.get("gap_list") or []) if str(item).strip()]
    if gaps and not steps:
        steps.append(f"把最大缺口先补成可验证数据：{_clean_sentence_end(gaps[0])}。")
    if not steps:
        steps = [
            f"围绕“{title}”跑一个一周最小验证，先记录真实参与和反馈。",
            "补齐目标人群、触达渠道、成本、转化和复购字段，再决定是否扩大。",
            "验证结束后复盘证据强弱，证据不足就保守下调结论。",
        ]
    recommendation = f"建议先做“{title}”的小规模验证，跑通证据最强的场景后再决定是否扩大投入。"
    feasibility["opportunity_id"] = title
    feasibility["recommendation"] = recommendation
    feasibility["action_plan"] = steps[:5]
    artifact["recommendation"] = recommendation
    artifact["action_plan"] = steps[:5]
    return recommendation, steps[:5]


def _campaign_story_lines(req: ChatRequest, artifact: dict[str, Any], citations: list[dict[str, Any]]) -> list[str]:
    # 已弃用：这是写死攀岩/会员/赞助的活动叙事模板，换数据会串味、不泛化。
    # 行动方案现在统一由 LLM（_llm_feasibility_action_plan）按真实证据生成，这里不再追加模板段落。
    return []
    blob = _campaign_story_blob(req, artifact, citations)
    request_text = str(req.message or "")
    if not re.search(r"(活动|推广|企划|拉新|新客|转化|宣传|曝光|名声|campaign|promotion)", request_text, re.I):
        return []
    if not re.search(r"(攀岩|climb|climbing|门店|会员|到店|活动|周边|赞助)", blob, re.I):
        return []

    signals = _evidence_signals(artifact.get("corpus", {}).get("hits", []), citations)
    goal_markers = _markers_for_terms(signals, citations, ("新客", "转化", "到店", "复购", "客流", "会员", "活动"))
    merch_markers = _markers_for_terms(signals, citations, ("周边", "t恤", "T恤", "衣服", "logo", "Logo", "打卡", "曝光"))
    sponsor_markers = _markers_for_terms(signals, citations, ("护手", "护肤", "手部", "赞助", "联名", "品牌", "修复"))
    goal_known = bool(re.search(r"(新客|转化|到店|复购|曝光|宣传|名声|拉新)", request_text))

    lines = [
        "**活动企划建议**",
        (
            "1. 先把目标问清楚："
            + (
                "本轮可以按“新客到店转化 + 品牌传播”作为主目标，复购作为副指标。"
                if goal_known
                else "请客户在“新客到店转化、老会员复购、品牌曝光”里选一个主目标，再定预算和周期。"
            )
            + (f" {goal_markers}" if goal_markers else "")
        ).rstrip(),
        (
            "2. 活动主线建议做“会员挑战日/跨店打卡赛”：用一到两个高活跃门店先试点，设置报名、到店、完赛、二次到访四个漏斗指标。"
            + (f" {goal_markers}" if goal_markers else "")
        ).rstrip(),
    ]
    if re.search(r"(周边|t恤|T恤|衣服|logo|Logo|打卡|曝光)", blob, re.I):
        lines.append(
            (
                "3. 传播钩子用 Logo 周边：把攀岩馆专属 T 恤或徽章作为参赛奖励，让会员在其他攀岩馆或社交平台继续露出品牌。"
                + (f" {merch_markers}" if merch_markers else "")
            ).rstrip()
        )
    if re.search(r"(护手|护肤|手部|赞助|联名|修复)", blob, re.I):
        lines.append(
            (
                "4. 赞助方向优先找护手霜、手部修复或运动恢复类品牌：攀岩后的手部磨损是自然场景，适合做试用装、完赛包和联合打卡。"
                + (f" {sponsor_markers}" if sponsor_markers else "")
            ).rstrip()
        )
    lines.append(
        "5. 如果这个方向认可，建议直接生成项目书、执行计划、活动海报和周边衣服概念图；若要生成海报/周边，请先上传透明 PNG Logo 作为参考图。"
    )
    return lines


def _campaign_action_steps(req: ChatRequest, artifact: dict[str, Any], citations: list[dict[str, Any]]) -> list[str]:
    lines = _campaign_story_lines(req, artifact, citations)
    if not lines:
        return []
    steps: list[str] = [
        "先确认主目标：新客到店转化、老会员复购、品牌曝光三者只能选一个主指标，另两个作为副指标。",
        "以“会员挑战日/跨店打卡赛”做首轮试点，范围控制在一到两个高活跃门店，并记录报名、到店、完赛和二次到访。",
    ]
    story_text = "\n".join(lines)
    if re.search(r"(周边|T恤|衣服|徽章|Logo|logo)", story_text):
        steps.append("把 Logo T 恤、徽章或贴纸做成参赛奖励，让参与者在其他攀岩馆和社交平台继续露出品牌。")
    if re.search(r"(护手|护肤|手部|赞助|联名|修复)", story_text):
        steps.append("找护手霜、手部修复或运动恢复类品牌做赞助，把试用装、完赛包和联合打卡放进活动机制。")
    steps.append("方向确认后生成项目书、执行计划、活动海报和周边衣服概念图；若缺 Logo，先让客户上传透明 PNG。")
    return steps[:5]


def _campaign_story_blob(req: ChatRequest, artifact: dict[str, Any], citations: list[dict[str, Any]]) -> str:
    parts = [str(req.message or "")]
    corpus = artifact.get("corpus") or {}
    profile = corpus.get("profile") or {}
    if isinstance(profile, dict):
        parts.extend(str(profile.get(key) or "") for key in ("name", "profile_summary", "customer_summary"))
    for hit in (corpus.get("hits") or [])[:12]:
        if isinstance(hit, dict):
            parts.extend(str(hit.get(key) or "") for key in ("title", "content", "snippet", "source_file"))
    for item in citations[:8]:
        parts.append(str(item.get("snippet") or ""))
    return "\n".join(parts)


def _markers_for_terms(signals: list[dict[str, str]], citations: list[dict[str, Any]], terms: tuple[str, ...]) -> str:
    markers: list[str] = []
    for signal in signals:
        text = str(signal.get("text") or "")
        if any(term.lower() in text.lower() for term in terms):
            for marker in re.findall(r"\[\d+\]", signal.get("markers") or ""):
                if marker not in markers:
                    markers.append(marker)
        if len(markers) >= 2:
            break
    if not markers:
        for item in citations:
            text = str(item.get("snippet") or "")
            if any(term.lower() in text.lower() for term in terms):
                marker = f"[{item.get('marker')}]"
                if marker not in markers:
                    markers.append(marker)
            if len(markers) >= 2:
                break
    return " ".join(markers[:2])


def _structured_answer_v10(req: ChatRequest, decision: RoutingDecision, artifact: dict[str, Any]) -> dict[str, Any]:
    if _is_conversation_answer(req, decision):
        return _structured_chat_answer_v10(req, decision, artifact)
    citations, _ = _build_citations(artifact)
    field_labels = _customer_field_labels(artifact)
    citations = sanitize_citations(citations, field_labels)
    feasibility = artifact.get("feasibility") or {}
    if isinstance(feasibility, dict):
        feasibility = _diversify_feasibility_scores_data(dict(feasibility))
        artifact["feasibility"] = feasibility
    market = artifact.get("market") or {}
    audit = artifact.get("audit") or {}
    auto_analyze = _is_auto_analyze_request(req)
    verdict = verdict_label(feasibility.get("verdict") or "unknown")
    overall = confidence_label(feasibility.get("overall_confidence") or "speculative")
    recommendation, action_plan = _ensure_feasibility_action_plan(req, artifact, citations)
    marker_hint = " ".join(f"[{item['marker']}]" for item in citations[:2])
    lines: list[str] = [
        "**行动方案**",
        f"一句话推荐：{sanitize_customer_text(recommendation, field_labels)} {marker_hint}".rstrip(),
        "",
    ]
    plan_limit = 3 if auto_analyze else 5
    for index, step in enumerate(action_plan[:plan_limit], start=1):
        lines.append(f"{index}. {sanitize_customer_text(step, field_labels)}")
    campaign_lines = _campaign_story_lines(req, artifact, citations)
    if campaign_lines:
        lines.append("")
        lines.extend(sanitize_customer_text(item, field_labels) for item in (campaign_lines[:3] if auto_analyze else campaign_lines))
    lines.extend(
        [
            "",
            f"当前判断：{verdict}；整体置信度：{overall}；审计结论：{audit_label(audit.get('verdict', 'not_run'))}。",
        ]
    )
    if _preset_outcome_requested(req.message) or "preset_outcome_request_rejected" in (feasibility.get("guardrails") or []):
        lines.append("用户要求预设结论或打高分已被忽略；本次只按工作区证据和可复核证据判断。")
    verdict_contract = artifact.get("verdict") or {}
    if verdict_contract.get("revised"):
        blind = verdict_contract.get("blind") or {}
        revised = verdict_contract.get("revised") or {}
        diff_text = "；".join(
            f"{item.get('dim')} {item.get('blind')}→{item.get('revised')}"
            for item in (verdict_contract.get("disagreement") or [])[:3]
        )
        lines.append(
            f"审计后已修订结论：初判“{blind.get('judgment', '待判断')}”，修订后为“{revised.get('judgment', '待判断')}”。{diff_text}"
        )
    elif artifact.get("verdict"):
        lines.append("经独立审计核验，结论未变。")
    if market.get("positioning_note"):
        market_markers = " ".join(f"[{item['marker']}]" for item in citations if item.get("source_type") == "market")
        lines.append(f"外部市场只作为参考：{sanitize_customer_text(market.get('positioning_note'), field_labels)} {market_markers}".rstrip())
    dimensions = feasibility.get("dimensions") or []
    lines.append("")
    lines.append("**评分**")
    if dimensions:
        for dimension in dimensions[:5]:
            markers = _evidence_markers(dimension.get("evidence") or [], citations)
            label = _friendly_dimension_name(dimension.get("name"))
            score = dimension.get("score", "n/a")
            rationale = sanitize_customer_text(dimension.get("rationale") or "证据不足，需要补充验证。", field_labels)
            if auto_analyze:
                rationale = _clip_customer_signal(rationale, 110)
            lines.append(f"- {label} {score}/5：{rationale} {markers}".rstrip())
    else:
        lines.append("- 数据充分度 0/5：当前没有足够的已验证证据形成维度评分。")
    gaps = feasibility.get("gap_list") or []
    lines.append("")
    lines.append("**风险/缺口**")
    if gaps:
        for gap in gaps[: 3 if auto_analyze else 4]:
            lines.append(f"- {sanitize_customer_text(gap, field_labels)}")
    else:
        lines.append("- 暂未发现额外缺口，但仍建议用新样本验证关键假设。")
    lines.append("")
    lines.append("**依据**")
    if citations:
        for item in citations[: 3 if auto_analyze else 5]:
            snippet = sanitize_customer_text(item.get("snippet") or "", field_labels)
            source = sanitize_customer_text(item.get("source_label") or item.get("source_file") or "工作区资料", field_labels)
            lines.append(f"- [{item.get('marker')}] {source}：{_clip_customer_signal(snippet, 120)}")
    else:
        lines.append("- 当前没有可展示的结构化证据引用。")
    markdown = sanitize_customer_text("\n".join(lines).strip(), field_labels)
    return {
        "markdown": markdown,
        "citations": citations,
        "_llm": {"mode": "batch11_p0_fix_action_first_renderer", "response_id": None, "usage": {}},
        "output_contract": _output_contract(decision.intent),
    }


def _structured_corpus_answer_v10(req: ChatRequest, artifact: dict[str, Any]) -> dict[str, Any]:
    citations, _ = _build_citations(artifact)
    field_labels = _customer_field_labels(artifact)
    citations = sanitize_citations(citations, field_labels)
    hits = artifact.get("corpus", {}).get("hits", [])
    signals = _evidence_signals(hits, citations)
    topic = sanitize_customer_text(_evidence_topic_from_hits(hits) or "当前资料机会", field_labels)
    lines: list[str] = ["**综合判断**"]
    if signals:
        lines.extend(sanitize_customer_text(item, field_labels) for item in _corpus_insight_text(topic, signals, len(hits)))
    else:
        lines.append(f"直接回答：当前资料还没有命中足够具体的记录，暂时不能围绕“{topic}”形成有据判断。")
    lines.append("")
    lines.append("**建议动作**")
    if signals:
        for index, item in enumerate(_corpus_action_steps(signals, citations)[:4], start=1):
            action = sanitize_customer_text(item, field_labels).strip()
            lines.append(f"{index}. {action}")
    else:
        lines.append("1. 先补充与目标用户、场景、预算或活动结果相关的资料，再生成更具体方案。")
    campaign_lines = _campaign_story_lines(req, artifact, citations)
    if campaign_lines:
        lines.append("")
        lines.extend(sanitize_customer_text(item, field_labels) for item in campaign_lines)
    markdown = sanitize_customer_text("\n".join(lines).strip(), field_labels)
    return {
        "markdown": markdown,
        "citations": citations,
        "_llm": {"mode": "batch10_corpus_contract_renderer", "response_id": None, "usage": {}},
        "output_contract": _output_contract("corpus_qa"),
    }


def _combined_markers(signals: list[dict[str, str]], limit: int = 3) -> str:
    markers: list[str] = []
    for signal in signals:
        for marker in re.findall(r"\[\d+\]", signal.get("markers") or ""):
            if marker not in markers:
                markers.append(marker)
            if len(markers) >= limit:
                return " ".join(markers)
    return " ".join(markers)


def _corpus_insight_text(topic: str, signals: list[dict[str, str]], hit_count: int) -> list[str]:
    first = signals[0]
    second = signals[1] if len(signals) > 1 else first
    third = signals[2] if len(signals) > 2 else second
    fourth = signals[3] if len(signals) > 3 else third
    lead_markers = _combined_markers([first, second, third], 3)
    tail_markers = _combined_markers([fourth, third, second], 2)
    return [
        f"直接回答：这批资料更支持先从“{topic}”切入做小范围验证；判断依据不是单条记录，而是 {hit_count} 条资料里同时出现的场景、活动和转化线索。 {lead_markers}".rstrip(),
        f"综合看，{first['text']}，并且{second['text']}，说明可先把活动、权益或触达设计落在已有门店和人群信号上。{third['text']}，可以作为复盘指标的来源，避免只看曝光而不看到访、转化或复购。 {lead_markers}".rstrip(),
        f"落地时建议把{fourth['text']}作为分层对照，先验证哪类人群、门店或合作权益真正带来结果，再决定是否扩大投入。 {tail_markers}".rstrip(),
    ]


def _corpus_action_steps(signals: list[dict[str, str]], citations: list[dict[str, Any]]) -> list[str]:
    first = signals[0]
    second = signals[1] if len(signals) > 1 else first
    third = signals[2] if len(signals) > 2 else second
    fourth = signals[3] if len(signals) > 3 else third
    return [
        f"先用{first['text']}确定首轮试点场景和目标人群，范围控制在可复盘的小样本内 {first['markers']}".rstrip(),
        f"把{second['text']}转成活动钩子、合作权益或触达文案，确保用户知道为什么参与 {second['markers']}".rstrip(),
        f"用{third['text']}定义复盘指标，至少记录触达、参与、转化/复购、成本和用户反馈 {third['markers']}".rstrip(),
        f"再用{fourth['text']}做分层复盘，找出真正拉动结果的门店、人群或内容 {fourth['markers']}".rstrip(),
        f"本次证据引用共 {len(citations)} 条；新增资料后应重新生成结论。",
    ]


async def _answer_event_stream(payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def worker() -> None:
        try:
            for item in stream_grounded_answer(payload):
                loop.call_soon_threadsafe(queue.put_nowait, item)
        except Exception as exc:
            loop.call_soon_threadsafe(
                queue.put_nowait,
                {"type": "error", "message": _clean_text(exc, 500)},
            )
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=worker, name="dataforge-answer-stream", daemon=True).start()
    while True:
        try:
            item = await asyncio.wait_for(queue.get(), timeout=8)
        except asyncio.TimeoutError:
            yield {
                "type": "progress",
                "agent": "df-answer-writer",
                "name": "stream_grounded_answer",
                "status": "running",
            }
            continue
        if item is None:
            break
        yield item


class _ChatStreamUnavailable(Exception):
    """流式没拿到有效内容，且尚未对外发出任何 delta —— 安全回退到同步渲染。"""


async def _stream_chat_answer_frames(
    req: ChatRequest,
    decision: RoutingDecision,
    artifact: dict[str, Any],
    conversation_id: str,
    state: dict[str, Any],
) -> AsyncIterator[str]:
    """真 token 流式的会话作答：边生成边发 delta，结束后用「保留结构」的清洗版落到 final。
    一旦发出过 delta 就绝不抛异常（避免和同步回退重复出文）。"""
    citations, _ = _build_citations(artifact)
    field_labels = _customer_field_labels(artifact)
    citations = sanitize_citations(citations, field_labels)
    feasibility = artifact.get("feasibility") or {}
    if not isinstance(feasibility, dict):
        feasibility = {}
    payload = _grounded_chat_payload(req, artifact, citations, feasibility, field_labels)

    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def worker() -> None:
        try:
            for item in stream_grounded_chat_answer(payload):
                loop.call_soon_threadsafe(queue.put_nowait, item)
        except Exception as exc:
            loop.call_soon_threadsafe(queue.put_nowait, {"type": "error", "message": str(exc)[:300]})
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=worker, name="df-chat-stream", daemon=True).start()

    raw_parts: list[str] = []
    meta: dict[str, Any] = {"mode": "grounded_chat_stream", "response_id": None, "usage": {}}
    emitted = False
    while True:
        try:
            item = await asyncio.wait_for(queue.get(), timeout=25)
        except asyncio.TimeoutError:
            break
        if item is None:
            break
        kind = item.get("type")
        if kind == "delta":
            delta = item.get("delta") or ""
            if delta:
                raw_parts.append(delta)
                emitted = True
                yield _frame("answer_delta", {"delta": delta}, conversation_id)
                await asyncio.sleep(0)
        elif kind == "meta":
            meta = {key: value for key, value in item.items() if key != "type"} or meta

    raw = "".join(raw_parts).strip()
    if not emitted or len(raw) < 12:
        # 还没对外发过任何 delta → 安全回退
        raise _ChatStreamUnavailable()

    # 发过 delta 之后：只做清洗与落库，任何异常都吞掉并用 raw 兜底，绝不再抛（防重复出文）
    try:
        finalized = _finalize_grounded_chat_text(raw, field_labels)
        md, is_plan = finalized if finalized else (raw, _looks_like_plan_markdown(raw))
        offer = _produce_offer_for(req, feasibility, plan=is_plan)
        if offer:
            artifact["produce_offer"] = offer
        meta = {**meta, "mode": "grounded_chat_plan_stream" if is_plan else "grounded_chat_stream"}
    except Exception:
        md, meta = raw, {**meta, "mode": "grounded_chat_stream"}
    state["text"] = md
    state["meta"] = meta
    artifact["answer"] = {"markdown": md, "text": md, "citations": citations, "_llm": meta}
    artifact["citations"] = citations
    artifact["output_contract"] = _chat_output_contract(decision.intent)
    yield _frame("model_response", {"agent": "df-answer-writer", **meta}, conversation_id)


async def _stream_answer_frames(
    req: ChatRequest,
    decision: RoutingDecision,
    artifact: dict[str, Any],
    conversation_id: str,
    state: dict[str, Any],
) -> AsyncIterator[str]:
    # 会话作答优先走真流式；失败（未发出任何 delta）再回退到同步渲染。
    if _is_conversation_answer(req, decision):
        try:
            async for frame in _stream_chat_answer_frames(req, decision, artifact, conversation_id, state):
                yield frame
            return
        except _ChatStreamUnavailable:
            pass
        except Exception:
            pass
    answer = _structured_answer_v10(req, decision, artifact)
    text = _customer_text(answer.get("markdown") or _final_text(decision, artifact), artifact)
    meta = dict(answer.get("_llm") or {})
    citations = sanitize_citations(answer.get("citations", []), _customer_field_labels(artifact))
    for delta in _chunk_text(text, 96):
        if delta:
            yield _frame("answer_delta", {"delta": delta}, conversation_id)
            await asyncio.sleep(0)
    state["text"] = text
    state["meta"] = meta
    artifact["answer"] = {"markdown": text, "text": text, "citations": citations, "_llm": meta}
    artifact["citations"] = citations
    artifact["output_contract"] = answer.get("output_contract") or _output_contract(decision.intent)
    yield _frame("model_response", {"agent": "df-answer-writer", **meta}, conversation_id)
    return

    payload = _answer_payload(req, decision, artifact)
    parts: list[str] = []
    pending_delta = ""
    meta: dict[str, Any] = {}
    stream_error: str | None = None

    async def flush_delta() -> AsyncIterator[str]:
        nonlocal pending_delta
        if pending_delta:
            chunk = pending_delta
            pending_delta = ""
            yield _frame("answer_delta", {"delta": chunk}, conversation_id)
            await asyncio.sleep(0)

    async for item in _answer_event_stream(payload):
        item_type = item.get("type")
        if item_type == "delta":
            delta = str(item.get("delta") or "")
            if delta:
                parts.append(delta)
                pending_delta += delta
                if len(pending_delta) >= 96:
                    async for frame in flush_delta():
                        yield frame
        elif item_type == "meta":
            async for frame in flush_delta():
                yield frame
            meta = {key: value for key, value in item.items() if key != "type"}
        elif item_type == "error":
            async for frame in flush_delta():
                yield frame
            stream_error = str(item.get("message") or "answer stream failed")
        elif item_type == "progress":
            async for frame in flush_delta():
                yield frame
            yield _frame("progress", {key: value for key, value in item.items() if key != "type"}, conversation_id)
    async for frame in flush_delta():
        yield frame

    if not "".join(parts).strip():
        fallback = _final_text(decision, artifact)
        for delta in _chunk_text(fallback):
            if delta:
                parts.append(delta)
                yield _frame("answer_delta", {"delta": delta}, conversation_id)
                await asyncio.sleep(0)
        meta = {
            "mode": "answer_fallback_template",
            "response_id": None,
            "usage": {},
            "error": stream_error,
        }
    elif stream_error:
        suffix = "\n\n（流式回答中断，以上内容为已完成的模型输出；结构化产物已保留在本次结果中。）"
        for delta in _chunk_text(suffix):
            parts.append(delta)
            yield _frame("answer_delta", {"delta": delta}, conversation_id)
            await asyncio.sleep(0)
        meta = meta or {"mode": "responses_stream_partial_error", "response_id": None, "usage": {}}
        meta["error"] = stream_error

    text = "".join(parts)
    state["text"] = text
    state["meta"] = meta
    artifact["answer"] = {"text": text, "_llm": meta}
    yield _frame("model_response", {"agent": "df-answer-writer", **meta}, conversation_id)


async def _progress_frames(
    task: asyncio.Task[Any],
    conversation_id: str,
    agent: str,
    name: str,
    *,
    interval: int = 8,
    extra: dict[str, Any] | None = None,
) -> AsyncIterator[str]:
    payload = {"agent": agent, "name": name, "status": "running"}
    if extra:
        payload.update(extra)
    while not task.done():
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=interval)
        except asyncio.TimeoutError:
            yield _frame("progress", payload, conversation_id)


async def _producer_frames(artifact: dict[str, Any], conversation_id: str) -> AsyncIterator[str]:
    yield _frame("role_change", {"agent": "df-producer"}, conversation_id)
    reference_count = len(artifact.get("reference_images") or workspace_reference_images(str(artifact.get("workspace_id") or "")))
    yield _frame("tool_call", {"agent": "df-producer", "name": "render_pdf_report", "args": {"template": "project_proposal"}}, conversation_id)
    yield _frame("tool_call", {"agent": "df-producer", "name": "generate_image", "args": {"size": "1024x1024", "reference_count": reference_count}}, conversation_id)
    yield _frame("tool_call", {"agent": "df-producer", "name": "narrate_summary", "args": {"voice": "zh-CN-XiaoxiaoNeural"}}, conversation_id)
    producer_task = asyncio.create_task(run_in_threadpool(_run_producer, artifact))
    while not producer_task.done():
        try:
            artifact["proposal"] = await asyncio.wait_for(asyncio.shield(producer_task), timeout=8)
        except asyncio.TimeoutError:
            yield _frame(
                "progress",
                {"agent": "df-producer", "name": "produce_artifacts", "status": "running"},
                conversation_id,
            )
    if "proposal" not in artifact:
        artifact["proposal"] = await producer_task
    yield _frame(
        "tool_result",
        {
            "agent": "df-producer",
            "name": "render_pdf_report",
            "bytes": artifact["proposal"]["pdf"].get("bytes"),
            "mode": artifact["proposal"]["pdf"].get("mode"),
            "artifact_url": artifact["proposal"]["artifact_urls"].get("pdf"),
        },
        conversation_id,
    )
    yield _frame(
        "tool_result",
        {
            "agent": "df-producer",
            "name": "generate_image",
            "bytes": artifact["proposal"]["concept_image"].get("bytes"),
            "mode": artifact["proposal"]["concept_image"].get("mode"),
            "artifact_url": artifact["proposal"]["artifact_urls"].get("concept_image"),
        },
        conversation_id,
    )
    yield _frame(
        "tool_result",
        {
            "agent": "df-producer",
            "name": "narrate_summary",
            "bytes": artifact["proposal"]["audio_summary"].get("bytes"),
            "mode": artifact["proposal"]["audio_summary"].get("mode"),
            "artifact_url": artifact["proposal"]["artifact_urls"].get("audio_summary"),
        },
        conversation_id,
    )


def _last_assistant_text(history: list[dict[str, Any]]) -> str:
    for item in reversed(history):
        if item.get("role") == "assistant":
            text = _clean_text(item.get("text"), 1600)
            if text:
                return text
    return ""


def _fast_followup_reply(req: ChatRequest, history: list[dict[str, Any]]) -> dict[str, Any]:
    previous = _last_assistant_text(history)
    if not previous:
        return {
            "text": "我需要先有上一轮分析结果，才能按你的要求调整。请先完成一次分析，或把要调整的内容贴给我。",
            "mode": "fast_followup_missing_context",
            "response_id": None,
            "usage": {},
        }
    current = _intent_message(req.message)
    compact_previous = _clean_text(previous, 220)
    history_artifact = {
        "workspace_id": req.workspace_id,
        "_conversation_history": _compact_history(history),
    }
    topic = _last_history_user_topic(history_artifact) or "上一轮方案"
    constraint = _chat_constraint_phrase(current)
    if constraint == "预算减半":
        text = (
            f"可以，基于上一轮“{topic}”的方向，预算减半时不要扩大投放面。"
            "建议保留最能验证需求的核心环节，把范围缩到 1-2 个门店/客群，先看报名、到场、转化和单次成本；"
            "如果这些指标仍成立，再恢复到完整方案。"
        )
    elif re.search(r"(证据|依据|为什么|最强|最弱)", current):
        text = (
            f"基于上一轮回答，先看支撑“{topic}”的工作区证据是否同时覆盖需求、执行条件和转化结果。"
            f"当前可继续追问具体证据面板；上一轮核心判断是：{compact_previous}"
        )
    else:
        text = (
            f"可以沿用上一轮“{topic}”的主方向，但把这次新增约束先当成范围调整，而不是重跑完整分析。"
            f"我的建议是先保留核心验证指标，再缩小对象、预算或周期；上一轮判断摘要：{compact_previous}"
        )
    return {
        "text": text,
        "mode": "fast_followup_renderer",
        "response_id": None,
        "usage": {},
    }


def _lightweight_reply(req: ChatRequest, decision: RoutingDecision, history: list[dict[str, Any]]) -> dict[str, Any]:
    if decision.intent == "followup_edit" and _looks_like_context_followup(req.message):
        return _fast_followup_reply(req, history)
    context = workspace_context(req.workspace_id)
    payload = {
        "workspace_id": req.workspace_id,
        "workspace_context": context,
        "current_message": req.message,
        "conversation_history": _compact_history(history),
        "routing": decision.model_dump(),
        "style_nonce": uuid.uuid4().hex[:8],
    }
    if decision.intent == "followup_edit":
        previous = _last_assistant_text(history)
        if not previous:
            return {
                "text": "我需要先有上一轮分析结果，才能按你的要求改写。请先完成一次分析，或把要改写的内容贴给我。",
                "mode": "followup_missing_context",
                "response_id": None,
                "usage": {},
            }
        payload["previous_assistant_answer"] = previous
        return run_followup_rewrite(payload)
    return run_coordinator_direct_reply(payload)


async def _emit_lightweight_final(
    req: ChatRequest,
    decision: RoutingDecision,
    artifact: dict[str, Any],
    conv_id: str,
    history: list[dict[str, Any]],
) -> AsyncIterator[str]:
    result = await run_in_threadpool(_lightweight_reply, req, decision, history)
    fast_path = (artifact.get("routing_meta") or {}).get("fast_path")
    field_labels = {} if fast_path else _workspace_field_labels(req.workspace_id)
    text = sanitize_customer_text(
        _strip_raw_ref_leaks(str(result.get("text") or "").strip() or "我可以继续帮你处理当前工作区。"),
        field_labels,
    )
    text = _ensure_next_step_hint(text)
    for delta in _chunk_text(text, 96):
        yield _frame("answer_delta", {"delta": delta}, conv_id)
        await asyncio.sleep(0)
    meta = {key: result.get(key) for key in ("mode", "response_id", "usage", "error") if key in result}
    artifact["answer"] = {
        "markdown": text,
        "text": text,
        "citations": [],
        "confidence": "speculative",
        "confidence_label": confidence_label("speculative"),
        "_llm": meta,
    }
    artifact["output_contract"] = _chat_output_contract(decision.intent)
    final_payload = {
        "text": text,
        "routing": decision.model_dump(),
        "artifact": artifact,
        "output_contract": artifact["output_contract"],
        "confidence": "speculative",
        "confidence_label": confidence_label("speculative"),
    }
    yield _frame("model_response", {"agent": "df-coordinator", **meta}, conv_id)
    yield _frame("final", final_payload, conv_id)
    asyncio.create_task(
        _persist_chat_completion(
            conv_id,
            req.workspace_id,
            text,
            decision.intent,
            decision.intent,
            final_payload,
            artifact,
        )
    )


async def _persist_chat_completion(
    conversation_id: str,
    workspace_id: str,
    text: str,
    verdict: str,
    status: str,
    final_payload: dict[str, Any],
    artifact: dict[str, Any],
) -> None:
    try:
        citations = artifact.get("citations") or (artifact.get("answer") or {}).get("citations") or []
        await run_in_threadpool(_persist_assistant_message, conversation_id, workspace_id, text, verdict, citations)
        await run_in_threadpool(complete_run, conversation_id, status=status, final=final_payload, artifact=artifact)
    except Exception:
        return


async def orchestrate_chat(req: ChatRequest) -> AsyncIterator[str]:
    conv_id = req.conversation_id or str(uuid.uuid4())
    new_conversation = req.conversation_id is None
    history = conversation_context(req.conversation_id, limit=20) if req.conversation_id else []
    working_req = _request_with_history(req, history)
    artifact: dict[str, Any] = {
        "workspace_id": req.workspace_id,
        "conversation_id": conv_id,
        "_conversation_history": _compact_history(history),
    }
    start_run(conv_id, req.workspace_id, req.message)
    yield _frame("ready", {"conversation_id": conv_id, "workspace_id": req.workspace_id}, conv_id)
    yield _frame("user", {"text": req.message}, conv_id)
    await run_in_threadpool(_persist_user_message, conv_id, req.workspace_id, req.message, new_conversation)

    # 负责任 AI：先用 Azure AI Content Safety 过一遍用户输入（jailbreak 注入 + 有害类别），
    # 命中就安全拒答、不进入多 Agent 链。服务异常时 fail-open（screen_input 内部兜底），不影响正常使用。
    screen = await run_in_threadpool(content_safety.screen_input, req.message)
    if screen.get("checked") and not screen.get("allowed"):
        yield _frame("content_safety", {"blocked": True, "jailbreak": screen.get("jailbreak"), "categories": screen.get("categories", [])}, conv_id)
        refusal = content_safety.refusal_message(screen)
        artifact["answer"] = {"markdown": refusal, "text": refusal, "citations": [], "_llm": {"mode": "content_safety_block"}}
        artifact["output_contract"] = _chat_output_contract("smalltalk_or_meta")
        final_payload = {
            "text": refusal,
            "routing": {"intent": "content_safety_block"},
            "artifact": artifact,
            "content_safety": {"blocked": True, "jailbreak": screen.get("jailbreak"), "categories": screen.get("categories", [])},
        }
        await run_in_threadpool(_persist_assistant_message, conv_id, req.workspace_id, refusal, "content_safety_block")
        complete_run(conv_id, status="content_safety_block", final=final_payload, artifact=artifact)
        yield _frame("final", final_payload, conv_id)
        return

    fast_route = await run_in_threadpool(_preflight_fast_route, req, history)
    if fast_route:
        decision, route_meta = fast_route
    else:
        decision, route_meta = await run_in_threadpool(_coordinator, working_req, history)
    if _suppress_auto_analyze_producer(req, decision):
        route_meta = {**route_meta, "producer_suppressed": "auto_analyze"}
    artifact["routing"] = decision.model_dump()
    artifact["routing_meta"] = route_meta
    producer_requested = "df-producer" in decision.experts
    yield _frame(
        "route",
        {
            "intent": decision.intent,
            "reason": decision.reason,
            "experts": decision.experts,
            "output_mode": decision.output_mode,
            "needs_clarification": decision.needs_clarification,
            **route_meta,
        },
        conv_id,
    )
    yield _frame("plan", decision.model_dump(), conv_id)
    if getattr(req, "playbook", None):
        try:
            profile = workspace_context(req.workspace_id).get("profile") or {}
            pm_skill = playbook_suggestion(req.playbook, profile if isinstance(profile, dict) else {})
            provenance = _tool_provenance(
                "pm_playbook_suggest",
                f"playbook={pm_skill.get('label')}",
                source_type="pm_skill",
                confidence="speculative",
            )
            artifact["pm_skill"] = pm_skill | {"provenance": provenance}
            yield _frame(
                "tool_result",
                {
                    "agent": "df-coordinator",
                    "name": "pm_playbook_suggest",
                    "playbook": pm_skill.get("label"),
                    "sections": pm_skill.get("artifact_sections", []),
                    "provenance": provenance,
                },
                conv_id,
            )
        except Exception as exc:
            artifact["pm_skill"] = {"error": _clean_text(exc, 200)}
    if decision.needs_clarification:
        guidance = await run_in_threadpool(_clarify_guidance, req, decision, conv_id)
        clarify = _structured_clarify(req, guidance)
        decision.clarifying_question = clarify["question"]
        artifact["clarify"] = clarify
        artifact["output_contract"] = _output_contract("clarify_needed")
        clarify_payload = {
            "question": clarify["question"],
            "clarify": clarify,
            "options": clarify["options"],
            "allow_multi": clarify["allow_multi"],
            "allow_freeform": clarify["allow_freeform"],
            "confidence": "speculative",
            "confidence_label": confidence_label("speculative"),
            "reason": decision.reason,
            "mode": guidance.get("mode"),
            "response_id": guidance.get("response_id"),
            "usage": guidance.get("usage") or {},
            "output_contract": artifact["output_contract"],
        }
        frame = _frame("clarify", clarify_payload, conv_id)
        await run_in_threadpool(
            _persist_assistant_message,
            conv_id,
            req.workspace_id,
            str(decision.clarifying_question or ""),
            "clarify",
        )
        complete_run(
            conv_id,
            status="clarify",
            final={"clarify": clarify_payload, "routing": decision.model_dump(), "artifact": artifact},
            artifact=artifact,
        )
        yield frame
        return

    if decision.intent in {"smalltalk_or_meta", "followup_edit"}:
        async for frame in _emit_lightweight_final(req, decision, artifact, conv_id, history):
            yield frame
        return

    if "df-corpus-analyst" in decision.experts:
        fast_corpus_path = route_meta.get("fast_path") == "corpus_qa"
        corpus_query = _intent_message(req.message) if fast_corpus_path else working_req.message
        corpus_req = req.model_copy(update={"message": corpus_query})
        corpus_top_k = 5 if fast_corpus_path else 8
        corpus_use_vector = not fast_corpus_path
        yield _frame("role_change", {"agent": "df-corpus-analyst"}, conv_id)
        yield _frame(
            "tool_call",
            {
                "agent": "df-corpus-analyst",
                "name": "search_pack_context",
                "args": {"workspace_id": req.workspace_id, "query": corpus_query, "top_k": corpus_top_k, "use_vector": corpus_use_vector},
            },
            conv_id,
        )
        artifact["corpus"] = await run_in_threadpool(_run_corpus_analyst, corpus_req, corpus_top_k, corpus_use_vector)
        retrieval_modes = sorted({str(hit.get("retrieval_mode") or "unknown") for hit in artifact["corpus"]["hits"]})
        yield _frame(
            "tool_result",
            {
                "agent": "df-corpus-analyst",
                "name": "search_pack_context",
                "count": len(artifact["corpus"]["hits"]),
                "retrieval_modes": retrieval_modes,
            },
            conv_id,
        )

    early_market_task: asyncio.Task[Any] | None = None
    if "df-market-researcher" in decision.experts and artifact.get("corpus", {}).get("hits"):
        market_seed = {
            "workspace_id": artifact.get("workspace_id"),
            "conversation_id": artifact.get("conversation_id"),
            "corpus": artifact.get("corpus", {}),
        }
        early_market_task = asyncio.create_task(run_in_threadpool(_run_market_researcher, market_seed))

    if "df-feasibility-analyst" in decision.experts:
        yield _frame("role_change", {"agent": "df-feasibility-analyst"}, conv_id)
        try:
            feasibility_task = asyncio.create_task(run_in_threadpool(_run_feasibility_analyst, working_req, artifact))
            async for frame in _progress_frames(
                feasibility_task,
                conv_id,
                "df-feasibility-analyst",
                "analyze_feasibility",
            ):
                yield frame
            artifact["feasibility"] = await feasibility_task
            artifact["_blind_feasibility"] = json.loads(json.dumps(artifact["feasibility"], ensure_ascii=False))
            yield _frame(
                "blind_verdict",
                {
                    "agent": "df-feasibility-analyst",
                    "verdict": make_blind_verdict(artifact["feasibility"]),
                    "dimensions": [
                        {
                            "dim": _friendly_dimension_name(item.get("name")),
                            "score": item.get("score"),
                            "confidence": item.get("confidence"),
                        }
                        for item in (artifact["feasibility"].get("dimensions") or [])
                    ],
                },
                conv_id,
            )
            cache_info = (artifact["feasibility"].get("_llm") or {}).get("cache")
            if cache_info:
                yield _frame("cache", {"agent": "df-feasibility-analyst", **cache_info}, conv_id)
            for event, data in _agent_tool_events("df-feasibility-analyst", artifact["feasibility"].get("_llm", {})):
                yield _frame(event, data, conv_id)
            if artifact["feasibility"]["_llm"].get("response_id"):
                yield _frame("model_response", {"agent": "df-feasibility-analyst", **artifact["feasibility"]["_llm"]}, conv_id)
        except Exception as exc:
            error_payload = {"agent": "df-feasibility-analyst", "message": str(exc)}
            frame = _frame("error", error_payload, conv_id)
            await run_in_threadpool(_persist_assistant_message, conv_id, req.workspace_id, str(exc), "error")
            complete_run(conv_id, status="error", final=error_payload, artifact=artifact)
            yield frame
            return

    if "df-market-researcher" in decision.experts:
        yield _frame("role_change", {"agent": "df-market-researcher"}, conv_id)
        feasibility = artifact.get("feasibility", {})
        category = str(feasibility.get("opportunity_id") or "workspace product").replace("-", " ")
        if feasibility.get("_llm", {}).get("mode") == "empty_evidence_deterministic":
            artifact["market"] = {
                "competitors": [],
                "positioning_note": "Market lookup skipped because no workspace evidence matched the request.",
                "mode": "skipped_empty_evidence",
                "tool_provenance": {
                    "market_lookup": _tool_provenance(
                        "market_lookup",
                        category,
                        fallback="Skipped because no workspace evidence matched the request.",
                    )
                },
            }
            yield _frame(
                "tool_result",
                {
                    "agent": "df-market-researcher",
                    "name": "market_lookup",
                    "count": 0,
                    "skipped": "empty_evidence",
                    "provenance": artifact["market"]["tool_provenance"]["market_lookup"],
                },
                conv_id,
            )
            category = ""
        if not category:
            pass
        else:
            yield _frame(
                "tool_call",
                {
                    "agent": "df-market-researcher",
                    "name": "market_lookup",
                    "args": {"category": category, "keywords": category.split()[:4]},
                    "protocol": "foundry_agent_mcp",
                    "server_label": "dataforge_market",
                },
                conv_id,
            )
            yield _frame(
                "tool_call",
                {
                    "agent": "df-market-researcher",
                    "name": "foundry_native_web_search",
                    "args": {
                        "query": category,
                        "preferred": "bing_grounding",
                        "fallbacks": ["web_search_preview", "web_search"],
                        "confidence": "market_inferred",
                    },
                },
                conv_id,
            )
            try:
                market_task = early_market_task or asyncio.create_task(run_in_threadpool(_run_market_researcher, artifact))
                async for frame in _progress_frames(
                    market_task,
                    conv_id,
                    "df-market-researcher",
                    "research_market",
                ):
                    yield frame
                artifact["market"] = await market_task
                market_provenance = artifact["market"].get("tool_provenance", {})
                yield _frame(
                    "tool_result",
                    {
                        "agent": "df-market-researcher",
                        "name": "market_lookup",
                        "count": len(artifact["market"]["competitors"]),
                        "provenance": market_provenance.get("market_lookup"),
                    },
                    conv_id,
                )
                yield _frame(
                    "tool_result",
                    {
                        "agent": "df-market-researcher",
                        "name": "foundry_native_web_search",
                        "count": len(artifact["market"].get("external_findings", [])),
                        "sources": artifact["market"].get("sources", []),
                        "mode": artifact["market"].get("_llm", {}).get("mode"),
                        "verification": artifact["market"].get("_llm", {}).get("verification"),
                        "error": artifact["market"].get("_llm", {}).get("error"),
                        "provenance": market_provenance.get("foundry_native_web_search"),
                    },
                    conv_id,
                )
            except Exception as exc:
                artifact["market"] = {
                    "competitors": [],
                    "positioning_note": "Market lookup unavailable.",
                    "error": str(exc),
                    "tool_provenance": {
                        "market_lookup": _tool_provenance("market_lookup", category, error=str(exc), fallback="Market lookup failed gracefully."),
                        "foundry_native_web_search": _tool_provenance(
                            "foundry_native_web_search",
                            category,
                            source_type="foundry_web",
                            confidence="market_inferred",
                            error=str(exc),
                            fallback="Foundry web search failed gracefully.",
                        ),
                    },
                }
                yield _frame(
                    "tool_result",
                    {"agent": "df-market-researcher", "name": "market_lookup", "count": 0, "error": str(exc), "provenance": artifact["market"]["tool_provenance"]["market_lookup"]},
                    conv_id,
                )
                yield _frame(
                    "tool_result",
                    {
                        "agent": "df-market-researcher",
                        "name": "foundry_native_web_search",
                        "count": 0,
                        "error": str(exc),
                        "provenance": artifact["market"]["tool_provenance"]["foundry_native_web_search"],
                    },
                    conv_id,
                )

    if "df-auditor" in decision.experts:
        yield _frame("role_change", {"agent": "df-auditor"}, conv_id)
        try:
            audit_task = asyncio.create_task(run_in_threadpool(_audit_artifact, working_req, artifact))
            async for frame in _progress_frames(
                audit_task,
                conv_id,
                "df-auditor",
                "audit_report",
            ):
                yield frame
            audit, audit_meta = await audit_task
            for event, data in _agent_tool_events("df-auditor", audit_meta):
                yield _frame(event, data, conv_id)
            if audit_meta.get("response_id"):
                yield _frame("model_response", {"agent": "df-auditor", **audit_meta}, conv_id)
        except Exception as exc:
            error_payload = {"agent": "df-auditor", "message": str(exc)}
            frame = _frame("error", error_payload, conv_id)
            await run_in_threadpool(_persist_assistant_message, conv_id, req.workspace_id, str(exc), "error")
            complete_run(conv_id, status="error", final=error_payload, artifact=artifact)
            yield frame
            return
        yield _frame("audit", audit.model_dump(), conv_id)
    else:
        audit = AuditVerdict(verdict="pass", issues=[], target_expert=None)

    if audit.verdict == "revise" and audit.target_expert == "df-feasibility-analyst":
        yield _frame("role_change", {"agent": "df-feasibility-analyst", "revision": 1}, conv_id)
        try:
            previous_feasibility = artifact.get("feasibility") or {}
            revision_task = asyncio.create_task(run_in_threadpool(_run_feasibility_analyst, working_req, artifact, audit.model_dump()))
            async for frame in _progress_frames(
                revision_task,
                conv_id,
                "df-feasibility-analyst",
                "revise_feasibility",
                extra={"revision": 1},
            ):
                yield frame
            revision_feasibility = await revision_task
            revision_meta = revision_feasibility.get("_llm", {})
            for event, data in _agent_tool_events("df-feasibility-analyst", revision_meta):
                yield _frame(event, {"revision": 1, **data}, conv_id)
            if revision_meta.get("response_id"):
                yield _frame("model_response", {"agent": "df-feasibility-analyst", "revision": 1, **revision_meta}, conv_id)
            if (
                revision_meta.get("mode") == "fallback_after_agent_error"
                and previous_feasibility
                and previous_feasibility.get("_llm", {}).get("mode") != "fallback_after_agent_error"
            ):
                preserved = {**previous_feasibility}
                preserved_meta = dict(preserved.get("_llm") or {})
                warnings = list(preserved_meta.get("evidence_warnings") or [])
                warnings.append("revision_failed_preserved_previous_feasibility")
                preserved_meta["evidence_warnings"] = warnings
                preserved_meta["revision_warning"] = _clean_text(revision_meta.get("error"), 500)
                preserved["_llm"] = preserved_meta
                artifact["feasibility"] = preserved
            else:
                artifact["feasibility"] = revision_feasibility
            yield _frame("role_change", {"agent": "df-auditor", "revision": 1}, conv_id)
            revision_audit_task = asyncio.create_task(run_in_threadpool(_audit_artifact, working_req, artifact))
            async for frame in _progress_frames(
                revision_audit_task,
                conv_id,
                "df-auditor",
                "audit_revision",
                extra={"revision": 1},
            ):
                yield frame
            audit, audit_meta = await revision_audit_task
            for event, data in _agent_tool_events("df-auditor", audit_meta):
                yield _frame(event, {"revision": 1, **data}, conv_id)
            if audit_meta.get("response_id"):
                yield _frame("model_response", {"agent": "df-auditor", "revision": 1, **audit_meta}, conv_id)
            yield _frame("audit", audit.model_dump(), conv_id)
        except Exception as exc:
            yield _frame("error", {"agent": "revision-loop", "message": str(exc)}, conv_id)
            verdict_contract = _apply_audit_and_verdict_contract(artifact, audit)
            if verdict_contract.get("revised"):
                yield _frame("revised_verdict", verdict_contract, conv_id)
            answer_state: dict[str, Any] = {}
            async for frame in _stream_answer_frames(working_req, decision, artifact, conv_id, answer_state):
                yield frame
            if producer_requested:
                async for frame in _producer_frames(artifact, conv_id):
                    yield frame
            summary = _customer_text(answer_state.get("text") or _final_text(decision, artifact), artifact)
            artifact.setdefault("output_contract", _output_contract(decision.intent))
            final_payload = {
                "text": summary,
                "routing": decision.model_dump(),
                "artifact": artifact,
                "output_contract": artifact["output_contract"],
            }
            frame = _frame("final", final_payload, conv_id)
            await run_in_threadpool(
                _persist_assistant_message,
                conv_id,
                req.workspace_id,
                summary,
                _artifact_verdict(artifact, "completed_with_revision_error"),
            )
            if decision.intent == "feasibility_analysis":
                await run_in_threadpool(_persist_last_analysis, req.workspace_id, final_payload)
            complete_run(conv_id, status="completed_with_revision_error", final=final_payload, artifact=artifact)
            yield frame
            return

    verdict_contract = _apply_audit_and_verdict_contract(artifact, audit)
    if verdict_contract.get("revised"):
        yield _frame("revised_verdict", verdict_contract, conv_id)
    answer_state: dict[str, Any] = {}
    async for frame in _stream_answer_frames(working_req, decision, artifact, conv_id, answer_state):
        yield frame
    if producer_requested:
        async for frame in _producer_frames(artifact, conv_id):
            yield frame
    summary = _customer_text(answer_state.get("text") or _final_text(decision, artifact), artifact)
    artifact.setdefault("output_contract", _output_contract(decision.intent))
    final_payload = {
        "text": summary,
        "routing": decision.model_dump(),
        "artifact": artifact,
        "output_contract": artifact["output_contract"],
    }
    frame = _frame("final", final_payload, conv_id)
    if decision.intent != "feasibility_analysis":
        yield frame
        asyncio.create_task(
            _persist_chat_completion(
                conv_id,
                req.workspace_id,
                summary,
                _artifact_verdict(artifact, "completed"),
                "completed",
                final_payload,
                artifact,
            )
        )
        return
    await run_in_threadpool(
        _persist_assistant_message,
        conv_id,
        req.workspace_id,
        summary,
        _artifact_verdict(artifact, "completed"),
    )
    if decision.intent == "feasibility_analysis":
        await run_in_threadpool(_persist_last_analysis, req.workspace_id, final_payload)
    complete_run(conv_id, status="completed", final=final_payload, artifact=artifact)
    yield frame


def _final_text(decision: RoutingDecision, artifact: dict[str, Any]) -> str:
    if decision.intent == "corpus_qa":
        hits = artifact.get("corpus", {}).get("hits", [])
        titles = ", ".join(hit.get("title", "Untitled") for hit in hits[:3])
        return f"\u8d44\u6599\u68c0\u7d22\u5b8c\u6210\uff0c\u547d\u4e2d {len(hits)} \u6761\u3002\u4e3b\u8981\u6765\u6e90\uff1a{titles}\u3002"
    feasibility = artifact.get("feasibility", {})
    if feasibility.get("_llm", {}).get("mode") == "empty_evidence_deterministic":
        gaps = "; ".join(_clean_sentence_end(gap) for gap in feasibility.get("gap_list", [])[:2])
        gap_text = _clean_sentence_end(gaps or "\u8bc1\u636e\u4e0d\u8db3")
        return (
            f"\u8d44\u6599\u68c0\u7d22\u5b8c\u6210\uff0c\u4f46\u672a\u627e\u5230\u8db3\u4ee5\u652f\u6491\u8be5\u8bf7\u6c42\u7684\u5de5\u4f5c\u533a\u8bc1\u636e\u3002"
            f"\u7ed3\u8bba\uff1a{verdict_label(feasibility.get('verdict', 'unknown'))}\uff1b\u4e3b\u8981\u7f3a\u53e3\uff1a{gap_text}\u3002"
        )
    verdict = verdict_label(feasibility.get("verdict", "unknown"))
    gaps = "; ".join(_clean_sentence_end(gap) for gap in feasibility.get("gap_list", [])[:2])
    confidence = confidence_label(feasibility.get("overall_confidence", "unknown"))
    return (
        f"\u5df2\u5b8c\u6210\u8bed\u6599\u68c0\u7d22\u3001\u6a21\u578b\u53ef\u884c\u6027\u8bc4\u4f30\u548c\u5ba1\u8ba1\u3002"
        f"\u7ed3\u8bba\uff1a{verdict}\uff1b\u7f6e\u4fe1\u5ea6\uff1a{confidence}\u3002"
        f"\u4e3b\u8981\u7f3a\u53e3\uff1a{gaps or '\u8bf7\u67e5\u770b\u7ed3\u6784\u5316\u8bc1\u636e'}\u3002"
    )
