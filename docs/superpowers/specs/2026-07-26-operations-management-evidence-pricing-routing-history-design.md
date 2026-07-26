# Operations Management Evidence, Pricing, Routing, and AI History Design

**Date:** 2026-07-26
**Status:** Approved for implementation planning
**Scope:** DataForge Operations Management frontend, FinOps evidence ingestion, official-price estimates, per-Agent model routing, and persistent Operations AI history

## 1. Purpose

Upgrade the existing Operations Management experience without changing the MAF analysis kernel or the product's workspace, data, conversation, run, and artifact responsibilities.

The release must make the Operations Management pages useful to IT and finance users:

- charts must represent the magnitude of real data;
- every displayed metric must have a defined source and calculation;
- missing evidence must produce a specific, actionable state rather than a generic “unavailable” label;
- estimated cost must use official published model prices only;
- model selection must support a workspace default plus per-Agent overrides;
- Operations AI history must persist across refreshes, sessions, and devices;
- frontend behavior and visual quality are the primary acceptance gate.

## 2. Current Evidence and Root Causes

The 2026-07-26 production inspection established the following baseline:

- The trend bars are not hard-coded. For the selected workspace, `13,437` and `14,045` Token rendered at approximately `175.1px` and `183px`. They look equal because the values differ by only about 4.5%, and the chart lacks a visible scale, value labels, and metric switching.
- The organization-level daily series contains `61,425` and `14,045` Token. A correct zero-baseline chart should make this difference visually obvious.
- The FinOps ledger contains 13 observed requests and 75,470 Token. Ten requests have complete Token evidence; three do not.
- No request is currently priced because no active official-price mapping covers the observed deployments.
- APIM custom metrics are present. The inspected seven-day window contained 20 `Total Tokens` metric records totaling 199,425 Token.
- The FinOps APIM backfill queries `ApiManagementGatewayLogs` and `ApiManagementGatewayLlmLog`, but those tables contained no records in the same workspace and period. The collector therefore cannot reconcile requests and reports zero APIM-governed requests.
- The current Operations AI keeps messages only in React component state. Navigation, reload, or another device loses the conversation.
- A secure workspace model-routing API and UI already exist, but the schema is execution-kind based and the page is not surfaced as the requested Settings workflow.

These observations define implementation work. The release must fix the evidence and presentation paths; it must not fabricate replacement values.

## 3. Accepted Product Direction

The accepted direction combines frontend refinement and evidence-layer repair in one release.

### 3.1 Navigation and page structure

Keep `运营管理` as the primary navigation label. Its tabs are:

1. `运营总览`
2. `成本分析`
3. `效能与 ROI`
4. `风险与优化`

Remove budget-limit configuration and budget-consumption widgets from this release. Cost visibility comes first.

### 3.2 Operations overview

The overview uses the approved “metrics first” layout:

- one stable six-card metric band;
- one dominant trend chart;
- one compact data-trust panel;
- one contribution-attribution panel with switchable dimensions;
- one actionable-attention panel;
- one small Operations AI button.

The six core metrics are:

1. estimated cost;
2. requests;
3. Token;
4. success rate;
5. P95 latency;
6. cache hit rate.

APIM coverage belongs in `数据可信度`, not in the core business KPI band. It describes evidence quality rather than business performance.

The attribution panel switches among department, workspace, Agent, and model instead of rendering four repetitive panels.

The attention panel contains only items with a valid drill-down or correction action.

### 3.3 Visual language

- Use the existing application design system and one consistent thin-line icon family.
- Do not use emoji, text glyphs, or placeholder squares as production icons.
- Keep a common zero baseline for quantitative bar charts.
- Display axis labels and exact values; do not truncate the axis to exaggerate small differences.
- Provide Token, request, estimated-cost, and P95 metric switches on the trend chart.
- Preserve hover/focus feedback and keyboard access.
- Every KPI and non-obvious chart includes a small help icon. Its tooltip states the definition, denominator, source, time range, and evidence limitations in concise Chinese.

## 4. Evidence Architecture

The data path is:

