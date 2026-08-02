# DataForge Operations Demo Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a database-backed, internally traceable operations demo with workspace-scoped budgets, administrator email alerts, adaptive cost and cache visualizations, DataForge-owned ROI, and evidence-specific risk analysis.

**Architecture:** Extend the existing `backend/finops` ledger rather than create a parallel dashboard store. Seed only an allowlisted workspace through an idempotent service, keep scenario provenance in an internal SQL table, and expose the resulting facts through the existing FinOps query APIs. Keep calculations deterministic; Agent output explains stored evidence but never creates metric values during refresh.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, Azure SQL/pyodbc, Azure Communication Services Email, React 18, Vite, Node test runner, Playwright.

## Global Constraints

- Preserve MAF, workspaces, data, conversations, artifacts, and existing Easy Auth.
- Seed only the configured demo workspace; never enumerate and seed other workspaces.
- Do not show a demo-data badge in the customer UI.
- Persist an internal seed batch so initialization is idempotent and reversible.
- Do not expose raw identity, prompt, provider response, cache key, secret, or internal error body.
- Replace customer-visible APIM, Foundry, and Azure Monitor product names with product-language labels.
- Costs remain model-price estimates and must not be presented as cloud billing.
- Automatic refresh is five minutes, pauses while hidden, and never invokes a model.
- Automatic governance actions and external-provider runtime routing remain disabled.
- Use additive SQL only.

---

## File Structure

- `backend/finops/demo_workspace_seed.py`: deterministic scenario construction and idempotent seed orchestration.
- `backend/finops/demo_seed_repository.py`: narrow protocol and in-memory repository for seed ownership metadata.
- `backend/finops/sql_demo_seed.py`: SQL implementation for seed batch ownership and request cleanup/upsert.
- `backend/finops/budget_subjects.py`: workspace-scoped manual budget subjects.
- `backend/finops/member_budget_router.py`: workspace Owner/Admin authorization and direct recipient email contract.
- `backend/finops/member_budget_service.py`: budget subject validation and direct-recipient notification behavior.
- `backend/finops/member_budget_evaluator.py`: threshold dedupe and delivery without directory email lookup.
- `backend/finops/roi_economics.py`: versioned DataForge ROI formulas and unit economics.
- `backend/finops/opportunities.py`: policy-specific evidence references and distinct recommendations.
- `backend/finops/router.py`: additive API projections for cache economics, ROI, and evidence refs.
- `backend/finops/request_detail.py`: business-safe request/conversation evidence projection.
- `backend/sql/finops_schema.sql`: additive seed, budget-subject, and ROI columns/tables.
- `web/src/FinOpsPortal.jsx`: five-minute refresh, adaptive charts, ROI, risk and evidence-specific drawer flow.
- `web/src/finopsViewModel.js`: axis, cache economics, ROI and evidence-selector view models.
- `web/src/MemberBudgetSettingsPage.jsx`: display-name budgets and direct administrator email form.
- `web/src/memberBudgetViewModel.js`: direct-recipient and workspace-subject projections.
- `web/src/api.js`: workspace-scoped budget and evidence-ref query parameters.
- `web/src/styles.css`: accessible adaptive charts, cache cards, ROI form and responsive risk layout.

---

### Task 1: Idempotent Demo Workspace Ledger

**Files:**
- Create: `backend/finops/demo_seed_repository.py`
- Create: `backend/finops/sql_demo_seed.py`
- Create: `backend/finops/demo_workspace_seed.py`
- Modify: `backend/sql/finops_schema.sql`
- Test: `tests/test_finops_demo_workspace_seed.py`
- Test: `tests/test_finops_sql_demo_seed.py`

**Interfaces:**
- Produces: `seed_demo_workspace(repository, seed_repository, *, tenant_ref, workspace_id, allowed_workspace_id, batch="operations-v1", now=None) -> DemoSeedResult`
- Produces: `DemoSeedRepository.replace_batch(*, tenant_ref, workspace_id, batch, events) -> tuple[int, int]`
- Consumes: `FinOpsRequestEvent`, `FinOpsRepository.upsert_events`.

- [ ] **Step 1: Write failing domain tests**

