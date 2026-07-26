# Operations Management Evidence, Pricing, Routing, and AI History Implementation Plan

> **Execution rule:** Implement this plan task-by-task with test-first changes and a
> focused commit after every green task. Preserve the existing MAF business kernel,
> Easy Auth boundary, workspace authorization, and production traffic until the
> candidate acceptance gate passes.

**Goal:** Turn Operations Management into a trustworthy FinOps/ROI dashboard with
official-price estimates, APIM reconciliation, per-Agent model routing, and
cross-device Operations AI history.

**Architecture:** Application events remain the request ledger. APIM gateway and
LLM logs reconcile into that ledger by the existing HMAC correlation reference and
are never added as a second request. A versioned server-owned price catalog maps
deployments to official public price keys. SQL stores all durable pricing, routing,
conversation, and evidence state; Redis remains a query cache only. The React portal
renders real zero-baseline series, explicit evidence states, and focused correction
actions.

**Stack:** FastAPI, Pydantic, pyodbc/Azure SQL, Azure Cache for Redis, Azure API
Management, Azure Monitor/Log Analytics, React, Vite, Node test runner, Playwright,
Azure Container Apps.

---

## Task 1: Add the official-price catalog domain

**Files:**

- Create: `backend/finops/official_pricing.py`
- Create: `backend/finops/data/official_model_prices.json`
- Create: `tests/test_finops_official_pricing.py`
- Modify: `backend/finops/models.py`

**Step 1: Write failing catalog tests**

Cover:

- an immutable revision and reviewed timestamp are required;
- only `USD` is accepted in this release;
- input and output prices are non-negative per one million Token;
- optional cached-input and reasoning prices remain absent unless published;
- every entry has an HTTPS Microsoft or Azure official source URL;
- lookup rejects a deployment alias and accepts only an official price key;
- unknown keys return an explicit `unpriced` result rather than zero.

Run:

```powershell
python -m pytest tests/test_finops_official_pricing.py -q
```

Expected: failures because the catalog types and loader do not exist.

**Step 2: Implement strict catalog types and loader**

Add:

```python
class OfficialPrice(BaseModel):
    price_key: str
    provider: Literal["azure-openai", "openai"]
    official_model: str
    display_name: str
    deployment_type: str
    region_class: str
    currency: Literal["USD"]
    input_per_million: Decimal
    output_per_million: Decimal
    cached_input_per_million: Decimal | None = None
    reasoning_per_million: Decimal | None = None
    source_url: AnyHttpUrl
    effective_at: datetime
    reviewed_at: datetime
    revision: str

class OfficialPriceCatalog:
    def get(self, price_key: str) -> OfficialPrice | None: ...
    def estimate(self, price_key: str, tokens: FinOpsTokens) -> EstimatedCost: ...
```

Load the bundled JSON once per process, validate it at startup, and keep its order
stable for the Settings picker.

Populate the catalog only with model/deployment combinations confirmed against the
official Microsoft pricing page or Azure Retail Prices API during implementation.
Record the exact official source URL, effective date, review time, and revision in
the JSON. Do not infer a Terra/Sol/Luna alias from its display name.

**Step 3: Verify and commit**

Run:

```powershell
python -m pytest tests/test_finops_official_pricing.py tests/test_finops_ingestion.py -q
```

Commit:

```powershell
git add backend/finops/official_pricing.py backend/finops/data/official_model_prices.json backend/finops/models.py tests/test_finops_official_pricing.py
git commit -m "feat: add official model price catalog"
```

## Task 2: Add durable pricing mappings and Operations AI conversations

**Files:**

- Modify: `backend/sql/finops_schema.sql`
- Create: `backend/finops/sql_pricing.py`
- Create: `backend/finops/assistant_store.py`
- Create: `backend/finops/sql_assistant.py`
- Modify: `backend/finops/sql_repository.py`
- Modify: `tests/test_finops_sql.py`
- Create: `tests/test_finops_assistant_store.py`

