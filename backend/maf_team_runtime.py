"""Dynamic collaboration selection and execution for first-class MAF agents."""

from __future__ import annotations

import asyncio
import copy
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Callable, Literal
from uuid import uuid4

from agent_framework import (
    AgentExecutorResponse,
    FunctionalWorkflow,
    Workflow,
    workflow,
)
from agent_framework.orchestrations import SequentialBuilder
from pydantic import BaseModel, Field

from .maf_agents import MafAgentRegistry
from .maf_contracts import (
    MAX_MAF_REVISIONS,
    CollaborationPattern,
    CollaborationPlan,
    MafAgentRecord,
    MafRunSummary,
    MafRuntimeMode,
)


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
]

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


class MafTeamRequest(BaseModel):
    """Normalized semantic routing fields plus bounded participant input."""

    intent: str
    output_mode: str
    needs_workspace: bool
    needs_external: bool
    high_impact: bool
    payload: dict[str, Any] = Field(default_factory=dict)


class RuntimeCollaborationPlan(CollaborationPlan):
    selected_agents: tuple[str, ...]
    reason_codes: tuple[str, ...] = ()
    required_branches: tuple[str, ...] = ()


class MafRuntimeEvent(BaseModel):
    sequence: int = Field(ge=1)
    event: RuntimeEventName
    status: str
    agent_id: str | None = None
    branch_id: str | None = None
    source_agent_id: str | None = None
    target_agent_id: str | None = None
    duration_ms: float | None = Field(default=None, ge=0)
    reason_codes: tuple[str, ...] = ()
    mode: str | None = None
    selected_agents: tuple[str, ...] = ()
    skipped_agents: tuple[str, ...] = ()
    max_revisions: int | None = Field(default=None, ge=0, le=MAX_MAF_REVISIONS)
    verdict: str | None = None
    error_category: str | None = None


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


class RuntimeMafRunSummary(MafRunSummary):
    mode: str
    rounds: int = Field(default=0, ge=0, le=MAX_MAF_REVISIONS)
    selected_agents: tuple[str, ...] = ()
    skipped_agents: tuple[str, ...] = ()


class MafTeamRunResult(BaseModel):
    summary: RuntimeMafRunSummary
    events: list[MafRuntimeEvent]
    branch_results: list[MafBranchResult] = Field(default_factory=list)
    artifact: dict[str, Any] = Field(default_factory=dict)
    gaps: list[str] = Field(default_factory=list)
    degraded: bool = False
    branch_overlap_ms: float = Field(default=0, ge=0)
    completed_agents: set[str] = Field(default_factory=set)


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
            max_revisions=MAX_MAF_REVISIONS,
        )
    specialist = _specialist_for_intent(intent)
    return _plan(
        CollaborationPattern.SPECIALIST_HANDOFF,
        ("df-coordinator", specialist),
        (_intent_reason_code(intent),),
    )


@dataclass
class _RunState:
    events: list[MafRuntimeEvent] = field(default_factory=list)
    completed_agents: set[str] = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def emit(self, event: RuntimeEventName, status: str, **kwargs: Any) -> MafRuntimeEvent:
        async with self.lock:
            item = MafRuntimeEvent(
                sequence=len(self.events) + 1,
                event=event,
                status=status,
                **kwargs,
            )
            self.events.append(item)
            return item


@dataclass(frozen=True)
class _BranchObservation:
    event: Literal["started", "completed", "failed"]
    branch_id: str
    agent_id: AgentId
    required: bool
    observed_ns: int
    output: dict[str, Any] = field(default_factory=dict)
    error_category: str | None = None


def _normalize_agent_output(response: Any) -> dict[str, Any]:
    if isinstance(response, Mapping):
        return dict(response)
    value = getattr(response, "value", None)
    if isinstance(value, Mapping):
        return dict(value)
    text = getattr(response, "text", None)
    if isinstance(text, str):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}
        return dict(parsed) if isinstance(parsed, Mapping) else {"value": parsed}
    return {"value": value if value is not None else response}