```python
def test_seed_is_workspace_bounded_and_idempotent():
    ledger = InMemoryFinOpsRepository()
    seeds = InMemoryDemoSeedRepository()
    first = seed_demo_workspace(
        ledger, seeds,
        tenant_ref="tenant_demo",
        workspace_id="ws-demo",
        allowed_workspace_id="ws-demo",
        now=datetime(2026, 7, 30, tzinfo=timezone.utc),
    )
    second = seed_demo_workspace(
        ledger, seeds,
        tenant_ref="tenant_demo",
        workspace_id="ws-demo",
        allowed_workspace_id="ws-demo",
        now=datetime(2026, 7, 30, tzinfo=timezone.utc),
    )
    assert first.event_count >= 120
    assert second.created == 0
    assert second.updated == first.event_count
    assert {event.cache.state for event in first.events} >= {"hit", "miss", "bypassed"}
    assert len({event.estimated_cost.amount for event in first.events}) >= 8


def test_seed_rejects_non_allowlisted_workspace():
    with pytest.raises(PermissionError, match="demo workspace"):
        seed_demo_workspace(
            InMemoryFinOpsRepository(),
            InMemoryDemoSeedRepository(),
            tenant_ref="tenant_demo",
            workspace_id="ws-other",
            allowed_workspace_id="ws-demo",
        )
```

- [ ] **Step 2: Run tests and verify the missing-module failure**

Run: `python -m pytest tests/test_finops_demo_workspace_seed.py -q`

Expected: FAIL because `backend.finops.demo_workspace_seed` does not exist.

- [ ] **Step 3: Add additive seed ownership schema**

```sql
IF OBJECT_ID(N'df_finops.demo_seed_event', N'U') IS NULL
BEGIN
    CREATE TABLE df_finops.demo_seed_event (
        tenant_ref NVARCHAR(128) NOT NULL,
        workspace_id NVARCHAR(160) NOT NULL,
        seed_batch NVARCHAR(64) NOT NULL,
        request_ref NVARCHAR(128) NOT NULL,
        updated_at DATETIME2(7) NOT NULL,
        CONSTRAINT PK_finops_demo_seed_event
            PRIMARY KEY (tenant_ref, workspace_id, seed_batch, request_ref)
    );
END;
```

- [ ] **Step 4: Implement deterministic event construction**

```python
@dataclass(frozen=True)
class DemoSeedResult:
    batch: str
    event_count: int
    created: int
    updated: int
    events: tuple[FinOpsRequestEvent, ...]


def seed_demo_workspace(
    repository,
    seed_repository,
    *,
    tenant_ref: str,
    workspace_id: str,
    allowed_workspace_id: str,
    batch: str = "operations-v1",
    now: datetime | None = None,
) -> DemoSeedResult:
    if workspace_id != allowed_workspace_id:
        raise PermissionError("demo workspace is not allowlisted")
    events = tuple(_scenario_events(tenant_ref, workspace_id, now or utc_now()))
    created, updated = seed_repository.replace_batch(
        tenant_ref=tenant_ref,
        workspace_id=workspace_id,
        batch=batch,
        events=events,
    )
    repository.upsert_events(events)
    return DemoSeedResult(batch, len(events), created, updated, events)
```

Construct stable request refs from `sha256(f"{batch}:{workspace_id}:{day}:{index}")`, and use fixed scenario arrays for six Agents, four models, four actor refs, latency, status, tokens and cache state. Include at least one same-analysis miss followed by hit with the hit carrying `avoided_tokens`.

- [ ] **Step 5: Implement SQL replacement semantics**

Within one transaction, lock the existing `(tenant_ref, workspace_id, seed_batch)` rows, compare request refs, upsert the new batch ownership rows, and delete only ownership rows removed from the new version. Never delete a request event unless it is owned by this exact batch and no other batch references it.

- [ ] **Step 6: Run focused tests**

Run: `python -m pytest tests/test_finops_demo_workspace_seed.py tests/test_finops_sql_demo_seed.py -q`

Expected: all tests PASS.

- [ ] **Step 7: Commit**

```powershell
git add backend/finops/demo_seed_repository.py backend/finops/sql_demo_seed.py backend/finops/demo_workspace_seed.py backend/sql/finops_schema.sql tests/test_finops_demo_workspace_seed.py tests/test_finops_sql_demo_seed.py
git commit -m "feat(finops): add bounded operations demo ledger"
```

---

### Task 2: Workspace Display-Name Budgets and Direct Administrator Email

**Files:**
- Create: `backend/finops/budget_subjects.py`
- Modify: `backend/finops/member_budget_router.py`
- Modify: `backend/finops/member_budget_service.py`
- Modify: `backend/finops/member_budget_evaluator.py`
- Modify: `backend/finops/member_budgets.py`
- Modify: `backend/finops/sql_member_budgets.py`
- Modify: `backend/sql/finops_schema.sql`
- Modify: `web/src/api.js`
- Modify: `web/src/MemberBudgetSettingsPage.jsx`
- Modify: `web/src/memberBudgetViewModel.js`
- Test: `tests/test_finops_member_budget_router.py`
- Test: `tests/test_finops_member_budget_service.py`
- Test: `tests/test_finops_member_budget_evaluator.py`
- Test: `web/src/memberBudgetViewModel.test.mjs`
- Test: `web/tests/finops-portal-acceptance.spec.mjs`

