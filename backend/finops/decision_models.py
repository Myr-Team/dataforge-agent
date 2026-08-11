from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


DecisionEvidenceState = Literal[
    "observed", "estimated", "verified", "partial", "unavailable", "not_recorded"
]


class DecisionMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    label: str
    value: float | int | None
    unit: str
    status: DecisionEvidenceState
    explanation: str


class DecisionStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: str
    title: str
    summary: str
    evidence_state: DecisionEvidenceState


class RoiDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: DecisionStatement
    case_story: dict[str, Any]
    metrics: list[DecisionMetric]
    value_bridge: dict[str, Any]
    evidence_maturity: dict[str, Any]
    unit_economics_trend: list[dict[str, Any]]
    forecast_validation: dict[str, Any]
    verified_roi: dict[str, Any]
    capability_explanation: dict[str, list[str]]
    scenarios: list[dict[str, Any]]
    evidence_gaps: list[str]


class RiskDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: DecisionStatement
    risk_domains: list[dict[str, Any]]
    risk_matrix: list[dict[str, Any]]
    priorities: list[dict[str, Any]]
    optimization_portfolio: list[dict[str, Any]]
    portfolio_metadata: dict[str, Any]
    selected_evidence_summaries: list[dict[str, Any]]
    evidence_sets: list[dict[str, Any]] = Field(default_factory=list)
    insight: dict[str, Any] | None
    drafts: list[dict[str, Any]]
    governance_capability: dict[str, Any]
