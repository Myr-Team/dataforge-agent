# Task 3 Report: Collaboration Selector and Team Runtime

## Scope

Owned files only:

- `backend/maf_team_runtime.py`
- `tests/test_maf_team_runtime.py`
- `.superpowers/sdd/task-3-report.md`

The runtime consumes an already-created, authorization-bound `MafAgentRegistry`. It does not import or call `create_agent_registry()`.

## Implementation

- Selects direct, concurrent research, specialist handoff, or bounded review exclusively from normalized intent, output mode, workspace/external evidence requirements, and impact level.
- Executes every participant through the object returned by `MafAgentRegistry.agent(...)`.
- Runs corpus and market as two failure-isolated, single-participant MAF `SequentialBuilder` workflows. Each receives the actual registry Agent, and both workflows are launched concurrently with `asyncio.gather(..., return_exceptions=True)`.
- Keeps deterministic specialist transfer and bounded review inside a real MAF `FunctionalWorkflow`.
- Emits sequenced Pydantic events for plans, participants, branches, handoffs, and review decisions.
- Degrades on optional market failure while preserving corpus evidence.
- Marks required corpus failure as `insufficient_evidence` and disables a stronger verdict.
- Caps review revisions at two.

## TDD Evidence

### RED

Command:

```powershell
python -m pytest tests/test_maf_team_runtime.py -q
```

Result before production implementation: collection failed with `ModuleNotFoundError: No module named 'backend.maf_team_runtime'`.

Historical initial RED: after the concrete orchestration requirement was clarified, an additional focused run asserted a MAF graph workflow for the concurrent pattern. It failed with `AttributeError: 'MafTeamRuntime' object has no attribute 'last_pattern_workflow'` before the first, since-superseded shared-workflow implementation.

### GREEN

Command:

```powershell
python -m pytest tests/test_maf_team_runtime.py -q
```

Result: `9 passed, 3 warnings`.

The warnings are upstream MAF experimental-feature warnings; tests make no network calls.

Command:

```powershell
python -m pytest tests/test_maf_contracts.py tests/test_maf_agents.py tests/test_maf_team_runtime.py -q
```

Result after the concurrent Task 2 remediation was present: `37 passed, 3 warnings`.

Command:

```powershell
python -m compileall -q backend/maf_team_runtime.py tests/test_maf_team_runtime.py
```

Result: exit code `0`.

## Broader Verification

Command:

```powershell
python -m pytest -q
```

Result: `96 passed, 1 failed, 3 warnings`.

The remaining failure is outside Task 3 in `tests/test_tracing_telemetry.py::test_agent_trace_emits_foundry_agent_identity_without_raw_actor_email`. It expects `"OK"` in `repr(opentelemetry.trace.status.Status)`, while the installed OpenTelemetry version renders an object address. No Task 3 file appears in the failure path.

`ruff` and `black` are not installed in this environment, so their checks could not run. `git diff --check` and Python compilation are used as the local static gates.

## API and Dependency Notes

- The installed dependency set is `agent-framework-core==1.11.0`, `agent-framework-foundry==1.10.1`, and `agent-framework-orchestrations==1.0.0`.
- Current evidence execution uses two `SequentialBuilder(participants=[registry_agent])` workflows. Both are real MAF graph workflows whose sole participant is the actual authorization-bound registry Agent.
- Stable `ConcurrentBuilder` cannot provide the required one-Agent failure-isolated branch: its fan-out construction rejects fewer than two targets. It is therefore not used by the current Task 3 implementation.
- MAF marks the outer `FunctionalWorkflow` experimental in this release. The isolated evidence branches use stable graph workflows and public workflow events.

## Concerns

- Full-suite completion is blocked by the unrelated OpenTelemetry representation assertion described above.
- `HandoffBuilder` is model/tool-directed and only accepts concrete `Agent` instances because it clones agents and injects tools. Task 3 instead performs the already-selected semantic handoff deterministically inside the MAF functional workflow, preserving the offline structural fake boundary and preventing model-controlled routing.
- Other workers modified Task 2 and UI files concurrently. Those files were not edited or staged by Task 3.

## Review Remediation

### RED

Command:

```powershell
python -m pytest tests/test_maf_team_runtime.py -q
```

Result: `8 passed, 9 failed, 1 warning in 5.73s`.

The failing regressions covered the missing concurrent revision budget, adapter participants instead of registry agents, missing `maf_plan` rendering metadata, nested positive verdicts after required corpus failure, ignored concurrent `revise` results, unbounded handoff intent text, and analyst failure paths that could still return a positive verdict.

### Implementation

- High-impact workspace-plus-external plans now carry `max_revisions=2`, and the concurrent path applies the same bounded `revise` loop as the dedicated review path.
- A failed initial feasibility analysis skips audit and returns `insufficient_evidence`; a failed revision keeps the last valid analyst artifact, sanitizes every nested `verdict` and `*_verdict`, and returns `insufficient_evidence`.
- Required corpus failure applies that same recursive verdict ceiling to feasibility and audit payloads, not only the top-level verdict.
- Two single-participant `SequentialBuilder` workflows receive `MafAgentRegistry.agent(...)` objects directly. They are launched concurrently, while a shared queue consumes supported `Workflow.run(..., stream=True)` events (`executor_invoked`, `executor_completed`, and `executor_failed`) in arrival order.
- The offline fake is a narrow MAF `SupportsAgentRun`-compatible double: `id`, `name`, `description`, `run(..., stream=...)`, `create_session`, and `get_session`. This matches the current stable Agent participant contract without requiring a Foundry connection.
- `maf_plan` includes `mode`, `selected_agents`, `skipped_agents`, and `max_revisions`; handoff intent reasons are limited to known codes or `intent:other`.