**Interfaces:**
- Produces: `BudgetSubject(subject_ref, workspace_id, display_name, department_label, primary_model, enabled)`
- Changes notification write body to `{recipient_email, sender_display_name, subject_template, body_template, enabled, base_revision}`.
- Requires every budget request to include `workspace_id`.

- [ ] **Step 1: Write failing authorization and direct-email tests**

```python
def test_workspace_admin_can_manage_budget_without_tenant_app_role(client, admin_headers):
    response = client.get(
        "/api/finops/member-budgets?workspace_id=ws-demo",
        headers=admin_headers,
    )
    assert response.status_code == 200


def test_member_cannot_manage_workspace_budget(client, member_headers):
    response = client.get(
        "/api/finops/member-budgets?workspace_id=ws-demo",
        headers=member_headers,
    )
    assert response.status_code == 403


def test_notification_accepts_direct_recipient_email(client, admin_headers):
    response = client.put(
        "/api/finops/notification-settings?workspace_id=ws-demo",
        headers=admin_headers,
        json={
            "recipient_email": "admin@example.com",
            "sender_display_name": "DataForge",
            "subject_template": "{member_name} 预算提醒",
            "body_template": "{member_name} 已使用 {usage_pct}%",
            "enabled": False,
            "base_revision": 0,
        },
    )
    assert response.status_code == 200
    assert response.json()["item"]["recipient_email"] == "admin@example.com"
```

- [ ] **Step 2: Verify focused tests fail**

Run: `python -m pytest tests/test_finops_member_budget_router.py tests/test_finops_member_budget_service.py -q`

Expected: FAIL because workspace-scoped authorization and direct email are not implemented.

- [ ] **Step 3: Add budget subject schema and models**

```sql
IF OBJECT_ID(N'df_finops.budget_subject', N'U') IS NULL
BEGIN
    CREATE TABLE df_finops.budget_subject (
        tenant_ref NVARCHAR(128) NOT NULL,
        workspace_id NVARCHAR(160) NOT NULL,
        subject_ref NVARCHAR(128) NOT NULL,
        display_name NVARCHAR(120) NOT NULL,
        department_label NVARCHAR(120) NULL,
        primary_model NVARCHAR(160) NULL,
        enabled BIT NOT NULL,
        revision INT NOT NULL,
        updated_at DATETIME2(7) NOT NULL,
        CONSTRAINT PK_finops_budget_subject
            PRIMARY KEY (tenant_ref, workspace_id, subject_ref)
    );
END;
```

Generate `subject_ref` using the existing HMAC secret and normalized `(workspace_id, display_name)`; never accept a client-supplied identity ID.

- [ ] **Step 4: Replace tenant app-role gate with workspace role authorization**

```python
def _context(request: Request) -> tuple[str, str, str, Mapping[str, Any]]:
    if not _enabled():
        raise HTTPException(status_code=404, detail="Not found")
    actor = actor_from_request(request, fallback=False)
    if not is_trusted_tenant_identity(actor):
        raise HTTPException(status_code=403, detail="Trusted tenant identity required")
    workspace_id = str(request.query_params.get("workspace_id") or "").strip()
    if not workspace_id:
        raise HTTPException(status_code=422, detail="workspace_id is required")
    role = active_workspace_role(workspace_id, actor)
    if role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Workspace budget administrator required")
    secret = str(os.environ.get("DF_FINOPS_HMAC_SECRET") or "").strip()
    tenant_id = str(actor.get("tenant_id") or "").strip()
    actor_id = str(actor.get("actor_id") or "").strip()
    if not secret or not tenant_id or not actor_id:
        raise HTTPException(status_code=503, detail="FinOps scope is unavailable")
    return (
        canonical_tenant_ref(tenant_id, secret=secret),
        canonical_actor_ref(tenant_id, actor_id, secret=secret),
        workspace_id,
        actor,
    )
```

All list, create, update, disable, notification, test-email and alert handlers must use the returned single workspace ID.

- [ ] **Step 5: Store direct recipient email and preserve safe output**

Keep the existing non-null internal `recipient_actor_ref` column by storing the authenticated administrator's opaque `actor_ref`. Validate `recipient_email` with Pydantic `EmailStr` or the repository's existing bounded email validator. Remove `active_admins` lookup from notification save and send; evaluator uses the persisted email after configuration revision checks.

Automatic enablement is rejected unless the setting has a prior successful test-send marker:

