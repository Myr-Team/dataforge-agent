from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .assistant_knowledge import retrieve_finops_knowledge


_SAFE_REF = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{2,255}$")
_SAFE_REQUEST_REF = re.compile(r"^req_[A-Za-z0-9_-]{4,123}$")
_REQUEST_REF_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_.:-])(req_[A-Za-z0-9_.:-]{4,123})(?![A-Za-z0-9_.:-])",
    re.IGNORECASE,
)
EvidenceState = Literal["observed", "estimated", "partial", "unavailable"]
AssistantPolicyType = Literal[
    "error_rate",
    "p95_latency",
    "daily_cost_budget",
    "token_spike",
    "apim_coverage",
    "unpriced_requests",
    "cache_hit_rate",
]


class _AssistantSafetyError(ValueError):
    """Model output crossed the authorized evidence boundary."""


class AssistantWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_value: str = Field(alias="from", min_length=1, max_length=40)
    to_value: str = Field(alias="to", min_length=1, max_length=40)


class AssistantFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    department_id: str | None = Field(default=None, max_length=160)
    workspace_id: str | None = Field(default=None, max_length=160)
    actor_ref: str | None = Field(default=None, max_length=160)
    agent_id: str | None = Field(default=None, max_length=160)
    model: str | None = Field(default=None, max_length=160)


class AssistantMetricContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric_id: str = Field(min_length=1, max_length=96)
    label: str = Field(min_length=1, max_length=120)
    value: float | str | None = Field(default=None)
    unit: str = Field(default="", max_length=24)
    dimension: str | None = Field(default=None, max_length=48)
    dimension_value: str | None = Field(default=None, max_length=160)
    window: AssistantWindow
    filters: AssistantFilters
    data_status: str = Field(
        pattern=r"^(complete|available|observed|estimated|partial|unavailable|insufficient_data)$"
    )
    evidence_state: EvidenceState
    cache_state: Literal["hit", "miss", "bypassed", "unavailable"] | None = None
    policy_type: AssistantPolicyType | None = None
    evidence_refs: list[str] = Field(default_factory=list, max_length=3)

    @field_validator("value")
    @classmethod
    def bounded_value(cls, value: float | str | None) -> float | str | None:
        if isinstance(value, str):
            return " ".join(value.split())[:120]
        return value

    @field_validator("evidence_refs")
    @classmethod
    def safe_request_evidence_refs(cls, values: list[str]) -> list[str]:
        cleaned = [str(value or "").strip() for value in values]
        if any(not _SAFE_REQUEST_REF.fullmatch(value) for value in cleaned):
            raise ValueError("invalid request evidence reference")
        return list(dict.fromkeys(cleaned))


class AssistantTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=600)

    @field_validator("content")
    @classmethod
    def clean_content(cls, value: str) -> str:
        return " ".join(value.split())


class AssistantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=600)
    mode: Literal["quick", "deep"] = "quick"
    metric_context: AssistantMetricContext
    history: list[AssistantTurn] = Field(default_factory=list, max_length=6)
    conversation_ref: str | None = Field(
        default=None,
        pattern=r"^foc_[0-9a-f]{32}$",
    )

    @field_validator("question")
    @classmethod
    def clean_question(cls, value: str) -> str:
        return " ".join(value.split())


class AssistantAnswerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str | None = Field(default=None, min_length=1, max_length=1600)
    conclusion: str | None = Field(default=None, min_length=1, max_length=600)
    basis: str | None = Field(default=None, min_length=1, max_length=800)
    impact: str | None = Field(default=None, min_length=1, max_length=600)
    recommendation: str | None = Field(default=None, min_length=1, max_length=600)
    caveat: str | None = Field(default=None, min_length=1, max_length=400)
    evidence_refs: list[str] = Field(min_length=1, max_length=20)
    suggested_questions: list[str] = Field(default_factory=list, max_length=4)

    @field_validator(
        "answer",
        "conclusion",
        "basis",
        "impact",
        "recommendation",
        "caveat",
    )
    @classmethod
    def clean_answer(cls, value: str | None) -> str | None:
        return " ".join(value.split()) if value is not None else None

    @field_validator("evidence_refs")
    @classmethod
    def safe_evidence_refs(cls, values: list[str]) -> list[str]:
        cleaned = [str(value or "").strip() for value in values]
        if any(not _SAFE_REF.fullmatch(value) for value in cleaned):
            raise ValueError("invalid evidence reference")
        return list(dict.fromkeys(cleaned))

    @field_validator("suggested_questions")
    @classmethod
    def clean_questions(cls, values: list[str]) -> list[str]:
        cleaned = [" ".join(str(value or "").split())[:160] for value in values]
        return [value for value in dict.fromkeys(cleaned) if value]

    @model_validator(mode="after")
    def requires_conclusion_or_legacy_answer(self) -> "AssistantAnswerOutput":
        if not self.conclusion and not self.answer:
            raise ValueError("assistant response requires a conclusion")
        return self