`application event → APIM reconciliation → official-price estimation → SQL ledger/rollups → query API → Operations Management`

### 4.1 Application events

Application events remain the request-level primary ledger for:

- tenant and authorized workspace scope;
- request reference and correlation reference;
- run and Agent attribution;
- effective route and deployment;
- status, latency, and error category;
- input, output, cached-input, reasoning, and total Token when observed;
- cache state;
- routing-policy revision.

Missing provider usage remains missing. It must not be inferred from string length or replaced with zero.

### 4.2 APIM evidence

Retain the existing `llm-emit-token-metric` policy and privacy-safe dimensions for aggregate gateway evidence.

Add the APIM diagnostic settings required to populate:

- `ApiManagementGatewayLogs`;
- `ApiManagementGatewayLlmLog`.

Body capture remains zero bytes. Prompt, completion, authorization, secret, and raw identity values remain disabled.

The scheduled backfill runs every five minutes and reconciles application and APIM observations by the existing HMAC-derived correlation reference.

Reconciliation rules:

- matching application and APIM evidence is merged;
- it is never added as a second request;
- non-streaming provider usage remains preferred when complete;
- APIM streaming usage remains explicitly estimated;
- unmatched APIM observations remain aggregate evidence and do not create unattributed tenant requests;
- aggregate APIM Token and application Token are never added together.

The APIM backfill job must use the current backend release image. A successful job execution is not sufficient acceptance; it must report nonzero observations and reconciliations for controlled gateway calls.

### 4.3 Data-trust response

The overview response includes:

```json
{
  "trust": {
    "pricing": {
      "priced_requests": 0,
      "unpriced_requests": 13,
      "coverage_pct": 0,
      "state": "unpriced"
    },
    "tokens": {
      "known_requests": 10,
      "unknown_requests": 3,
      "coverage_pct": 76.9231,
      "state": "partial"
    },
    "apim": {
      "app_observed_requests": 13,
      "apim_governed_requests": 0,
      "unmatched_metric_records": 20,
      "coverage_pct": 0,
      "state": "reconciliation_pending"
    }
  }
}
```

Counts are illustrative of the inspected snapshot, not seed data. Production responses are calculated from the selected scope and window.

## 5. Official Price Estimates

### 5.1 Price catalog

Create a server-owned, versioned official price catalog with:

- provider;
- official model identifier;
- display name;
- deployment type;
- applicable region or region class;
- currency;
- input price per million Token;
- output price per million Token;
- cached-input and reasoning prices only when the official source publishes distinct rates;
- official source URL;
- effective date;
- reviewed timestamp;
- immutable catalog revision.

The release uses official public prices only. It does not ingest Azure Cost Management data, enterprise discounts, invoices, or negotiated rates.

### 5.2 Deployment mapping

Internal deployment aliases such as Terra, Sol, or Luna are not billing SKUs by themselves. Add an explicit mapping:

```json
{
  "deployment": "gpt-5.6-terra",
  "official_price_key": "azure-openai:gpt-5.1:global-standard:global",
  "mapping_revision": 4
}
```

The small edit button beside an unpriced metric opens a mapping workflow. Owners may select only a compatible entry from the server-owned official catalog. The UI does not accept an arbitrary price.

If a deployment cannot be matched reliably:

- display `未计价`;
- show the number of affected requests;
- provide the mapping action to an Owner;
- retain Token, request, latency, and quality statistics;
- never substitute zero cost.

### 5.3 Request cost

Each priced request records:

- effective Agent;
- effective deployment;
- official price key;
- input/output Token categories used in the formula;
- amount and currency;
- official price revision;
- routing-policy revision;
- evidence state.

Changing a mapping or price revision affects future requests only. Historical request estimates remain tied to their recorded revision.

The Cost Analysis page contains:

- total estimated cost;
- priced-request coverage;
- average cost per successful request;
- cost trend;
- department/workspace/Agent/model attribution;
- Agent and model cost structure;
- official-price catalog status;
- unpriced deployment queue with the edit action.

## 6. Per-Agent Model Routing

Extend the existing workspace routing policy without creating a separate uncontrolled model endpoint.

The effective precedence is:

