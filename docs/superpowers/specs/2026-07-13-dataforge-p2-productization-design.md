# DataForge P2 Productization Design

## Goal

Deliver productization, Azure governance, and multi-industry validation as one
traceable P2 release without weakening DataForge's evidence boundaries.

The release combines three workstreams:

- A: product reliability, response quality, latency, cost, durable tasks, and
  connector lifecycle;
- B: Azure observability, measured ROI, Entra collaboration, authorization,
  and auditability;
- C: capability-pack generalization, evidence-based iteration, customer
  onboarding, and branded artifact delivery.

P2 is implemented on `codex/p2-productization` as a stacked branch above PR
#15. The final P2 pull request remains one review unit but uses separate,
revertible commits for A, B, C, and production rollout.

## Current Production Baseline

- Backend build `71dc166` is deployed and healthy.
- First-class MAF runs as a deterministic 10 percent canary.
- The verified canary completed corpus, market, feasibility, and audit agents,
  including one bounded revision.
- A full canary currently costs about 65k tokens and roughly 88 seconds of
  workflow wall time on the reference workspace.
- Market search can return valid URLs that are semantically unrelated to the
  opportunity. A location-intelligence test returned fitness and training
  products as competitors.
- Artifact jobs, outcome events, experiment versions, workspace roles, and
  same-origin Easy Auth identity forwarding already exist.
- App Insights and OpenTelemetry are configured, but configuration status is
  not proof that a recent agent trace reached Azure Monitor.
- Foundry native ROI is not configured and must not be represented as active.

## Product Rules

1. No business name, dataset name, file name, industry name, or keyword list
   may determine a conclusion, score, verdict, or winning opportunity.
2. Capability packs may define vocabulary, metric families, validation
   methods, and artifact structure only.
3. Workspace evidence, external market inference, assumptions, targets,
   synthetic data, and observed outcomes remain visibly separate.
4. External sources that fail relevance or provenance checks cannot support a
   score, verdict promotion, or competitor claim.
5. Missing values remain unknown. Estimated, measured, and verified ROI are
   separate states.
6. Connector credentials and access tokens are never logged, traced, returned
   to the browser, or stored in workspace files.
7. Easy Auth configuration is not changed by application code.
8. Every long-running operation is durable across navigation, refresh,
   replica replacement, and user re-login.
9. P2 cannot promote the MAF canary until quality, latency, token, and failure
   gates are measured on both the legacy and MAF paths.

## Workstream A: Productization

### A1. Market Evidence Relevance Gate

Add a backend-owned market query and relevance boundary:

```text
normalized user goal + selected opportunity + corpus evidence digest
  -> market query plan
  -> provider search
  -> source normalization
  -> deterministic relevance features
  -> model relevance judgment with typed reasons
  -> accepted market evidence or explicit market gap
```

Each market source records:

- source URL and normalized domain;
- title and bounded snippet;
- retrieval query and query purpose;
- opportunity terms derived from the current analysis, not an allowlist;
- deterministic lexical/semantic relevance features;
- typed relevance verdict: `accepted`, `adjacent`, or `rejected`;
- rejection reasons and observation time.

Only `accepted` sources enter competitor comparison or market scoring.
`adjacent` sources may appear in a separate context section. `rejected`
sources remain trace metadata and are not customer citations.

The final response states `external evidence unavailable` when no source
passes. It never substitutes unrelated sources to make the result look
complete.

### A2. Shared Evidence Bundle And Execution Budget

Build one bounded, typed evidence bundle per analysis and share it across
participants. The bundle contains evidence references, compact quotes, data
profile facts, gaps, and the selected capability packs. Raw rows and complete
documents are not repeated in every agent prompt.

Optimization rules:

- skip market search when no external comparison is required;
- skip audit for low-impact factual workspace reads;
- send only disputed dimensions and affected evidence into revision;
- keep one correction retry for invalid typed outputs;
- cap per-agent evidence count, quote length, output tokens, and total calls;
- persist observed cache hits, prompt-cache tokens, and budget termination.