class AssistantAnswerSections(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conclusion: str = Field(min_length=1, max_length=600)
    basis: str = Field(min_length=1, max_length=800)
    impact: str = Field(min_length=1, max_length=600)
    recommendation: str = Field(min_length=1, max_length=600)
    caveat: str = Field(min_length=1, max_length=400)


class AssistantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ready", "insufficient_data", "unavailable"]
    answer: str
    evidence_refs: list[str] = Field(default_factory=list)
    evidence_labels: list[str] = Field(default_factory=list)
    knowledge_citations: list[str] = Field(default_factory=list, max_length=4)
    evidence_state: EvidenceState
    suggested_questions: list[str] = Field(default_factory=list)
    sections: AssistantAnswerSections | None = None

    @model_validator(mode="after")
    def ready_requires_evidence(self) -> "AssistantResponse":
        if self.status == "ready" and not self.evidence_refs:
            raise ValueError("ready assistant response requires evidence")
        return self


class FinOpsAssistantService:
    def __init__(
        self,
        *,
        model_runner: Callable[..., Mapping[str, Any]],
    ) -> None:
        self._model_runner = model_runner

    def answer(
        self,
        *,
        request: AssistantRequest,
        evidence_payload: Mapping[str, Any],
    ) -> AssistantResponse:
        allowed_refs = {
            str(value or "").strip()
            for value in evidence_payload.get("evidence_refs", [])
            if _SAFE_REF.fullmatch(str(value or "").strip())
        }
        evidence_labels = _evidence_labels(
            evidence_payload.get("evidence_catalog"),
            allowed_refs=allowed_refs,
        )
        if not allowed_refs:
            return AssistantResponse(
                status="insufficient_data",
                answer="当前指标缺少可复核证据，暂不能生成分析结论。",
                evidence_refs=[],
                evidence_state="unavailable",
                suggested_questions=[],
            )
        knowledge_entries = retrieve_finops_knowledge(
            metric_id=request.metric_context.metric_id,
            policy_type=request.metric_context.policy_type,
            question=request.question,
        )
        knowledge_citations = _knowledge_citations(knowledge_entries)
        if request.mode == "quick" and not _quick_model_enabled():
            return _grounded_quick_response(
                request=request,
                allowed_refs=allowed_refs,
                evidence_labels=evidence_labels,
                knowledge_citations=knowledge_citations,
            )
        metric_context = request.metric_context.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
        )
        metric_context["evidence_refs"] = sorted(allowed_refs)
        payload = {
            "task": (
                "根据所选运营指标回答用户问题，必须分别输出 conclusion、basis、impact、"
                "recommendation、caveat 五个结构化字段；仅在 evidence_refs 字段引用允许的证据；"
                "回答正文只能使用 evidence_catalog 的 display_name，不得输出原始 evidence ref；"
                "知识目录只能解释定义、公式和判断边界，当前数值只能来自授权运营证据；"
                "不要批准或执行治理动作。"
            ),
            "question": request.question,
            "metric_context": metric_context,
            "history": [
                item.model_dump(mode="json")
                for item in request.history
            ],
            "evidence": dict(evidence_payload),
            "evidence_refs": sorted(allowed_refs),
            "knowledge_context": {
                "version": "finops-knowledge-v1",
                "usage_boundary": "知识仅用于解释概念和公式，不能生成当前数值或新增证据。",
                "entries": knowledge_entries,
            },
        }
        try:
            raw = self._model_runner(
                "df-finops-analyst",
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                response_schema=AssistantAnswerOutput.model_json_schema(),
                max_output_tokens=650 if request.mode == "quick" else 1200,
                request_timeout_seconds=6.0 if request.mode == "quick" else None,
                retry_limit=0 if request.mode == "quick" else None,
            )
        except Exception:
            if request.mode == "quick":
                return _grounded_quick_response(
                    request=request,
                    allowed_refs=allowed_refs,
                    evidence_labels=evidence_labels,
                )
            return _unavailable_response()
        try:
            structured = raw.get("structured")
            if not isinstance(structured, Mapping):
                raise ValueError("assistant returned no structured output")
            output = AssistantAnswerOutput.model_validate(structured)
            cited = set(output.evidence_refs)
            if not cited.issubset(allowed_refs):
                raise _AssistantSafetyError("assistant cited evidence outside the allowed scope")
            prose_refs = _model_prose_request_refs(output)
            if not prose_refs.issubset(allowed_refs):
                raise _AssistantSafetyError("assistant prose cited evidence outside the allowed scope")
            public_labels = [
                evidence_labels.get(ref) or f"运营证据 {index + 1}"
                for index, ref in enumerate(output.evidence_refs)
            ]
            sections = _public_sections(
                output,
                evidence_labels=evidence_labels,
                evidence_state=request.metric_context.evidence_state,
                allowed_refs=allowed_refs,
            )
            suggested_questions = [
                _public_answer(
                    question,
                    evidence_refs=sorted(allowed_refs),
                    evidence_labels=evidence_labels,
                )
                for question in output.suggested_questions
            ]
            if any(
                _request_refs_in_text(value)
                for value in [
                    *sections.model_dump(mode="json").values(),
                    *suggested_questions,
                ]
            ):
                raise _AssistantSafetyError("assistant public prose contains a raw evidence reference")
            return AssistantResponse(
                status="ready",
                answer=sections.conclusion,
                evidence_refs=output.evidence_refs,
                evidence_labels=public_labels,
                knowledge_citations=knowledge_citations,
                evidence_state=request.metric_context.evidence_state,
                suggested_questions=suggested_questions,
                sections=sections,
            )
        except _AssistantSafetyError:
            return _unavailable_response()
        except Exception:
            if request.mode == "quick":
                return _grounded_quick_response(
                    request=request,
                    allowed_refs=allowed_refs,
                    evidence_labels=evidence_labels,
                    knowledge_citations=knowledge_citations,
                )
            return _unavailable_response()


