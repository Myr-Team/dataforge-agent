from __future__ import annotations

import json
import re
from typing import Any, Callable, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_SAFE_REF = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{2,255}$")
EvidenceState = Literal["observed", "estimated", "partial", "unavailable"]


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

    @field_validator("value")
    @classmethod
    def bounded_value(cls, value: float | str | None) -> float | str | None:
        if isinstance(value, str):
            return " ".join(value.split())[:120]
        return value


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

    @field_validator("question")
    @classmethod
    def clean_question(cls, value: str) -> str:
        return " ".join(value.split())


class AssistantAnswerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1, max_length=1600)
    evidence_refs: list[str] = Field(min_length=1, max_length=20)
    suggested_questions: list[str] = Field(default_factory=list, max_length=4)

    @field_validator("answer")
    @classmethod
    def clean_answer(cls, value: str) -> str:
        return " ".join(value.split())

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


class AssistantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ready", "insufficient_data", "unavailable"]
    answer: str
    evidence_refs: list[str] = Field(default_factory=list)
    evidence_state: EvidenceState
    suggested_questions: list[str] = Field(default_factory=list)

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
        if not allowed_refs:
            return AssistantResponse(
                status="insufficient_data",
                answer="当前指标缺少可复核证据，暂不能生成分析结论。",
                evidence_refs=[],
                evidence_state="unavailable",
                suggested_questions=[],
            )
        payload = {
            "task": "根据所选运营指标回答用户问题，仅引用 evidence_refs 中的证据；不要批准或执行治理动作。",
            "question": request.question,
            "metric_context": request.metric_context.model_dump(
                mode="json",
                by_alias=True,
                exclude_none=True,
            ),
            "history": [
                item.model_dump(mode="json")
                for item in request.history
            ],
            "evidence": dict(evidence_payload),
            "evidence_refs": sorted(allowed_refs),
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
            return AssistantResponse(
                status="ready",
                answer=output.answer,
                evidence_refs=output.evidence_refs,
                evidence_state=request.metric_context.evidence_state,
                suggested_questions=output.suggested_questions,
            )
        except Exception:
            return AssistantResponse(
                status="unavailable",
                answer="当前分析暂不可用，运营数据本身不受影响。",
                evidence_refs=[],
                evidence_state="unavailable",
                suggested_questions=[],
            )
