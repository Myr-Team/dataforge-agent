from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


Confidence = Literal["data_confirmed", "market_inferred", "speculative"]


class Evidence(BaseModel):
    source_type: Literal["corpus", "market", "computed"]
    ref: str
    quote: str | None = None


class RoutingDecision(BaseModel):
    workspace_id: str
    intent: str
    experts: list[str]
    output_mode: Literal["chat", "report", "full_package"]
    needs_clarification: bool
    clarifying_question: str | None = None
    reason: str


class DocCorpusProfile(BaseModel):
    workspace_id: str
    assets: list[str]
    asset_evidence: list[Evidence]
    gaps_observed: list[str]


class OpportunityCard(BaseModel):
    id: str
    title: str
    description: str
    supporting_evidence: list[Evidence]

    @model_validator(mode="after")
    def _requires_evidence(self) -> "OpportunityCard":
        if not self.supporting_evidence:
            raise ValueError("OpportunityCard.supporting_evidence must not be empty")
        return self


class FeasibilityDimension(BaseModel):
    name: Literal["market", "technical", "asset_data", "resource_cost", "differentiation_risk"]
    score: int = Field(ge=0, le=5)
    rationale: str
    evidence: list[Evidence]
    confidence: Confidence

    @model_validator(mode="after")
    def _requires_evidence(self) -> "FeasibilityDimension":
        if not self.evidence:
            raise ValueError("FeasibilityDimension.evidence must not be empty")
        return self


class FeasibilityReport(BaseModel):
    opportunity_id: str
    dimensions: list[FeasibilityDimension]
    verdict: Literal["feasible", "conditional", "not_yet_feasible"]
    overall_confidence: Confidence
    gap_list: list[str]


class MarketComparison(BaseModel):
    opportunity_id: str
    competitors: list[dict[str, Any]]
    positioning_note: str


class ProjectProposal(BaseModel):
    opportunity_id: str
    pdf_blob_url: str
    concept_image_blob_url: str | None = None
    audio_summary_blob_url: str | None = None


class AuditVerdict(BaseModel):
    verdict: Literal["pass", "revise"]
    issues: list[str]
    target_expert: str | None = None


class SearchPackContextRequest(BaseModel):
    workspace_id: str = "demo-corpus"
    query: str = Field(min_length=2)
    top_k: int = Field(default=5, ge=1, le=20)


class SearchPackContextResponse(BaseModel):
    workspace_id: str
    query: str
    hits: list[dict[str, Any]]
    count: int
    source_index: str


class UploadResponse(BaseModel):
    workspace_id: str
    name: str
    format: str
    indexed_count: int
    profile_summary: str


class WorkspaceSummary(BaseModel):
    workspace_id: str
    name: str
    doc_count: int
    format: str | None = None
    profile_summary: str | None = None
    created_at: str | None = None


class WorkspacesResponse(BaseModel):
    workspaces: list[WorkspaceSummary]


class WorkspaceDeleteResponse(BaseModel):
    workspace_id: str
    deleted: bool
    deleted_docs: int
    deleted_blobs: int = 0


class RenderPdfRequest(BaseModel):
    proposal: dict[str, Any]
    template: str = "project_proposal"


class GenerateImageRequest(BaseModel):
    prompt: str
    size: str = "1024x1024"


class NarrateSummaryRequest(BaseModel):
    text: str
    voice: str = "zh-CN-XiaoxiaoNeural"


class ProduceRequest(BaseModel):
    workspace_id: str = "demo-corpus"
    conversation_id: str | None = None
    feasibility: dict[str, Any]
    corpus: dict[str, Any] = Field(default_factory=dict)
    market: dict[str, Any] = Field(default_factory=dict)


class ChatRequest(BaseModel):
    workspace_id: str = "demo-corpus"
    message: str
    conversation_id: str | None = None