**Step 1: Write failing SQL and repository tests**

Specify additive, idempotent DDL for:

```text
df_finops.official_price_mapping
  tenant_ref, deployment, official_price_key, mapping_revision,
  updated_by_ref, updated_at

df_finops.assistant_conversation
  tenant_ref, actor_ref, workspace_id, conversation_ref, title,
  created_at, updated_at, expires_at

df_finops.assistant_message
  tenant_ref, actor_ref, workspace_id, conversation_ref, message_id,
  role, content, metric_context_payload, created_at
```

Require tenant, actor, and workspace in every conversation key. Verify:

- compare-and-swap rejects stale mapping revisions;
- conversations never cross tenant, user, or workspace scope;
- messages preserve order;
- the default expiry is 30 days;
- cleanup deletes expired conversations and messages;
- returned rows do not expose raw identity values.

Run:

```powershell
python -m pytest tests/test_finops_sql.py tests/test_finops_assistant_store.py -q
```

Expected: failures for missing schema and repositories.

**Step 2: Implement additive schema and typed repositories**

Use the existing SQL connection and HMAC identity helpers. Do not introduce a
separate database connection path. Add indexes for mapping lookup, recent
conversations, ordered message reads, and expiry cleanup.

Expose:

```python
class PriceMappingRepository(Protocol):
    def list(self, tenant_ref: str) -> tuple[DeploymentPriceMapping, ...]: ...
    def upsert(self, mapping: DeploymentPriceMapping, *, base_revision: int) -> DeploymentPriceMapping: ...

class AssistantConversationStore(Protocol):
    def list_conversations(self, scope: AssistantScope, *, limit: int, cursor: str | None) -> CursorPage: ...
    def get_messages(self, scope: AssistantScope, conversation_ref: str) -> tuple[AssistantMessage, ...]: ...
    def append(self, scope: AssistantScope, conversation_ref: str, message: AssistantMessage) -> None: ...
    def clear(self, scope: AssistantScope, conversation_ref: str) -> None: ...
    def purge_expired(self, now: datetime) -> int: ...
```

**Step 3: Verify and commit**

Run:

```powershell
python -m pytest tests/test_finops_sql.py tests/test_finops_assistant_store.py -q
```

Commit:

```powershell
git add backend/sql/finops_schema.sql backend/finops/sql_pricing.py backend/finops/assistant_store.py backend/finops/sql_assistant.py backend/finops/sql_repository.py tests/test_finops_sql.py tests/test_finops_assistant_store.py
git commit -m "feat: persist price mappings and operations ai history"
```

## Task 3: Connect price mappings to request estimates

**Files:**

- Modify: `backend/finops/ingestion.py`
- Modify: `backend/finops/management.py`
- Modify: `backend/finops/router.py`
- Modify: `backend/finops/models.py`
- Modify: `backend/finops/sql_repository.py`
- Modify: `tests/test_finops_ingestion.py`
- Modify: `tests/test_finops_api.py`
- Create: `tests/test_finops_pricing_api.py`

**Step 1: Write failing pricing and API tests**

Cover:

- `GET /api/finops/pricing/catalog`;
- `GET /api/finops/pricing/mappings`;
- Owner-only `PUT /api/finops/pricing/mappings/{deployment}`;
- the write body accepts an official price key and `base_revision`, not a rate;
- a stale revision returns `409`;
- a member may read catalog status but may not edit mappings;
- a mapped request records amount, currency, official price key, catalog revision,
  and routing revision;
- an unmapped request remains `unpriced` with `amount=null`;
- historical priced rows are not rewritten after a mapping changes.

Run:

```powershell
python -m pytest tests/test_finops_ingestion.py tests/test_finops_pricing_api.py tests/test_finops_api.py -q
```

Expected: failures for missing mapping endpoints and estimator integration.

**Step 2: Implement future-request pricing**

