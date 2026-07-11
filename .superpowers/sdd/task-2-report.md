# Task 2 Report: First-Class MAF Agent Registry and Tool Boundaries

## Design-Version Note

The post-review dependency decision supersedes the original Task 2 brief's 1.8.1 package pins. The verified fully stable set is:

- `agent-framework-core==1.11.0`
- `agent-framework-foundry==1.10.1`
- `agent-framework-orchestrations==1.0.0`

The registry API remains compatible with Task 3 factory injection: `client_factory` keeps its original position and `workspace_id` is a keyword-only authorization context. Blank or missing workspace IDs are rejected for both production and injected factories.

## Scope

Implemented only the Task 2 backend registry surface:

- `backend/maf_agents.py`
- `backend/requirements.txt`
- `tests/test_maf_agents.py`

The report itself is required by the task brief. No existing prompt content was copied or modified.

## Implementation

- Pinned the stable MAF core, Foundry, and orchestration packages listed above.
- Added immutable `AgentSpec` records and `MafAgentRegistry` with `ids()`, `spec()`, and `agent()` lookups.
- `create_agent_registry()` derives all six agent IDs, prompt filenames, and role-scoped tool names from the existing `agents.build_agents.AGENTS` definitions, then loads instructions from the existing files under `agents/prompts/`.
- The default factory constructs `Agent` objects with `FoundryChatClient`, `DefaultAzureCredential`, `FOUNDRY_PROJECT_ENDPOINT`, `DF_CHAT_DEPLOYMENT`, prompt text, role name, description, and only the role's assigned local, MCP, or Foundry-hosted tools.
- The market role's MCP configuration uses `allowed_tools=["market_lookup"]` and auto-approval; no other role receives it.
- The corpus search tool closes over the authorized registry workspace and exposes only `query` and `top_k` to the model. Model input cannot replace `workspace_id`.
- The producer image tool no longer accepts model-controlled reference URLs or paths. It passes an empty reference list until an authorized workspace resolver is added to this MAF path.
- Strict Pydantic tool schemas reject extra top-level arguments, constrain `top_k` to 1 through 20, and constrain image size to the three values in `agents/tool_schemas.json`.
- The injectable `client_factory` returns deterministic fake agents in tests, so registry tests do not access Azure or the network.

## TDD Evidence

1. Added `tests/test_maf_agents.py` before the implementation.
2. Ran `python -m pytest tests/test_maf_agents.py -q`.
3. Observed the expected RED failure: `ModuleNotFoundError: No module named 'backend.maf_agents'`.
4. Implemented the minimal registry and provider wiring.
5. Re-ran the original focused suite: `4 passed`.
6. Added review regression tests before remediation and observed failures for missing workspace context, model-controlled workspace/reference arguments, loose schemas, unmaterialized tool-scope assertions, and missing stable orchestration pins.
7. Implemented the review remediation and re-ran the focused suite: `12 passed`.

## Verification

### Focused registry tests

`python -m pytest tests/test_maf_agents.py -q`

- Result after review remediation: `12 passed`.
- Covers all six IDs, existing prompt loading, exact materialized tool boundaries for every role, workspace-bound search invocation, strict schema rejection, safe image invocation, Foundry helper configuration, MCP allowlisting/approval, factory construction, and unknown-ID handling.
- No Azure credentials or network calls are required.
- Combined compatibility run with `tests/test_maf_team_runtime.py`: `21 passed, 1 warning`; the warning is the upstream experimental marker for functional workflows.

### Stable MAF installation

Installed and inspected these resolved packages:

- `agent-framework-core==1.11.0`
- `agent-framework-foundry==1.10.1`
- `agent-framework-openai==1.10.1` (transitive)
- `agent-framework-orchestrations==1.0.0`

Offline imports of `ConcurrentBuilder` and `HandoffBuilder` from `agent_framework.orchestrations` succeed. `python -m pip check` reports `No broken requirements found` for the installed environment.

### Previous full-suite baseline

`python -m pytest -q`

- The pre-remediation Task 2 run produced `77 passed, 1 failed, 2 warnings`.
- Failure: `tests/test_tracing_telemetry.py::test_agent_trace_emits_foundry_agent_identity_without_raw_actor_email`.
- The failure asserts that `repr(opentelemetry.trace.Status(...))` contains `OK`. The assertion does not hold for OpenTelemetry API 1.39.0 or 1.43.0; 1.39.0 is within Agent Framework core 1.8.1's supported range. The failing test and tracing implementation are outside Task 2 ownership and were not changed.

### Full requirements install caveat

`python -m pip install -r backend/requirements.txt` cannot complete on the current Python 3.14 environment because the existing `pydantic==2.10.3` pin has no compatible CPython 3.14 wheel. Pip then attempts a source build, which fails because `link.exe` is not available. This is independent of the MAF package set; direct installation of the three exact stable MAF pins succeeds.

## Concerns

- Reproduce the full requirements install with the project-supported Python version or update the existing Pydantic/toolchain baseline in a separate dependency-maintenance task.
- Replace the tracing test's `repr(Status)` assertion with a semantic `StatusCode.OK` assertion in a separate, out-of-scope test maintenance task.
- MAF PDF generation can use only sanitized internal references returned for the registry's bound workspace; model-provided source locations remain unsupported.

## Critical PDF Source Remediation (2026-07-12)

- Replaced the global PDF function tool with a registry-scoped tool that closes over the authorized `workspace_id`.
- Recursively strips model-controlled `brand_logo_url`, `logo_url`, and `reference_images` fields before calling `render_pdf_report`.
- Resolves reference metadata only through `workspace_reference_images(authorized_workspace_id)` and ignores every supplied URL, blob URL, source path, local path, or cross-workspace path.
- Rebuilds at most three trusted PNG/JPEG/WebP references as encoded internal URLs for the exact authorized workspace. Non-image filenames and duplicate filenames are discarded.
- Preserved the strict `_RenderPdfReportInput` schema and all existing role-scoping behavior.

TDD red command:

`python -m pytest tests/test_maf_agents.py -q`

- Result before implementation: `12 passed, 2 failed`; both failures showed that workspace resolution and proposal sanitization were absent.

Focused verification command:

`python -m pytest tests/test_maf_agents.py -q`

- Result after implementation: `14 passed`.