Reference-suite release targets:

- unsupported-claim rate must not increase;
- groundedness must not decrease;
- P50 full-analysis wall time <= 45 seconds;
- P95 full-analysis wall time <= 75 seconds;
- median full-analysis tokens <= 30k;
- no required corpus or audit branch may be removed solely to meet latency.

Targets are rollout gates, not values shown as achieved before measurement.

### A3. Unified Durable Task Center

Create a generic task store used by analysis, upload ingestion, connector
import/sync, artifact generation, outcome extraction, and experiment refresh.

Task contract:

```json
{
  "task_id": "task_...",
  "workspace_id": "...",
  "kind": "analysis|ingest|connector_import|connector_sync|artifact|iteration",
  "status": "queued|running|partial|completed|failed|cancelled",
  "progress": {"current": 0, "total": null, "stage": "..."},
  "source": {"run_id": null, "file_ids": [], "connector_id": null},
  "result": {"run_id": null, "artifact_job_id": null, "file_ids": []},
  "errors": [],
  "warnings": [],
  "actor": {},
  "created_at": "...",
  "updated_at": "..."
}
```

Persistence follows the existing local-plus-Blob store pattern with atomic
worker claims and stale-task recovery. Cancellation is cooperative and only
offered for stages that can stop safely. Retry creates a new attempt linked to
the original task; it does not erase failure history.

Frontend behavior:

- one global task drawer and completion notification system;
- page-local progress links to the same task record;
- navigation and refresh do not lose active operations;
- completed tasks link to the run, imported files, experiment, or artifacts;
- non-blocking failures preserve completed partial results.

### A4. Connector Lifecycle

Replace process/session-only connector state with durable connector records and
secret references.

- Public metadata persists in the DataForge connector store.
- Credentials persist in Azure Key Vault through the Container App managed
  identity.
- DataForge stores only a Key Vault secret identifier and credential version.
- Existing manual encrypted-session mode remains an explicit fallback when Key
  Vault is not configured.
- Reconnect tests the secret without returning it.
- Disconnect revokes the active session; deleting a connector also removes or
  disables the secret according to retention policy.
- Scheduled sync and incremental import record source table/container, query or
  prefix, cursor/watermark, source observation time, actor, and imported file
  version.

SQL remains read-only. Identifiers are validated and quoted by the driver;
table names never become raw SQL fragments. Blob prefixes and container names
are validated before access.

## Workstream B: Azure Governance And ROI

### B1. Trace Delivery Proof

Extend monitoring status from configuration-only to delivery-aware:

- configured SDK/exporter state;
- last local span emitted;
- last Azure Monitor trace correlation ID observed;
- most recent successful export time;
- recent export error category;
- a deep link to the matching Application Insights transaction when one can be
  built without exposing secrets.

Run and agent spans include hashed workspace/actor correlation, run ID,
collaboration pattern, participant ID, tool name, cache state, retry count,
token usage, outcome event IDs, task IDs, and status. They exclude raw prompts,
raw evidence, email, connector credentials, and model reasoning.

Experimental Foundry GenAI instrumentation is enabled only after a canary proves
it does not break SSE, response parsing, or MAF spans. Otherwise DataForge's
explicit OpenTelemetry instrumentation remains authoritative.

### B2. Measured ROI And Foundry Adapter

The existing outcome ledger remains the source of customer business inputs.
Add a normalized ROI snapshot:

- observed model/tool/infrastructure cost;
- explicit human-time assumptions;
- measured human-time and business outcomes;
- attribution window and confidence;
- workspace, run, experiment, artifact, actor, and outcome lineage;
- verification actor and time;
- status: `estimated`, `measured`, or `verified`.

Add an optional Foundry ROI adapter that:

