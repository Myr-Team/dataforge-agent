# Task 4 Report: Orchestrator Integration, Fallback, Run Store, and Telemetry

## Scope

Implemented the Task 4 brief only in the assigned orchestrator, persistence, schema, tracing, integration-test, telemetry-test, and report files.

The integration preserves the existing route, authorization, SSE, final payload, and AUDIT behavior. The new team runtime is constructed only when `runtime_mode()` is `FULL` and `canary_selected(workspace_id, conversation_id)` returns true. OFF, AUDIT, and non-selected FULL requests do not construct an agent registry.

## RED

Initial command:

```powershell
python -m pytest tests/test_maf_integration.py tests/test_tracing_telemetry.py -q
```

After changing the new tracing import to allow behavioral collection, the expected RED result was `5 failed, 2 passed`:

- `backend.orchestrator` did not expose FULL runtime configuration or integration.
- typed MAF events did not produce a run summary.
- run details did not expose event-derived MAF state.
- no per-agent MAF trace context existed.
- the tracing status assertion still depended on `repr(Status)` rather than `StatusCode.OK`.

Additional RED checks were run before their corresponding changes:

- participant status attribute: failed with unexpected `status` argument;
- run-detail MAF summary: failed with missing `maf` key;
- bounded fallback category: failed with missing `error_category`.

## Implementation

- Added a narrow `_try_full_maf_runtime(...)` adapter that maps semantic route fields to `MafTeamRequest`, creates `create_agent_registry(workspace_id=req.workspace_id)`, and runs `MafTeamRuntime`.
- Inserted the adapter after clarification/lightweight exits and before the unchanged legacy specialist body.
- Translated every typed runtime event to its new SSE event. Agent starts also emit legacy-compatible `role_change`; completed/revision review events emit legacy-compatible `audit` frames.
- Added one exception boundary around FULL construction, execution, event adaptation, and finalization. It emits one redacted `maf_fallback` and then falls through to the legacy body once, without recursion.
- Merged runtime artifacts into existing public artifact fields and emitted one final. Required workspace-evidence failure bypasses legacy answer composition and remains `insufficient_evidence` at the top level and nested feasibility level.
- Derived persisted MAF mode, selected/completed agents, reason codes, fallback, rounds, durations, and observed token counts from recorded events. Both run lists and run details expose the same summary.
- Added content-free participant spans with hashed actor identity and bounded agent, collaboration, branch, handoff, duration, retry, tool, token, status, and error-category attributes. Raw prompts, messages, evidence, credentials, and email are not attached.
- Changed the brittle root-span assertion to compare `Status.status_code is StatusCode.OK`.

## GREEN

Focused command:

```powershell
python -m pytest tests/test_maf_integration.py tests/test_tracing_telemetry.py -q
```

Result: `11 passed in 3.51s`.

Static gates:

```powershell
git diff --check
python -m compileall -q backend tests
```

Result: both exited successfully with no output.

## Full Suite

Command:

```powershell
python -m pytest -q
```

Result: `123 passed, 2 failed, 1 warning in 6.97s`.

The two failures are both in the out-of-scope deterministic evaluation harness:

- `tests/test_maf_evaluation_contract.py::test_deterministic_eval_measures_metrics_and_preserves_unknown_tokens`
- `tests/test_maf_evaluation_contract.py::test_forced_runtime_failure_falls_back_exactly_once`

Both fail because `eval/run_maf_runtime_eval.py::_DeterministicAgent` is rejected by the installed Agent Framework `SequentialBuilder` as not implementing `SupportsAgentRun`. Running only `python -m pytest tests/test_maf_evaluation_contract.py -q` reproduces the same issue with `4 passed, 2 failed`. Task 4 does not own either file, and no out-of-scope change was made.

## Concerns

- The current typed runtime events contain participant duration/status/branch/handoff data but do not carry token usage, tool names, or retry counts. The span API records those values when supplied and otherwise omits unknown values rather than fabricating them.
- The deterministic evaluation double needs to adopt the same current `SupportsAgentRun` contract used by `tests/test_maf_team_runtime.py`, but that fix belongs to its owner.