At ingestion time, resolve the effective deployment through
`PriceMappingRepository`, then estimate with `OfficialPriceCatalog`. Store all
evidence and revision fields on the request fact. Preserve the current unknown-token
behavior; a mapped deployment with incomplete Token evidence is still `partial`, not
zero cost.

Do not retroactively mutate priced facts. Existing unpriced facts remain visibly
unpriced; controlled calls after mapping activation provide the first priced
acceptance samples.

**Step 3: Verify and commit**

Run:

```powershell
python -m pytest tests/test_finops_ingestion.py tests/test_finops_pricing_api.py tests/test_finops_api.py -q
```

Commit:

```powershell
git add backend/finops/ingestion.py backend/finops/management.py backend/finops/router.py backend/finops/models.py backend/finops/sql_repository.py tests/test_finops_ingestion.py tests/test_finops_api.py tests/test_finops_pricing_api.py
git commit -m "feat: estimate request cost from official mappings"
```

## Task 4: Expose data trust and metric-specific trends

**Files:**

- Modify: `backend/finops/query.py`
- Modify: `backend/finops/router.py`
- Modify: `backend/finops/query_cache.py`
- Modify: `tests/test_finops_query.py`
- Modify: `tests/test_finops_api.py`

**Step 1: Write failing query tests**

Define:

```python
class FinOpsTrust(BaseModel):
    pricing: PricingTrust
    tokens: TokenTrust
    apim: ApimTrust

def trends(..., metric: Literal["tokens", "requests", "estimated_cost", "p95_latency_ms"]) -> TrendResponse: ...
```

Verify:

- overview/bootstrap returns pricing, Token, and APIM coverage counts plus state;
- states distinguish `syncing`, `no_samples`, `unpriced`, `partial`,
  `reconciliation_pending`, `complete`, `forbidden`, and `failed`;
- coverage denominators exclude records that are not eligible;
- trend buckets use a common zero baseline and return exact values;
- cost trend values are null when the bucket contains only unpriced requests;
- cache keys include metric and all scope/window filters.

Run:

```powershell
python -m pytest tests/test_finops_query.py tests/test_finops_api.py -q
```

Expected: failures for the trust block and metric selector.

**Step 2: Implement aggregate queries and compatible responses**

Keep existing response fields for backward compatibility. Add the trust block to
bootstrap/overview and accept `metric` on `/api/finops/trends`. Return exact numeric
values and data states; presentation scaling stays in the frontend.

**Step 3: Verify and commit**

Run:

```powershell
python -m pytest tests/test_finops_query.py tests/test_finops_api.py -q
```

Commit:

```powershell
git add backend/finops/query.py backend/finops/router.py backend/finops/query_cache.py tests/test_finops_query.py tests/test_finops_api.py
git commit -m "feat: expose finops trust and metric trends"
```

## Task 5: Repair the APIM evidence path

**Files:**

- Modify: `infra/apim/dataforge-telemetry.json`
- Modify: `backend/finops/apim_collector.py`
- Modify: `backend/finops/apim_backfill.py`
- Modify: `tests/test_finops_apim_collector.py`
- Modify: `tests/test_finops_apim_backfill.py`
- Modify: `docs/monitoring-azure-state.md`

**Step 1: Write failing collector tests**

Test:

- gateway and LLM log rows normalize into the same correlation model;
- matching app/APIM evidence reconciles one request;
- custom metrics remain aggregate evidence and are never summed with request Token;
- streaming Token remains `estimated`;
- unmatched APIM rows cannot create a cross-tenant request;
- prompt, completion, body, authorization, and provider response ID are discarded;
- collector summaries include observed, matched, unmatched, and failed counts.

Run:

```powershell
python -m pytest tests/test_finops_apim_collector.py tests/test_finops_apim_backfill.py -q
```

Expected: failures for missing reconciliation evidence and summary behavior.

**Step 2: Add privacy-safe APIM resource diagnostics**