```python
if payload.get("enabled") is True and not current.test_email_succeeded_at:
    raise ValueError("test_email_required")
```

- [ ] **Step 6: Extend the bounded seed with budget subjects**

After the subject repository exists, extend `seed_demo_workspace` to upsert four display-name subjects and three budgets in the allowlisted workspace. Use stable opaque subject refs, including one `$200` budget with `thresholds_pct=[80, 95]` and estimated spend near `$190`. Do not create or enable a recipient email setting during seed execution.

- [ ] **Step 7: Update the frontend contract**

Pass `workspaceId` from `SettingsCenter` to `MemberBudgetSettingsPage`, append it to all budget API calls, replace the member-only dropdown with server-provided display-name subjects, and replace administrator selection with:

```jsx
<label>
  管理员收件邮箱
  <input
    type="email"
    value={recipientEmail}
    onChange={(event) => setRecipientEmail(event.target.value)}
    autoComplete="email"
    required
  />
</label>
```

Remove customer copy containing “Entra 成员” or “租户 FinOps 管理员角色”.

- [ ] **Step 8: Run backend, Node and Playwright budget tests**

Run:

```powershell
python -m pytest tests/test_finops_member_budget_router.py tests/test_finops_member_budget_service.py tests/test_finops_member_budget_evaluator.py -q
node --test src/memberBudgetViewModel.test.mjs
npx playwright test tests/finops-portal-acceptance.spec.mjs --grep "budget|email"
```

Expected: all selected tests PASS.

- [ ] **Step 9: Commit**

```powershell
git add backend/finops/budget_subjects.py backend/finops/member_budget_router.py backend/finops/member_budget_service.py backend/finops/member_budget_evaluator.py backend/finops/member_budgets.py backend/finops/sql_member_budgets.py backend/sql/finops_schema.sql web/src/api.js web/src/MemberBudgetSettingsPage.jsx web/src/memberBudgetViewModel.js tests/test_finops_member_budget_router.py tests/test_finops_member_budget_service.py tests/test_finops_member_budget_evaluator.py web/src/memberBudgetViewModel.test.mjs web/tests/finops-portal-acceptance.spec.mjs
git commit -m "feat(finops): add workspace demo budgets and direct email"
```

---

### Task 3: Cache Economics and Adaptive Chart Scale

**Files:**
- Modify: `backend/finops/query.py`
- Modify: `backend/finops/rollups.py`
- Modify: `backend/finops/router.py`
- Modify: `web/src/finopsViewModel.js`
- Modify: `web/src/FinOpsPortal.jsx`
- Modify: `web/src/styles.css`
- Test: `tests/test_finops_query.py`
- Test: `tests/test_finops_router.py`
- Test: `web/src/finopsViewModel.test.mjs`
- Test: `web/tests/finops-portal-acceptance.spec.mjs`

**Interfaces:**
- Produces trend cache object: `{hit, miss, bypassed, unavailable, eligible_requests, avoided_tokens, estimated_savings}`.
- Produces: `niceFinOpsAxis(values, tickCount=4) -> {max, ticks}`.

- [ ] **Step 1: Write failing cache and axis tests**

```javascript
test("nice axis preserves zero and separates unequal cost values", () => {
  const axis = niceFinOpsAxis([0.0004, 0.0021, 0.0097], 4);
  assert.equal(axis.ticks.at(-1), 0);
  assert.ok(axis.max >= 0.0097);
  assert.notEqual(
    finopsBarPercent(0.0021, axis.max),
    finopsBarPercent(0.0097, axis.max),
  );
});
```

```python
def test_trends_include_cache_economics():
    payload = query_service.trends(scope, bucket="day")
    cache = payload["items"][0]["cache"]
    assert set(cache) >= {
        "hit", "miss", "bypassed", "eligible_requests",
        "avoided_tokens", "estimated_savings",
    }
```

- [ ] **Step 2: Verify tests fail**

Run: `python -m pytest tests/test_finops_query.py tests/test_finops_router.py -q && node --test src/finopsViewModel.test.mjs`

Expected: FAIL on missing cache economics and axis helpers.

- [ ] **Step 3: Add deterministic cache economics**

Aggregate only events with explicit cache evidence. `avoided_tokens` is summed from hit events. `estimated_savings` is calculated only when the event has a reliable official price mapping; otherwise return `null` and `data_status="partial"`.

- [ ] **Step 4: Implement a zero-based friendly axis**