def _unavailable_response() -> AssistantResponse:
    return AssistantResponse(
        status="unavailable",
        answer="当前分析暂不可用，运营数据本身不受影响。",
        evidence_refs=[],
        evidence_state="unavailable",
        suggested_questions=[],
    )


def _quick_model_enabled() -> bool:
    return str(os.environ.get("DF_FINOPS_QUICK_MODEL_ENABLED") or "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _grounded_quick_response(
    *,
    request: AssistantRequest,
    allowed_refs: set[str],
    evidence_labels: Mapping[str, str],
    knowledge_citations: list[str] | None = None,
) -> AssistantResponse:
    context = request.metric_context
    refs = sorted(allowed_refs)[:3]
    public_labels = [
        evidence_labels.get(ref) or f"运营证据 {index + 1}"
        for index, ref in enumerate(refs)
    ]
    value = context.value
    if value is None or value == "":
        value_text = "当前值未记录"
    else:
        value_text = f"当前值为 {value}{context.unit}"
    data_status = {
        "complete": "完整",
        "partial": "部分",
        "unavailable": "不可用",
        "insufficient_data": "样本不足",
    }.get(context.data_status, context.data_status)
    evidence_state = {
        "observed": "已观测",
        "estimated": "估算",
        "partial": "部分",
        "unavailable": "不可用",
    }.get(context.evidence_state, context.evidence_state)
    recommendation = {
        "apim_coverage": "先定位未纳入统一入口或来源待确认的调用链，按工作区与路由复核影响范围，再保存整改草案。",
        "cache_hit_rate": "先复核可缓存请求、未命中与绕过原因，再按工作区和模型比较可避免 Token。",
        "unpriced_requests": "先补齐模型与有效价目修订的映射，无法可靠匹配的请求继续保持未计价。",
        "daily_cost_budget": "先核对估算成本口径与预算周期，再由管理员评审提醒阈值。",
        "error_rate": "先按错误类别、模型和路由定位集中失败来源，再复核代表请求。",
        "p95_latency": "先按模型、路由与缓存状态拆分慢请求，再确认是否为持续性问题。",
        "token_spike": "先与相同时段基线对比输入、输出和推理 Token，再定位贡献最大的调用。",
    }.get(
        context.policy_type,
        "先复核代表证据与筛选范围，再把建议保存为可审阅草案。",
    )
    sections = AssistantAnswerSections(
        conclusion=(
            f"“{context.label}”{value_text}。当前有 {len(refs)} 条授权证据可复核，"
            "可以作为本窗口的运营判断，但不应脱离证据范围直接执行变更。"
        ),
        basis=(
            f"数据完整性为{data_status}，证据状态为{evidence_state}。"
            f"代表证据包括：{'、'.join(public_labels)}。"
        ),
        impact=(
            "该指标会影响当前筛选范围内的成本、体验或治理优先级；具体影响量仍需结合阈值和对比周期复核。"
        ),
        recommendation=recommendation,
        caveat=(
            "这是基于当前时间窗口、筛选条件和授权证据生成的快速解释；不等同于实际账单、审批结论或生产变更。"
        ),
    )
    return AssistantResponse(
        status="ready",
        answer=sections.conclusion,
        evidence_refs=refs,
        evidence_labels=public_labels,
        knowledge_citations=list(knowledge_citations or [])[:4],
        evidence_state=context.evidence_state,
        suggested_questions=[
            f"{context.label}与上一周期相比发生了什么变化？",
            f"哪些证据最能影响{context.label}的处置优先级？",
        ],
        sections=sections,
    )


