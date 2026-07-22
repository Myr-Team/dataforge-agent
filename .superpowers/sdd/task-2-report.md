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

---

## Task 2 (2026-07-22): Actual Route and Telemetry Persistence

### Scoped files

- `backend/model_policy.py`
- `backend/foundry_client.py`
- `backend/maf_agents.py`
- `backend/orchestrator.py`
- `backend/run_store.py`
- `tests/test_model_policy.py`
- `tests/test_model_route_telemetry.py`

### What changed

- Added execution-kind route selection in `backend/model_policy.py`:
  - `full_analysis` and `audit_repair` select analysis-capable routes.
  - `follow_up` can select a dedicated follow-up route only when the caller enables it.
  - direct reply paths prefer non-analysis chat routes when available.
- Added `SelectedTextRoute`, `select_text_route_record(...)`, `select_text_route(...)`, and `model_route_scope(...)` so Foundry calls, APIM headers, MAF gateway headers, and persisted run records read the same selected route.
- `backend/foundry_client.py` now:
  - resolves model deployment from the active route scope;
  - stamps APIM-bound requests with the scoped `x-dataforge-model-route`;
  - records `route`, `deployment`, `selection`, `fallback_reason`, `execution_kind`, `latency_ms`, and compatibility keys in `_response_meta(...)`;
  - captures per-call latency in `_responses_create_with_retry(...)`.
- `backend/maf_agents.py` now builds the APIM/MAF chat client from the active scoped route instead of independently resolving an analysis route.
- `backend/orchestrator.py` now scopes routing/LLM calls by execution kind:
  - coordinator direct replies;
  - lightweight follow-up answer-composer / assessment / direct reply;
  - feasibility analyst;
  - auditor;
  - MAF full-analysis runtime setup.
- `backend/run_store.py` now persists a normalized model record with:
  - `route`
  - `deployment`
  - `selection`
  - `fallback_reason`
  - `execution_kind`
  - `latency_ms`
  - normalized `usage` (`prompt` / `completion` / `total`)
  - compatibility fields (`model_route`, `model_deployment`, `provider_usage`).

### TDD evidence

Red commands:

- `python -m pytest tests/test_model_policy.py::test_full_analysis_never_selects_followup_candidate_route -q`
  - failed with `ImportError: cannot import name 'select_text_route'`
- `python -m pytest tests/test_model_route_telemetry.py::test_followup_run_persists_selected_route_model_usage_and_latency -q`
  - failed with `ImportError: cannot import name 'model_route_scope'`

Green commands:

- `python -m pytest tests/test_model_policy.py::test_full_analysis_never_selects_followup_candidate_route -q`
  - `1 passed`
- `python -m pytest tests/test_model_route_telemetry.py::test_followup_run_persists_selected_route_model_usage_and_latency -q`
  - `1 passed`
- `python -m pytest tests/test_model_policy.py tests/test_model_route_telemetry.py tests/test_tracing_telemetry.py -q`
  - `16 passed`
- `python -m pytest tests/test_model_policy.py tests/test_model_route_telemetry.py tests/test_tracing_telemetry.py tests/test_gateway_client.py tests/test_maf_agents.py -q`
  - `38 passed`

### Residual risks

- Task 2 does not yet gate follow-up candidate routing on offline evaluation. That eligibility layer is still Task 4.
- Image-generation calls remain out of scope, by design.

### Correction pass (2026-07-22): telemetry minimization and route-scope regressions

Scoped files:

- `backend/foundry_client.py`
- `backend/run_store.py`
- `tests/test_model_policy.py`

What changed:

- `_usage_dict(...)` now emits only the DataForge-safe allowlisted token fields:
  - `input_tokens`
  - `output_tokens`
  - `total_tokens`
- Provider-specific or arbitrary extra usage fields such as cache details, reasoning content, or other future keys are dropped before response metadata is produced.
- `backend/run_store.py` no longer persists raw `provider_usage` into run model rows. Persisted model telemetry keeps only the normalized `usage` block (`prompt` / `completion` / `total`) plus the selected route metadata.
- Added deterministic `model_route_scope(...)` regression tests covering:
  - ordinary exit restoring the default route;
  - nested scope exit restoring the outer route;
  - exception exit restoring the prior/default route;
  - no cross-request route bleed after scope exit.

TDD evidence:

Red command:

- `python -m pytest tests/test_model_policy.py::test_response_metadata_records_effective_route_and_deployment tests/test_model_policy.py::test_run_store_persists_effective_model_route_and_deployment tests/test_model_policy.py::test_model_route_scope_restores_default_after_exit tests/test_model_policy.py::test_model_route_scope_restores_outer_route_when_nested tests/test_model_policy.py::test_model_route_scope_restores_prior_route_after_exception tests/test_model_policy.py::test_model_route_scope_does_not_bleed_between_requests -q`
  - Result before the fix: `2 failed, 4 passed`
  - Failure 1 proved `_response_meta(...)` leaked unrecognized provider usage fields (`cache_read_tokens`, `reasoning_content`).
  - Failure 2 proved `run_store` still persisted raw `provider_usage`, including unrecognized fields (`cache_read_tokens`, `sensitive_detail`).

Green commands:

- `python -m pytest tests/test_model_policy.py::test_response_metadata_records_effective_route_and_deployment tests/test_model_policy.py::test_run_store_persists_effective_model_route_and_deployment tests/test_model_policy.py::test_model_route_scope_restores_default_after_exit tests/test_model_policy.py::test_model_route_scope_restores_outer_route_when_nested tests/test_model_policy.py::test_model_route_scope_restores_prior_route_after_exception tests/test_model_policy.py::test_model_route_scope_does_not_bleed_between_requests -q`
  - Result after the fix: `6 passed`
- `python -m pytest tests/test_model_policy.py tests/test_model_route_telemetry.py tests/test_tracing_telemetry.py tests/test_gateway_client.py tests/test_maf_agents.py -q`
  - Result after the fix: `42 passed`

Residual risks after correction:

- This correction intentionally narrows model response usage telemetry to the three server-recognized token counters only. If a future provider introduces a new token counter we actually want, it must be explicitly allowlisted first.
- Task 2 still does not decide whether the follow-up candidate route is eligible from offline evaluation evidence. That gate remains Task 4.

### Final correction (2026-07-22): preserve unknown allowlisted counters as null

Reviewer follow-up found one remaining truthfulness bug in the previous correction: partial provider usage such as `{"input_tokens": None, "output_tokens": 3, "total_tokens": None}` was still losing the unknown counters during normalization. The response metadata kept only `output_tokens`, and the persisted run model row kept only `completion`.

What changed:

- `backend/foundry_client.py::_usage_dict(...)`
  - now returns the three DataForge-allowlisted counters whenever the provider usage object exposes any recognized token keys;
  - preserves explicit unknown values as `None`;
  - still strips arbitrary extra provider fields;
  - still keeps observed `0` as `0`.
- `backend/run_store.py::_normalized_observed_usage(...)`
  - now mirrors that contract for persisted model telemetry;
  - returns exactly the normalized `prompt` / `completion` / `total` keys when any recognized provider token key is present;
  - preserves unknown counters as `None` instead of dropping them.

TDD evidence:

Red command:

- `python -m pytest tests/test_model_policy.py::test_response_metadata_preserves_unknown_allowlisted_usage_fields tests/test_model_policy.py::test_run_store_persists_partial_usage_without_fabricating_unknown_counts -q`
  - Result before the fix: `2 failed`
  - Failure 1 showed `_response_meta(...)["usage"]` collapsed to `{"output_tokens": 3}`.
  - Failure 2 showed persisted model usage collapsed to `{"completion": 3}`.

Green commands:

- `python -m pytest tests/test_model_policy.py::test_response_metadata_preserves_unknown_allowlisted_usage_fields tests/test_model_policy.py::test_run_store_persists_partial_usage_without_fabricating_unknown_counts -q`
  - Result after the fix: `2 passed`
- `python -m pytest tests/test_model_policy.py tests/test_model_route_telemetry.py tests/test_tracing_telemetry.py tests/test_gateway_client.py tests/test_maf_agents.py -q`
  - Result after the fix: `44 passed`

Residual risk after final correction:

- This change fixes model response metadata and persisted model rows only. Higher-level aggregate token summaries still follow their own existing normalization rules and should be evaluated separately if we want end-to-end unknown-token propagation in every summary surface.

### Final-final correction (2026-07-22): absent usage objects no longer fabricate null counters

Reviewer follow-up found one last absent-versus-explicit-unknown bug in `backend/foundry_client.py`: for non-dict usage objects, `_usage_has_known_keys(...)` synthesized a dict containing every allowlisted key before checking membership. That meant an object with no recognized token attributes at all still looked like a provider that had explicitly returned all three counters as unknown, so `_usage_dict(...)` emitted:

