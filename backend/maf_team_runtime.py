"""Dynamic collaboration selection and execution for first-class MAF agents."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json
import logging
import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Literal
from uuid import uuid4

from agent_framework import (
    AgentExecutorResponse,
    FunctionalWorkflow,
    Workflow,
    workflow,
)
from agent_framework.orchestrations import SequentialBuilder
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .maf_agents import MafAgentRegistry
from .maf_contracts import (
    MAX_MAF_REVISIONS,
    CollaborationPattern,
    CollaborationPlan,
    MafAgentRecord,
    MafRunSummary,
    MafRuntimeMode,
)
from .evidence_bundle import (
    BundleLimits,
    EvidenceBundle,
    MAX_EVIDENCE_ITEMS,
    MAX_EVIDENCE_QUOTE_CHARS,
    build_evidence_bundle,
    bundle_for_agent,
)
from .market_relevance import (
    assess_market_comparison,
    market_relevance_trace,
    public_market_comparison,
    unavailable_market_comparison,
)
from .schemas import Evidence, MarketComparison


logger = logging.getLogger("dataforge.maf")


AgentId = Literal[
    "df-coordinator",
    "df-corpus-analyst",
    "df-market-researcher",
    "df-feasibility-analyst",
    "df-auditor",
    "df-producer",
]
RuntimeEventName = Literal[
    "maf_plan",
    "maf_agent_started",
    "maf_agent_completed",
    "maf_branch_started",
    "maf_branch_joined",
    "maf_handoff",
    "maf_review",
    "maf_budget_exhausted",
]

MAX_MAF_AGENT_CALLS = 8
MAX_MARKET_SOURCES = 6


def _configured_limit(
    configured: int | None,
    *,
    environment_name: str,
    hard_limit: int,
) -> int:
    value = configured
    if value is None:
        try:
            value = int(os.environ.get(environment_name, str(hard_limit)))
        except (TypeError, ValueError):
            value = hard_limit
    return min(hard_limit, max(0, int(value)))

ALL_AGENT_IDS: tuple[AgentId, ...] = (
    "df-coordinator",
    "df-corpus-analyst",
    "df-market-researcher",
    "df-feasibility-analyst",
    "df-auditor",
    "df-producer",
)


class TransientAgentError(RuntimeError):
    """An optional participant failed in a way that may succeed on a later run."""


class ContractValidationError(ValueError):
    """An agent exhausted the single bounded output-contract correction."""


class ExecutionBudgetExceeded(RuntimeError):
    """A bounded runtime budget prevented an additional participant action."""


class MafAuditVerdict(str, Enum):
    PASS = "pass"
    REVISE = "revise"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


_PLACEHOLDER_IDENTITIES = {"", "na", "none", "null", "unknown", "untitled"}
_PLACEHOLDER_CHUNK_IDENTITIES = _PLACEHOLDER_IDENTITIES | {"chunk"}


def _identity_is_placeholder(value: Any, *, chunk: bool = False) -> bool:
    text = str(value or "").strip().lower()
    placeholders = _PLACEHOLDER_CHUNK_IDENTITIES if chunk else _PLACEHOLDER_IDENTITIES
    components = [text, *re.split(r"[#:]+", text)]
    for component in components:
        compact = re.sub(r"[^a-z0-9]+", "", component)
        if compact in placeholders:
            return True
        filename = re.split(r"[/\\]", component)[-1]
        stem = filename.rsplit(".", 1)[0] if "." in filename else filename
        if re.sub(r"[^a-z0-9]+", "", stem) in placeholders:
            return True
    return False


class AuthoritativeCorpusHit(BaseModel):
    """Typed retrieval hit; semantic validity is checked fail-close at execution."""

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    source_file: str | None = None
    sheet: str | None = None
    row: str | int | None = None
    chunk_id: str | None = None
    content: str | None = None

    def is_traceable(self) -> bool:
        if self.source_file is not None and _identity_is_placeholder(self.source_file):
            return False
        if self.chunk_id is not None and _identity_is_placeholder(self.chunk_id, chunk=True):
            return False
        if self.id is not None and _identity_is_placeholder(self.id, chunk=True):
            return False
        return bool(
            str(self.id or "").strip()
            or (
                str(self.source_file or "").strip()
                and (
                    str(self.chunk_id or "").strip()
                    or str(self.row or "").strip()
                )
            )
        )

    def evidence_refs(self) -> set[str]:
        parts = [
            str(self.source_file or "unknown"),
            str(self.sheet or ""),
            str(self.row or ""),
            str(self.chunk_id or self.id or "chunk"),
        ]
        refs = {"#".join(part for part in parts if part)}
        if self.id:
            refs.add(self.id)
        return refs


class AuthoritativeCorpus(BaseModel):
    """Backend-owned retrieval result passed to MAF participants."""

    model_config = ConfigDict(extra="allow")

    hits: list[AuthoritativeCorpusHit] = Field(default_factory=list)
    profile: dict[str, Any] = Field(default_factory=dict)
    opportunities: list[dict[str, Any]] = Field(default_factory=list)


class RubricScoreScale(BaseModel):
    min: float
    max: float
    precision: float = Field(gt=0)

    @model_validator(mode="after")
    def _ordered_range(self) -> "RubricScoreScale":
        if self.max <= self.min:
            raise ValueError("rubric score max must be greater than min")
        return self


class RubricDimension(BaseModel):
    name: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    weight: float = Field(ge=0, le=1)
    criteria: dict[str, str]


class FeasibilityRubric(BaseModel):
    """Typed authoritative rubric loaded from the repository."""

    rubric_version: str = Field(min_length=1)
    score_scale: RubricScoreScale
    dimensions: list[RubricDimension] = Field(min_length=1)
    verdict_thresholds: dict[str, dict[str, float | int]]
    confidence_policy: dict[str, str]
    calibration_gate: dict[str, Any]

    @model_validator(mode="after")
    def _unique_dimensions(self) -> "FeasibilityRubric":
        names = [dimension.name for dimension in self.dimensions]
        if len(names) != len(set(names)):
            raise ValueError("rubric dimension names must be unique")
        return self


class MafTeamRequest(BaseModel):
    """Normalized semantic routing fields plus bounded participant input."""

    intent: str
    output_mode: str
    needs_workspace: bool
    needs_external: bool
    high_impact: bool
    payload: dict[str, Any] = Field(default_factory=dict)
    authoritative_corpus: AuthoritativeCorpus = Field(default_factory=AuthoritativeCorpus)
    evidence_catalog: list[Evidence] = Field(default_factory=list)
    rubric: FeasibilityRubric | None = None
    rubric_version: str | None = None
    evidence_bundle: EvidenceBundle | None = None

    @model_validator(mode="before")
    @classmethod
    def _malformed_required_evidence_becomes_empty(cls, value: Any) -> Any:
        if not isinstance(value, Mapping) or not value.get("needs_workspace"):
            return value
        normalized = dict(value)
        try:
            normalized["authoritative_corpus"] = AuthoritativeCorpus.model_validate(
                normalized.get("authoritative_corpus") or {}
            )
        except (TypeError, ValueError):
            normalized["authoritative_corpus"] = AuthoritativeCorpus()
        try:
            catalog = normalized.get("evidence_catalog") or []
            if not isinstance(catalog, list):
                raise TypeError("evidence_catalog must be a list")
            normalized["evidence_catalog"] = [
                item if isinstance(item, Evidence) else Evidence.model_validate(item)
                for item in catalog
            ]
        except (TypeError, ValueError):
            normalized["evidence_catalog"] = []
        return normalized

    @model_validator(mode="after")
    def _rubric_version_matches(self) -> "MafTeamRequest":
        if self.rubric is not None and self.rubric_version != self.rubric.rubric_version:
            raise ValueError("rubric_version must match the typed rubric")
        return self


class RuntimeCollaborationPlan(CollaborationPlan):
    selected_agents: tuple[str, ...]
    reason_codes: tuple[str, ...] = ()
    required_branches: tuple[str, ...] = ()


class MafRuntimeEvent(BaseModel):
    sequence: int = Field(ge=1)
    event: RuntimeEventName
    status: Literal["running", "completed", "failed", "revision_requested", "budget_exhausted"]
    agent_id: AgentId | None = None
    branch_id: str | None = None
    source_agent_id: AgentId | None = None
    target_agent_id: AgentId | None = None
    duration_ms: float | None = Field(default=None, ge=0)
    reason_codes: tuple[str, ...] = ()
    mode: str | None = None
    selected_agents: tuple[str, ...] = ()
    skipped_agents: tuple[str, ...] = ()
    max_revisions: int | None = Field(default=None, ge=0, le=MAX_MAF_REVISIONS)
    verdict: MafAuditVerdict | None = None
    error_category: Literal["transient", "content_policy", "contract_validation", "permanent", "budget_exhausted"] | None = None
    response_id: str | None = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    retry_count: int | None = Field(default=None, ge=0, le=100)
    tool_names: tuple[str, ...] | None = Field(default=None, max_length=12)
    cache_hit: bool | None = None
    started_ns: int | None = Field(default=None, ge=0)
    completed_ns: int | None = Field(default=None, ge=0)

    @field_validator("tool_names")
    @classmethod
    def _validate_tool_names(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is None:
            return None
        if any(
            not name
            or len(name) > 80
            or _SAFE_TELEMETRY_NAME.fullmatch(name) is None
            for name in value
        ):
            raise ValueError("tool_names must contain bounded telemetry identifiers")
        return value


class MafBranchResult(BaseModel):
    branch_id: str
    agent_id: str
    required: bool
    status: Literal["completed", "failed"]
    output: dict[str, Any] = Field(default_factory=dict)
    started_ns: int
    completed_ns: int
    error_category: str | None = None

    @property
    def duration_ms(self) -> float:
        return (self.completed_ns - self.started_ns) / 1_000_000


class RuntimeExecutionBudget(BaseModel):
    max_agent_calls: int = Field(ge=0)
    max_market_sources: int = Field(ge=0)
    agent_calls: int = Field(ge=0)
    max_revision_rounds: int = Field(ge=0, le=MAX_MAF_REVISIONS)
    workflow_duration_ms: float = Field(ge=0)
    participant_duration_ms: float = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    budget_exhausted: bool = False
    termination_reasons: tuple[str, ...] = ()


def _unknown_execution_budget() -> RuntimeExecutionBudget:
    return RuntimeExecutionBudget(
        max_agent_calls=0,
        max_market_sources=0,
        agent_calls=0,
        max_revision_rounds=0,
        workflow_duration_ms=0,
        participant_duration_ms=0,
    )


class RuntimeMafRunSummary(MafRunSummary):
    mode: str
    rounds: int = Field(default=0, ge=0, le=MAX_MAF_REVISIONS)
    selected_agents: tuple[str, ...] = ()
    skipped_agents: tuple[str, ...] = ()
    execution_budget: RuntimeExecutionBudget = Field(default_factory=_unknown_execution_budget)
    evidence_bundle: dict[str, Any] = Field(default_factory=dict)


class MafTeamRunResult(BaseModel):
    summary: RuntimeMafRunSummary
    events: list[MafRuntimeEvent]
    branch_results: list[MafBranchResult] = Field(default_factory=list)
    artifact: dict[str, Any] = Field(default_factory=dict)
    gaps: list[str] = Field(default_factory=list)
    degraded: bool = False
    branch_overlap_ms: float = Field(default=0, ge=0)
    completed_agents: set[str] = Field(default_factory=set)
    market_relevance_trace: dict[str, Any] = Field(default_factory=dict)


def _market_gate_context(request: MafTeamRequest) -> tuple[str, str]:
    opportunity_parts = [str(request.payload.get("query") or "")]
    for item in request.authoritative_corpus.opportunities[:8]:
        if isinstance(item, Mapping):
            opportunity_parts.extend(
                str(item.get(key) or "")
                for key in ("id", "title", "description")
                if item.get(key)
            )
    evidence_parts = [str(hit.content or "") for hit in request.authoritative_corpus.hits[:24]]
    evidence_parts.extend(str(item.quote or "") for item in request.evidence_catalog[:24])
    return "\n".join(opportunity_parts), "\n".join(evidence_parts)


def _unavailable_market_for_request(request: MafTeamRequest) -> dict[str, Any]:
    opportunity, _ = _market_gate_context(request)
    return unavailable_market_comparison(opportunity)


def _agent_records(agent_ids: tuple[str, ...]) -> list[MafAgentRecord]:
    return [MafAgentRecord(agent_id=agent_id, role=agent_id) for agent_id in agent_ids]


def _plan(
    pattern: CollaborationPattern,
    selected_agents: tuple[str, ...],
    reason_codes: tuple[str, ...],
    *,
    required_branches: tuple[str, ...] = (),
    max_revisions: int = 0,
) -> RuntimeCollaborationPlan:
    return RuntimeCollaborationPlan(
        pattern=pattern,
        agents=_agent_records(selected_agents),
        selected_agents=selected_agents,
        reason_codes=reason_codes,
        required_branches=required_branches,
        max_revisions=max_revisions,
    )


def _specialist_for_intent(intent: str) -> AgentId:
    specialists: dict[str, AgentId] = {
        "corpus_qa": "df-corpus-analyst",
        "workspace_research": "df-corpus-analyst",
        "market_research": "df-market-researcher",
        "feasibility_analysis": "df-feasibility-analyst",
        "audit": "df-auditor",
        "review": "df-auditor",
        "produce": "df-producer",
        "artifact_generation": "df-producer",
    }
    return specialists.get(intent, "df-feasibility-analyst")


def _intent_reason_code(intent: str) -> str:
    """Return a bounded observability code without echoing untrusted intent text."""
    known_intents = {
        "corpus_qa",
        "workspace_research",
        "market_research",
        "feasibility_analysis",
        "audit",
        "review",
        "produce",
        "artifact_generation",
    }
    return f"intent:{intent}" if intent in known_intents else "intent:other"


def select_collaboration_plan(
    *,
    intent: str,
    output_mode: str,
    needs_workspace: bool,
    needs_external: bool,
    high_impact: bool,
) -> RuntimeCollaborationPlan:
    """Select a collaboration pattern from normalized semantic fields only."""
    if output_mode == "chat" and not high_impact and not needs_external:
        return _plan(
            CollaborationPattern.DIRECT,
            ("df-coordinator",),
            ("lightweight_chat",),
            required_branches=("workspace",) if needs_workspace else (),
        )
    if needs_workspace and needs_external:
        return _plan(
            CollaborationPattern.CONCURRENT_RESEARCH,
            (
                "df-corpus-analyst",
                "df-market-researcher",
                "df-feasibility-analyst",
                "df-auditor",
            ),
            ("workspace_evidence_required", "external_signal_required"),
            required_branches=("workspace",),
            max_revisions=MAX_MAF_REVISIONS if high_impact else 0,
        )
    if high_impact:
        return _plan(
            CollaborationPattern.BOUNDED_REVIEW,
            ("df-feasibility-analyst", "df-auditor"),
            ("high_impact_review_required",),
            required_branches=("workspace",) if needs_workspace else (),
            max_revisions=MAX_MAF_REVISIONS,
        )
    specialist = _specialist_for_intent(intent)
    return _plan(
        CollaborationPattern.SPECIALIST_HANDOFF,
        ("df-coordinator", specialist),
        (_intent_reason_code(intent),),
        required_branches=("workspace",) if needs_workspace else (),
    )


@dataclass
class _RunState:
    events: list[MafRuntimeEvent] = field(default_factory=list)
    completed_agents: set[str] = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    event_sink: Callable[[MafRuntimeEvent], Any] | None = None
    started_ns: int = field(default_factory=time.perf_counter_ns)
    max_agent_calls: int = MAX_MAF_AGENT_CALLS
    agent_calls: int = 0
    termination_reasons: list[str] = field(default_factory=list)

    async def emit(self, event: RuntimeEventName, status: str, **kwargs: Any) -> MafRuntimeEvent:
        async with self.lock:
            item = MafRuntimeEvent(
                sequence=len(self.events) + 1,
                event=event,
                status=status,
                **kwargs,
            )
            self.events.append(item)
            if self.event_sink is not None:
                pending = self.event_sink(item)
                if inspect.isawaitable(pending):
                    await pending
            return item

    async def reserve_agent_call(self, agent_id: AgentId, branch_id: str | None) -> bool:
        async with self.lock:
            if self.agent_calls < self.max_agent_calls:
                self.agent_calls += 1
                return True
        await self.record_budget_exhaustion("agent_calls_exhausted", agent_id, branch_id)
        return False

    async def record_budget_exhaustion(
        self,
        reason: str,
        agent_id: AgentId | None = None,
        branch_id: str | None = None,
    ) -> None:
        async with self.lock:
            if "budget_exhausted" not in self.termination_reasons:
                self.termination_reasons.append("budget_exhausted")
            if reason not in self.termination_reasons:
                self.termination_reasons.append(reason)
        await self.emit(
            "maf_budget_exhausted",
            "budget_exhausted",
            agent_id=agent_id,
            branch_id=branch_id,
            reason_codes=("budget_exhausted", reason),
        )


@dataclass(frozen=True)
class _BranchObservation:
    event: Literal["started", "completed", "failed"]
    branch_id: str
    agent_id: AgentId
    required: bool
    observed_ns: int
    output: dict[str, Any] = field(default_factory=dict)
    error_category: str | None = None
    telemetry: dict[str, Any] = field(default_factory=dict)


def _normalize_agent_output(response: Any) -> dict[str, Any]:
    if isinstance(response, Mapping):
        return dict(response)
    try:
        value = getattr(response, "value", None)
    except (TypeError, ValueError):
        value = None
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        if isinstance(dumped, Mapping):
            return dict(dumped)
    text = getattr(response, "text", None)
    if isinstance(text, str):
        stripped = text.strip()
        if stripped.startswith("```") and stripped.endswith("```"):
            lines = stripped.splitlines()
            stripped = "\n".join(lines[1:-1]).strip() if len(lines) >= 3 else stripped
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            start = stripped.find("{")
            end = stripped.rfind("}")
            if start < 0 or end <= start:
                return {"text": text}
            try:
                parsed = json.loads(stripped[start : end + 1])
            except json.JSONDecodeError:
                return {"text": text}
        return dict(parsed) if isinstance(parsed, Mapping) else {"value": parsed}
    return {"value": value if value is not None else response}


_SAFE_TELEMETRY_NAME = re.compile(r"^[A-Za-z0-9_.:-]+$")


def _safe_telemetry_name(value: Any, *, limit: int) -> str | None:
    text = str(value or "").strip()
    if not text or len(text) > limit or not _SAFE_TELEMETRY_NAME.fullmatch(text):
        return None
    return text


def _safe_nonnegative_int(value: Any, *, maximum: int | None = None) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    normalized = max(0, int(value))
    return min(normalized, maximum) if maximum is not None else normalized


def _safe_agent_telemetry(response: Any) -> dict[str, Any]:
    """Project only trusted runtime/provider response attributes into telemetry."""
    sources: list[Mapping[str, Any]] = []
    additional = getattr(response, "additional_properties", None)
    if isinstance(additional, Mapping):
        sources.append(additional)

    response_id = _safe_telemetry_name(getattr(response, "response_id", None), limit=128)
    if response_id is None:
        response_id = next(
            (
                safe
                for source in sources
                if (safe := _safe_telemetry_name(source.get("response_id"), limit=128)) is not None
            ),
            None,
        )

    response_usage = getattr(response, "usage_details", None)
    usage_sources = [response_usage] if isinstance(response_usage, Mapping) else []

    def token_value(*names: str) -> int | None:
        for usage in usage_sources:
            for name in names:
                value = _safe_nonnegative_int(usage.get(name))
                if value is not None:
                    return value
        return None

    retry_count = next(
        (
            value
            for source in sources
            if (value := _safe_nonnegative_int(source.get("retry_count"), maximum=100)) is not None
        ),
        None,
    )
    tool_names: list[str] = []
    for source in sources:
        candidates = source.get("tool_names")
        if not isinstance(candidates, (list, tuple)):
            continue
        for candidate in candidates:
            safe = _safe_telemetry_name(candidate, limit=80)
            if safe and safe not in tool_names:
                tool_names.append(safe)
            if len(tool_names) >= 12:
                break
        if len(tool_names) >= 12:
            break

    cache_hit: bool | None = None
    for source in sources:
        direct = source.get("cache_hit")
        cache = source.get("cache")
        nested = cache.get("hit") if isinstance(cache, Mapping) else None
        candidate = direct if isinstance(direct, bool) else nested
        if isinstance(candidate, bool):
            cache_hit = candidate
            break

    metadata = {
        "response_id": response_id,
        "input_tokens": token_value("input_tokens", "input_token_count"),
        "output_tokens": token_value("output_tokens", "output_token_count"),
        "total_tokens": token_value("total_tokens", "total_token_count"),
        "retry_count": retry_count,
        "tool_names": tuple(tool_names),
        "cache_hit": cache_hit,
    }
    return {key: value for key, value in metadata.items() if value is not None and value != ()}


def _aggregate_agent_telemetry(
    attempts: list[dict[str, Any]],
    *,
    contract_retries: int,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if attempts:
        response_id = attempts[-1].get("response_id")
        if response_id is not None:
            metadata["response_id"] = response_id
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            if all(key in attempt for attempt in attempts):
                metadata[key] = sum(int(attempt[key]) for attempt in attempts)

        tool_names: list[str] = []
        for attempt in attempts:
            for name in attempt.get("tool_names") or ():
                if name not in tool_names:
                    tool_names.append(name)
                if len(tool_names) == 12:
                    break
            if len(tool_names) == 12:
                break
        if tool_names:
            metadata["tool_names"] = tuple(tool_names)

        cache_hit = attempts[-1].get("cache_hit")
        if isinstance(cache_hit, bool):
            metadata["cache_hit"] = cache_hit

    provider_retries = sum(int(attempt.get("retry_count") or 0) for attempt in attempts)
    retry_count = provider_retries + contract_retries
    if retry_count:
        metadata["retry_count"] = min(100, retry_count)
    return metadata


def _contract_correction(error: Exception, contract_name: str) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    error_rows = getattr(error, "errors", None)
    if callable(error_rows):
        try:
            for item in error_rows(include_input=False)[:12]:
                issues.append(
                    {
                        "location": [str(part)[:80] for part in item.get("loc") or ()],
                        "type": str(item.get("type") or "validation_error")[:80],
                    }
                )
        except TypeError:
            issues = []
    if not issues:
        issues = [{"type": type(error).__name__[:80]}]
    return {
        "contract": contract_name,
        "instruction": "Return only one JSON object that satisfies the named contract.",
        "issues": issues,
    }


def _valid_required_corpus(request: MafTeamRequest) -> bool:
    if not request.needs_workspace:
        return True
    valid_hits = [
        hit
        for hit in request.authoritative_corpus.hits
        if str(hit.content or "").strip() and hit.is_traceable()
    ]
    if not valid_hits:
        return False
    traceable_refs = {
        ref
        for hit in valid_hits
        for ref in hit.evidence_refs()
    }
    return any(
        item.source_type == "corpus"
        and not _identity_is_placeholder(item.ref, chunk=True)
        and item.ref.strip() in traceable_refs
        and str(item.quote or "").strip()
        for item in request.evidence_catalog
    )


_CONTENT_POLICY_MARKERS = (
    "content_filter",
    "content policy",
    "content_policy",
    "responsibleaipolicyviolation",
    "safety policy",
)
_TRANSIENT_MARKERS = (
    "connecterror",
    "connect error",
    "connecttimeout",
    "readtimeout",
    "dns",
    "getaddrinfo",
    "name resolution",
    "nodename nor servname",
    "timeout",
    "timed out",
    "connection",
    "network",
    "rate limit",
    "ratelimit",
    "temporar",
    "service unavailable",
)


def _error_status_code(error: Any) -> int | None:
    for candidate in (
        getattr(error, "status_code", None),
        getattr(getattr(error, "response", None), "status_code", None),
    ):
        if isinstance(candidate, int):
            return candidate
    return None


def _error_descriptor(error: Any) -> str:
    nested = getattr(error, "error", None)
    values = (
        type(error).__name__,
        getattr(error, "error_type", None),
        getattr(error, "code", None),
        getattr(nested, "code", None),
        getattr(error, "message", None),
        str(error),
    )
    return " ".join(str(value) for value in values if value).lower()[:2000]


def _safe_error_diagnostic(error: Any) -> dict[str, Any]:
    error_type = str(getattr(error, "error_type", None) or type(error).__name__)[:80]
    message = str(getattr(error, "message", None) or str(error))[:2000]
    lowered = message.lower()
    status_code = _error_status_code(error)
    if status_code is None:
        status_match = re.search(r"(?:error|status)\s+code\s*[:=]\s*(\d{3})", message, re.IGNORECASE)
        if status_match:
            status_code = int(status_match.group(1))
    provider_match = re.search(
        r"['\"]code['\"]\s*:\s*['\"]([A-Za-z0-9_.-]{1,80})",
        message,
    )
    missing_attribute_match = re.search(
        r"has no attribute\s+['\"]([A-Za-z_][A-Za-z0-9_]{0,79})['\"]",
        message,
    )
    traceback_text = str(getattr(error, "traceback", None) or "")[:12000]
    traceback_frames = re.findall(
        r'File\s+"([^"]+)",\s+line\s+(\d+),\s+in\s+([A-Za-z_][A-Za-z0-9_]*)',
        traceback_text,
    )
    inner_error_types = [
        candidate
        for candidate in re.findall(r"\b([A-Z][A-Za-z0-9_]*(?:Error|Exception))\(", message)
        if candidate != error_type
    ]
    reason_hint = "unclassified"
    for markers, label in (
        (("permissiondenied", "permission denied"), "permission_denied"),
        (("invalid subscription key", "invalid api key"), "invalid_credential"),
        (("api version not supported", "unsupported api version"), "api_version_unsupported"),
        (("different context", "contextvar", "different event loop"), "context_mismatch"),
        (("event loop is closed",), "event_loop_closed"),
        (("client has been closed", "client is closed"), "client_closed"),
        (("unexpected keyword argument",), "client_signature"),
        (("maximum context length", "too many tokens", "context_length_exceeded"), "context_limit"),
        (("no running event loop", "cannot be called from a running event loop"), "event_loop"),
        (("not supported", "unsupported"), "unsupported_operation"),
        (("not iterable", "not subscriptable", "indices must be integers"), "client_contract"),
    ):
        if any(marker in lowered for marker in markers):
            reason_hint = label
            break
    diagnostic: dict[str, Any] = {
        "error_type": error_type,
        "reason_hint": reason_hint,
        "message_length": len(message),
        "message_fingerprint": hashlib.sha256(message.encode("utf-8")).hexdigest()[:12],
    }
    if status_code is not None:
        diagnostic["status_code"] = status_code
    if provider_match:
        diagnostic["provider_code"] = provider_match.group(1)
    if missing_attribute_match:
        diagnostic["missing_attribute"] = missing_attribute_match.group(1)
    if inner_error_types:
        diagnostic["inner_error_type"] = inner_error_types[-1][:80]
    if traceback_frames:
        path, line, function = traceback_frames[-1]
        diagnostic["origin_file"] = re.split(r"[/\\]", path)[-1][:80]
        diagnostic["origin_function"] = function[:80]
        diagnostic["origin_line"] = int(line)
    return diagnostic


def _log_agent_failure(agent_id: str, branch_id: str | None, error: Any) -> None:
    diagnostic = _safe_error_diagnostic(error)
    logger.warning(
        "maf_agent_failure agent=%s branch=%s error_type=%s inner_error_type=%s "
        "status_code=%s provider_code=%s reason_hint=%s missing_attribute=%s "
        "origin=%s:%s:%s message_length=%s fingerprint=%s",
        agent_id,
        branch_id or "none",
        diagnostic.get("error_type", "unknown"),
        diagnostic.get("inner_error_type", "none"),
        diagnostic.get("status_code", "none"),
        diagnostic.get("provider_code", "none"),
        diagnostic.get("reason_hint", "unclassified"),
        diagnostic.get("missing_attribute", "none"),
        diagnostic.get("origin_file", "none"),
        diagnostic.get("origin_line", "none"),
        diagnostic.get("origin_function", "none"),
        diagnostic.get("message_length", 0),
        diagnostic.get("message_fingerprint", "none"),
    )


def classify_agent_error(error: Exception) -> str:
    descriptor = _error_descriptor(error)
    if any(marker in descriptor for marker in _CONTENT_POLICY_MARKERS):
        return "content_policy"
    status_code = _error_status_code(error)
    if isinstance(error, (TransientAgentError, TimeoutError, ConnectionError)):
        return "transient"
    if status_code in {408, 409, 425, 429} or (status_code is not None and status_code >= 500):
        return "transient"
    if any(marker in descriptor for marker in _TRANSIENT_MARKERS):
        return "transient"
    if isinstance(error, (TypeError, ValueError)):
        return "contract_validation"
    return "permanent"


def _error_category(error: Exception) -> str:
    return classify_agent_error(error)


def classify_workflow_error(details: Any) -> str:
    descriptor = _error_descriptor(details)
    if any(marker in descriptor for marker in _CONTENT_POLICY_MARKERS):
        return "content_policy"
    status_code = _error_status_code(details)
    if status_code in {408, 409, 425, 429} or (status_code is not None and status_code >= 500):
        return "transient"
    if any(marker in descriptor for marker in _TRANSIENT_MARKERS):
        return "transient"
    error_type = str(getattr(details, "error_type", ""))
    if error_type == TransientAgentError.__name__:
        return "transient"
    if error_type in {TypeError.__name__, ValueError.__name__, ContractValidationError.__name__}:
        return "contract_validation"
    return "permanent"


def _workflow_error_category(details: Any) -> str:
    return classify_workflow_error(details)


def _force_insufficient_evidence(value: Any) -> Any:
    """Preserve usable content while removing every nested positive verdict claim."""
    if isinstance(value, Mapping):
        return {
            str(key): (
                "insufficient_evidence"
                if str(key) == "verdict" or str(key).endswith("_verdict")
                else _force_insufficient_evidence(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_force_insufficient_evidence(item) for item in value]
    return value


def _disputed_dimensions(audit: Mapping[str, Any]) -> list[str]:
    dimensions: list[str] = []
    for issue in audit.get("issues") or []:
        if not isinstance(issue, Mapping):
            continue
        dimension = str(issue.get("dimension") or "").strip()
        if dimension and dimension not in dimensions:
            dimensions.append(dimension)
    return dimensions


class MafTeamRuntime:
    """Run a selected team using agents from an authorization-bound registry."""

    def __init__(
        self,
        registry: MafAgentRegistry,
        *,
        max_revisions: int | None = None,
        max_agent_calls: int | None = None,
        max_market_sources: int | None = None,
        bundle_limits: BundleLimits | None = None,
        feasibility_validator: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        audit_validator: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self._registry = registry
        self._max_revisions = _configured_limit(
            max_revisions,
            environment_name="DF_MAF_MAX_REVISIONS",
            hard_limit=MAX_MAF_REVISIONS,
        )
        self._max_agent_calls = _configured_limit(
            max_agent_calls,
            environment_name="DF_MAF_MAX_AGENT_CALLS",
            hard_limit=MAX_MAF_AGENT_CALLS,
        )
        self._max_market_sources = _configured_limit(
            max_market_sources,
            environment_name="DF_MAF_MAX_MARKET_SOURCES",
            hard_limit=MAX_MARKET_SOURCES,
        )
        self._bundle_limits = bundle_limits or BundleLimits(
            max_items=MAX_EVIDENCE_ITEMS,
            max_quote_chars=MAX_EVIDENCE_QUOTE_CHARS,
        )
        self._feasibility_validator = feasibility_validator
        self._audit_validator = audit_validator or self._validate_audit_verdict
        self.last_workflow: FunctionalWorkflow | None = None
        self.last_pattern_workflow: Workflow | None = None

    @staticmethod
    def _validate_audit_verdict(output: dict[str, Any]) -> dict[str, Any]:
        verdict = MafAuditVerdict(str(output.get("verdict") or ""))
        if verdict not in {MafAuditVerdict.PASS, MafAuditVerdict.REVISE}:
            raise ValueError("agent audit verdict must be pass or revise")
        return {**output, "verdict": verdict.value}

    @staticmethod
    def _validate_market_comparison(output: dict[str, Any]) -> dict[str, Any]:
        return MarketComparison.model_validate(output).model_dump(
            mode="json",
            by_alias=True,
        )

    async def run(
        self,
        request: MafTeamRequest | Mapping[str, Any],
        *,
        event_sink: Callable[[MafRuntimeEvent], Any] | None = None,
    ) -> MafTeamRunResult:
        normalized = request if isinstance(request, MafTeamRequest) else MafTeamRequest.model_validate(request)
        bundle = build_evidence_bundle(
            normalized.authoritative_corpus,
            {"workspace_id": normalized.payload.get("workspace_id"), "intent": normalized.intent},
            normalized.payload.get("capability_packs") if isinstance(normalized.payload.get("capability_packs"), list) else [],
            self._bundle_limits,
        )
        normalized = normalized.model_copy(update={"evidence_bundle": bundle})
        plan = select_collaboration_plan(
            intent=normalized.intent,
            output_mode=normalized.output_mode,
            needs_workspace=normalized.needs_workspace,
            needs_external=normalized.needs_external,
            high_impact=normalized.high_impact,
        )
        if plan.max_revisions:
            plan = plan.model_copy(update={"max_revisions": min(plan.max_revisions, self._max_revisions)})
        state = _RunState(event_sink=event_sink, max_agent_calls=self._max_agent_calls)

        @workflow(name=f"dataforge-{plan.pattern.value}")
        async def team_workflow(_: dict[str, Any]) -> MafTeamRunResult:
            return await self._execute(normalized, plan, state)

        self.last_workflow = team_workflow
        run_result = await team_workflow.run(normalized.model_dump())
        outputs = run_result.get_outputs()
        if not outputs or not isinstance(outputs[-1], MafTeamRunResult):
            raise RuntimeError("MAF team workflow did not produce a typed result")
        return outputs[-1]

    async def _execute(
        self,
        request: MafTeamRequest,
        plan: RuntimeCollaborationPlan,
        state: _RunState,
    ) -> MafTeamRunResult:
        await state.emit(
            "maf_plan",
            "completed",
            reason_codes=plan.reason_codes,
            mode=plan.pattern.value,
            selected_agents=plan.selected_agents,
            skipped_agents=tuple(agent_id for agent_id in ALL_AGENT_IDS if agent_id not in plan.selected_agents),
            max_revisions=plan.max_revisions,
        )
        if request.needs_workspace and not _valid_required_corpus(request):
            artifact = {
                "verdict": MafAuditVerdict.INSUFFICIENT_EVIDENCE.value,
                "strong_verdict_allowed": False,
            }
            gaps = ["workspace_evidence_unavailable"]
            degraded = True
            branches = []
            overlap = 0
            rounds = 0
        elif plan.pattern is CollaborationPattern.DIRECT:
            artifact, gaps, degraded, branches, overlap, rounds = await self._run_direct(request, state)
        elif plan.pattern is CollaborationPattern.CONCURRENT_RESEARCH:
            artifact, gaps, degraded, branches, overlap, rounds = await self._run_concurrent(
                request, state, max_revisions=plan.max_revisions
            )
        elif plan.pattern is CollaborationPattern.SPECIALIST_HANDOFF:
            artifact, gaps, degraded, branches, overlap, rounds = await self._run_handoff(request, state)
        else:
            artifact, gaps, degraded, branches, overlap, rounds = await self._run_review(request, state)

        artifact = dict(artifact)
        artifact["capability_packs"] = list(request.evidence_bundle.capability_packs) if request.evidence_bundle else []
        # Capability packs provide questions and validation methods only. The evidence
        # guard remains the authority for every verdict, including degraded paths.
        artifact["verdict_source"] = "evidence_guard"
        relevance_trace = dict(artifact.pop("_market_relevance_trace", {}))

        selected = plan.selected_agents
        records = [
            MafAgentRecord(
                agent_id=agent_id,
                role=agent_id,
                status="completed" if agent_id in state.completed_agents else "failed",
            )
            for agent_id in selected
        ]
        skipped = tuple(agent_id for agent_id in ALL_AGENT_IDS if agent_id not in selected)
        completed_events = [
            event
            for event in state.events
            if event.event == "maf_agent_completed"
        ]

        def observed_token_total(field_name: str) -> int | None:
            if not completed_events or any(getattr(event, field_name) is None for event in completed_events):
                return None
            return sum(int(getattr(event, field_name) or 0) for event in completed_events)

        execution_budget = RuntimeExecutionBudget(
            max_agent_calls=self._max_agent_calls,
            max_market_sources=self._max_market_sources,
            agent_calls=state.agent_calls,
            max_revision_rounds=plan.max_revisions,
            workflow_duration_ms=(time.perf_counter_ns() - state.started_ns) / 1_000_000,
            participant_duration_ms=sum(float(event.duration_ms or 0) for event in completed_events),
            input_tokens=observed_token_total("input_tokens"),
            output_tokens=observed_token_total("output_tokens"),
            total_tokens=observed_token_total("total_tokens"),
            budget_exhausted=bool(state.termination_reasons),
            termination_reasons=tuple(state.termination_reasons),
        )
        summary = RuntimeMafRunSummary(
            run_id=str(uuid4()),
            runtime_mode=MafRuntimeMode.FULL,
            collaboration=plan,
            status="degraded" if degraded else "completed",
            revisions=rounds,
            agents=records,
            mode=plan.pattern.value,
            rounds=rounds,
            selected_agents=selected,
            skipped_agents=skipped,
            execution_budget=execution_budget,
            evidence_bundle=request.evidence_bundle.persisted_metadata() if request.evidence_bundle else {},
            metadata={"gaps": gaps, "degraded": degraded},
        )
        return MafTeamRunResult(
            summary=summary,
            events=state.events,
            branch_results=branches,
            artifact=artifact,
            gaps=gaps,
            degraded=degraded,
            branch_overlap_ms=overlap,
            completed_agents=state.completed_agents,
            market_relevance_trace=relevance_trace,
        )

    async def _invoke(
        self,
        agent_id: AgentId,
        payload: Mapping[str, Any],
        state: _RunState,
        *,
        branch_id: str | None = None,
        validator: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        contract_name: str | None = None,
    ) -> tuple[dict[str, Any], Exception | None]:
        if not await state.reserve_agent_call(agent_id, branch_id):
            return {}, ExecutionBudgetExceeded("agent_calls_exhausted")
        started_ns = time.perf_counter_ns()
        await state.emit(
            "maf_agent_started",
            "running",
            agent_id=agent_id,
            branch_id=branch_id,
        )
        attempts: list[dict[str, Any]] = []
        contract_retries = 0
        current_payload = dict(payload)
        while True:
            if contract_retries and not await state.reserve_agent_call(agent_id, branch_id):
                error = ExecutionBudgetExceeded("agent_calls_exhausted")
                completed_ns = time.perf_counter_ns()
                telemetry = _aggregate_agent_telemetry(
                    attempts,
                    contract_retries=contract_retries,
                )
                await state.emit(
                    "maf_agent_completed",
                    "failed",
                    agent_id=agent_id,
                    branch_id=branch_id,
                    duration_ms=(completed_ns - started_ns) / 1_000_000,
                    error_category="budget_exhausted",
                    started_ns=started_ns,
                    completed_ns=completed_ns,
                    **telemetry,
                )
                return {}, error
            try:
                response = await self._registry.agent(agent_id).run(
                    json.dumps(current_payload, ensure_ascii=False, default=str)
                )
            except Exception as error:
                _log_agent_failure(agent_id, branch_id, error)
                completed_ns = time.perf_counter_ns()
                telemetry = _aggregate_agent_telemetry(
                    attempts,
                    contract_retries=contract_retries,
                )
                await state.emit(
                    "maf_agent_completed",
                    "failed",
                    agent_id=agent_id,
                    branch_id=branch_id,
                    duration_ms=(completed_ns - started_ns) / 1_000_000,
                    error_category=_error_category(error),
                    started_ns=started_ns,
                    completed_ns=completed_ns,
                    **telemetry,
                )
                return {}, error

            output = _normalize_agent_output(response)
            attempts.append(_safe_agent_telemetry(response))
            if validator is None:
                break
            try:
                output = validator(output)
                break
            except (TypeError, ValueError) as validation_error:
                if contract_retries == 0:
                    contract_retries = 1
                    current_payload = {
                        **dict(payload),
                        "contract_correction": _contract_correction(
                            validation_error,
                            contract_name or "structured_output",
                        ),
                    }
                    continue
                error = ContractValidationError(
                    f"{contract_name or 'structured output'} validation failed after one correction"
                )
                completed_ns = time.perf_counter_ns()
                telemetry = _aggregate_agent_telemetry(
                    attempts,
                    contract_retries=contract_retries,
                )
                await state.emit(
                    "maf_agent_completed",
                    "failed",
                    agent_id=agent_id,
                    branch_id=branch_id,
                    duration_ms=(completed_ns - started_ns) / 1_000_000,
                    error_category="contract_validation",
                    started_ns=started_ns,
                    completed_ns=completed_ns,
                    **telemetry,
                )
                return {}, error

        telemetry = _aggregate_agent_telemetry(
            attempts,
            contract_retries=contract_retries,
        )
        state.completed_agents.add(agent_id)
        completed_ns = time.perf_counter_ns()
        await state.emit(
            "maf_agent_completed",
            "completed",
            agent_id=agent_id,
            branch_id=branch_id,
            duration_ms=(completed_ns - started_ns) / 1_000_000,
            started_ns=started_ns,
            completed_ns=completed_ns,
            **telemetry,
        )
        return output, None

    @staticmethod
    def _participant_payload(
        request: MafTeamRequest,
        agent_id: AgentId = "df-coordinator",
    ) -> dict[str, Any]:
        payload = dict(request.payload)
        # Selection metadata is persisted on the run, but agents receive only the
        # relevant registered guidance through their evidence-bundle view.
        payload.pop("capability_packs", None)
        history = payload.get("conversation_history")
        if isinstance(history, list):
            bounded_history: list[dict[str, str]] = []
            for item in history[-6:]:
                if not isinstance(item, Mapping):
                    continue
                role = str(item.get("role") or "user")[:24]
                content = str(item.get("content") or item.get("text") or "")[:1200]
                if content:
                    bounded_history.append({"role": role, "content": content})
            payload["conversation_history"] = bounded_history

        bundle = request.evidence_bundle
        if bundle is None:
            bundle = build_evidence_bundle(
                request.authoritative_corpus,
                {"workspace_id": request.payload.get("workspace_id"), "intent": request.intent},
                [],
            )
        return {**payload, "evidence_bundle": bundle_for_agent(bundle, agent_id)}

    @staticmethod
    def _feasibility_payload(
        request: MafTeamRequest,
        artifact: Mapping[str, Any],
        *,
        audit_feedback: Mapping[str, Any] | None = None,
        revision: int = 0,
        revision_scope: list[str] | None = None,
    ) -> dict[str, Any]:
        bundle = request.evidence_bundle
        if bundle is None:
            bundle = build_evidence_bundle(
                request.authoritative_corpus,
                {"workspace_id": request.payload.get("workspace_id"), "intent": request.intent},
                [],
            )
        payload = {
            "workspace_id": request.payload.get("workspace_id"),
            "user_request": request.payload.get("query"),
            "candidate_opportunities": request.authoritative_corpus.opportunities[:6],
            "evidence_bundle": bundle_for_agent(bundle, "df-feasibility-analyst"),
            "rubric": request.rubric.model_dump(mode="json") if request.rubric is not None else None,
            "rubric_version": request.rubric_version,
            "market": artifact.get("market") or {},
            "revision_round": revision,
        }
        if audit_feedback is not None:
            feedback = dict(audit_feedback)
            if revision_scope is not None:
                feedback["issues"] = [
                    dict(issue)
                    for issue in feedback.get("issues") or []
                    if isinstance(issue, Mapping) and str(issue.get("dimension") or "") in revision_scope
                ]
            payload["audit_feedback"] = feedback
        if revision_scope is not None:
            payload["revision_scope"] = list(revision_scope)
        previous = artifact.get("feasibility")
        if isinstance(previous, Mapping):
            previous_feasibility = dict(previous)
            if revision_scope is not None and isinstance(previous_feasibility.get("dimensions"), list):
                previous_feasibility["dimensions"] = [
                    dict(dimension)
                    for dimension in previous_feasibility["dimensions"]
                    if isinstance(dimension, Mapping)
                    and str(dimension.get("name") or "") in revision_scope
                ]
            payload["previous_feasibility"] = previous_feasibility
        return payload

    async def _run_direct(
        self, request: MafTeamRequest, state: _RunState
    ) -> tuple[dict[str, Any], list[str], bool, list[MafBranchResult], float, int]:
        output, error = await self._invoke(
            "df-coordinator",
            self._participant_payload(request, "df-coordinator"),
            state,
        )
        if error:
            return (
                {"verdict": "insufficient_evidence", "strong_verdict_allowed": False},
                ["coordinator_unavailable"],
                True,
                [],
                0,
                0,
            )
        return output, [], False, [], 0, 0

    async def _run_branch(
        self,
        branch_id: str,
        agent_id: AgentId,
        required: bool,
        payload: Mapping[str, Any],
        state: _RunState,
    ) -> MafBranchResult:
        started_ns = time.perf_counter_ns()
        await state.emit(
            "maf_branch_started",
            "running",
            agent_id=agent_id,
            branch_id=branch_id,
        )
        output, error = await self._invoke(agent_id, payload, state, branch_id=branch_id)
        completed_ns = time.perf_counter_ns()
        status: Literal["completed", "failed"] = "failed" if error else "completed"
        await state.emit(
            "maf_branch_joined",
            status,
            agent_id=agent_id,
            branch_id=branch_id,
            duration_ms=(completed_ns - started_ns) / 1_000_000,
            error_category=_error_category(error) if error else None,
        )
        return MafBranchResult(
            branch_id=branch_id,
            agent_id=agent_id,
            required=required,
            status=status,
            output=output,
            started_ns=started_ns,
            completed_ns=completed_ns,
            error_category=_error_category(error) if error else None,
        )

    async def _run_concurrent(
        self,
        request: MafTeamRequest,
        state: _RunState,
        *,
        max_revisions: int,
    ) -> tuple[dict[str, Any], list[str], bool, list[MafBranchResult], float, int]:
        branch_specs: tuple[tuple[str, AgentId, bool], ...] = (
            ("workspace", "df-corpus-analyst", True),
            ("external", "df-market-researcher", False),
        )
        workflows = [
            (
                branch_id,
                agent_id,
                required,
                SequentialBuilder(participants=[self._registry.agent(agent_id)]).build(),
            )
            for branch_id, agent_id, required in branch_specs
        ]
        self.last_pattern_workflow = workflows[0][3]
        observations: asyncio.Queue[_BranchObservation] = asyncio.Queue()
        observer = asyncio.create_task(
            self._observe_concurrent_branches(observations, state, expected=len(branch_specs))
        )
        branch_tasks = [
            asyncio.create_task(
                self._run_isolated_branch(
                    workflow,
                    branch_id=branch_id,
                    agent_id=agent_id,
                    required=required,
                    payload=self._participant_payload(request, agent_id),
                    observations=observations,
                    state=state,
                    validator=(
                        self._validate_market_comparison
                        if agent_id == "df-market-researcher"
                        else None
                    ),
                )
            )
            for branch_id, agent_id, required, workflow in workflows
        ]
        try:
            pending = set(branch_tasks)
            while pending:
                completed, pending = await asyncio.wait(
                    pending,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if any(task.cancelled() for task in completed):
                    raise asyncio.CancelledError()
            observed_results = await observer
        finally:
            for task in branch_tasks:
                if not task.done():
                    task.cancel()
            if not observer.done():
                observer.cancel()
            await asyncio.gather(*branch_tasks, observer, return_exceptions=True)
        branch_results = [observed_results[agent_id] for _, agent_id, _ in branch_specs]
        by_id = {branch.branch_id: branch for branch in branch_results}
        corpus = by_id["workspace"]
        market = by_id["external"]
        if (
            market.status == "completed"
            and len(list(market.output.get("competitors") or [])) > self._max_market_sources
        ):
            await state.record_budget_exhaustion(
                "market_sources_exhausted",
                "df-market-researcher",
                "external",
            )
            market = market.model_copy(
                update={"status": "failed", "output": {}, "error_category": "budget_exhausted"}
            )
        branches = [corpus, market]
        overlap_ns = max(
            0,
            min(branch.completed_ns for branch in branches)
            - max(branch.started_ns for branch in branches),
        )
        gaps: list[str] = []
        if corpus.status == "failed":
            gaps.append("workspace_evidence_unavailable")
        if market.status == "failed":
            gaps.append("external_signal_unavailable")

        artifact: dict[str, Any] = {
            "hits": [item.model_dump(mode="json", exclude_none=True) for item in request.authoritative_corpus.hits],
            "strong_verdict_allowed": corpus.status == "completed" and _valid_required_corpus(request),
        }
        if market.status == "failed":
            unavailable_market = _unavailable_market_for_request(request)
            artifact["market"] = public_market_comparison(unavailable_market)
            artifact["_market_relevance_trace"] = market_relevance_trace(unavailable_market)
            gaps.append("external_market_evidence_unavailable")
        elif market.status == "completed":
            opportunity, evidence_digest = _market_gate_context(request)
            gated_market = assess_market_comparison(opportunity, evidence_digest, market.output)
            artifact["market"] = public_market_comparison(gated_market)
            artifact["_market_relevance_trace"] = market_relevance_trace(gated_market)
            if gated_market["market_evidence_status"] == "unavailable":
                gaps.append("external_market_evidence_unavailable")
        if not artifact["strong_verdict_allowed"]:
            return (
                _force_insufficient_evidence(artifact)
                | {"verdict": MafAuditVerdict.INSUFFICIENT_EVIDENCE.value},
                gaps,
                True,
                branches,
                overlap_ns / 1_000_000,
                0,
            )
        feasibility, feasibility_error = await self._invoke(
            "df-feasibility-analyst",
            self._feasibility_payload(request, artifact),
            state,
            validator=self._feasibility_validator,
            contract_name="FeasibilityReport",
        )
        artifact["feasibility"] = feasibility
        artifact["_blind_feasibility"] = copy.deepcopy(feasibility)
        if feasibility_error:
            gaps.append("feasibility_unavailable")
        if feasibility_error:
            return (
                _force_insufficient_evidence(artifact) | {"verdict": "insufficient_evidence"},
                gaps,
                True,
                branches,
                overlap_ns / 1_000_000,
                0,
            )

        if max_revisions:
            artifact, review_gaps, rounds = await self._run_bounded_audit(
                request,
                state,
                artifact,
                max_revisions=max_revisions,
                replace_analysis=lambda current, analysis: {**current, "feasibility": analysis},
            )
            gaps.extend(gap for gap in review_gaps if gap not in gaps)
        else:
            audit, audit_error = await self._run_audit(request, state, artifact, revision=0)
            artifact["audit"] = audit
            if audit_error:
                gaps.append("audit_unavailable")
                artifact = _force_insufficient_evidence(artifact) | {"verdict": "insufficient_evidence"}
            else:
                artifact["verdict"] = audit.get("verdict", feasibility.get("verdict"))
            rounds = 0

        if corpus.status == "failed":
            artifact = _force_insufficient_evidence(artifact) | {"verdict": "insufficient_evidence"}
        return artifact, gaps, bool(gaps), branches, overlap_ns / 1_000_000, rounds

    async def _run_isolated_branch(
        self,
        workflow_instance: Workflow,
        *,
        branch_id: str,
        agent_id: AgentId,
        required: bool,
        payload: Mapping[str, Any],
        observations: asyncio.Queue[_BranchObservation],
        state: _RunState,
        validator: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        terminal_observed = False
        if not await state.reserve_agent_call(agent_id, branch_id):
            await observations.put(
                _BranchObservation(
                    "failed",
                    branch_id,
                    agent_id,
                    required,
                    time.perf_counter_ns(),
                    error_category="budget_exhausted",
                )
            )
            return
        run_stream = workflow_instance.run(
            json.dumps(payload, ensure_ascii=False, default=str), stream=True
        )
        try:
            async for framework_event in run_stream:
                if framework_event.executor_id != agent_id:
                    continue
                observed_ns = time.perf_counter_ns()
                if framework_event.type == "executor_invoked":
                    await observations.put(
                        _BranchObservation("started", branch_id, agent_id, required, observed_ns)
                    )
                elif framework_event.type == "executor_completed":
                    response = next(
                        (
                            item.agent_response
                            for item in (framework_event.data or [])
                            if isinstance(item, AgentExecutorResponse) and item.executor_id == agent_id
                        ),
                        None,
                    )
                    if response is not None:
                        output = _normalize_agent_output(response)
                        try:
                            if validator is not None:
                                output = validator(output)
                        except (TypeError, ValueError):
                            terminal_observed = True
                            await observations.put(
                                _BranchObservation(
                                    "failed",
                                    branch_id,
                                    agent_id,
                                    required,
                                    observed_ns,
                                    error_category="contract_validation",
                                    telemetry=_safe_agent_telemetry(response),
                                )
                            )
                            continue
                        terminal_observed = True
                        await observations.put(
                            _BranchObservation(
                                "completed",
                                branch_id,
                                agent_id,
                                required,
                                observed_ns,
                                output=output,
                                telemetry=_safe_agent_telemetry(response),
                            )
                        )
                elif framework_event.type == "executor_failed":
                    terminal_observed = True
                    _log_agent_failure(agent_id, branch_id, framework_event.data)
                    await observations.put(
                        _BranchObservation(
                            "failed",
                            branch_id,
                            agent_id,
                            required,
                            observed_ns,
                            error_category=_workflow_error_category(framework_event.data),
                        )
                    )
            await run_stream.get_final_response()
        except Exception as error:
            if not terminal_observed:
                _log_agent_failure(agent_id, branch_id, error)
                await observations.put(
                    _BranchObservation(
                        "failed",
                        branch_id,
                        agent_id,
                        required,
                        time.perf_counter_ns(),
                        error_category=_error_category(error),
                    )
                )
            raise

    async def _observe_concurrent_branches(
        self,
        observations: asyncio.Queue[_BranchObservation],
        state: _RunState,
        *,
        expected: int,
    ) -> dict[AgentId, MafBranchResult]:
        started_ns: dict[AgentId, int] = {}
        results: dict[AgentId, MafBranchResult] = {}
        while len(results) < expected:
            observation = await observations.get()
            if observation.event == "started":
                started_ns[observation.agent_id] = observation.observed_ns
                await state.emit(
                    "maf_branch_started",
                    "running",
                    agent_id=observation.agent_id,
                    branch_id=observation.branch_id,
                )
                await state.emit(
                    "maf_agent_started",
                    "running",
                    agent_id=observation.agent_id,
                    branch_id=observation.branch_id,
                )
                continue

            started = started_ns.get(observation.agent_id, observation.observed_ns)
            status: Literal["completed", "failed"] = observation.event
            duration_ms = (observation.observed_ns - started) / 1_000_000
            if status == "completed":
                state.completed_agents.add(observation.agent_id)
            await state.emit(
                "maf_agent_completed",
                status,
                agent_id=observation.agent_id,
                branch_id=observation.branch_id,
                duration_ms=duration_ms,
                error_category=observation.error_category,
                started_ns=started,
                completed_ns=observation.observed_ns,
                **observation.telemetry,
            )
            await state.emit(
                "maf_branch_joined",
                status,
                agent_id=observation.agent_id,
                branch_id=observation.branch_id,
                duration_ms=duration_ms,
                error_category=observation.error_category,
            )
            results[observation.agent_id] = MafBranchResult(
                branch_id=observation.branch_id,
                agent_id=observation.agent_id,
                required=observation.required,
                status=status,
                output=observation.output,
                started_ns=started,
                completed_ns=observation.observed_ns,
                error_category=observation.error_category,
            )
        return results

    async def _run_handoff(
        self, request: MafTeamRequest, state: _RunState
    ) -> tuple[dict[str, Any], list[str], bool, list[MafBranchResult], float, int]:
        coordinator, coordinator_error = await self._invoke(
            "df-coordinator",
            self._participant_payload(request, "df-coordinator"),
            state,
        )
        if coordinator_error:
            return (
                {"verdict": "insufficient_evidence", "strong_verdict_allowed": False},
                ["coordinator_unavailable"],
                True,
                [],
                0,
                0,
            )
        target = _specialist_for_intent(request.intent)
        await state.emit(
            "maf_handoff",
            "running",
            source_agent_id="df-coordinator",
            target_agent_id=target,
            reason_codes=(_intent_reason_code(request.intent),),
        )
        specialist_payload = {
            **self._participant_payload(request, target),
            "coordinator": coordinator,
        }
        validator = None
        contract_name = None
        if target == "df-feasibility-analyst":
            specialist_payload = self._feasibility_payload(request, {})
            validator = self._feasibility_validator
            contract_name = "FeasibilityReport"
        elif target == "df-auditor":
            validator = self._audit_validator
            contract_name = "AuditVerdict"
        elif target == "df-market-researcher":
            validator = self._validate_market_comparison
            contract_name = "MarketComparison"
        specialist, specialist_error = await self._invoke(
            target,
            specialist_payload,
            state,
            validator=validator,
            contract_name=contract_name,
        )
        await state.emit(
            "maf_handoff",
            "completed" if not specialist_error else "failed",
            source_agent_id="df-coordinator",
            target_agent_id=target,
            reason_codes=(_intent_reason_code(request.intent),),
            error_category=_error_category(specialist_error) if specialist_error else None,
        )
        gaps = []
        if coordinator_error:
            gaps.append("coordinator_unavailable")
        if specialist_error:
            gaps.append("specialist_unavailable")
        if target == "df-market-researcher":
            if specialist_error:
                gated_market = _unavailable_market_for_request(request)
            else:
                opportunity, evidence_digest = _market_gate_context(request)
                gated_market = assess_market_comparison(opportunity, evidence_digest, specialist)
            artifact = {
                "market": public_market_comparison(gated_market),
                "_market_relevance_trace": market_relevance_trace(gated_market),
            }
            if gated_market["market_evidence_status"] == "unavailable":
                gaps.append("external_market_evidence_unavailable")
        elif target == "df-feasibility-analyst" and not specialist_error:
            artifact = {
                "feasibility": specialist,
                "_blind_feasibility": copy.deepcopy(specialist),
            }
        elif target == "df-corpus-analyst" and not specialist_error:
            artifact = {
                "hits": [item.model_dump(mode="json", exclude_none=True) for item in request.authoritative_corpus.hits],
                "corpus_summary": specialist,
            }
        else:
            artifact = specialist
        return artifact, gaps, bool(gaps), [], 0, 0

    async def _run_audit(
        self,
        request: MafTeamRequest,
        state: _RunState,
        artifact: Mapping[str, Any],
        *,
        revision: int,
    ) -> tuple[dict[str, Any], Exception | None]:
        await state.emit(
            "maf_review",
            "running",
            agent_id="df-auditor",
            reason_codes=(f"revision:{revision}",),
        )
        audit, audit_error = await self._invoke(
            "df-auditor",
            {
                "workspace_id": request.payload.get("workspace_id"),
                "user_request": request.payload.get("query"),
                "revision_round": revision,
                "feasibility": dict(artifact.get("feasibility") or {}),
                "evidence_bundle": bundle_for_agent(
                    request.evidence_bundle
                    or build_evidence_bundle(
                        request.authoritative_corpus,
                        {"workspace_id": request.payload.get("workspace_id"), "intent": request.intent},
                        [],
                    ),
                    "df-auditor",
                ),
                "market": artifact.get("market") or {},
            },
            state,
            validator=self._audit_validator,
            contract_name="AuditVerdict",
        )
        verdict = MafAuditVerdict(str(audit["verdict"])) if not audit_error else None
        await state.emit(
            "maf_review",
            "completed" if not audit_error else "failed",
            agent_id="df-auditor",
            verdict=verdict,
            reason_codes=(f"revision:{revision}",),
            error_category=_error_category(audit_error) if audit_error else None,
        )
        return audit, audit_error

    async def _run_bounded_audit(
        self,
        request: MafTeamRequest,
        state: _RunState,
        artifact: Mapping[str, Any],
        *,
        max_revisions: int,
        replace_analysis: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    ) -> tuple[dict[str, Any], list[str], int]:
        """Audit a valid analyst artifact, preserving it if a later revision fails."""
        gaps: list[str] = []
        revisions = 0
        last_valid_artifact = copy.deepcopy(dict(artifact))
        while True:
            audit, audit_error = await self._run_audit(
                request,
                state,
                last_valid_artifact,
                revision=revisions,
            )
            verdict = MafAuditVerdict(str(audit["verdict"])) if not audit_error else None
            if audit_error:
                gaps.append("audit_unavailable")
                return _force_insufficient_evidence(last_valid_artifact) | {"verdict": "insufficient_evidence"}, gaps, revisions
            if verdict is not MafAuditVerdict.REVISE:
                return {
                    **last_valid_artifact,
                    "audit": audit,
                    "verdict": verdict.value if verdict is not None else MafAuditVerdict.INSUFFICIENT_EVIDENCE.value,
                }, gaps, revisions
            if revisions >= max_revisions:
                await state.record_budget_exhaustion("revision_rounds_exhausted", "df-feasibility-analyst")
                gaps.append("revision_budget_exhausted")
                return _force_insufficient_evidence(last_valid_artifact) | {"verdict": "insufficient_evidence"}, gaps, revisions

            await state.emit(
                "maf_review",
                "revision_requested",
                agent_id="df-auditor",
                verdict=verdict,
                reason_codes=(f"revision:{revisions}",),
            )
            revisions += 1
            revision_scope = _disputed_dimensions(audit)
            analysis, analyst_error = await self._invoke(
                "df-feasibility-analyst",
                self._feasibility_payload(
                    request,
                    last_valid_artifact,
                    audit_feedback=audit,
                    revision=revisions,
                    revision_scope=revision_scope,
                ),
                state,
                validator=self._feasibility_validator,
                contract_name="FeasibilityReport",
            )
            if analyst_error:
                gaps.append("feasibility_unavailable")
                return _force_insufficient_evidence(last_valid_artifact) | {"verdict": "insufficient_evidence"}, gaps, revisions
            last_valid_artifact = replace_analysis(last_valid_artifact, analysis)

    async def _run_review(
        self, request: MafTeamRequest, state: _RunState
    ) -> tuple[dict[str, Any], list[str], bool, list[MafBranchResult], float, int]:
        analysis, analyst_error = await self._invoke(
            "df-feasibility-analyst",
            self._feasibility_payload(request, {}),
            state,
            validator=self._feasibility_validator,
            contract_name="FeasibilityReport",
        )
        if analyst_error:
            return {"verdict": "insufficient_evidence"}, ["feasibility_unavailable"], True, [], 0, 0
        artifact = {
            "feasibility": analysis,
            "_blind_feasibility": copy.deepcopy(analysis),
        }
        artifact, gaps, revisions = await self._run_bounded_audit(
            request,
            state,
            artifact,
            max_revisions=self._max_revisions,
            replace_analysis=lambda current, revised: {
                **current,
                "feasibility": revised,
            },
        )
        return artifact, gaps, bool(gaps), [], 0, revisions


__all__ = [
    "ContractValidationError",
    "ExecutionBudgetExceeded",
    "MAX_MAF_AGENT_CALLS",
    "MAX_MARKET_SOURCES",
    "AuthoritativeCorpus",
    "AuthoritativeCorpusHit",
    "FeasibilityRubric",
    "MafAuditVerdict",
    "MafBranchResult",
    "MafRuntimeEvent",
    "MafTeamRequest",
    "MafTeamRunResult",
    "MafTeamRuntime",
    "RuntimeCollaborationPlan",
    "RuntimeExecutionBudget",
    "RuntimeMafRunSummary",
    "TransientAgentError",
    "classify_agent_error",
    "classify_workflow_error",
    "select_collaboration_plan",
]
