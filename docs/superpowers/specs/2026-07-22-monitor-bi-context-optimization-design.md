# Monitor BI and Context Optimization Design

## Status and objective

**Status:** approved design, pending implementation plan review.

**Objective:** add a concise, trustworthy `Monitor` area to DataForge. It gives
workspace owners a business-intelligence view of governed text-model activity:
model consumption, token usage, latency, quality evidence, member attribution,
and verified return on investment. The page must be a decision surface, not a
second trace viewer.

The same delivery adds the backend contracts needed to observe model routing and
context optimization. It does not claim a saving, cost, quality improvement, or
Foundry ROI connection that cannot be supported by recorded evidence.

## Scope

### In scope

1. A top-level `Monitor` navigation item, shown in desktop and mobile
   navigation.
2. A current-workspace dashboard and an owner-only "all accessible workspaces"
   scope selector.
3. One atomic workspace monitor read model, rather than client-side joins over
   the existing governance endpoints.
4. Data-backed visualizations for a selected 7-day or 30-day interval:
   - request, success, and latency trend;
   - input/output/total token trend;
   - model and route consumption distribution;
   - member attribution for permitted owners and administrators;
   - quality, audit, and independently verified outcome evidence;
   - verified ROI when both cost and value inputs are available.
5. A Context Pack contract that selects bounded, relevant workspace evidence
   and durable conversation facts for follow-up calls.
6. A model-route decision record for every newly routed text-model call.
7. Offline before/after evaluation records that can prove or reject an
   optimization before it becomes the default route.

### Out of scope

- Easy Auth, Entra tenant configuration, billing export, or a separate admin
  portal.
- Raw prompts, conversation transcripts, credentials, Entra claims, model
  chain-of-thought, or unredacted telemetry in the UI or monitor API.
- A synthetic dollar saving, a fake ROI result, or a hard-coded industry,
  dataset, or customer conclusion.
- Routing image-generation calls through the text-model gateway. The initial
  dashboard labels image activity as outside the governed text-model scope.
- Direct code reuse from EvalAgentic. Its implementation depends on GitHub
  Copilot models and optional hosted Mem0; DataForge will implement only the
  applicable patterns on its existing Azure boundary.

## Product information architecture

`Monitor` is a top-level navigation destination, separate from `Settings`.
Settings remains the place for member administration, connection configuration,
and detailed governance evidence. Run history remains the path for a specific
trace.

The monitor page starts with a scope selector:

- `Current workspace` is the default and follows the active workspace.
- `All accessible workspaces` is visible only to an owner. It aggregates only
  workspaces where the actor has owner access; it never exposes another
  workspace name, member, or usage row to an unauthorized actor.

The page contains five fixed-height top metrics:

1. **Calls** - observed calls, success rate, and known/unknown attribution
   coverage.
2. **Tokens** - observed input, output, and total tokens. Unknown usage is
   counted separately and never treated as zero.
3. **Cost** - model-cost estimate only when a versioned price catalog matches
   the recorded model and usage. Otherwise show `Price catalog unavailable`.
4. **Quality** - evidence coverage, completed audits, rework/downgrade count,
   and offline evaluator coverage. A missing evaluator result is not a pass.
5. **Verified ROI** - verified business value less model and evaluator cost.
   If either independent outcome value or cost is missing, show `Pending
   verification` instead of a percentage.

The dashboard then presents three compact, stable chart regions:

- time-series trend for calls, tokens, and P95 latency;
- grouped horizontal model/route consumption bars, with tooltips that disclose
  `observed`, `estimated`, or `unavailable` basis;
- a right-side action panel for the most material, data-backed optimization
  opportunity and a small member-attribution table.

Detailed trace payloads, IDs, audit-event pagination, raw reasons, and
invitation history remain outside this page.

## Visual and interaction constraints

The dashboard is a calm enterprise control surface using the existing blue and
white DataForge visual system.

- Layout uses CSS grid with explicit `minmax` tracks and fixed chart-frame
  heights. Loading, error, empty, and ready states occupy the same frame, so a
  fetch cannot shift buttons or adjacent panels.
- Workspace/scope/date changes retain the page shell; only the data layer uses
  a short opacity/translate transition. No width, height, or font-size
  animation is allowed.
- Chart primitives use SVG and CSS in the existing React stack. No charting
  dependency is introduced solely for this page.
- Panels have at most 8px radius, restrained borders, clear legends, keyboard
  focus, and responsive horizontal overflow controls where necessary.
- Large operational details are progressively disclosed through a contextual
  link to the relevant run or governance view, not by nesting cards inside
  cards.

## Read model and API contract

Add a single owner/admin-protected endpoint:

`GET /api/monitoring?scope=current|portfolio&workspace_id={id}&from={ISO}&to={ISO}`

`current` requires owner/admin access to `workspace_id`. `portfolio` returns
only workspaces owned by the current actor. The endpoint returns an allowlisted
projection:

```json
{
  "scope": { "kind": "current", "workspace_id": "...", "label": "..." },
  "window": { "from": "...", "to": "...", "timezone": "UTC" },
  "freshness": { "generated_at": "...", "sources": ["run_store", "apim", "outcomes"] },
  "summary": {
    "calls": { "observed": 0, "succeeded": 0, "failed": 0, "unknown": 0 },
    "tokens": { "input": null, "output": null, "total": null, "known_runs": 0, "unknown_runs": 0 },
    "cost": { "status": "unavailable", "amount": null, "currency": "USD", "price_catalog_version": null },
    "quality": { "evidence_coverage_pct": null, "audited_runs": 0, "rework_runs": 0, "evaluator_coverage_pct": null },
    "roi": { "status": "pending_verification", "verified_value": null, "model_cost": null, "evaluator_cost": null, "roi_pct": null }
  },
  "series": { "daily": [] },
  "models": [],
  "routes": [],
  "members": [],
  "opportunity": { "status": "unavailable", "kind": null, "message": "No eligible optimization evidence yet." },
  "coverage": { "governed_text_calls": 0, "out_of_scope_image_calls": 0 }
}
```

Every count carries a source and a coverage field. A model row is emitted only
when the recorded run or APIM evidence identifies a model. Legacy rows are
reported as `unknown`, never assigned to a default model.

## Model routing and Context Pack

### Route contract

New text calls record one of four routes:

- `direct_reply`: bounded informational response without workspace analysis;
- `follow_up`: workspace-aware response using a Context Pack;
- `full_analysis`: full evidence and multi-agent analysis;
- `audit_repair`: evidence or audit remediation.

Each record contains the route, configured deployment/model identifier,
timestamp, actor reference, workspace reference, sanitized correlation ID,
observed token usage when returned by the provider, and latency. The record
does not include a raw prompt or internal reasoning.

A lightweight classifier may recommend a route, but the orchestrator remains
the policy authority. A request requiring evidence review, an audit, or a full
analysis cannot be silently downgraded to a cheaper model. Provider failure,
missing configuration, unknown route, or a failed classifier falls back to the
existing safe route and records `fallback_reason` from an allowlist.

### Context Pack

A Context Pack is bounded structured input, not full conversation replay. It
contains only:

- active workspace/data revision identifiers;
- concise, validated workspace profile facts;
- relevant evidence references and their display-safe summaries;
- most recent verified conclusion and audit constraints;
- a bounded set of durable conversation facts scoped to workspace, actor, and
  conversation; and
- the current user request.

It excludes raw historical messages unless the existing conversation route
requires them, in which case the legacy path remains available for comparison.
The pack receives a deterministic content fingerprint and version. Cache reuse
is permitted only for the same permitted scope and a valid data/evidence
revision. Cache entries expire and can be invalidated when workspace evidence
changes.

## Evaluation and ROI integrity

Optimization is evaluated asynchronously against a curated, sanitized suite of
real DataForge scenarios. For each case, record baseline and candidate route,
context token count, end-to-end latency, model usage, evidence coverage,
groundedness/evidence score, completion score, and evaluator version.

The candidate becomes eligible for default routing only when the recorded
evaluation reports no degradation in the configured evidence and completion
thresholds. Otherwise it remains an experiment or is disabled. The dashboard
shows sample count and evaluator coverage; it never converts an unreviewed
comparison into a saving claim.

ROI uses only independently verified outcome events plus observed/qualified
cost data. It has three explicit states: `verified`, `pending_verification`,
and `unavailable`. Foundry ROI Preview can later be added as a provider source,
but its absence must not change a local state to `verified`.

## Security and operational boundaries

- Existing owner/admin backend authorization is authoritative; the frontend
  only reflects the returned permission contract.
- APIM is the primary governed telemetry source for text/MAF calls. Model
  consumption based on local run records is labeled separately from APIM
  evidence. The dashboard must not imply complete gateway coverage for image
  calls.
- All telemetry dimensions are allowlisted. Workspace references remain hashed
  on external telemetry boundaries where the current APIM policy requires it.
- The monitor endpoint must gracefully return partial source states. A failing
  external metric query cannot erase run-store metrics or produce a global
  error screen.

## Acceptance criteria

1. An authorized owner sees a stable `Monitor` page for a current workspace
   and can select the authorized portfolio scope; other roles cannot request
   portfolio data through the API.
2. Page transition, loading, retry, date filtering, and scope changes do not
   change chart or toolbar geometry before data is ready.
3. A real routed text request appears with its actual route, model, token
   usage, latency, and actor attribution. A legacy run is visibly `unknown`
   rather than assigned invented values.
4. The cost and ROI cards remain unavailable/pending when price or verified
   business-value evidence is absent.
5. The model and route charts reconcile with the same data returned by the
   monitor endpoint and can be traced to a visible run without exposing raw
   prompts.
6. Context Pack and baseline paths are covered by offline evaluation tests;
   a quality regression disables candidate defaulting.
7. Backend tests, frontend unit tests, Vite build, desktop/mobile browser
   smoke, and a signed-in candidate deployment pass before production traffic
   changes.

## Delivery order

1. Build the monitor aggregation service and permission tests.
2. Add monitor view-model tests and the fixed-layout UI shell.
3. Surface real model/route telemetry and reconcile it to run records.
4. Add Context Pack construction, bounded cache, and default-path fallback.
5. Add offline evaluation storage/runner and gate route eligibility.
6. Validate end to end in a candidate Container Apps revision, then request a
   production-promotion decision.