Extend the ARM template to create the APIM service diagnostic setting for the
supported gateway and LLM log categories, targeting the existing Log Analytics
workspace. Retain the API-level Application Insights diagnostic and token metric.
Keep frontend/backend request and response body byte limits at zero and do not add
header capture.

**Step 3: Implement reconciliation summaries**

Normalize current Azure table schemas defensively, use the existing HMAC correlation
key, and persist only allowed fields. Make the five-minute job fail visibly when the
query fails, while treating a valid zero-row interval as a successful empty run.

**Step 4: Verify and commit**

Run:

```powershell
python -m pytest tests/test_finops_apim_collector.py tests/test_finops_apim_backfill.py -q
```

Commit:

```powershell
git add infra/apim/dataforge-telemetry.json backend/finops/apim_collector.py backend/finops/apim_backfill.py tests/test_finops_apim_collector.py tests/test_finops_apim_backfill.py docs/monitoring-azure-state.md
git commit -m "fix: reconcile apim evidence into finops ledger"
```

## Task 6: Extend routing from execution kind to Agent

**Files:**

- Modify: `backend/workspace_model_config.py`
- Modify: `backend/model_policy.py`
- Modify: `backend/maf_agents.py`
- Modify: `backend/maf_team_runtime.py`
- Modify: `backend/orchestrator.py`
- Modify: `backend/control_plane.py`
- Modify: `tests/test_workspace_model_config.py`
- Modify: `tests/test_model_policy.py`
- Modify: `tests/test_model_routing_api.py`
- Modify: `tests/test_maf_agents.py`
- Modify: `tests/test_orchestrator.py`

**Step 1: Write failing routing tests**

Verify:

- policy validation accepts only registered Agent IDs and compatible routes;
- precedence is manual override, Agent assignment, execution kind, workspace default,
  then fallback;
- an unavailable primary route selects its configured fallback;
- Owner writes require `base_revision`, and drift returns `409`;
- one-click apply-all expands to explicit compatible Agent assignments;
- each MAF Agent client receives its own effective route;
- runtime/FinOps events record Agent ID, effective route, deployment, and routing
  revision actually used.

Run:

```powershell
python -m pytest tests/test_workspace_model_config.py tests/test_model_policy.py tests/test_model_routing_api.py tests/test_maf_agents.py tests/test_orchestrator.py -q
```

Expected: failures because only execution-kind assignments exist.

**Step 2: Implement policy schema and selection**

Add:

```python
def select_text_route_record(
    execution_kind: str,
    *,
    agent_id: str | None = None,
    manual_route_id: str | None = None,
) -> SelectedTextRoute: ...
```

Extend the persisted schema with `default_route_id`, `agent_assignments`,
`execution_kind_assignments`, and revision metadata while still reading the existing
execution-kind format. Build Agent chat clients from each Agent's selected route
instead of sharing one route-derived client across the registry.

Extend `MafRuntimeEvent` only with allowlisted route evidence; do not expose provider
response IDs to clients.

**Step 3: Verify and commit**

Run:

```powershell
python -m pytest tests/test_workspace_model_config.py tests/test_model_policy.py tests/test_model_routing_api.py tests/test_maf_agents.py tests/test_orchestrator.py -q
```

Commit:

```powershell
git add backend/workspace_model_config.py backend/model_policy.py backend/maf_agents.py backend/maf_team_runtime.py backend/orchestrator.py backend/control_plane.py tests/test_workspace_model_config.py tests/test_model_policy.py tests/test_model_routing_api.py tests/test_maf_agents.py tests/test_orchestrator.py
git commit -m "feat: route and meter models per agent"
```

## Task 7: Persist Operations AI history

**Files:**

- Modify: `backend/finops/assistant.py`
- Modify: `backend/finops/router.py`
- Modify: `tests/test_finops_assistant.py`
- Modify: `tests/test_finops_api.py`

**Step 1: Write failing assistant API tests**

Cover:

- `GET /api/finops/assistant/conversations`;
- `POST /api/finops/assistant/conversations`;
- `GET /api/finops/assistant/conversations/{conversation_ref}/messages`;
- `DELETE /api/finops/assistant/conversations/{conversation_ref}`;
- `/assistant/query` appends user and assistant messages server-side;
- only the current tenant, user, and workspace may access a conversation;
- the assistant receives current metric context but no raw prompt/completion,
  identity, secret, or internal error text;
- expiry is 30 days and clear is auditable.

Run:

```powershell
python -m pytest tests/test_finops_assistant.py tests/test_finops_api.py -q
```

Expected: failures because the current assistant accepts component-local history.

**Step 2: Implement conversation-backed assistant flow**

Make `conversation_ref` the durable context key. Keep a bounded server-side history
window for model calls while returning the complete paginated UI history. Generate a
safe title from the first user question and allow a new conversation after clear.

**Step 3: Verify and commit**

Run:

```powershell
python -m pytest tests/test_finops_assistant.py tests/test_finops_api.py -q
```

Commit:

```powershell
git add backend/finops/assistant.py backend/finops/router.py tests/test_finops_assistant.py tests/test_finops_api.py
git commit -m "feat: persist operations ai conversations"
```

## Task 8: Rebuild the Operations Management presentation

**Files:**

- Create: `web/src/FinOpsMetricHelp.jsx`
- Create: `web/src/FinOpsPricingMappingModal.jsx`
- Create: `web/src/AgentModelRoutingModal.jsx`
- Modify: `web/src/FinOpsPortal.jsx`
- Modify: `web/src/FinOpsAssistant.jsx`
- Modify: `web/src/finopsViewModel.js`
- Modify: `web/src/finopsPreload.js`
- Modify: `web/src/ModelRoutingPage.jsx`
- Modify: `web/src/modelRoutingViewModel.js`
- Modify: `web/src/components.jsx`
- Modify: `web/src/api.js`
- Modify: `web/src/styles.css`
- Modify: `web/src/finopsViewModel.test.mjs`
- Modify: `web/src/finopsLayout.test.mjs`
- Modify: `web/src/modelRoutingViewModel.test.mjs`
- Create: `web/src/finopsPricingViewModel.test.mjs`

**Step 1: Write failing view-model and layout tests**

Assert:

- overview has exactly six business KPI cards;
- APIM coverage appears in the trust panel, not KPI cards;
- trend supports Token, request, estimated cost, and P95;
- chart geometry uses a shared zero baseline and proportional values;
- exact values and scale labels are present;
- attribution uses one tabbed panel;
- attention items without an action are omitted;
- states map to `正在同步`, `暂无样本`, `未计价`, `待接入`, `待对账`,
  `数据不完整`, `无权查看`, or `加载失败`;
- every KPI and non-obvious chart exposes help content with definition, source,
  window, and limitation;
- Cost Analysis contains no budget cap/consumption controls;
- an unpriced deployment exposes the small mapping edit action;
- model routing modal supports default, per-Agent primary/fallback, and apply-all;
- Operations AI restores server history after close/reopen.

Run:

```powershell
Set-Location web
node --test src/finopsViewModel.test.mjs src/finopsLayout.test.mjs src/modelRoutingViewModel.test.mjs src/finopsPricingViewModel.test.mjs
```

Expected: failures against the existing repeated-panel and component-local-history UI.

**Step 2: Implement the approved overview**

Use the existing design tokens and thin-line icon set. Build:

- six-card KPI band;
- dominant SVG trend with metric switch, axis, labels, focus/hover marker, and
  proportional zero-baseline geometry;
- compact data-trust panel;
- tabbed department/workspace/Agent/model attribution;
- actionable attention panel;
- compact Operations AI popover.

Keep the navigation shell stable while data preloads. Use stale-while-revalidate:
render a fresh cache immediately, retain a stale cache with a syncing indicator,
deduplicate in-flight requests, and abort responses that no longer match the active
scope.

**Step 3: Implement Cost Analysis, routing, and AI history**