- reports `not_configured` until the Foundry-side agent and ROI configuration
  are discoverable;
- maps DataForge run/agent/outcome IDs to provider identifiers;
- reads provider ROI values without replacing local source evidence;
- shows reconciliation differences and timestamps;
- never reports native ROI as connected based only on an environment flag.

Per-member chargeback shows observed tokens and estimated provider cost by
model, task kind, workspace, and time window. Unknown token or price data stays
unknown rather than zero.

### B3. Entra Collaboration And Immutable Audit

Complete the collaboration lifecycle:

- search the permitted Entra directory scope;
- invite by existing tenant user or external email;
- email/Graph invitation state: pending, accepted, expired, failed, revoked;
- activate workspace membership only when trusted Easy Auth object and tenant
  claims match the accepted invitation;
- owner/admin/editor/viewer authorization remains centralized;
- role changes, revocation, analyses, data mutation, connector actions,
  artifacts, outcome verification, and experiment promotion emit audit events.

Audit events are append-only and contain actor, action, workspace, resource,
result, timestamp, request/run/task correlation, and bounded reason metadata.
They exclude content, credentials, tokens, and raw claims.

## Workstream C: Generalization And Customer Validation

### C1. Capability Pack Registry

Create data-driven capability packs for:

- growth and retention;
- productization and pricing;
- site and channel selection;
- operational efficiency;
- campaign and service design;
- risk, compliance, and data readiness.

Pack selection uses business goal, schema roles, metric types, time coverage,
entity relationships, and quality signals. The selector returns multiple packs
with reasons and confidence. Packs define questions, metrics, evidence needs,
validation patterns, and artifact sections. They do not define conclusions,
scores, named opportunities, or preferred industries.

### C2. Experiment-Centered Iteration

Promote the existing experiment ledger to the primary iteration experience.

Each version shows:

- hypothesis and decision;
- evidence added, removed, contradicted, or strengthened;
- assumptions, targets, synthetic values, and observed values;
- metric baseline, target, observation window, sample, and stop criteria;
- verdict, score, scope, segment, pricing, pilot, and artifact decision deltas;
- source file/connector versions and verification status.

Generating a plan or artifact attaches to the current version and does not
create fake evidence progress. A new version requires a new decision or new
source-linked evidence. Synthetic feedback cannot strengthen the verdict.

### C3. Adaptive Onboarding And Artifact Delivery

Onboarding asks for business goal, decision to support, audience, available
data, sensitive fields, time horizon, and desired validation outcome. It does
not ask the user to choose an industry template.

Artifact records gain customer-facing title, version, capability packs,
experiment link, source run, generated time, brand profile version, content
summary, and download status. PDF, roadmap, validation plan, risk register,
concept image, and audio use the same artifact registry and version naming.

Brand assets are workspace-scoped and versioned. The user can choose whether a
logo appears in documents, images, both, or neither. Transparent logos are
composited according to the target medium rather than placed on an arbitrary
white or transparent patch.

## API Surface

New or expanded contracts include:

- `GET /api/workspaces/{id}/tasks`
- `GET /api/tasks/{task_id}`
- `POST /api/tasks/{task_id}/cancel`
- `POST /api/tasks/{task_id}/retry`
- `GET /api/workspaces/{id}/market-evidence/{run_id}`
- `GET /api/workspaces/{id}/connectors`
- `POST /api/workspaces/{id}/connectors/{connector_id}/reconnect`
- `POST /api/workspaces/{id}/connectors/{connector_id}/sync`
- `DELETE /api/workspaces/{id}/connectors/{connector_id}`
- `GET /api/workspaces/{id}/governance/trace-status`
- `GET /api/workspaces/{id}/governance/roi`
- `GET /api/workspaces/{id}/governance/audit-events`
- `GET /api/workspaces/{id}/capability-packs`
- `GET /api/workspaces/{id}/experiments/{version_id}`

