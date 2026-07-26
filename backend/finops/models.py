from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


CallClass = Literal["model", "tool", "embedding", "image", "speech", "mcp"]
CacheState = Literal["hit", "miss", "bypassed", "unavailable"]
GatewayCoverage = Literal["apim_governed", "app_observed", "unmanaged", "unknown"]
EvidenceState = Literal["observed", "estimated", "partial", "unavailable"]
UsageSource = Literal["provider", "application", "apim", "unknown"]
_APIM_CORRELATION = re.compile(
    r"^(?:[0-9a-f]{32}|[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12})$",
    re.IGNORECASE,
)


class TokenUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: int | None = Field(default=None, ge=0)
    output: int | None = Field(default=None, ge=0)
    cached_input: int | None = Field(default=None, ge=0)
    reasoning: int | None = Field(default=None, ge=0)
    total: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_total(self) -> "TokenUsage":
        if self.total is not None:
            known_parts = [self.input, self.output, self.reasoning]
            known_sum = sum(value for value in known_parts if value is not None)
            if known_sum and self.total < known_sum:
                raise ValueError("total tokens cannot be lower than known token categories")
        return self

    @property
    def observed(self) -> bool:
        return any(value is not None for value in (self.input, self.output, self.cached_input, self.reasoning, self.total))


class CacheEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: CacheState = "unavailable"
    eligible: bool | None = None
    avoided_tokens: int | None = Field(default=None, ge=0)


class EstimatedCost(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: float | None = Field(default=None, ge=0)
    currency: Literal["USD"] = "USD"
    status: Literal["estimated", "partial", "unavailable"] = "unavailable"
    price_card_revision: str | None = Field(default=None, max_length=128)
    official_price_key: str | None = Field(default=None, max_length=240)
    mapping_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_price_evidence(self) -> "EstimatedCost":
        if self.amount is None and self.status == "estimated":
            raise ValueError("estimated cost requires an amount")
        if self.amount is not None and self.status == "unavailable":
            raise ValueError("available cost cannot have unavailable status")
        return self


class FinOpsRequestEvent(BaseModel):
    """Privacy-bounded request fact.

    Raw provider identifiers, prompts, completions, identities, secrets, and
    error bodies are rejected by ``extra='forbid'``.
    """

    model_config = ConfigDict(extra="forbid")

    request_ref: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{7,127}$")
    occurred_at: datetime
    call_class: CallClass
    tenant_ref: str = Field(min_length=1, max_length=128)
    department_id: str | None = Field(default=None, max_length=128)
    workspace_id: str = Field(min_length=1, max_length=160)
    actor_ref: str | None = Field(default=None, max_length=128)
    run_id: str | None = Field(default=None, max_length=160)
    agent_id: str | None = Field(default=None, max_length=128)
    model: str | None = Field(default=None, max_length=160)
    deployment: str | None = Field(default=None, max_length=160)
    route: str | None = Field(default=None, max_length=128)
    execution_kind: str | None = Field(default=None, max_length=64)
    status: Literal["succeeded", "failed", "cancelled", "unknown"]
    error_category: str | None = Field(default=None, max_length=64)
    latency_ms: int | None = Field(default=None, ge=0)
    tokens: TokenUsage = Field(default_factory=TokenUsage)
    cache: CacheEvidence = Field(default_factory=CacheEvidence)
    gateway_coverage: GatewayCoverage = "unknown"
    estimated_cost: EstimatedCost = Field(default_factory=EstimatedCost)
    evidence_state: EvidenceState = "unavailable"
    correlation_ref: str | None = Field(default=None, max_length=128)
    apim_correlation_id: str | None = Field(default=None, max_length=36)
    usage_source: UsageSource = "unknown"
    streaming: bool | None = None
    internal_correlation_key: str | None = Field(default=None, exclude=True, repr=False)

    @field_validator(
        "tenant_ref",
        "department_id",
        "workspace_id",
        "actor_ref",
        "run_id",
        "agent_id",
        "model",
        "deployment",
        "route",
        "execution_kind",
        "error_category",
        "correlation_ref",
        "internal_correlation_key",
        mode="before",
    )
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("apim_correlation_id")
    @classmethod
    def validate_apim_correlation(cls, value: str | None) -> str | None:
        text = str(value or "").strip().lower()
        if not text:
            return None
        if not _APIM_CORRELATION.fullmatch(text):
            raise ValueError("invalid APIM correlation")
        return text