```javascript
export function niceFinOpsAxis(values = [], tickCount = 4) {
  const maximum = Math.max(0, ...values.filter(Number.isFinite));
  if (maximum === 0) return { max: 1, ticks: [1, 0.67, 0.33, 0] };
  const rough = maximum / Math.max(1, tickCount - 1);
  const power = 10 ** Math.floor(Math.log10(rough));
  const fraction = rough / power;
  const nice = fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 5 ? 5 : 10;
  const step = nice * power;
  const max = Math.ceil(maximum / step) * step;
  return {
    max,
    ticks: Array.from({ length: tickCount }, (_, index) =>
      max - (max / (tickCount - 1)) * index),
  };
}

export function finopsBarPercent(value, axisMax) {
  return Number.isFinite(value) && value > 0 && axisMax > 0
    ? Math.min(100, value / axisMax * 100)
    : 0;
}
```

Remove the visual `Math.max(2, proportionalPercent)` floor from vertical trend bars. A positive but tiny value may use a one-pixel accessibility marker, but the filled height must remain mathematically proportional.

- [ ] **Step 5: Add cache cards and hover detail**

Add cards for hit rate, avoided Tokens and estimated savings. Trend tooltips list hit/miss/bypassed, and Agent/model breakdowns include cache hit rate.

- [ ] **Step 6: Run focused tests**

Run:

```powershell
python -m pytest tests/test_finops_query.py tests/test_finops_router.py -q
node --test src/finopsViewModel.test.mjs
npx playwright test tests/finops-portal-acceptance.spec.mjs --grep "cost|cache|axis"
```

Expected: all selected tests PASS and Playwright asserts unequal bar bounding-box heights.

- [ ] **Step 7: Commit**

```powershell
git add backend/finops/query.py backend/finops/rollups.py backend/finops/router.py web/src/finopsViewModel.js web/src/FinOpsPortal.jsx web/src/styles.css tests/test_finops_query.py tests/test_finops_router.py web/src/finopsViewModel.test.mjs web/tests/finops-portal-acceptance.spec.mjs
git commit -m "feat(finops): add cache economics and adaptive charts"
```

---

### Task 4: DataForge-Owned ROI Scenarios

**Files:**
- Modify: `backend/finops/roi_economics.py`
- Modify: `backend/control_plane.py`
- Modify: `backend/roi_scenario_store.py`
- Modify: `web/src/FinOpsPortal.jsx`
- Modify: `web/src/finopsViewModel.js`
- Modify: `web/src/api.js`
- Modify: `web/src/styles.css`
- Test: `tests/test_finops_roi_economics.py`
- Test: `tests/test_control_plane_roi.py`
- Test: `web/src/finopsViewModel.test.mjs`
- Test: `web/tests/finops-portal-acceptance.spec.mjs`

**Interfaces:**
- Consumes ROI inputs: `hours_saved`, `hourly_value`, `avoided_loss_or_revenue`, `implementation_cost`, `monthly_fixed_cost`, `evaluation_months`.
- Produces: `monthly_benefit`, `monthly_total_cost`, `monthly_net_benefit`, `roi_ratio`, `payback_months`, `formula_revision`.

- [ ] **Step 1: Write failing formula tests**

```python
def test_local_roi_formula_is_versioned_and_reproducible():
    result = calculate_roi(
        hours_saved=40,
        hourly_value=50,
        avoided_loss_or_revenue=1000,
        implementation_cost=6000,
        monthly_fixed_cost=200,
        model_cost=100,
        evaluation_months=12,
    )
    assert result.monthly_benefit == 3000
    assert result.monthly_total_cost == 800
    assert result.monthly_net_benefit == 2200
    assert result.roi_ratio == pytest.approx(2.75)
    assert result.payback_months == pytest.approx(6000 / 2700)
    assert result.formula_revision == "dataforge-roi-v1"
```

- [ ] **Step 2: Verify ROI tests fail**

Run: `python -m pytest tests/test_finops_roi_economics.py tests/test_control_plane_roi.py -q`

Expected: FAIL on missing calculation inputs and formula revision.

- [ ] **Step 3: Implement the formula**

Use:

```python
monthly_benefit = hours_saved * hourly_value + avoided_loss_or_revenue
implementation_amortization = implementation_cost / evaluation_months
monthly_total_cost = implementation_amortization + monthly_fixed_cost + model_cost
monthly_net_benefit = monthly_benefit - monthly_total_cost
roi_ratio = monthly_net_benefit / monthly_total_cost if monthly_total_cost > 0 else None
payback_months = (
    implementation_cost / (monthly_benefit - monthly_fixed_cost - model_cost)
    if monthly_benefit > monthly_fixed_cost + model_cost
    else None
)
```

Persist all inputs and `formula_revision` with the scenario revision. Keep estimated and verified outcomes separate.

- [ ] **Step 4: Extend the bounded seed with ROI evidence**

