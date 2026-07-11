# Task 2 Report: First-Class MAF Agent Registry and Tool Boundaries

## Scope

Implemented only the Task 2 backend registry surface:

- `backend/maf_agents.py`
- `backend/requirements.txt`
- `tests/test_maf_agents.py`

The report itself is required by the task brief. No existing prompt content was copied or modified.

## Implementation

- Added `agent-framework-foundry==1.8.1` alongside the existing core 1.8.1 dependency.
- Added immutable `AgentSpec` records and `MafAgentRegistry` with `ids()`, `spec()`, and `agent()` lookups.
- `create_agent_registry()` derives all six agent IDs, prompt filenames, and role-scoped tool names from the existing `agents.build_agents.AGENTS` definitions, then loads instructions from the existing files under `agents/prompts/`.
- The default factory constructs 1.8.1 `Agent` objects with `FoundryChatClient`, `DefaultAzureCredential`, `FOUNDRY_PROJECT_ENDPOINT`, `DF_CHAT_DEPLOYMENT`, prompt text, role name, description, and only the role's assigned local, MCP, or Foundry-hosted tools.
- The market role's MCP configuration uses `allowed_tools=["market_lookup"]` and auto-approval; no other role receives it.
- The injectable `client_factory` returns deterministic fake agents in tests, so registry tests do not access Azure or the network.

## TDD Evidence

1. Added `tests/test_maf_agents.py` before the implementation.
2. Ran `python -m pytest tests/test_maf_agents.py -q`.
3. Observed the expected RED failure: `ModuleNotFoundError: No module named 'backend.maf_agents'`.
4. Implemented the minimal registry and provider wiring.
5. Re-ran the focused suite: `4 passed` with two upstream Agent Framework experimental warnings.

## Verification

### Focused registry tests

`python -m pytest tests/test_maf_agents.py -q`

- Result: `4 passed, 2 warnings`.
- Covers all six IDs, use of existing auditor prompt text, exact tool boundaries for every role, factory construction and lookup, and unknown-ID handling.
- No Azure credentials or network calls are required.

### Foundry provider installation

Installed and inspected these resolved packages:

- `agent-framework-core==1.8.1`
- `agent-framework-foundry==1.8.1`
- `agent-framework-openai==1.8.1`
- `azure-ai-inference==1.0.0b9`

`python -m pip check` reports `No broken requirements found` for the installed environment.

### Full test suite

`python -m pytest -q`

- Result: `77 passed, 1 failed, 2 warnings`.
- Failure: `tests/test_tracing_telemetry.py::test_agent_trace_emits_foundry_agent_identity_without_raw_actor_email`.
- The failure asserts that `repr(opentelemetry.trace.Status(...))` contains `OK`. The assertion does not hold for OpenTelemetry API 1.39.0 or 1.43.0; 1.39.0 is within Agent Framework core 1.8.1's supported range. The failing test and tracing implementation are outside Task 2 ownership and were not changed.

### Full requirements install

`python -m pip install -r backend/requirements.txt` cannot complete on the current Python 3.14 environment because the existing `pydantic==2.10.3` pin has no compatible CPython 3.14 wheel. Pip then attempts a source build, which fails because `link.exe` is not available. This is independent of the new Foundry provider dependency; direct installation of the Task 2 MAF 1.8.1 packages succeeds.

## Concerns

- Reproduce the full requirements install with the project-supported Python version or update the existing Pydantic/toolchain baseline in a separate dependency-maintenance task.
- Replace the tracing test's `repr(Status)` assertion with a semantic `StatusCode.OK` assertion in a separate, out-of-scope test maintenance task.
- MAF emits two upstream `ExperimentalWarning` messages during import; they do not affect registry behavior.