Rename the cost tab to `成本分析`. Render official catalog status, priced coverage,
cost trend, cost attribution, and the unpriced queue. Open the mapping modal from the
small edit icon.

Surface the Agent routing modal in Settings and record the actual selected model in
subsequent statistics. Load Operations AI conversations from the server, preserve
them after close/reopen, offer conversation selection and clear, and keep the popover
local rather than a full-height drawer.

**Step 4: Verify and commit**

Run:

```powershell
Set-Location web
node --test
npm run build
```

Commit:

```powershell
git add web/src/FinOpsMetricHelp.jsx web/src/FinOpsPricingMappingModal.jsx web/src/AgentModelRoutingModal.jsx web/src/FinOpsPortal.jsx web/src/FinOpsAssistant.jsx web/src/finopsViewModel.js web/src/finopsPreload.js web/src/ModelRoutingPage.jsx web/src/modelRoutingViewModel.js web/src/components.jsx web/src/api.js web/src/styles.css web/src/finopsViewModel.test.mjs web/src/finopsLayout.test.mjs web/src/modelRoutingViewModel.test.mjs web/src/finopsPricingViewModel.test.mjs
git commit -m "feat: refine operations management dashboard"
```

## Task 9: Add signed-in desktop and mobile acceptance

**Files:**

- Modify: `web/tests/finopsMockApi.mjs`
- Modify: `web/tests/finops-operations-management.spec.mjs`
- Create: `web/tests/finops-pricing-routing-history.spec.mjs`
- Modify: `web/playwright.config.mjs`

**Step 1: Add deterministic browser tests**

Cover both desktop `1440x1000` and mobile `390x844`:

- the entire navigation shell is present on the first painted screenshot;
- no `Failed to fetch`, generic unavailable text, overflow, clipped tooltip, or
  placeholder icon appears;
- a large Token difference renders a correspondingly large bar difference;
- switching metrics changes values, units, and accessible descriptions;
- hover/focus reveals exact chart data and cache/evidence status;
- unpriced edit opens the official mapping picker;
- model routing edits default and per-Agent values and handles `409` drift;
- Operations AI persists after close/reopen and clear starts a new conversation;
- loading, empty, partial, forbidden, and failed fixtures show their exact states.

**Step 2: Run browser acceptance**

Run:

```powershell
Set-Location web
npx playwright test tests/finops-operations-management.spec.mjs tests/finops-pricing-routing-history.spec.mjs
```

Expected: all scenarios pass with screenshots and no console/page errors.

**Step 3: Commit**

```powershell
git add web/tests/finopsMockApi.mjs web/tests/finops-operations-management.spec.mjs web/tests/finops-pricing-routing-history.spec.mjs web/playwright.config.mjs
git commit -m "test: cover operations management acceptance"
```

## Task 10: Run full regression and deploy zero-traffic candidates

**Files:**

- Create: `docs/validation/2026-07-26-operations-management-evidence-pricing-candidate.md`
- Modify: `docs/monitoring-azure-state.md`

**Step 1: Run local regression**

From the repository root:

```powershell
python -m pytest -q
Set-Location web
node --test
npm run build
npx playwright test
```

Record exact pass/fail totals and artifact paths. A skipped or unavailable acceptance
case remains open; do not summarize it as passed.

**Step 2: Verify the source tree**

Run:

```powershell
git status --short
git diff --check
git log --oneline --decorate -12
```

Exclude all pre-existing untracked workspace fixtures, `.superpowers/brainstorm/`,
`web/.playwright-cli/`, and `web/test-results/` from commits and image build context.

**Step 3: Build immutable candidate images**

Use the clean committed source state:

```powershell
az acr build --registry acrdataforgedev --image dataforge-backend:opsmgmt-evidence-<shortsha> --file backend/Dockerfile .
az acr build --registry acrdataforgedev --image dataforge-web:opsmgmt-evidence-<shortsha> --file web/Dockerfile .
```

Capture image digests. Do not use a floating tag.