### Stable MAF API Check

The installed dependency set is `agent-framework-core==1.11.0`, `agent-framework-foundry==1.10.1`, and `agent-framework-orchestrations==1.0.0`.

`SequentialBuilder` accepts `participants: Sequence[SupportsAgentRun | Executor]` and supports one actual Agent participant. `Workflow.run(..., stream=True)` emits the lifecycle and executor events consumed by the shared observer. Stable `ConcurrentBuilder` requires at least two fan-out targets, so it cannot supply the required one-Agent failure-isolated branch and is not part of the current implementation.

### GREEN

Command:

```powershell
python -m pytest tests/test_maf_team_runtime.py -q
```

Result: `17 passed, 1 warning in 5.36s`.

Command:

```powershell
python -m pytest tests/test_maf_contracts.py tests/test_maf_agents.py tests/test_maf_team_runtime.py -q
```

Result: `47 passed, 1 warning in 5.28s`.

Command:

```powershell
python -m compileall -q backend/maf_team_runtime.py tests/test_maf_team_runtime.py
git diff --check
```

Result: both commands exited `0`.

### Superseded Shared-Workflow Concern

Historical note: the first remediation still used one shared multi-participant workflow, where a participant failure could terminate the shared orchestration. The second re-review replaced that design with two independently failing `SequentialBuilder` workflows, so this is not a current implementation concern.

## Second Re-review: Failure Isolation and Native Event Order

This section defines the current Task 3 implementation and supersedes all preceding shared-workflow implementation notes.

### RED

Command:

```powershell
python -m pytest tests/test_maf_team_runtime.py::test_immediate_market_failure_does_not_cancel_slow_corpus tests/test_maf_team_runtime.py::test_native_branch_failures_emit_in_arrival_order -q
```

Result: `2 failed, 1 warning in 4.20s`.

The slow corpus branch retained evidence in the offline run, but the runtime delayed the native market failure until after corpus completion and then synthesized failures in fixed workspace/external order. The assertions observed `market_failed.sequence == 8` after `corpus_completed.sequence == 6`, and both-failure order was `df-corpus-analyst`, then `df-market-researcher` despite market failing first.

### Stable Builder Constraint

The first isolation attempt used one `ConcurrentBuilder` per registry Agent. With installed stable `agent-framework-orchestrations==1.0.0`, this is not buildable: `ConcurrentBuilder(participants=[agent]).build()` calls `WorkflowBuilder.add_fan_out_edges(...)`, and stable `FanOutEdgeGroup` rejects one target with `ValueError: FanOutEdgeGroup must contain at least two targets.`

The supported one-Agent orchestration is `SequentialBuilder(participants=[agent]).build()`. It accepts the same public `SupportsAgentRun` contract, wraps the actual registry Agent in a MAF `AgentExecutor`, and emits the same native `executor_invoked`, `executor_completed`, and `executor_failed` workflow events. Adding a dummy second concurrent participant would change branch semantics and introduce unrelated events, so it was not used.

### Implementation

- Workspace and external evidence now run in two independent one-Agent MAF workflows launched together with `asyncio.gather(..., return_exceptions=True)`.
- Each workflow receives its actual `MafAgentRegistry.agent(...)` object exactly once; no agent call is retried or duplicated.
- Both workflow streams publish native start, completion, and failure observations into one shared `asyncio.Queue`.
- One observer consumes that queue and emits `maf_agent_completed` and `maf_branch_joined` in native arrival order instead of synthesizing missing results from fixed branch order.
- An early optional market failure cannot terminate or cancel the independent corpus workflow.

### GREEN

Command:

```powershell
python -m pytest tests/test_maf_team_runtime.py::test_isolated_branch_builders_receive_registry_agents tests/test_maf_team_runtime.py::test_immediate_market_failure_does_not_cancel_slow_corpus tests/test_maf_team_runtime.py::test_native_branch_failures_emit_in_arrival_order -q
```

Result: `3 passed, 1 warning in 3.83s`.

Command:

```powershell
python -m pytest tests/test_maf_team_runtime.py -q
```

Result: `19 passed, 1 warning in 5.20s`.

Command:

```powershell
python -m pytest tests/test_maf_contracts.py tests/test_maf_agents.py tests/test_maf_team_runtime.py -q
```

Result: `49 passed, 1 warning in 5.58s`.

Command:

```powershell
python -m compileall -q backend/maf_team_runtime.py tests/test_maf_team_runtime.py
git diff --check
```

Result: both commands exited `0`.

### Remaining Concern

`FunctionalWorkflow` still emits the upstream MAF experimental warning. The isolated evidence branches themselves use stable graph workflows and public native workflow events.