1. manual request override, only where the existing trusted flow permits it;
2. per-Agent primary route;
3. execution-kind primary route for backward compatibility;
4. workspace default route;
5. compatible fallback route.

The policy schema adds:

```json
{
  "base_revision": 7,
  "default_route_id": "default",
  "agent_assignments": {
    "df-feasibility-analyst": {
      "primary_route_id": "terra",
      "fallback_route_id": "default"
    }
  },
  "execution_kind_assignments": {}
}
```

Control rules:

- only server-allowlisted routes are selectable;
- capability compatibility is enforced by the backend;
- Owner permission is required to edit;
- `base_revision` prevents silent overwrite and returns `409` on drift;
- the Settings modal shows the affected Agents before save;
- a one-click “apply to all compatible Agents” action is available;
- changes apply to the next run;
- every update is audited;
- every model call persists the actual Agent, route, deployment, selection source, and policy revision.

FinOps aggregation always uses the actual recorded deployment and Agent, not the current configuration.

## 7. Persistent Operations AI

Create a separate SQL-backed Operations AI conversation store.

Scope and retention:

- partition by tenant, canonical actor reference, and workspace;
- retain for 30 days;
- support cross-device retrieval after authentication;
- enforce the same workspace authorization as the dashboard;
- allow the user to start a new conversation and clear their history;
- expire rows through a scheduled retention job.

Only messages explicitly exchanged with Operations AI are stored. Business-run prompts, provider responses, internal errors, and arbitrary trace payloads do not enter this store.

The small AI popover:

- reopens the most recent conversation;
- lists recent conversations without occupying the full right side of the application;
- preserves the selected metric context;
- can receive a typed metric reference instead of asking the user to restate the metric;
- keeps the current page visible while chatting;
- displays loading, retry, empty, and expired-history states.

## 8. API Changes

Existing read APIs remain backward compatible. Add or extend:

### Operations bootstrap

`GET /api/finops/bootstrap`

Add the `trust` block and exact source freshness without removing current fields.

### Trend metrics

`GET /api/finops/trends?bucket=hour|day&metric=tokens|requests|cost|p95`

The response includes the value, component series where relevant, sample count, status, and currency.

### Official pricing

- `GET /api/finops/pricing/catalog`
- `GET /api/finops/pricing/mappings`
- `PUT /api/finops/pricing/mappings/{deployment}`

The write accepts an official catalog key and `base_revision`, not an arbitrary amount or source URL.

### Model routing

- retain `GET /api/workspaces/{workspace_id}/governance/model-routing`;
- extend `PUT /api/workspaces/{workspace_id}/governance/model-routing` with default and per-Agent assignments plus `base_revision`.

### Operations AI

- `GET /api/finops/assistant/conversations`
- `POST /api/finops/assistant/conversations`
- `GET /api/finops/assistant/conversations/{conversation_ref}/messages`
- `POST /api/finops/assistant/conversations/{conversation_ref}/messages`
- `DELETE /api/finops/assistant/conversations/{conversation_ref}`
- `DELETE /api/finops/assistant/history`

Conversation references are opaque. All queries derive the tenant and actor from trusted identity claims.

## 9. Frontend State Semantics

Replace generic `不可用` presentation with specific states:

- `正在同步`: a source refresh is active;
- `暂无样本`: the source is connected but the selected window has no qualifying events;
- `未计价`: Token exists but no official-price mapping covers the deployment;
- `待接入`: the source configuration is absent;
- `待对账`: APIM evidence exists but has not reconciled to application events;
- `数据不完整`: only part of the selected request set has evidence;
- `无权查看`: the identity lacks the required scope;
- `加载失败`: the request failed and retry is available.

Do not replace legitimate missing evidence with a synthetic number. An empty state must explain why the value is absent and, where authorized, provide the next action.

Navigation and page chrome render from the initial shell and do not wait for the FinOps bootstrap response. Prefetch Operations Management data after authentication and workspace scope resolution. Use stale-while-revalidate behavior so a refresh indicator does not blank already loaded data.

## 10. Security and Privacy