def _error_category(error: Exception) -> str:
    if isinstance(error, TransientAgentError):
        return "transient"
    if isinstance(error, (TypeError, ValueError)):
        return "contract_validation"
    return "permanent"


def _workflow_error_category(details: Any) -> str:
    error_type = str(getattr(details, "error_type", ""))
    if error_type == TransientAgentError.__name__:
        return "transient"
    if error_type in {TypeError.__name__, ValueError.__name__}:
        return "contract_validation"
    return "permanent"


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


class MafTeamRuntime:
    """Run a selected team using agents from an authorization-bound registry."""

    def __init__(self, registry: MafAgentRegistry, *, max_revisions: int = MAX_MAF_REVISIONS) -> None:
        self._registry = registry
        self._max_revisions = min(MAX_MAF_REVISIONS, max(0, max_revisions))
        self.last_workflow: FunctionalWorkflow | None = None
        self.last_pattern_workflow: Workflow | None = None

    async def run(self, request: MafTeamRequest | Mapping[str, Any]) -> MafTeamRunResult:
        normalized = request if isinstance(request, MafTeamRequest) else MafTeamRequest.model_validate(request)
        plan = select_collaboration_plan(
            intent=normalized.intent,
            output_mode=normalized.output_mode,
            needs_workspace=normalized.needs_workspace,
            needs_external=normalized.needs_external,
            high_impact=normalized.high_impact,
        )
        if plan.max_revisions:
            plan = plan.model_copy(update={"max_revisions": min(plan.max_revisions, self._max_revisions)})
        state = _RunState()

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
        if plan.pattern is CollaborationPattern.DIRECT:
            artifact, gaps, degraded, branches, overlap, rounds = await self._run_direct(request, state)
        elif plan.pattern is CollaborationPattern.CONCURRENT_RESEARCH:
            artifact, gaps, degraded, branches, overlap, rounds = await self._run_concurrent(
                request, state, max_revisions=plan.max_revisions
            )
        elif plan.pattern is CollaborationPattern.SPECIALIST_HANDOFF:
            artifact, gaps, degraded, branches, overlap, rounds = await self._run_handoff(request, state)
        else:
            artifact, gaps, degraded, branches, overlap, rounds = await self._run_review(request, state)

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
        )

    async def _invoke(
        self,
        agent_id: AgentId,
        payload: Mapping[str, Any],
        state: _RunState,
        *,
        branch_id: str | None = None,
    ) -> tuple[dict[str, Any], Exception | None]:
        started_ns = time.perf_counter_ns()
        await state.emit(
            "maf_agent_started",
            "running",
            agent_id=agent_id,
            branch_id=branch_id,
        )
        try:
            response = await self._registry.agent(agent_id).run(
                json.dumps(payload, ensure_ascii=False, default=str)
            )
        except Exception as error:
            await state.emit(
                "maf_agent_completed",
                "failed",
                agent_id=agent_id,
                branch_id=branch_id,
                duration_ms=(time.perf_counter_ns() - started_ns) / 1_000_000,
                error_category=_error_category(error),
            )
            return {}, error
        output = _normalize_agent_output(response)
        state.completed_agents.add(agent_id)
        await state.emit(
            "maf_agent_completed",
            "completed",
            agent_id=agent_id,
            branch_id=branch_id,
            duration_ms=(time.perf_counter_ns() - started_ns) / 1_000_000,
        )
        return output, None

    async def _run_direct(
        self, request: MafTeamRequest, state: _RunState
    ) -> tuple[dict[str, Any], list[str], bool, list[MafBranchResult], float, int]:
        output, error = await self._invoke("df-coordinator", request.payload, state)
        return output, (["coordinator_unavailable"] if error else []), error is not None, [], 0, 0

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
        branch_runs = [
            self._run_isolated_branch(
                workflow,
                branch_id=branch_id,
                agent_id=agent_id,
                required=required,
                payload=request.payload,
                observations=observations,
            )
            for branch_id, agent_id, required, workflow in workflows
        ]
        await asyncio.gather(*branch_runs, return_exceptions=True)
        observed_results = await observer
        branch_results = [observed_results[agent_id] for _, agent_id, _ in branch_specs]
        by_id = {branch.branch_id: branch for branch in branch_results}
        corpus = by_id["workspace"]
        market = by_id["external"]
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

        artifact = {
            "hits": corpus.output.get("hits", []),
            "external_signals": market.output.get("signals", []),
            "strong_verdict_allowed": corpus.status == "completed",
        }
        feasibility, feasibility_error = await self._invoke(
            "df-feasibility-analyst",
            {"request": request.payload, "evidence": artifact, "gaps": gaps},
            state,
        )
        artifact["feasibility"] = feasibility
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
            audit, audit_error = await self._run_audit(state, artifact, revision=0)
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
    ) -> None:
        terminal_observed = False
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
                        terminal_observed = True
                        await observations.put(
                            _BranchObservation(
                                "completed",
                                branch_id,
                                agent_id,
                                required,
                                observed_ns,
                                output=_normalize_agent_output(response),
                            )
                        )
                elif framework_event.type == "executor_failed":
                    terminal_observed = True
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
        coordinator, coordinator_error = await self._invoke("df-coordinator", request.payload, state)
        target = _specialist_for_intent(request.intent)
        await state.emit(
            "maf_handoff",
            "running",
            source_agent_id="df-coordinator",
            target_agent_id=target,
            reason_codes=(_intent_reason_code(request.intent),),
        )
        specialist, specialist_error = await self._invoke(
            target,
            {"request": request.payload, "coordinator": coordinator},
            state,
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
        return specialist, gaps, bool(gaps), [], 0, 0

    async def _run_audit(
        self,
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
            {"artifact": dict(artifact), "revision": revision},
            state,
        )
        verdict = str(audit.get("verdict") or "unknown")
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
            audit, audit_error = await self._run_audit(state, last_valid_artifact, revision=revisions)
            verdict = str(audit.get("verdict") or "unknown")
            if audit_error:
                gaps.append("audit_unavailable")
                return _force_insufficient_evidence(last_valid_artifact) | {"verdict": "insufficient_evidence"}, gaps, revisions
            if verdict != "revise":
                return {**last_valid_artifact, "audit": audit, "verdict": verdict}, gaps, revisions
            if revisions >= max_revisions:
                return _force_insufficient_evidence(last_valid_artifact) | {"verdict": "insufficient_evidence"}, gaps, revisions

            await state.emit(
                "maf_review",
                "revision_requested",
                agent_id="df-auditor",
                verdict=verdict,
                reason_codes=(f"revision:{revisions}",),
            )
            revisions += 1
            analysis, analyst_error = await self._invoke(
                "df-feasibility-analyst",
                {
                    "request": request.payload,
                    "previous_artifact": last_valid_artifact,
                    "audit": audit,
                    "revision": revisions,
                },
                state,
            )
            if analyst_error:
                gaps.append("feasibility_unavailable")
                return _force_insufficient_evidence(last_valid_artifact) | {"verdict": "insufficient_evidence"}, gaps, revisions
            last_valid_artifact = replace_analysis(last_valid_artifact, analysis)

    async def _run_review(
        self, request: MafTeamRequest, state: _RunState
    ) -> tuple[dict[str, Any], list[str], bool, list[MafBranchResult], float, int]:
        artifact, analyst_error = await self._invoke("df-feasibility-analyst", request.payload, state)
        if analyst_error:
            return {"verdict": "insufficient_evidence"}, ["feasibility_unavailable"], True, [], 0, 0
        artifact, gaps, revisions = await self._run_bounded_audit(
            request,
            state,
            artifact,
            max_revisions=self._max_revisions,
            replace_analysis=lambda _current, analysis: analysis,
        )
        return artifact, gaps, bool(gaps), [], 0, revisions


__all__ = [
    "MafBranchResult",
    "MafRuntimeEvent",
    "MafTeamRequest",
    "MafTeamRunResult",
    "MafTeamRuntime",
    "RuntimeCollaborationPlan",
    "RuntimeMafRunSummary",
    "TransientAgentError",
    "select_collaboration_plan",
]