Add one stable ROI scenario and at least two business outcome events for the allowlisted workspace. One outcome remains observed and one is reviewer-verified so the UI can demonstrate both “情景测算” and “可复核 ROI” without upgrading an estimate to a verified result.

- [ ] **Step 5: Add ROI parameter UI**

Add an “调整测算参数” action to the ROI page. Use a compact modal with six numeric fields, formula explanation, save revision and conflict reload. Display monthly benefit, cost, net benefit, ROI and payback period, plus a clear “情景测算 / 可复核结果” state.

Remove Foundry or Azure ROI copy from customer UI.

- [ ] **Step 6: Run ROI tests**

Run:

```powershell
python -m pytest tests/test_finops_roi_economics.py tests/test_control_plane_roi.py -q
node --test src/finopsViewModel.test.mjs
npx playwright test tests/finops-portal-acceptance.spec.mjs --grep "ROI"
```

Expected: all selected tests PASS.

- [ ] **Step 7: Commit**

```powershell
git add backend/finops/roi_economics.py backend/control_plane.py backend/roi_scenario_store.py web/src/FinOpsPortal.jsx web/src/finopsViewModel.js web/src/api.js web/src/styles.css tests/test_finops_roi_economics.py tests/test_control_plane_roi.py web/src/finopsViewModel.test.mjs web/tests/finops-portal-acceptance.spec.mjs
git commit -m "feat(finops): add DataForge ROI scenarios"
```

---

### Task 5: Risk-Specific Evidence and Agent Explanation

**Files:**
- Modify: `backend/finops/anomalies.py`
- Modify: `backend/finops/opportunities.py`
- Modify: `backend/finops/agent_inputs.py`
- Modify: `backend/finops/router.py`
- Modify: `backend/finops/request_detail.py`
- Modify: `web/src/api.js`
- Modify: `web/src/FinOpsPortal.jsx`
- Modify: `web/src/finopsViewModel.js`
- Modify: `web/src/evidenceDrawer.test.mjs`
- Test: `tests/test_finops_opportunities.py`
- Test: `tests/test_finops_request_detail.py`
- Test: `web/tests/finops-evidence-drawer.spec.mjs`

**Interfaces:**
- Produces on anomaly/opportunity/finding: `evidence_refs: list[str]`.
- Changes `openEvidence(reason)` to `openEvidence({reason, evidenceRefs, policyType})`.
- Adds bounded request query parameter `request_ref` for exact authorized lookup.

- [ ] **Step 1: Write failing distinct-evidence tests**

```python
def test_cache_opportunity_uses_cache_miss_evidence():
    rows = build_opportunity_queue(
        anomalies=[{
            "anomaly_id": "a1",
            "policy_type": "cache_hit_rate",
            "severity": "warning",
            "sample_count": 30,
            "evidence_state": "observed",
            "evidence_refs": ["req_miss", "req_hit"],
        }],
        recommendations=[],
        priced_cost=1.0,
        priced_coverage_pct=100,
    )
    assert rows[0]["evidence_refs"] == ["req_miss", "req_hit"]
```

```javascript
test("evidence selection prefers the clicked finding reference", () => {
  assert.equal(
    evidenceRequestRef({ evidenceRefs: ["req_slow"], fallbackItems: [{ request_ref: "req_latest" }] }),
    "req_slow",
  );
});
```

- [ ] **Step 2: Verify tests fail**

Run: `python -m pytest tests/test_finops_opportunities.py tests/test_finops_request_detail.py -q && node --test src/evidenceDrawer.test.mjs`

Expected: FAIL because evidence refs are not projected or selected.

- [ ] **Step 3: Attach relevant references**

When evaluating each policy, retain up to five authorized request refs selected by:

- `p95_latency`: highest latency;
- `error_rate`: matching failed requests;
- `cache_hit_rate`: eligible miss or bypassed requests;
- `token_spike`: largest token totals;
- `unpriced_requests`: unpriced requests;
- `daily_cost_budget`: largest priced cost contributors;
- `apim_coverage`: unmanaged or unknown gateway coverage.

The API may return only opaque refs, never raw correlation or identity fields.

- [ ] **Step 4: Fix drawer selection**

```javascript
const openEvidence = useCallback(async ({ reason, evidenceRefs = [], policyType = "" }) => {
  const directRef = evidenceRefs.find((value) => typeof value === "string" && value);
  if (directRef) {
    const detail = await loadFinOpsRequest(directRef, query, { signal: controller.signal });
    // set drawer state
    return;
  }
  const list = await loadFinOpsRequests(
    { ...query, policy_type: policyType, limit: 20 },
    { signal: controller.signal },
  );
  const selected = list.items?.[0];
  // load selected.request_ref
}, [permissions, query]);
```