- Preserve Easy Auth tenant derivation and server-side workspace narrowing.
- Do not change authentication topology.
- Keep person-level breakdown restricted to admin or Owner.
- Keep price mapping and model routing Owner-only.
- Never expose provider response IDs, raw APIM request/response bodies, authorization headers, secrets, raw actor IDs, or internal error text.
- Keep `DF_FINOPS_ACTIONS_ENABLED=0`; this release does not enable automated production governance actions.
- Official-price estimates must be labeled as estimates, not invoices.

## 11. Testing and Acceptance

Frontend behavior and visual quality are release gates, not optional review.

### 11.1 Contract and calculation tests

- Every displayed field has an API contract test.
- Duplicate and late APIM evidence reconciles idempotently.
- Controlled unmatched APIM observations do not create duplicate requests.
- Unknown Token and unknown price remain distinct.
- A hand-calculated request matches the displayed estimate and recorded catalog revision.
- A model-routing change records the actual Agent, deployment, route source, and policy revision.
- Cross-tenant and unauthorized-workspace queries fail.

### 11.2 Chart tests

- Trend bars use a common zero baseline.
- `61,425` and `14,045` produce a height ratio consistent with their values within rendering tolerance.
- Equal values render equal heights.
- Missing cost does not render as a zero-height priced bar.
- Axis labels, exact values, legend, tooltip, keyboard focus, and metric switch are tested.
- Attribution bars and doughnut segments use the same aggregate values as the table.

### 11.3 Operations AI tests

- Close and reopen restores the latest conversation.
- Reload restores the conversation.
- A second authenticated browser context retrieves the same user/workspace history.
- Another user or workspace cannot retrieve it.
- Clear and 30-day retention delete the expected rows.
- Metric context is attached without exposing hidden request content.

### 11.4 Model routing tests

- Owner can set workspace default and compatible per-Agent routes.
- Non-Owner cannot write.
- Incompatible and non-allowlisted routes fail.
- A stale base revision returns `409`.
- One controlled Agent changes to Terra on the next run while unaffected Agents retain their routes.
- FinOps attributes the resulting request to the actual new deployment and official price revision.

### 11.5 APIM acceptance

- Managed-identity gateway call succeeds; anonymous call remains `401`.
- `ApiManagementGatewayLogs` and `ApiManagementGatewayLlmLog` receive privacy-safe records.
- Application and APIM correlation joins for a controlled request.
- The request changes from application-observed to APIM-governed without increasing the request count.
- Prompt and completion bodies remain absent.
- The scheduled backfill uses the current backend image and reports observations plus reconciliations.

### 11.6 Browser and visual acceptance

Test authenticated desktop and mobile layouts:

- navigation sections appear immediately and do not arrive after a delayed capability flash;
- no loading state shifts the metric band or main chart;
- all icons use the production icon system and align consistently;
- help tooltips remain within the viewport;
- hover and focus states work;
- unpriced edit and model-routing modal work end to end;
- Operations AI stays a compact popover;
- no horizontal overflow at supported widths;
- no console errors or failed network requests;
- refresh preserves stable content while revalidating;
- all empty, partial, permission, and failure states are visually inspected;
- screenshots are captured at desktop and mobile sizes.

### 11.7 Release gate

1. Run the complete Python and Node suites and the Vite production build.
2. Deploy backend and web candidates with zero production traffic.
3. Execute real multi-Agent, multi-model, success, failure, slow-request, cache, APIM, pricing, and AI-history acceptance scenarios.
4. Record API payload evidence and browser screenshots.
5. Require candidate revisions to remain Healthy.
6. Switch traffic only after all required interface and frontend checks pass.
7. Do not enable production governance execution.

## 12. References

- [APIM LLM token metric policy](https://learn.microsoft.com/en-us/azure/api-management/llm-emit-token-metric-policy)
- [Monitor Azure API Management](https://learn.microsoft.com/en-us/azure/api-management/monitor-api-management)
- [Plan and manage Microsoft Foundry costs](https://learn.microsoft.com/en-us/azure/foundry/concepts/manage-costs)
- [Azure Retail Prices API](https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices)