**Step 4: Deploy additive SQL and APIM diagnostics**

Apply `backend/sql/finops_schema.sql` once with the existing controlled migration
identity and remove any temporary SQL firewall rule immediately afterward. Deploy the
APIM template to `dfmonapim721` in `rg-dataforge-dev`, then verify:

- the API diagnostic still captures zero body bytes;
- the APIM resource diagnostic targets the intended Log Analytics workspace;
- gateway and LLM log categories are enabled;
- no prompt/completion or authorization capture has been introduced.

**Step 5: Deploy zero-traffic Container Apps revisions**

Create backend and web candidate revisions from the immutable digests without
changing production traffic. Point the web candidate to the backend candidate
ingress for signed-in acceptance. Update `job-dataforge-finops-apim` to the same
backend candidate digest only after its configuration and managed identity are
confirmed unchanged.

Require all candidates and the job execution to reach a terminal healthy/succeeded
state before continuing.

**Step 6: Execute the evidence acceptance matrix**

Use real, authorized workspace calls:

1. successful, 4xx, 5xx, and controlled slow requests;
2. multiple Agents and at least two effective model routes;
3. APIM correlation joined to the DataForge request reference;
4. one mapped request manually calculated from Token and the recorded official
   catalog revision;
5. one intentionally unmapped deployment shown as `未计价`;
6. Redis-eligible repeated analysis showing real miss then hit;
7. member denied an unauthorized workspace and organization-level person detail;
8. Operations AI history visible after a second signed-in session/device context;
9. per-Agent routing change reflected in actual request statistics;
10. desktop/mobile screenshots, refresh, and first-paint navigation checks.

Also verify the APIM job summary reports nonzero observed rows and at least one
reconciliation. A green job with zero evidence does not pass.

**Step 7: Document candidate evidence and commit**

Record revision names, digests, flags, health, traffic, SQL/APIM results, API samples,
manual cost formula, APIM reconciliation counts, test totals, screenshots, open
items, and rollback revisions.

```powershell
git add docs/validation/2026-07-26-operations-management-evidence-pricing-candidate.md docs/monitoring-azure-state.md
git commit -m "docs: record operations management candidate validation"
```

## Task 11: Switch production traffic only after acceptance

**Files:**

- Create: `docs/validation/2026-07-26-operations-management-evidence-pricing-production.md`
- Modify: `docs/monitoring-azure-state.md`

**Step 1: Confirm release controls**

Verify:

- `DF_FINOPS_READ_ENABLED=1`;
- `DF_FINOPS_ACTIONS_ENABLED=0`;
- candidate backend and web remain Healthy;
- previous backend and web revisions are retained as rollback targets;
- all Task 10 gates are passed with current evidence.

Stop if any gate is incomplete.

**Step 2: Switch backend, verify compatibility, then switch web**

Move backend traffic first. Verify health and all existing plus new read APIs through
the signed-in web proxy. Then move web traffic. Do not enable governance execution.

**Step 3: Run production smoke and rollback drill**

Verify first load and hard refresh on desktop/mobile, all Operations Management tabs,
pricing mapping read path, Agent routing read path, AI history, request drill-down,
API health, APIM evidence freshness, and absence of console errors.

Test the documented traffic rollback command against revision names without deleting
either revision. Roll back immediately if health, authorization, evidence integrity,
or frontend acceptance fails.

**Step 4: Record final evidence and commit**

```powershell
git add docs/validation/2026-07-26-operations-management-evidence-pricing-production.md docs/monitoring-azure-state.md
git commit -m "docs: record operations management production rollout"
```

Final acceptance requires an explicit list of:

- tests and exact totals;
- candidate and production revision health/traffic;
- immutable image digests;
- API contract results;
- APIM observed/reconciled counts;
- official price source and manual cost calculation;
- desktop/mobile screenshot paths;
- confirmed `DF_FINOPS_ACTIONS_ENABLED=0`;
- retained rollback revisions;
- any acceptance item still open.