Use the first reverse-chronological fallback item, not `items[items.length - 1]`.

- [ ] **Step 5: Render distinct business context**

Show safe business-request and final-visible-answer summaries when the request is linked to a conversation. Rename technical labels to “网关关联”, “运行追踪” and “云端监控”.

- [ ] **Step 6: Add Agent explanation cards to risk page**

Pass bootstrap `insights` into `RiskPage`. Render FinOps and ROI analysis cards with finding-specific “查看证据” actions. Never trigger analysis from the five-minute refresh.

- [ ] **Step 7: Run focused tests**

Run:

```powershell
python -m pytest tests/test_finops_opportunities.py tests/test_finops_request_detail.py -q
node --test src/evidenceDrawer.test.mjs
npx playwright test tests/finops-evidence-drawer.spec.mjs
```

Expected: distinct risk buttons open distinct request titles and business summaries.

- [ ] **Step 8: Commit**

```powershell
git add backend/finops/anomalies.py backend/finops/opportunities.py backend/finops/agent_inputs.py backend/finops/router.py backend/finops/request_detail.py web/src/api.js web/src/FinOpsPortal.jsx web/src/finopsViewModel.js web/src/evidenceDrawer.test.mjs tests/test_finops_opportunities.py tests/test_finops_request_detail.py web/tests/finops-evidence-drawer.spec.mjs
git commit -m "fix(finops): link risks to distinct request evidence"
```

---

### Task 6: Product Copy and Five-Minute Refresh

**Files:**
- Modify: `web/src/FinOpsPortal.jsx`
- Modify: `web/src/finopsViewModel.js`
- Modify: `web/src/finopsInteraction.js`
- Modify: `web/src/monitorDashboardViewModel.js`
- Modify: `web/src/MonitorPage.jsx`
- Modify: `web/src/MemberBudgetSettingsPage.jsx`
- Modify: `web/src/components.jsx`
- Modify: `web/src/styles.css`
- Test: `web/src/finopsViewModel.test.mjs`
- Test: `web/src/monitorDashboardViewModel.test.mjs`
- Test: `web/tests/finops-portal-acceptance.spec.mjs`

**Interfaces:**
- Produces `FINOPS_REFRESH_MS = 300_000`.
- Customer-visible copies contain no `APIM`, `Foundry`, `Azure Monitor`, or `Entra 成员`.

- [ ] **Step 1: Write failing copy and refresh tests**

```javascript
test("customer labels hide infrastructure product names", () => {
  const source = readFileSync(new URL("../src/FinOpsPortal.jsx", import.meta.url), "utf8");
  for (const forbidden of ["APIM", "Foundry Trace", "Azure Monitor"]) {
    assert.equal(source.includes(forbidden), false);
  }
});

test("operations refresh interval is five minutes", () => {
  assert.equal(FINOPS_REFRESH_MS, 300_000);
});
```

- [ ] **Step 2: Verify tests fail**

Run: `node --test src/finopsViewModel.test.mjs src/monitorDashboardViewModel.test.mjs`

Expected: FAIL on old customer copy and 60-second interval.

- [ ] **Step 3: Centralize customer labels**

Use:

```javascript
export const CUSTOMER_INFRA_LABELS = Object.freeze({
  reconciliation: "请求对账",
  gatewayCoverage: "网关治理覆盖率",
  gateway: "统一网关",
  gatewayCorrelation: "网关关联",
  trace: "运行追踪",
  monitor: "云端监控",
});
```

Update customer-facing React and view-model strings while keeping backend field names unchanged.

- [ ] **Step 4: Implement visibility-aware refresh**

```javascript
export const FINOPS_REFRESH_MS = 300_000;

useEffect(() => {
  let lastRefreshAt = Date.now();
  const run = () => {
    if (document.hidden) return;
    lastRefreshAt = Date.now();
    refresh();
  };
  const timer = window.setInterval(run, FINOPS_REFRESH_MS);
  const onVisibilityChange = () => {
    if (!document.hidden && Date.now() - lastRefreshAt >= FINOPS_REFRESH_MS) run();
  };
  document.addEventListener("visibilitychange", onVisibilityChange);
  return () => {
    window.clearInterval(timer);
    document.removeEventListener("visibilitychange", onVisibilityChange);
  };
}, [refresh]);
```

Keep cached data during background update and preserve the manual refresh button.

- [ ] **Step 5: Run Node and Playwright tests**

Run:

```powershell
node --test
npx playwright test tests/finops-portal-acceptance.spec.mjs tests/finops-evidence-drawer.spec.mjs
```

Expected: all tests PASS, including fake-clock refresh assertions.

