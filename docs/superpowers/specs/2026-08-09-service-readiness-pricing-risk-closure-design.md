# DataForge service readiness, pricing, and risk closure design

Date: 2026-08-09
Status: approved approach A

## Goal

Make the production demo defensible end to end. An IT or finance administrator
must be able to associate an official model price, run a real FinOps risk scan,
open the evidence behind every displayed claim, and see whether the services
required by the demo are actually ready.

The existing MAF orchestration, workspace analysis flow, and business core stay
unchanged. This work closes production defects and missing operational
capabilities; it does not add autonomous production remediation or Azure bill
reconciliation.

## Current findings

1. Official price mappings are tenant-wide, but the current authorization check
   requires the caller to be Owner in every workspace they can access. The UI is
   embedded in one workspace and does not explain this tenant-wide boundary, so
   a valid administrator can receive an opaque 403.
2. The production candidate acceptance stops at the ROI decision because at
   least one maturity stage advertises evidence that is not an openable
   request-level reference.
3. The API reconciliation, rollup, and retention Container Apps jobs are Manual
   and use older backend images. They do not provide the continuous operating
   loop described by the portal.
4. Risk scanning already evaluates seven deterministic rules and persists its
   result, but the operator cannot see scan history, rule availability, evidence
   coverage, or a durable failed-run record. The UI therefore looks more like a
   presentation than an operational control.
5. The existing health endpoint validates infrastructure dependencies but does
   not prove that the authenticated application surfaces used in the demo are
   returning usable data.

## Architecture

### 1. Tenant-wide official pricing governance

Official deployment-to-price mappings remain tenant-wide because a deployment
has one provider price contract for the tenant. They are not duplicated into
workspace metadata.

Price writes require a trusted Easy Auth identity and an explicit tenant pricing
administrator grant. The grant is derived server-side from the authenticated
tenant and object ID. Production configuration uses
`DF_FINOPS_TENANT_OWNER_OIDS`; the existing all-workspaces-Owner rule remains a
backward-compatible fallback for environments that have not set the explicit
grant. Object IDs are identifiers, not credentials, and are never returned by
the pricing API.

`GET /api/finops/pricing/mappings` returns a bounded management capability:

- `scope: tenant`
- `can_manage: boolean`
- `authorization_source: entra_tenant_owner | workspace_owner_fallback | none`

The PUT and DELETE operations record mandatory audit events before changing
state. A missing audit store fails closed. Optimistic concurrency remains based
on `base_revision`; a conflict returns 409 and the browser reloads the current
mapping before asking the operator to retry.

The model settings UI labels the mapping as tenant-wide, disables write controls
when `can_manage` is false, and translates 403, 409, 422, and persistence errors
into specific Chinese recovery guidance. It never displays or accepts arbitrary
rates.

### 2. Durable risk scan runs

The seven existing rules remain the source of truth:

- error rate
- P95 latency
- daily estimated-cost budget
- token spike
- unified-entry coverage
- unpriced requests
- cache hit rate

Each manual scan first persists a `running` record, then persists either a
`completed` result or a `failed` result with a bounded safe error category. A
scan exposes the policy revision, ledger revision, requested scope, sample
count, rule counts, rule coverage, evidence coverage, start/finish time, and one
finding per rule. Raw identities, prompts, completions, provider responses, and
internal error text remain excluded.

Add read endpoints for bounded history and a single run:

- `GET /api/finops/risk/scans?workspace_id=...&limit=5`
- `GET /api/finops/risk/scans/{scan_ref}?workspace_id=...`

History is tenant- and workspace-scoped. A stored finding keeps the evidence
references selected at scan time. Opening an older scan never silently replaces
those references with evidence from a newer window.

The risk page presents a compact scan control above the decision views:

- last completed time and scan reference label
- evaluated/triggered/clear/insufficient/unavailable counts
- sample count, rule coverage, and evidence coverage
- explicit running, completed, failed, and retry states
- a compact history disclosure for the latest five scans
- per-rule result, observed value, threshold, reason, recommendation, and the
  evidence linked to that rule

Running a scan refreshes both the scan history and the current risk decision.
It does not execute remediation.

### 3. ROI request-level evidence integrity

