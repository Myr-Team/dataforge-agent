from __future__ import annotations

import json
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
    data_status: str = Field(pattern=r"^(complete|partial|unavailable|insufficient_data)$")
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
        metric_context = request.metric_context.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
        )
        metric_context["evidence_refs"] = sorted(allowed_refs)
        knowledge_entries = retrieve_finops_knowledge(
            metric_id=request.metric_context.metric_id,
            policy_type=request.metric_context.policy_type,
            question=request.question,
        )
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
                max_output_tokens=1200,
            )
            structured = raw.get("structured")
            if not isinstance(structured, Mapping):
                raise ValueError("assistant returned no structured output")
            output = AssistantAnswerOutput.model_validate(structured)
            cited = set(output.evidence_refs)
            if not cited.issubset(allowed_refs):
                raise ValueError("assistant cited evidence outside the allowed scope")
            prose_refs = _model_prose_request_refs(output)
            if not prose_refs.issubset(allowed_refs):
                raise ValueError("assistant prose cited evidence outside the allowed scope")
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
                raise ValueError("assistant public prose contains a raw evidence reference")
            return AssistantResponse(
                status="ready",
                answer=sections.conclusion,
                evidence_refs=output.evidence_refs,
                evidence_labels=public_labels,
                evidence_state=request.metric_context.evidence_state,
                suggested_questions=suggested_questions,
                sections=sections,
            )
        except Exception:
            return AssistantResponse(
                status="unavailable",
                answer="当前分析暂不可用，运营数据本身不受影响。",
                evidence_refs=[],
                evidence_state="unavailable",
                suggested_questions=[],
            )


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
