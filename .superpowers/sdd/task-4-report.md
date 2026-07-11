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

## Review Fix: Terminal Ownership, Safe Metadata, and Persistence

This section supersedes the first concern above. Typed completion events now carry observed safe metadata when present and omit unknown fields.

### RED

Tests were added before the review implementation for:

- post-runtime event adaptation failure without legacy fallback;
- post-runtime finalization failure after an answer delta without legacy fallback;
- `asyncio.CancelledError` propagation without fallback or legacy execution;
- legacy-compatible `model_response` frames and actual participant span inputs;
- direct and concurrent completion-event metadata extraction and redaction;
- real `start_run` / `record_event` / `complete_run` / `list_runs` / `get_run` persistence and schema parity;
- actual measured participant `started_ns` / `completed_ns` attributes;
- omission of unknown telemetry fields;
- typed-contract rejection of unsafe response and tool identifiers;
- redaction of unsafe model-response tool names and customer content.

Initial focused RED result: `7 failed, 29 passed, 1 warning`. The cancellation test already passed because `CancelledError` was not caught by `Exception`; the implementation now preserves that behavior explicitly. The timing RED check then failed in both the runtime event and tracing API because measured start/end fields were absent. The unknown-field RED check failed because `tool_names` serialized as an empty list instead of being omitted.

### Implementation

- The fallback catch now encloses only authorization-bound registry construction and `MafTeamRuntime.run(...)`. Runtime cancellation is explicitly re-raised.
- Once a typed MAF result exists, adaptation/finalization has separate terminal ownership. A failure emits one redacted `error` and one `final`, records `maf_terminal_error`, returns, and never enters legacy execution.
- Completed agent events carry bounded `response_id`, token counts, retry count, up to 12 safe tool names, cache state, status/error category, and actual monotonic start/end measurements. Unknown metadata is omitted.
- Direct invocation and isolated concurrent branches both extract the same safe projection from normalized output metadata and Agent Framework response metadata. Prompt, message, evidence, credential, email, and arbitrary metadata fields are not copied to events.
- Every successful completion emits a legacy-compatible `model_response` containing only agent/mode/status plus observed safe metadata and usage.
- Participant observation spans receive the event's actual timing and metadata. No unknown retry, token, tool, cache, response, or timing value is fabricated.
- MAF run summaries consume token counts from typed completion events. A real persistence-path test validates list/detail MAF parity, model usage persistence, and `RunSummary` / `RunDetailResponse` validation.

### GREEN

Focused Task 2-4 command:

```powershell
python -m pytest tests/test_maf_contracts.py tests/test_maf_agents.py tests/test_maf_team_runtime.py tests/test_maf_integration.py tests/test_tracing_telemetry.py -q
```

Result: `69 passed, 1 warning in 5.78s`. The warning is the upstream experimental Functional Workflow warning.

Static gates:

```powershell
git diff --check
python -m compileall -q backend tests
```

Result: both exited successfully.

Full suite result: `132 passed, 2 failed, 1 warning in 7.57s`. The only failures remain the Task 6 deterministic evaluation fake in `tests/test_maf_evaluation_contract.py`; `_DeterministicAgent` does not implement the installed Agent Framework `SupportsAgentRun` protocol. Per instruction, no evaluation file was edited.

## Final Cancellation Fix: Concurrent Branch Cleanup

### RED

Added `test_branch_local_cancellation_propagates_and_cleans_up_tasks` before the runtime change. The test injects `asyncio.CancelledError` into the market branch, wraps the runtime in a `0.25s` timeout, tracks both isolated branch tasks and the observer task, and requires cancellation propagation plus completed cleanup.

The initial run failed with `TimeoutError`: `gather(..., return_exceptions=True)` retained the branch-local cancellation as a result, then `_observe_concurrent_branches(...)` waited indefinitely for the canceled branch's missing terminal observation. The outer timeout canceled the observer.

### Implementation

- Concurrent branch coroutines are now explicit tasks.
- Gathered results are inspected and any branch-local `asyncio.CancelledError` is re-raised.
- A `finally` block cancels unfinished branch and observer tasks and awaits all of them with `return_exceptions=True`, preventing leaked tasks or observer hangs.
- Ordinary branch exceptions still publish failed observations and retain existing optional degradation behavior.

### GREEN

Targeted cancellation and degradation command:

```powershell
python -m pytest tests/test_maf_team_runtime.py::test_branch_local_cancellation_propagates_and_cleans_up_tasks tests/test_maf_team_runtime.py::test_optional_market_failure_degrades_without_losing_corpus tests/test_maf_team_runtime.py::test_immediate_market_failure_does_not_cancel_slow_corpus -q
```

Result: `3 passed, 1 warning in 3.55s`.

Focused Task 3/4 command:

```powershell
python -m pytest tests/test_maf_team_runtime.py tests/test_maf_integration.py -q
```

Result: `36 passed, 1 warning in 5.76s`. The warning is the existing upstream experimental Functional Workflow warning.

## Cancellation Latency Fix: Blocked Sibling

### RED

Strengthened the branch-local cancellation regression so the corpus branch blocks indefinitely until canceled while the market branch raises `asyncio.CancelledError`. The runtime is wrapped in a `0.15s` timeout and must raise `CancelledError`, cancel the blocked corpus sibling, finish the observer, and leave every tracked task done.

The initial run failed with `TimeoutError`. Although gathered cancellation was eventually inspected, `asyncio.gather(..., return_exceptions=True)` waited for the blocked corpus sibling first, so cancellation propagation was not prompt.

### Implementation

- Replaced all-branch gather waiting with `asyncio.wait(..., return_when=asyncio.FIRST_COMPLETED)` over the explicit branch tasks.
- Each completion batch is inspected immediately; a canceled branch raises `asyncio.CancelledError` without waiting for siblings.
- The existing `finally` cancels and awaits unfinished siblings and the observer.
- Ordinary branch exceptions continue through failed observations, and normal execution still waits for both branches.

### GREEN

Targeted cancellation, degradation, and normal-concurrency command:

```powershell
python -m pytest tests/test_maf_team_runtime.py::test_branch_local_cancellation_is_immediate_and_cleans_up_blocked_sibling tests/test_maf_team_runtime.py::test_optional_market_failure_degrades_without_losing_corpus tests/test_maf_team_runtime.py::test_immediate_market_failure_does_not_cancel_slow_corpus tests/test_maf_team_runtime.py::test_internal_and_external_research_run_concurrently -q
```

Result: `4 passed, 1 warning in 3.79s`.

Focused Task 3 runtime plus Task 4 cancellation command:

```powershell
python -m pytest tests/test_maf_team_runtime.py tests/test_maf_integration.py::test_full_runtime_cancellation_propagates_without_fallback_or_legacy -q
```

Result: `25 passed, 1 warning in 5.44s`. The warning is the existing upstream experimental Functional Workflow warning.
