from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


AgentKind = Literal["finops", "roi"]
InsightStatus = Literal["ready", "insufficient_data", "failed", "stale"]
FindingKind = Literal["cost_driver", "risk", "optimization", "roi", "evidence_gap"]
EvidenceState = Literal["observed", "estimated", "partial", "unavailable"]
ActionType = Literal[
    "apim_token_limit",
    "model_route",
    "cache_policy",
    "price_card_activation",
]
_SAFE_REF = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{2,255}$")


class InsightWindow(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        serialize_by_alias=True,
    )

    from_value: datetime = Field(alias="from")
    to_value: datetime = Field(alias="to")

    @model_validator(mode="after")
    def ordered(self) -> "InsightWindow":
        if self.from_value >= self.to_value:
            raise ValueError("insight window must be ordered")
        return self


class AgentFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: FindingKind
    statement: str = Field(min_length=1, max_length=600)
    evidence_refs: list[str] = Field(min_length=1, max_length=20)

    @field_validator("statement")
    @classmethod
    def clean_statement(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("evidence_refs")
    @classmethod
    def safe_evidence_refs(cls, values: list[str]) -> list[str]:
        return _unique_safe_refs(values)


class AgentDraftSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: ActionType
    reason: str = Field(min_length=1, max_length=600)
    payload: dict[str, Any]

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: str) -> str:
        return " ".join(value.split())


class FinOpsInsight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    insight_id: str = Field(pattern=r"^ins_[A-Za-z0-9_-]{12,60}$")
    agent_kind: AgentKind
    tenant_ref: str = Field(min_length=1, max_length=128)
    workspace_ids: list[str] = Field(min_length=1, max_length=100)
    window: InsightWindow
    trigger_type: str = Field(min_length=1, max_length=64)
    trigger_ref: str | None = Field(default=None, max_length=160)
    trigger_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=1200)
    findings: list[AgentFinding] = Field(default_factory=list, max_length=20)
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)
    evidence_state: EvidenceState = "unavailable"
    confidence: float | None = Field(default=None, ge=0, le=1)
    source_revisions: dict[str, str] = Field(default_factory=dict)
    evidence_gaps: list[str] = Field(default_factory=list, max_length=20)
    draft_suggestions: list[AgentDraftSuggestion] = Field(
        default_factory=list,
        max_length=10,
    )
    generated_at: datetime
    expires_at: datetime
    status: InsightStatus

    @field_validator("workspace_ids")
    @classmethod
    def clean_workspaces(cls, values: list[str]) -> list[str]:
        cleaned = [str(value or "").strip() for value in values]
        if any(not value or len(value) > 160 for value in cleaned):
            raise ValueError("invalid workspace scope")
        return list(dict.fromkeys(cleaned))

    @field_validator("evidence_refs")
    @classmethod
    def clean_evidence_refs(cls, values: list[str]) -> list[str]:
        return _unique_safe_refs(values)

    @field_validator("evidence_gaps")
    @classmethod
    def clean_evidence_gaps(cls, values: list[str]) -> list[str]:
        cleaned = [" ".join(str(value or "").split())[:300] for value in values]
        return [value for value in dict.fromkeys(cleaned) if value]

    @field_validator("source_revisions")
    @classmethod
    def clean_source_revisions(cls, values: dict[str, str]) -> dict[str, str]:
        result: dict[str, str] = {}
        for key, value in values.items():
            clean_key = str(key or "").strip()
            clean_value = str(value or "").strip()
            if clean_key and clean_value:
                result[clean_key[:64]] = clean_value[:160]
        return result

    @model_validator(mode="after")
    def validate_evidence_contract(self) -> "FinOpsInsight":
        if self.generated_at >= self.expires_at:
            raise ValueError("insight expiry must follow generation")
        if self.status in {"ready", "stale"}:
            if not self.findings or not self.evidence_refs:
                raise ValueError("ready insight requires findings and evidence")
            allowlist = set(self.evidence_refs)
            if any(
                not set(finding.evidence_refs).issubset(allowlist)
                for finding in self.findings
            ):
                raise ValueError("finding evidence is outside the insight allowlist")
        if self.status == "insufficient_data" and not self.evidence_gaps:
            raise ValueError("insufficient_data requires evidence gaps")
        return self


def insight_fingerprint(
    *,
    tenant_ref: str,
    workspace_ids: list[str] | tuple[str, ...],
    agent_kind: AgentKind,
    trigger_type: str,
    trigger_ref: str | None,
    source_revision: str,
) -> str:
    payload = {
        "tenant_ref": str(tenant_ref or "").strip(),
        "workspace_ids": sorted(
            {str(value or "").strip() for value in workspace_ids if str(value or "").strip()}
        ),
        "agent_kind": agent_kind,
        "trigger_type": str(trigger_type or "").strip(),
        "trigger_ref": str(trigger_ref or "").strip() or None,
        "source_revision": str(source_revision or "").strip(),
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _unique_safe_refs(values: list[str]) -> list[str]:
    cleaned = [str(value or "").strip() for value in values]
    if any(not _SAFE_REF.fullmatch(value) for value in cleaned):
        raise ValueError("invalid evidence reference")
    return list(dict.fromkeys(cleaned))
