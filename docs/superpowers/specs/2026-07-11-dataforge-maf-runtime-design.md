# DataForge MAF Runtime Design

## Goal

Upgrade DataForge from a MAF-owned audit/revision graph whose executors wrap existing functions to a progressively enabled multi-agent runtime with first-class specialist participants, dynamic collaboration patterns, truthful observability, and a stable legacy fallback.

## Scope

This change includes:

- first-class MAF participants for coordination, workspace evidence analysis, market research, feasibility, audit, and composition;
- runtime selection between direct, concurrent, handoff, and bounded-review collaboration patterns;
- backward-compatible SSE and run-record contracts that expose the agents that actually ran;
- per-agent latency, token, tool, retry, error, and handoff telemetry;
- feature-flagged rollout with automatic fallback to the current orchestrator path;
- focused UI updates in Agent Flow and run trace views;
- deterministic tests and production canary verification.

This change does not include:

- open-ended Magentic orchestration;
- replacing authentication or workspace authorization;
- changing feasibility scoring rules or weakening evidence ceilings;
- encoding industry names, dataset names, or demo scenarios into routing;
- migrating DataForge to Foundry Hosted Agents;
- enabling native Foundry ROI.

## Architecture

The existing preflight route, evidence boundaries, schemas, persistence, and artifact contracts remain authoritative. A new `MafTeamRuntime` receives the normalized routing decision and a typed collaboration request.

```text
request
  -> existing preflight and coordinator route
  -> CollaborationSelector
       -> direct
       -> concurrent_research
       -> specialist_handoff
       -> bounded_review
  -> normalized artifact/result contracts
  -> answer composer or producer
  -> SSE, run store, OpenTelemetry
```

The runtime uses the existing files under `agents/prompts/` as the single prompt source. It must not duplicate role instructions in Python. Each participant receives a bounded instruction set, a typed input contract, and only the tools required for its role.

The verified stable dependency set is `agent-framework-core==1.11.0`, `agent-framework-foundry==1.10.1`, and `agent-framework-orchestrations==1.0.0`.

FULL mode preserves the legacy analysis authorities instead of treating model dictionaries as authoritative. Required workspace paths run backend corpus retrieval first and carry typed corpus-hit, evidence, and rubric contracts; at least one non-empty evidence ref and quote must resolve to an actual retrieved hit. Feasibility output is Pydantic-validated, evidence-verified, rubric-scored, and pre-audit guarded; audit output is Pydantic-validated; final feasibility is post-audit guarded and revalidated as a typed guarded report. A contract-invalid feasibility or audit response receives exactly one schema-correction retry.

## Runtime Modes

`DF_MAF_RUNTIME` controls the execution path:

- `off`: current orchestrator only;
- `audit`: current behavior, where MAF owns only the audit/revision loop;
- `full`: the new team runtime is eligible for the request.

For backward compatibility, `DF_USE_MAF=1` maps to `audit` only when `DF_MAF_RUNTIME` is absent.

`DF_MAF_TRAFFIC_PERCENT` is an integer from `0` to `100`. Eligibility uses a stable hash of workspace ID and conversation ID, not a dataset or industry name. A value of `0` keeps the new runtime disabled for production traffic while allowing explicit evaluation entrypoints. A value of `100` enables all eligible traffic.

## Participants

The team contains:

- `df-coordinator`: selects the collaboration pattern and required specialists;
- `df-corpus-analyst`: retrieves and summarizes workspace evidence;
- `df-market-researcher`: obtains explicitly separated external market signals;
- `df-feasibility-analyst`: produces evidence-bounded feasibility analysis;
- `df-auditor`: checks traceability, unsupported claims, and evidence ceilings;
- `df-producer`: composes confirmed plans and artifact inputs.

The first implementation uses in-process MAF agents backed by the Foundry project Responses API. Existing persisted Foundry Prompt Agents remain available to the legacy runtime and as a fallback during rollout.

## Collaboration Selection

`CollaborationSelector` consumes normalized intent, requested output, evidence requirements, risk level, and available workspace context. It returns a typed `CollaborationPlan` with a pattern, selected agents, reason codes, required branches, and revision cap.

Patterns:

- `direct`: coordinator handles simple workspace questions and conversational follow-ups;
- `concurrent_research`: corpus and market agents run in parallel only when both internal and external evidence are required;
- `specialist_handoff`: coordinator transfers task ownership to one specialist when a single bounded domain dominates;
- `bounded_review`: feasibility and audit agents execute a maximum of two revision rounds for material conclusions.

Every plan with `needs_workspace=true`, including `direct` and `bounded_review`, owns a required workspace-evidence dependency and fails closed when the authoritative corpus is empty or invalid.

Selection must not use business-name, file-name, dataset-name, or industry-name allowlists. The system records why an agent was selected and must also show that unnecessary agents were not invoked.

### Concurrent Branch Implementation

The accepted stable implementation launches two independent one-participant `SequentialBuilder` workflows together and observes their native events through one async queue. This is an intentional deviation from the original `ConcurrentBuilder` sketch: `agent-framework-orchestrations==1.0.0` rejects a fan-out with fewer than two targets, so one `ConcurrentBuilder` per failure-isolated branch is not valid. The two independent workflows preserve real overlap and prevent an optional market failure from cancelling required workspace work.

## Data Contracts

Run summaries add a backward-compatible `maf` object:

