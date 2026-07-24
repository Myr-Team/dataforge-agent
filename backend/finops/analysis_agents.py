from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from pydantic import BaseModel, ConfigDict, Field

from .governance import validate_typed_action_payload
from .insights import (
    AgentDraftSuggestion,
    AgentFinding,
    AgentKind,
    FinOpsInsight,
    InsightWindow,
    insight_fingerprint,
)


class AgentAnalysisOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=1200)
    findings: list[AgentFinding] = Field(min_length=1, max_length=20)
    evidence_state: str = Field(pattern=r"^(observed|estimated|partial|unavailable)$")
    confidence: float = Field(ge=0, le=1)
    draft_suggestions: list[AgentDraftSuggestion] = Field(
        default_factory=list,
        max_length=10,
    )


class FinOpsAnalysisAgent:
    def __init__(
        self,
        *,
        repository: Any,
        model_runner: Callable[..., Mapping[str, Any]],
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._model_runner = model_runner
        self._now = now or (lambda: datetime.now(timezone.utc))

    def analyze(
        self,
        *,
        agent_kind: AgentKind,
        tenant_ref: str,
        workspace_ids: tuple[str, ...],
        window: Mapping[str, Any],
        trigger_type: str,
        trigger_ref: str | None,
        source_revision: str,
        input_payload: Mapping[str, Any],
    ) -> FinOpsInsight:
        fingerprint = insight_fingerprint(
            tenant_ref=tenant_ref,
            workspace_ids=workspace_ids,
            agent_kind=agent_kind,
            trigger_type=trigger_type,
            trigger_ref=trigger_ref,
            source_revision=source_revision,
        )
        existing = self._repository.get_by_fingerprint(
            tenant_ref=tenant_ref,
            agent_kind=agent_kind,
            trigger_fingerprint=fingerprint,
        )
        if existing is not None:
            return existing
        now = self._now().astimezone(timezone.utc)
        common = {
            "insight_id": f"ins_{fingerprint[:16]}",
            "agent_kind": agent_kind,
            "tenant_ref": tenant_ref,
            "workspace_ids": list(workspace_ids),
            "window": InsightWindow.model_validate(window),
            "trigger_type": trigger_type,
            "trigger_ref": trigger_ref,
            "trigger_fingerprint": fingerprint,
            "source_revisions": {"input": source_revision},
            "generated_at": now,
            "expires_at": now + timedelta(hours=6),
        }
        if str(input_payload.get("status") or "") == "insufficient_data":
            return self._repository.save(
                FinOpsInsight(
                    **common,
                    title="证据不足",
                    summary="当前证据不足，暂不生成推测性结论。",
                    findings=[],
                    evidence_refs=[],
                    evidence_state="unavailable",
                    confidence=None,
                    evidence_gaps=_safe_gaps(input_payload.get("evidence_gaps")),
                    draft_suggestions=[],
                    status="insufficient_data",
                )
            )
        allowed_refs = {
            str(value or "").strip()
            for value in input_payload.get("evidence_refs", [])
            if str(value or "").strip()
        }
        if not allowed_refs:
            return self._repository.save(
                FinOpsInsight(
                    **common,
                    title="证据不足",
                    summary="当前证据不足，暂不生成推测性结论。",
                    findings=[],
                    evidence_refs=[],
                    evidence_state="unavailable",
                    confidence=None,
                    evidence_gaps=["可引用证据不足"],
                    draft_suggestions=[],
                    status="insufficient_data",
                )
            )
        try:
            response = self._model_runner(
                "df-finops-analyst" if agent_kind == "finops" else "df-roi-analyst",
                json.dumps(input_payload, ensure_ascii=False, separators=(",", ":")),
                response_schema=AgentAnalysisOutput.model_json_schema(),
                max_output_tokens=2200,
            )
            structured = response.get("structured")
            if not isinstance(structured, Mapping):
                raise ValueError("agent returned no structured output")
            output = AgentAnalysisOutput.model_validate(structured)
            used_refs = {
                ref for finding in output.findings for ref in finding.evidence_refs
            }
            if not used_refs or not used_refs.issubset(allowed_refs):
                raise ValueError("agent cited evidence outside the allowed scope")
            suggestions = [
                _validated_suggestion(item)
                for item in output.draft_suggestions
            ]
            insight = FinOpsInsight(
                **common,
                title=output.title,
                summary=output.summary,
                findings=output.findings,
                evidence_refs=sorted(used_refs),
                evidence_state=output.evidence_state,
                confidence=output.confidence,
                evidence_gaps=[],
                draft_suggestions=suggestions,
                status="ready",
            )
        except Exception:
            insight = FinOpsInsight(
                **common,
                title="分析暂不可用",
                summary="本次结构化分析未通过证据校验，页面聚合数据不受影响。",
                findings=[],
                evidence_refs=[],
                evidence_state="unavailable",
                confidence=None,
                evidence_gaps=["结构化分析未通过校验"],
                draft_suggestions=[],
                status="failed",
            )
        return self._repository.save(insight)


def _validated_suggestion(
    suggestion: AgentDraftSuggestion,
) -> AgentDraftSuggestion:
    if _contains_unsafe_material(suggestion.payload):
        raise ValueError("unsafe action payload")
    payload = validate_typed_action_payload(
        suggestion.action_type,
        suggestion.payload,
    )
    return suggestion.model_copy(update={"payload": payload})


def _contains_unsafe_material(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            _contains_unsafe_material(key) or _contains_unsafe_material(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_unsafe_material(item) for item in value)
    if not isinstance(value, str):
        return False
    lowered = value.lower()
    return any(
        marker in lowered
        for marker in (
            "<script",
            "</script",
            "<policies",
            "</policies",
            "powershell ",
            "bash -",
            "/subscriptions/",
        )
    )


def _safe_gaps(value: Any) -> list[str]:
    if not isinstance(value, list):
        return ["证据不足"]
    gaps = [" ".join(str(item or "").split())[:300] for item in value]
    return [item for item in dict.fromkeys(gaps) if item][:20] or ["证据不足"]