`{"input_tokens": None, "output_tokens": None, "total_tokens": None}`

That synthetic null usage then flowed into run persistence as:

`{"prompt": None, "completion": None, "total": None}`

What changed:

- `backend/foundry_client.py::_usage_has_known_keys(...)`
  - now distinguishes object attributes from synthetic placeholders;
  - for dict/model-dump payloads, it still preserves explicit allowlisted keys set to `None`;
  - for plain objects, it now checks `hasattr(...)` against the allowlisted token aliases instead of manufacturing a dict with every candidate key.
- No run-store logic change was required. Once absent usage stays absent in response metadata, persisted model rows keep `usage: {}` instead of three fabricated nulls.

TDD evidence:

Red command:

- `python -m pytest tests/test_model_policy.py::test_response_metadata_omits_absent_usage_object_without_known_counters tests/test_model_policy.py::test_run_store_does_not_fabricate_unknown_counts_for_absent_usage -q`
  - Result before the fix: `2 failed`
  - Failure 1 showed `_usage_dict(EmptyUsage())` returned `{"input_tokens": None, "output_tokens": None, "total_tokens": None}` instead of `{}`.
  - Failure 2 showed run persistence stored `{"prompt": None, "completion": None, "total": None}` instead of `{}` for the same absent-evidence case.

Green commands:

- `python -m pytest tests/test_model_policy.py::test_response_metadata_omits_absent_usage_object_without_known_counters tests/test_model_policy.py::test_run_store_does_not_fabricate_unknown_counts_for_absent_usage -q`
  - Result after the fix: `2 passed`
- `python -m pytest tests/test_model_policy.py tests/test_model_route_telemetry.py tests/test_tracing_telemetry.py tests/test_gateway_client.py tests/test_maf_agents.py -q`
  - Result after the fix: `46 passed`

Residual risk after final-final correction:

- This closes the absent-versus-explicit-unknown gap for response metadata and persisted model rows. If future callers bypass `_response_meta(...)` and inject their own usage objects into run events, they still need to honor the same contract: absent counters stay absent, explicit unknown counters stay explicit.

### Step-event correction (2026-07-22): sanitize persisted model_response step usage

Reviewer follow-up found that Task 2 still had one remaining provider-usage leak in the persisted `steps[]` trail. `record_event(...)` already built normalized model rows for `run["models"]`, but `_compact_step(...)` still stored the raw `model_response` payload, so `steps[0]["data"]["usage"]` retained provider-specific fields such as `cache_read_tokens`, `reasoning_content`, or other arbitrary extras.

What changed:

- `backend/run_store.py::_sanitize_event_data(...)`
  - now special-cases `model_response` events before step compaction;
  - routes them through a dedicated sanitizer instead of writing the raw event payload into `steps[]`.
- `backend/run_store.py::_sanitize_model_response_event_data(...)`
  - removes the raw `provider_usage` mirror if it is present;
  - normalizes `usage` with the same `_normalized_observed_usage(...)` helper used by persisted model rows;
  - therefore preserves observed `0`, preserves explicit unknown counters as `None`, and keeps absent evidence as `{}`.
- `tests/test_model_policy.py::test_run_store_persists_effective_model_route_and_deployment`
  - now asserts both persisted surfaces:
    - `run["models"][0]["usage"] == {"prompt": 0, "completion": 3, "total": 3}`
    - `run["steps"][0]["data"]["usage"] == {"prompt": 0, "completion": 3, "total": 3}`
  - and therefore proves provider-specific extras are no longer retained in the step trail.

TDD evidence:

Red command:

- `python -m pytest tests/test_model_policy.py::test_run_store_persists_effective_model_route_and_deployment -q`
  - Result before the fix: `1 failed`
  - Failure showed `steps[0]["data"]["usage"]` still contained `cache_read_tokens` and `sensitive_detail` instead of the normalized allowlist.

Green commands:

- `python -m pytest tests/test_model_policy.py::test_run_store_persists_effective_model_route_and_deployment -q`
  - Result after the fix: `1 passed`
- `python -m pytest tests/test_model_policy.py tests/test_model_route_telemetry.py tests/test_tracing_telemetry.py tests/test_gateway_client.py tests/test_maf_agents.py -q`
  - Result after the fix: `46 passed`

Residual risk after step-event correction:

- This closes the provider-usage leak for the `model_response` step path that Task 2 owns. If a future telemetry event introduces another raw provider payload type, it needs its own explicit sanitizer before persistence.