Existing endpoints remain backward compatible and may create generic task
records internally before their old response contracts are retired.

Every object endpoint resolves its workspace from the stored object before
authorization. Client-supplied workspace IDs are never trusted for access.

## Frontend Information Architecture

No new top-level navigation item is required.

- Workspaces: capability packs, current decision, evidence strength, active
  tasks, experiment status.
- Data: durable connectors, sync state, lineage, imported versions.
- Runs: actual collaboration pattern, agent timing/cost, source relevance,
  trace link, fallback/degradation.
- Conversation: fast/direct versus full-analysis route, evidence-aware answers,
  durable background completion.
- Artifacts: persistent registry, versions, brand profile, experiment lineage.
- Settings: members, invitations, role usage, audit events, Azure Monitor,
  Foundry ROI status, Key Vault connector state.

The UI uses truthful empty/error states and never falls back to `Demo User` for
an authenticated production workspace. Missing identity is shown as unknown or
signed out.

## Error Handling

- No relevant market evidence: continue with workspace evidence and a visible
  external-evidence gap.
- Required corpus failure: fail closed and prevent verdict promotion.
- Optional market failure: complete as degraded and explain the missing branch.
- Task worker crash: stale claim recovery resumes or marks retryable failure.
- Key Vault unavailable: connector remains disconnected; no credential value is
  copied into logs or fallback metadata.
- Azure Monitor export unavailable: local run remains valid and monitoring
  status becomes partial.
- Foundry ROI unavailable: local ROI remains available with provider status
  `not_configured` or `unavailable`.
- Invitation mismatch: membership stays pending and an audit event records the
  rejected activation without storing raw claims.

## Evaluation And Acceptance

### Automated

- full Python test suite and compilation;
- frontend production build and dependency audit;
- deterministic market relevance tests with relevant, adjacent, and unrelated
  sources;
- task crash/restart, retry, cancellation, idempotency, and cross-workspace
  authorization tests;
- Key Vault adapter tests using a fake secret client and production managed
  identity smoke without displaying secret values;
- ROI state and reconciliation tests;
- invitation claim-match and role enforcement tests;
- capability-pack selection tests across at least five domain-neutral schema
  shapes;
- experiment tests proving synthetic evidence cannot strengthen a verdict.

### Production

- legacy versus MAF A/B report on the same evaluation set;
- no irrelevant accepted competitor in the reference market test;
- MAF latency/token gates measured and published, not inferred;
- one analysis survives navigation and refresh through the task center;
- one artifact and one connector import survive replica replacement;
- one trusted Entra member invitation and one revoked/denied action are visible
  in the audit log;
- one estimated ROI workspace and one source-linked measured outcome are shown
  with distinct states;
- Azure Monitor trace correlation opens the matching transaction;
- two materially different data shapes select different capability packs and
  produce evidence-dependent conclusions;
- one observed feedback import creates a real evidence/decision delta and a new
  artifact version.

## Rollout

1. Capture a frozen production baseline for quality, latency, token use, source
   relevance, task failure, and connector recovery.
2. Land A behind feature flags and verify the unified task and market gates.
3. Land B with Key Vault/Graph/Monitor permissions configured through Azure;
   application code does not change Easy Auth.
4. Land C and run the multi-domain evaluation suite.
5. Deploy immutable backend and frontend images to preview.
6. Run full browser, API, connector, invitation, task recovery, and artifact
   acceptance in preview.
7. Deploy to production with MAF still at 10 percent.
8. Promote MAF to 50 and then 100 percent only if the published A/B gates pass.

## Non-Goals

- adding a Hugging Face model solely to make responses appear less rigid;
- open-ended autonomous agents without budgets or typed contracts;
- industry-specific scores, recommendations, or named opportunities;
- fabricating customer outcomes to demonstrate measured ROI;
- changing Azure Easy Auth configuration from application code;
- exposing connector credentials or raw user content in telemetry.