```json
{
  "runtime": "maf",
  "mode": "concurrent_research",
  "selected_agents": ["df-corpus-analyst", "df-market-researcher"],
  "selection_reason_codes": ["workspace_evidence_required", "external_signal_required"],
  "fallback": false,
  "rounds": 1,
  "duration_ms": 8420,
  "agent_work_ms": 12910,
  "tokens": {"prompt": 4200, "completion": 960, "total": 5160}
}
```

`duration_ms` is observed workflow wall time from the earliest participant start to the latest participant completion. `agent_work_ms` is the sum of participant durations and may exceed wall time when branches overlap. Missing token usage remains `null` or absent; it is never converted to zero.

New SSE and trace events are:

- `maf_plan`;
- `maf_agent_started` and `maf_agent_completed`;
- `maf_branch_started` and `maf_branch_joined`;
- `maf_handoff`;
- `maf_review`;
- `maf_fallback`.

Existing `role_change`, `audit`, `model_response`, and final events remain available. New event payloads include IDs, status, durations, token counts, tool names, retry counts, and reason codes. They do not include raw prompts, user messages, evidence rows, connector credentials, or model reasoning.

Runtime events are delivered through a live sink/async queue. SSE and detached participant spans open and close as execution happens; completed runs are not replayed to synthesize overlap.

## Frontend Behavior

Agent Flow renders the collaboration plan that actually ran rather than a fixed six-agent sequence. It distinguishes direct response, parallel branches, handoff, bounded review, and fallback.

Run trace groups events by agent and exposes:

- selected collaboration mode;
- participating and skipped agents;
- branch timing and join status;
- handoff source, target, and reason;
- review rounds and verdicts;
- per-agent latency, token usage, tools, retries, and errors.

The frontend reads the new events when available and preserves the existing rendering for older runs.

## Error Handling

Agent failures are classified as transient, content-policy, contract-validation, or permanent. Existing retry policy remains the Foundry boundary authority.

- A failed optional market branch degrades to internal-evidence-only analysis and records the gap.
- A failed required corpus branch prevents a stronger verdict and routes to a truthful clarification or degraded response.
- A contract-invalid agent response is rejected and retried at most once with schema correction.
- A MAF construction or runtime failure before the first MAF event emits `maf_fallback` and runs the existing path once.
- After any MAF event is emitted, MAF owns the terminal result: adaptation/finalization failure emits one error and one final response and never starts legacy execution.
- Content-policy, transient network/provider, contract-validation, and permanent errors are classified from bounded runtime/provider attributes.
- Audit/revision remains capped at two rounds.

Runtime artifact merge is allowlisted. MAF may contribute validated feasibility, validated audit, the blind feasibility snapshot, separated external signals, and its run summary. Workspace ID, conversation ID, routing, actor, output contract, and authoritative corpus remain owned by the orchestrator. `full_package` continues through the existing producer so PDF, image, plan, and audio assets are preserved.

## Observability

Every participant emits an OpenTelemetry span opened on its live start event and closed on its live terminal event with:

- `gen_ai.agent.id` and `gen_ai.agent.name`;
- collaboration mode and branch ID;
- workspace, conversation, and run correlation IDs;
- duration, token counts, retry count, cache state, tool names, and status;
- handoff source and target IDs where applicable.

Existing telemetry redaction remains mandatory. Customer content and actor email are hashed or omitted. The MAF trace labels are UTF-8 clean and must not reproduce the current mojibake found in older `maf_workflow` records.

Token, response, tool, retry, and cache telemetry comes only from trusted Agent Framework/provider response attributes and runtime-owned counters, never from the model-produced JSON value. Audit verdict telemetry uses a validated enum.

## Testing

Tests use deterministic fake agents at the runtime boundary and real MAF workflow objects. Model calls are not required for unit tests.

Required coverage:

- runtime mode and stable canary selection;
- prompt registry and per-role tool boundaries;
- direct path invokes only the coordinator;
- concurrent path starts corpus and market branches and joins both;
- optional branch failure degrades without losing successful evidence;
- specialist handoff records ownership transfer;
- audit revision loops once or twice and respects the cap;
- runtime failure emits fallback and calls the legacy path exactly once;
- SSE and run-summary compatibility;
- telemetry redaction and per-agent attributes;
- UTF-8-clean MAF descriptions;
- no MAF work for ordinary requests when the selector chooses the current lightweight path.

Before production default enablement, the MAF runtime must be compared with the legacy path on groundedness, unsupported-claim rate, agent-selection accuracy, latency, token cost, and task completion.

## Rollout

1. Land the runtime behind `DF_MAF_RUNTIME=off` and pass the full backend/frontend suite.
2. Run deterministic evaluations and connector-free production smoke tests through an explicit evaluation entrypoint.
3. Keep `DF_MAF_TRAFFIC_PERCENT=0` through full tests, deterministic evaluation, backend image import/start smoke, and broad review.
4. After those gates pass, run a separately approved production canary at `10` and inspect fallback rate, error rate, groundedness, latency, and token cost.
5. Keep traffic at `0` if the production canary gate is not explicitly approved or does not pass; increase to `50`, then `100`, only after passing evidence.
6. Keep `off` and `audit` as rollback modes until the full runtime has a stable production history.

## Acceptance Criteria

- production traces show at least one direct, concurrent, handoff, and bounded-review run;
- skipped agents are absent from invocation telemetry;
- concurrent branches overlap in time;
- a forced optional branch failure preserves the successful branch and records degradation;
- a forced runtime failure falls back exactly once;
- weak evidence triggers a visible audit revision without exceeding two rounds;
- run records and Agent Flow show the same collaboration facts;
- no customer message or email appears in new telemetry;
- all tests and frontend build pass;
- the canary completes real production analysis without weakening evidence controls.
