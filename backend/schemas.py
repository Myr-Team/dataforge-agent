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
    description: str | None = None
    format: str
    indexed_count: int
    profile_summary: str
    documents: list[dict[str, Any]] = Field(default_factory=list)
    reference_images: list[dict[str, Any]] = Field(default_factory=list)
    ingest_job_id: str | None = None
    ingest_status: dict[str, Any] = Field(default_factory=dict)


class WorkspaceSummary(BaseModel):
    workspace_id: str
    name: str
    doc_count: int
    format: str | None = None
    description: str | None = None
    profile_summary: str | None = None
    created_at: str | None = None
    documents: list[dict[str, Any]] = Field(default_factory=list)
    reference_images: list[dict[str, Any]] = Field(default_factory=list)


class WorkspacesResponse(BaseModel):
    workspaces: list[WorkspaceSummary]


class WorkspaceColumnDetail(BaseModel):
    table: str | None = None
    name: str
    friendly_label: str | None = None
    role: str | None = None
    signal: Literal["strong", "mid", "noise"] | None = None
    signal_score: float | None = None
    signal_reason: str | None = None
    missing_rate: float | int | None = None
    unique_count: int | None = None
    non_empty: int | None = None
    top_values: list[Any] = Field(default_factory=list)


class WorkspaceDocumentDetail(BaseModel):
    source_file: str | None = None
    name: str
    format: str | None = None
    bytes: int | None = None
    record_count: int | None = None
    status: str | None = None
    error: str | None = None
    ingest_job_id: str | None = None
    created_at: str | None = None
    profile_file: str | None = None
    external: bool = False


class WorkspaceReferenceImage(BaseModel):
    url: str
    role: str = "reference"
    filename: str
    blob_url: str | None = None
    blob_name: str | None = None
    source_file: str | None = None
    content_type: str | None = None
    bytes: int | None = None


class WorkspaceDetailResponse(BaseModel):
    workspace_id: str
    name: str
    description: str | None = None
    format: str
    rows: int = 0
    row_count: int = 0
    field_count: int = 0
    indexed_count: int = 0
    fill_rate: float = 0
    signal_score: float = 0
    signal_distribution: dict[str, float] = Field(default_factory=dict)
    columns: list[WorkspaceColumnDetail] = Field(default_factory=list)
    customer_summary: str | None = None
    doc_count: int = 0
    documents: list[WorkspaceDocumentDetail] = Field(default_factory=list)
    reference_images: list[WorkspaceReferenceImage] = Field(default_factory=list)
    profile_summary: str | None = None
    signals: list[str] = Field(default_factory=list)
    manifest: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None


class WorkspaceDeleteResponse(BaseModel):
    workspace_id: str
    deleted: bool
    deleted_docs: int
    deleted_blobs: int = 0


class RunStep(BaseModel):
    time: str | None = None
    event: str
    agent: str | None = None
    name: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class RunSummary(BaseModel):
    run_id: str
    time: str | None = None
    workspace_id: str | None = None
    verdict: str | None = None
    confidence: str | None = None
    status: str | None = None
    steps: list[RunStep] = Field(default_factory=list)
    step_count: int = 0


class RunsResponse(BaseModel):
    runs: list[RunSummary]


class ConversationSummary(BaseModel):
    conversation_id: str
    workspace_id: str | None = None
    title: str
    updated_at: str | None = None
    turn_count: int = 0
    last_verdict: str | None = None


class ConversationsResponse(BaseModel):
    conversations: list[ConversationSummary]


class WorkspaceDashboardResponse(BaseModel):
    workspace_id: str
    workspace: WorkspaceDetailResponse
    workspaces: list[WorkspaceSummary] = Field(default_factory=list)
    runs: list[RunSummary] = Field(default_factory=list)
    conversations: list[ConversationSummary] = Field(default_factory=list)
    health: dict[str, Any] = Field(default_factory=dict)
    dependency_details: dict[str, Any] = Field(default_factory=dict)


class ConversationMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    text: str
    time: str | None = None
    verdict: str | None = None


class ConversationDetailResponse(BaseModel):
    conversation_id: str
    workspace_id: str | None = None
    messages: list[ConversationMessage] = Field(default_factory=list)
    title: str | None = None
    updated_at: str | None = None
    turn_count: int = 0
    last_verdict: str | None = None


class RunDetailResponse(BaseModel):
    run_id: str
    conversation_id: str | None = None
    workspace_id: str | None = None
    message: str | None = None
    status: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    verdict: str | None = None
    confidence: str | None = None
    steps: list[RunStep] = Field(default_factory=list)
    answer_delta_summary: dict[str, Any] = Field(default_factory=dict)
    models: list[dict[str, Any]] = Field(default_factory=list)
    audit: dict[str, Any] | None = None
    final: dict[str, Any] | None = None
    artifact: dict[str, Any] | None = None
    persistence: dict[str, Any] | None = None


class RenderPdfRequest(BaseModel):
    proposal: dict[str, Any]
    template: str = "project_proposal"


class GenerateImageRequest(BaseModel):
    prompt: str
    size: str = "1024x1024"
    reference_image_urls: list[str] = Field(default_factory=list)


class NarrateSummaryRequest(BaseModel):
    text: str
    voice: str = "zh-CN-XiaoxiaoNeural"


class ProduceRequest(BaseModel):
    workspace_id: str = "demo-corpus"
    conversation_id: str | None = None
    feasibility: dict[str, Any]
    corpus: dict[str, Any] = Field(default_factory=dict)
    market: dict[str, Any] = Field(default_factory=dict)
    audit: dict[str, Any] = Field(default_factory=dict)
    answer: dict[str, Any] = Field(default_factory=dict)
    proposal: dict[str, Any] = Field(default_factory=dict)
    reference_images: list[dict[str, Any]] = Field(default_factory=list)
    narrative: str | None = None
    text: str | None = None


class ChatRequest(BaseModel):
    workspace_id: str = "demo-corpus"
    message: str
    conversation_id: str | None = None
    playbook: str | None = None
    artifact_mode: str | None = None
    ui_context: dict[str, Any] = Field(default_factory=dict)
