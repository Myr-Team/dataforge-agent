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
- Runs corpus and market branches through MAF `ConcurrentBuilder` imported from `agent_framework.orchestrations`, with typed fan-in results and measured timing overlap.
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

After the concrete orchestration requirement was clarified, an additional focused RED run asserted a MAF graph workflow for the concurrent pattern. It failed with `AttributeError: 'MafTeamRuntime' object has no attribute 'last_pattern_workflow'` before the `ConcurrentBuilder` implementation.

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

- Installed core/foundry packages are `1.8.1`.
- `ConcurrentBuilder` and `HandoffBuilder` are lazily exported from `agent_framework.orchestrations` and require the separate orchestration distribution.
- `agent-framework-orchestrations==1.0.0rc3` is compatible with core `>=1.8.0,<2` and is the pin tested here.
- Stable `agent-framework-orchestrations==1.0.0` requires core `>=1.9.0,<2`, so it conflicts with this repository's exact `agent-framework-core==1.8.1` pin.
- MAF marks `FunctionalWorkflow` experimental in this release. The concurrent fan-out/fan-in itself uses the concrete `ConcurrentBuilder` graph API.

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
- `ConcurrentBuilder` now receives `MafAgentRegistry.agent(...)` objects directly. The runtime consumes supported `Workflow.run(..., stream=True)` events (`executor_invoked` and `executor_completed`) for typed branch and participant observations, replacing the captured participant wrapper.
- The offline fake is a narrow MAF `SupportsAgentRun`-compatible double: `id`, `name`, `description`, `run(..., stream=...)`, `create_session`, and `get_session`. This matches the current stable `ConcurrentBuilder` participant contract without requiring a Foundry connection.
- `maf_plan` includes `mode`, `selected_agents`, `skipped_agents`, and `max_revisions`; handoff intent reasons are limited to known codes or `intent:other`.

### Stable MAF API Check

The installed dependency set is `agent-framework-core==1.11.0`, `agent-framework-foundry==1.10.1`, and `agent-framework-orchestrations==1.0.0`.

`ConcurrentBuilder` accepts `participants: Sequence[SupportsAgentRun | Executor]`, exposes `with_aggregator(...)`, and builds a `Workflow`. `Workflow.run(..., stream=True)` emits supported workflow lifecycle and executor events. The implementation uses that public surface; it does not depend on MAF-private participant wrappers.

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

### Remaining Concern

MAF aborts a concurrent workflow when any participant raises. The runtime records the successful direct-agent participant observations emitted before that failure and marks any unfinished branch failed; the required workspace branch still fails closed. The focused optional-market regression verifies corpus evidence remains available in this case.