def _knowledge_citations(entries: list[dict[str, str]]) -> list[str]:
    citations: list[str] = []
    for item in entries:
        citation = " ".join(str(item.get("citation") or "").split())[:240]
        if citation.startswith("内部知识：") and citation not in citations:
            citations.append(citation)
        if len(citations) >= 4:
            break
    return citations


def _evidence_labels(
    raw_catalog: Any,
    *,
    allowed_refs: set[str],
) -> dict[str, str]:
    labels: dict[str, str] = {}
    if not isinstance(raw_catalog, list):
        return labels
    for item in raw_catalog:
        if not isinstance(item, Mapping):
            continue
        ref = str(item.get("ref") or "").strip()
        label = " ".join(str(item.get("display_name") or "").split())[:320]
        if ref in allowed_refs and label:
            labels.setdefault(ref, label)
    return labels


def _public_answer(
    answer: str,
    *,
    evidence_refs: list[str],
    evidence_labels: Mapping[str, str],
) -> str:
    public = answer
    unique_refs = list(dict.fromkeys(evidence_refs))
    if unique_refs:
        labels = {
            ref: evidence_labels.get(ref) or f"运营证据 {index + 1}"
            for index, ref in enumerate(unique_refs)
        }
        alternatives = "|".join(
            re.escape(ref)
            for ref in sorted(unique_refs, key=len, reverse=True)
        )
        token_pattern = re.compile(
            rf"\[\s*(?P<bracket>{alternatives})\s*\]"
            rf"|(?<![A-Za-z0-9_.:-])(?P<plain>{alternatives})(?![A-Za-z0-9_.:-])"
        )

        def public_label(match: re.Match[str]) -> str:
            ref = match.group("bracket") or match.group("plain")
            return labels[ref]

        public = token_pattern.sub(public_label, public)
    public = re.sub(r"\s+([，。；：,.!?])", r"\1", public)
    public = " ".join(public.split()).strip()
    return public or "已基于相关运营证据完成分析，请通过“查看证据”复核明细。"


def _public_sections(
    output: AssistantAnswerOutput,
    *,
    evidence_labels: Mapping[str, str],
    evidence_state: EvidenceState,
    allowed_refs: set[str],
) -> AssistantAnswerSections:
    def public(value: str) -> str:
        return _public_answer(
            value,
            evidence_refs=sorted(allowed_refs),
            evidence_labels=evidence_labels,
        )

    conclusion = output.conclusion or output.answer or "已完成当前指标分析。"
    basis = output.basis or "结论仅基于当前筛选范围内已列出的可复核证据。"
    impact = output.impact or "现有证据不足以进一步量化业务影响。"
    recommendation = output.recommendation or "建议先复核证据，再决定是否进入治理流程。"
    caveat = output.caveat or (
        "当前证据不完整，结论仅用于辅助判断。"
        if evidence_state in {"partial", "unavailable"}
        else "结论仅适用于当前时间范围和筛选条件。"
    )
    return AssistantAnswerSections(
        conclusion=public(conclusion),
        basis=public(basis),
        impact=public(impact),
        recommendation=public(recommendation),
        caveat=public(caveat),
    )


def _request_refs_in_text(value: Any) -> set[str]:
    return {
        match.group(1)
        for match in _REQUEST_REF_TOKEN.finditer(str(value or ""))
    }


def _model_prose_request_refs(output: AssistantAnswerOutput) -> set[str]:
    values = [
        output.answer,
        output.conclusion,
        output.basis,
        output.impact,
        output.recommendation,
        output.caveat,
        *output.suggested_questions,
    ]
    return set().union(*(_request_refs_in_text(value) for value in values))
