"""Microsoft Agent Framework (MAF) orchestration for the audit ⇄ revise loop.

Why this exists
---------------
The legacy reasoning path ran the experts as a *fixed* pipeline: analyst →
auditor → (at most one) revision. In practice the single revision rarely fired,
so the multi-agent system behaved like a straight-line script with no real
branching — "流水线式调用，没有实际判断".

This module re-expresses the audit/revision stage as a real **Microsoft Agent
Framework workflow graph** (GA 1.0, ``agent-framework`` package). The auditor's
verdict drives a genuine *conditional edge*: a ``revise`` verdict routes control
back to the analyst (carrying the audit feedback), a ``pass`` verdict routes to
finalize. The cycle repeats up to ``max_revisions`` rounds. The routing decision
is now owned by the graph and the agent's own judgment, not by hard-coded
``if`` statements buried in the orchestrator.

The executors deliberately wrap the *existing* ``_run_feasibility_analyst`` /
``_audit_artifact`` functions instead of re-defining the Foundry agents, so the
battle-tested guardrails, schemas and prompts are reused verbatim — MAF supplies
the orchestration topology, not new model behavior.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Never

try:
    from .maf_contracts import MAX_MAF_REVISIONS, MafRuntimeMode, runtime_mode
except ImportError:  # pragma: no cover - supports direct module execution
    from maf_contracts import MAX_MAF_REVISIONS, MafRuntimeMode, runtime_mode

try:  # MAF is optional: if the package is absent we degrade to the legacy path.
    from agent_framework import WorkflowBuilder, WorkflowContext, executor

    MAF_AVAILABLE = True
    MAF_IMPORT_ERROR: str | None = None
except Exception as exc:  # pragma: no cover - exercised only when dep missing
    MAF_AVAILABLE = False
    MAF_IMPORT_ERROR = str(exc)


def maf_enabled() -> bool:
    """True when the operator opted into the MAF reasoning core *and* it imports."""
    return MAF_AVAILABLE and runtime_mode() in (MafRuntimeMode.AUDIT, MafRuntimeMode.FULL)


def default_max_revisions() -> int:
    try:
        return min(MAX_MAF_REVISIONS, max(0, int(os.environ.get("DF_MAF_MAX_REVISIONS", "2"))))
    except ValueError:
        return 2


@dataclass
class MafState:
    """Single message type that flows along every edge of the workflow."""

    artifact: dict[str, Any]
    audit: Any = None  # AuditVerdict (kept loosely typed to avoid a circular import)
    audit_meta: dict[str, Any] = field(default_factory=dict)
    revision: int = 0
    max_revisions: int = 2
    # Ordered record of each node execution; the orchestrator renders these as
    # SSE frames so the existing UI (Agent Flow, model_response, audit) is intact.
    steps: list[dict[str, Any]] = field(default_factory=list)


# The condition predicates are the actual "judgment" of the graph. They run on
# the message produced by the auditor node and decide which outgoing edge fires.
def _needs_revision(state: MafState) -> bool:
    audit = state.audit
    if audit is None:
        return False
    return (
        getattr(audit, "verdict", None) == "revise"
        and getattr(audit, "target_expert", None) == "df-feasibility-analyst"
        and state.revision < state.max_revisions
    )


def _is_done(state: MafState) -> bool:
    return not _needs_revision(state)


def graph_description(max_revisions: int) -> dict[str, Any]:
    """Static, serialisable description of the workflow graph for the UI/PPT."""
    return {
        "framework": "Microsoft Agent Framework",
        "framework_version": "1.0 (GA)",
        "package": "agent-framework",
        "pattern": "graph workflow with conditional cyclic edges",
        "start": "auditor",
        "max_revisions": max_revisions,
        "nodes": [
            {"id": "auditor", "agent": "df-auditor", "role": "审计 / 出具 verdict"},
            {"id": "reviser", "agent": "df-feasibility-analyst", "role": "依据审计反馈修订"},
            {"id": "finalize", "agent": None, "role": "收敛输出"},
        ],
        "edges": [
            {"from": "auditor", "to": "reviser", "condition": "verdict==revise 且未达到修订上限"},
            {"from": "auditor", "to": "finalize", "condition": "verdict==pass 或达到上限"},
            {"from": "reviser", "to": "auditor", "condition": "修订后重新审计"},
        ],
    }


def build_feasibility_workflow(
    req: Any,
    run_analyst: Callable[..., dict[str, Any]],
    audit_fn: Callable[..., tuple[Any, dict[str, Any]]],
):
    """Build a per-request MAF workflow that owns the audit ⇄ revise loop.

    ``run_analyst(req, artifact, audit_feedback) -> feasibility dict`` and
    ``audit_fn(req, artifact) -> (AuditVerdict, meta)`` are the existing
    orchestrator functions, injected so the graph reuses the real agents.
    """

    @executor(id="auditor")
    async def auditor_node(state: MafState, ctx: WorkflowContext[MafState]) -> None:
        # Tell the auditor which round this is: round 0 gets the strict rigor bar,
        # later rounds only re-check for genuine material problems (so the loop
        # converges instead of inventing fresh gaps every pass).
        state.artifact["_audit_round"] = state.revision
        # audit_fn does a blocking LLM call; run it off the event loop so the
        # orchestrator's SSE heartbeats keep flowing (otherwise a 30s+ silent
        # gap drops the browser connection mid-analysis).
        audit, meta = await asyncio.to_thread(audit_fn, req, state.artifact)
        state.audit = audit
        state.audit_meta = meta
        state.steps.append(
            {
                "node": "auditor",
                "agent": "df-auditor",
                "revision": state.revision,
                "meta": meta,
                "audit": audit.model_dump(),
            }
        )
        await ctx.send_message(state)

    @executor(id="reviser")
    async def reviser_node(state: MafState, ctx: WorkflowContext[MafState]) -> None:
        state.revision += 1
        previous = state.artifact.get("feasibility") or {}
        # run_analyst also blocks on an LLM call — keep it off the event loop.
        revised = await asyncio.to_thread(run_analyst, req, state.artifact, state.audit.model_dump())
        revised_meta = revised.get("_llm", {})
        # Preserve the prior (good) feasibility if the revision itself errored —
        # mirrors the legacy fallback so a failed retry never downgrades output.
        if (
            revised_meta.get("mode") == "fallback_after_agent_error"
            and previous
            and previous.get("_llm", {}).get("mode") != "fallback_after_agent_error"
        ):
            preserved = {**previous}
            preserved_meta = dict(preserved.get("_llm") or {})
            warnings = list(preserved_meta.get("evidence_warnings") or [])
            warnings.append("revision_failed_preserved_previous_feasibility")
            preserved_meta["evidence_warnings"] = warnings
            preserved_meta["revision_warning"] = str(revised_meta.get("error") or "")[:500]
            preserved["_llm"] = preserved_meta
            state.artifact["feasibility"] = preserved
        else:
            state.artifact["feasibility"] = revised
        state.steps.append(
            {
                "node": "reviser",
                "agent": "df-feasibility-analyst",
                "revision": state.revision,
                "meta": revised_meta,
            }
        )
        await ctx.send_message(state)

    @executor(id="finalize")
    async def finalize_node(state: MafState, ctx: WorkflowContext[Never, MafState]) -> None:
        await ctx.yield_output(state)

    workflow = (
        WorkflowBuilder(start_executor=auditor_node)
        .add_edge(auditor_node, reviser_node, condition=_needs_revision)
        .add_edge(auditor_node, finalize_node, condition=_is_done)
        .add_edge(reviser_node, auditor_node)
        .build()
    )
    return workflow


async def run_feasibility_audit_loop(
    req: Any,
    artifact: dict[str, Any],
    run_analyst: Callable[..., dict[str, Any]],
    audit_fn: Callable[..., tuple[Any, dict[str, Any]]],
    *,
    max_revisions: int | None = None,
) -> MafState:
    """Run the MAF audit/revise workflow and return the terminal state.

    ``state.audit`` is the final verdict, ``state.steps`` is the ordered per-node
    log the orchestrator turns into SSE frames, and ``artifact`` is mutated
    in place exactly like the legacy path.
    """
    rounds = default_max_revisions() if max_revisions is None else min(MAX_MAF_REVISIONS, max(0, max_revisions))
    workflow = build_feasibility_workflow(req, run_analyst, audit_fn)
    initial = MafState(artifact=artifact, max_revisions=rounds)
    result = await workflow.run(initial)
    outputs = result.get_outputs()
    if outputs:
        final = outputs[-1]
        if isinstance(final, MafState):
            return final
    # Defensive fallback: the loop always yields, but never trust that blindly.
    return initial