The ROI decision service only returns `evidence_refs` that match an available
request event in the selected workspace and window. Counts that cannot be
projected remain visible, but their stage is partial and carries an explicit
evidence gap instead of a broken link.

The demo workspace seed must provide valid request lineage for every stage that
is presented as observed or verified. Candidate acceptance distinguishes a
truthful empty evidence state from an invalid reference, and the demo gate
requires all advertised demo references to open successfully.

### 4. Continuous FinOps jobs

The three jobs use the same immutable backend image as the released API:

- reconciliation: every 5 minutes, UTC cron `*/5 * * * *`
- rollup refresh: every 15 minutes, UTC cron `*/15 * * * *`
- retention: daily at 02:00 UTC, cron `0 2 * * *`

Before production traffic changes, each job is started manually once against
the candidate image. A successful execution and safe structured output are
required. Scheduling is enabled only after that one-shot acceptance succeeds.
The previous job configuration is captured as the rollback target.

### 5. Service-readiness audit

Extend the candidate acceptance contract into two layers:

1. Infrastructure health: backend, web, MCP, Redis, SQL-backed FinOps queries,
   Foundry, Search, Blob, Speech, Content Safety, and provider secret
   persistence.
2. Authenticated product readiness: auth session, workspace access, dashboard,
   data assets, conversations, runs, artifacts, FinOps bootstrap/cost/ROI/risk,
   evidence detail, provider catalog, model routing, official prices, price
   mappings, risk scan history, and Operations AI selected-evidence response.

The audit returns only status, latency, counts, and bounded error categories.
It does not return credentials, raw Entra IDs, prompt/response text, or provider
payloads.

Settings receives a compact read-only “服务状态” view grouped into Identity,
Data, AI, FinOps, and Background Jobs. It is a diagnostic surface for IT users,
not another dashboard. Unknown or stale state is labeled honestly and has a
refresh action; it is never replaced with sample success data.

## Failure handling

- 401: keep Easy Auth boundary; never fall back to a local production identity.
- 403 pricing write: explain that a tenant pricing administrator is required.
- 409 pricing write: reload the current revision and preserve the operator's
  selected official price for comparison before retry.
- 422 pricing write: show the server compatibility reason and keep the route
  unpriced.
- Scan failure: persist `failed`, keep the last successful decision visible,
  and expose a retry action.
- Missing evidence: retain the metric/count, mark it partial, and remove the
  broken drill-down action.
- Background job failure: do not enable its schedule; keep the prior job
  definition available for rollback.

## Security and product boundaries

- `DF_FINOPS_ACTIONS_ENABLED=0` remains unchanged.
- Provider credentials remain write-only and Key Vault-backed.
- No arbitrary price, APIM XML, script, resource ID, prompt, completion, raw
  identity, secret, or internal error body is accepted or returned.
- Price and risk writes are audited.
- Risk scanning is read-only analysis. It may create a remediation draft only
  through the existing separate user action; it cannot approve or execute it.
- No existing auth/Easy Auth registration is weakened for testing.

## Acceptance

The release is eligible for production only when all of these pass:

1. A configured Entra tenant pricing administrator can create, update, conflict,
   reload, and remove a mapping; an ordinary member receives 403.
2. DeepSeek appears in the governed model picker and its cached-input,
   uncached-input, and output prices are reflected in new request estimates.
3. A risk scan persists running to completed, evaluates seven rules, records
   independent evidence, appears in history, and survives a page refresh.
4. A forced scan failure persists a safe failed record and can be retried.
5. Every displayed ROI and risk evidence link in the demo workspace opens the
   matching request detail.
6. All authenticated product-readiness checks pass with non-empty demo display
   data where the UI promises content.
7. Reconciliation, rollup, and retention one-shot executions succeed on the
   candidate image; the three schedules then match the approved cron values.
8. Python, Node, Vite, and isolated-port Playwright pass, including desktop and
   mobile visual checks for error, loading, empty, success, conflict, and scan
   history states.
9. Committed-code secret scanning and post-cutover critical-log checks are
   clean.
10. Backend and web candidates remain zero traffic until all gates pass; the
    previous production revisions remain the rollback targets.