- [ ] **Step 6: Commit**

```powershell
git add web/src/FinOpsPortal.jsx web/src/finopsViewModel.js web/src/finopsInteraction.js web/src/monitorDashboardViewModel.js web/src/MonitorPage.jsx web/src/MemberBudgetSettingsPage.jsx web/src/components.jsx web/src/styles.css web/src/finopsViewModel.test.mjs web/src/monitorDashboardViewModel.test.mjs web/tests/finops-portal-acceptance.spec.mjs
git commit -m "feat(web): productize operations labels and refresh"
```

---

### Task 7: Full Verification, Seed Acceptance, GitHub and Candidate Deployment

**Files:**
- Create: `docs/validation/2026-07-30-operations-demo-candidate.md`
- Modify: `backend/.env.example`
- Modify: deployment configuration only through existing scripts and Container Apps revision settings.

**Interfaces:**
- Adds `DF_FINOPS_DEMO_WORKSPACE_ID`, `DF_FINOPS_DEMO_SEED_ENABLED`, `DF_FINOPS_MEMBER_BUDGETS_ENABLED`, `DF_FINOPS_EMAIL_CONFIGURATION_ENABLED`, `DF_FINOPS_EMAIL_ALERTS_ENABLED`.

- [ ] **Step 1: Run the complete local suite**

Run:

```powershell
python -m pytest -q
Set-Location web
node --test
npm run build
npx playwright test
Set-Location ..
git diff --check
```

Expected: Python, Node, Vite and Playwright all exit 0; `git diff --check` is empty.

- [ ] **Step 2: Visually inspect desktop and mobile screenshots**

Capture and inspect:

- operations overview;
- cost analysis with unequal bars and adaptive y-axis;
- cache tooltip;
- ROI scenario;
- risk page filled with distinct cards;
- two different evidence drawers;
- budget and direct-email settings;
- mobile operations and budget layouts.

Reject the candidate for clipping, fixed-height equal bars, overlapping drawers, customer-facing infrastructure names, unreadable axes, or missing focus states.

- [ ] **Step 3: Build immutable backend and web images**

Use the repository's existing ACR build scripts. Record image digests without copying credentials or tokens into the validation document.

- [ ] **Step 4: Create zero-traffic Container Apps revisions**

Set:

```text
DF_FINOPS_DEMO_WORKSPACE_ID=$DemoWorkspaceId
DF_FINOPS_DEMO_SEED_ENABLED=1
DF_FINOPS_MEMBER_BUDGETS_ENABLED=1
DF_FINOPS_EMAIL_CONFIGURATION_ENABLED=1
DF_FINOPS_EMAIL_ALERTS_ENABLED=0
DF_FINOPS_ACTIONS_ENABLED=0
DF_EXTERNAL_PROVIDER_ROUTING_ENABLED=0
```

Do not enable automatic email alerts before a successful candidate test email.

- [ ] **Step 5: Execute additive migration twice**

Expected: both runs succeed; the runtime managed identity still lacks general DDL permission.

- [ ] **Step 6: Seed only the approved demo workspace**

Run the bounded initializer once and again. Expected: the first run creates the scenario facts; the second reports zero duplicates and only idempotent updates.

- [ ] **Step 7: Candidate functional acceptance**

Verify:

- authenticated bootstrap and all four operations tabs return 200;
- budget save and revision conflict handling;
- administrator recipient save and ACS test email;
- after the test email succeeds, enable candidate-only automatic alerts, evaluate the `$190/$200` member, verify one 95% alert is sent, and verify a repeated evaluator run does not send a duplicate;
- `miss → hit` cache evidence and savings;
- unequal chart heights for unequal data;
- two risks open different request evidence;
- ROI parameter save and reproducible calculation;
- five-minute refresh does not trigger Agent/model calls;
- candidate logs contain no traceback, secret, raw email in ordinary operations payloads, or schema error.

- [ ] **Step 8: Push to the new organization repository and open a PR**

```powershell
git push -u team codex/operations-demo-readiness
gh pr create --repo Myr-Team/dataforge-agent --base main --head codex/operations-demo-readiness --title "feat(finops): complete operations demo experience" --body-file docs/validation/2026-07-30-operations-demo-candidate.md
```

- [ ] **Step 9: Record candidate evidence**

Document test counts, screenshots, migration runs, image digests, candidate revisions, health, flags, seed counts, email test state, logs, rollback revisions and remaining gates in `docs/validation/2026-07-30-operations-demo-candidate.md`.

- [ ] **Step 10: Request production traffic approval**

Do not switch traffic in the same checkpoint. Present the candidate evidence and exact backend/web rollback revisions to the user for approval.
