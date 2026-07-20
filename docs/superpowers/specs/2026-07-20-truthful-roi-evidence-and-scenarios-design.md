# Truthful ROI Evidence and Scenario Measurement

## Status and scope

**Status:** proposed design, approved in principle on 2026-07-20. Implementation
starts only after this document is reviewed and the existing conversation
metadata/SSE allowlist work is complete.

**Goal:** make DataForge show a defensible view of agent cost and business
value. The product must distinguish observed operating cost, verified outcomes,
and hypothetical scenario calculations. It must not claim a native Azure AI
Foundry ROI integration when no supported provider API is connected.

**Out of scope:** changing Easy Auth, Entra tenant configuration, Key Vault
permissions, model pricing procurement, inventing business outcomes, or
presenting a scenario estimate as measured or verified value.

## Context and design decision

The current Foundry portal connection is an Application Insights trace source.
It provides telemetry such as spans, token counts, duration, and tool activity;
it is not evidence of an ROI data source. The existing DataForge code already
has a local snapshot calculator based on workspace runs, configured prices, and
outcome events. It also has a generic `FoundryRoiProvider` adapter boundary,
but no installed provider that reads an official Foundry ROI surface.

The product will use a three-layer model:

1. **Observed operating cost:** traceable usage totals, model/tool cost, and
   latency derived from recorded workspace runs and a configured price catalog.
2. **Business outcome evidence:** user-recorded outcomes such as revenue,
   conversion improvement, avoided cost, or saved hours. Every outcome has a
   source run, data/evidence revision, author, timestamp, measurement method,
   and verification state.
3. **ROI scenario measurement:** a separate, user-authored calculation using
   explicit assumptions. It is useful before a pilot has results but always
   remains an estimate.

The future Foundry adapter remains a capability boundary only. Until a
supported external provider is installed and its snapshot can be independently
validated, the UI must state `No official Foundry ROI source connected` rather
than invite configuration or imply an error.

## Alternative approaches considered

1. **Wait for a native Foundry integration.** This is the lowest engineering
   risk but leaves no customer value/ROI view today.
2. **Generate a single automatic ROI number.** This is attractive in a demo
   but is misleading because business revenue and savings are not observed by
   traces.
3. **Use evidence plus scenario measurement.** This is the chosen approach:
   it delivers value now without conflating measured cost, verified outcomes,
   and assumptions.

## Product surfaces

Rename the current ROI presentation to **Cost and Value**. It contains three
visually distinct sections with a shared time window and source links:

### Observed cost

- Model input/output tokens, tool calls, run duration, and computed cost.
- Each value links to the contributing runs/traces and price-catalog version.
- Missing price configuration renders `Not configured`; incomplete telemetry
  renders `Incomplete`; neither state displays a monetary total.

### Outcome evidence

- Shows outcome amount/value type, period, source run, evidence revision,
  author display name, verification method, and verified-at time.
- Allowed states are `verified`, `pending_verification`, and `not_recorded`.
- No business outcome means business value and realized ROI remain `Not
  recorded`; no fallback number is generated.

### Scenario measurement

- Lets an authorized editor create a bounded assumption set: expected benefit,
  pilot cost, adoption/conversion assumption, expected saved hours, and time
  horizon. The calculation records its formula and inputs.
- It is labeled `Estimated scenario`, never `verified`, and cannot change the
  status of observed cost or outcome evidence.
- Scenario records are versioned. Recalculation creates a new revision and
  preserves its prior assumptions and linked data revision.

## Data contracts

The backend exposes public, typed projections only. Names below describe the
required shape, not the final route names.

- `cost_evidence`: window, run/trace references, usage totals, price catalog
  reference, computed cost, completeness state, generated timestamp.
- `outcome_evidence[]`: bounded public ID, metric type, value/unit/currency,
  period, source run ID, evidence revision, verification state/method/time,
  actor display label, and created timestamp.
- `scenario[]`: bounded public ID, title, input assumptions, formula version,
  result, currency/unit, status `estimated`, source data/evidence revision,
  author display label, and timestamps.
- `foundry_integration`: state `not_connected`, `available`, or `verified`;
  provider name/version, observed timestamp, and bounded reason code. It must
  not expose provider credentials, raw attestation material, prompts, claims,
  or provider response bodies.

Every public projection is allowlisted. The same projection is used for
persistence, artifact summaries, and every SSE event; passing arbitrary nested
metadata through is forbidden.

## Calculation and evidence rules

- Usage cost = recorded input/output token quantities multiplied by the matched
  configured prices, plus any explicitly configured tool charges.
- Realized ROI is only calculated when a valid observed-cost value and at least
  one verified monetary outcome share a compatible currency and time window.
- Saved hours are operational evidence, not cash value, until a configured and
  recorded conversion method is supplied.
- Scenario ROI uses only its saved assumption record and never joins the
  verified result series.
- A verified provider snapshot may be displayed for comparison later, but it
  cannot promote a local estimate or unverified outcome.

## Error handling and access control

- A missing price catalog, outcome source, or trace data returns an explicit
  bounded state, not a synthetic zero or a healthy aggregate.
- Permission failures return a stable denied state; editors can write outcome
  and scenario records only if the workspace authorization result permits it.
- Owners and authorized reviewers can see actor display labels and evidence
  links. Other roles see only data authorized by the existing workspace policy.
- No prompt text, conversation history, Entra claims, email addresses outside
  member management, credentials, or raw trace attributes are exposed.

## Acceptance criteria

1. A signed-in workspace owner sees actual run/trace usage and a source-linked
   cost only when a matching price catalog is configured.
2. A workspace with no verified outcome renders `Not recorded` for realized
   business value and ROI.
3. An editor can create a scenario, refresh the page, and see the same
   versioned assumptions and result labeled `Estimated scenario`.
4. A verified outcome produces a realized ROI only when currency/window and
   evidence lineage are compatible; otherwise the UI explains why no value is
   calculated.
5. A scenario can never alter observed cost, outcome verification, or
   realized-ROI status.
6. The Foundry integration area says no official source is connected until a
   provider is actually installed and verified.
7. Public ROI, artifact, persistence, and SSE payloads pass strict typed
   projection tests and do not expose arbitrary nested metadata.
8. Backend tests, frontend tests/build, signed-in candidate validation at the
   measured desktop viewport and smaller breakpoints all pass before a traffic
   change.

## Delivery sequence

1. Replace generic metadata filtering with typed allowlisted projections for
   stored conversation data, artifacts, and every SSE event.
2. Audit and simplify the existing local ROI/Foundry adapter contract so the
   provider is a truthful future integration, not a simulated native service.
3. Add outcome evidence persistence, validation, and versioned scenario
   records.
4. Add Cost and Value API endpoints and connect the UI to authoritative load,
   empty, denied, and error states.
5. Add calculation, authorization, privacy, API, frontend, and browser tests.
6. Deploy a zero-traffic Container Apps candidate, validate signed-in flows,
   then request explicit production promotion approval.
