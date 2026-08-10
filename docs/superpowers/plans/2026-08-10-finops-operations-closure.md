# FinOps Operations Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the DeepSeek routing, member identity, email delivery, trend chart, ROI storytelling, and refresh-layout gaps without weakening tenant or workspace authorization.

**Architecture:** Keep provider credentials write-only and preserve the existing explicit provider-governance gate. Add a model-routing remediation action that calls the audited governance API, project only verified enterprise identity fields, distinguish email acceptance from recipient delivery, and improve the existing server-backed FinOps decision views rather than introducing static client data. Azure configuration remains an independently verified deployment step.

**Tech Stack:** FastAPI, Pydantic, Azure Communication Services Email, Azure Monitor Logs, Azure SQL, React, Vite, Node test runner, Playwright, Azure Container Apps.

## Global Constraints

- Provider credentials remain write-only and never return to the browser.
- Do not remove or bypass Easy Auth, tenant scoping, workspace RBAC, `DataForge.FinOpsAdmin`, audit persistence, or provider governance.
- `DF_FINOPS_ACTIONS_ENABLED` remains `0`; this change does not enable production remediation executors.
- A successful ACS send operation means accepted for delivery, not delivered to the recipient.
- Only verified same-tenant identities whose domains are explicitly trusted may expose name and email; all other identities remain pseudonymous.
- ROI scenario values remain estimated until independently verified business outcomes exist.
- Frontend charts use server values and truthful unavailable/partial/estimated states; no invented fallback numbers.
- Cloud changes are additive and verified in a zero-traffic candidate before production traffic changes.

---

### Task 1: DeepSeek routing remediation

**Files:**
- Modify: `web/src/ModelRoutingPage.jsx`
- Modify: `web/src/modelRoutingViewModel.js`
- Modify: `web/src/styles.css`
- Test: `web/src/modelRoutingViewModel.test.mjs`
- Test: `web/tests/finops-pricing-routing-remediation.spec.mjs`

**Interfaces:**
- Consumes: `loadModelProviders()`, `governModelProvider(providerId, baseRevision)`, and dynamic routes returned by `loadWorkspaceModelRouting(workspaceId)`.
- Produces: `governanceRemediations` in `modelRoutingViewModel()` and an audited “纳入模型路由” action on the model assignment page.

- [ ] **Step 1: Write failing view-model and browser tests**

```js
assert.deepEqual(view.governanceRemediations, [{
  providerId: "provider-deepseek",
  providerLabel: "DeepSeek 原厂",
  routeCount: 2,
  reason: "governance_required",
}]);
```

The Playwright test must open model settings with connected, priced, pending DeepSeek routes, click `纳入 DeepSeek 模型路由`, observe the audited provider governance request, reload routing, and select DeepSeek for one Agent.

- [ ] **Step 2: Run the tests and verify the new assertions fail**

Run: `node --test src/modelRoutingViewModel.test.mjs`

Expected: FAIL because `governanceRemediations` does not exist.

- [ ] **Step 3: Add the bounded remediation projection and action**

```js
const governanceRemediations = Object.values(routes
  .filter((route) => route.providerType === "deepseek" && route.unavailableReason === "governance_required")
  .reduce((items, route) => {
    const current = items[route.providerId] || { providerId: route.providerId, providerLabel: route.providerLabel, routeCount: 0, reason: route.unavailableReason };
    current.routeCount += 1;
    items[route.providerId] = current;
    return items;
  }, {}));
```

`ModelRoutingPage` must fetch the matching provider revision, call the existing audited governance endpoint, reload model routing, and show conflict-safe or permission-safe copy. It must not automatically govern immediately after secret creation.

- [ ] **Step 4: Suppress stale provider errors after a connected payload**

Update `providerConnectionsViewModel` so `safeErrorLabel` is empty when `connectionState === "connected"`; retain safe errors for degraded/invalid states.

- [ ] **Step 5: Run focused tests**

Run: `node --test src/providerConnectionsViewModel.test.mjs src/modelRoutingViewModel.test.mjs`

Expected: PASS.

---

### Task 2: Honest email acceptance and delivery evidence

**Files:**
- Modify: `backend/finops/acs_email.py`
- Create: `backend/finops/email_delivery_monitor.py`
- Modify: `backend/finops/member_budgets.py`
- Modify: `backend/finops/member_budget_service.py`
- Modify: `backend/finops/member_budget_router.py`
- Modify: `backend/finops/member_budget_repository.py`
- Modify: `backend/finops/sql_member_budgets.py`
- Modify: `backend/sql/finops_schema.sql`
- Modify: `web/src/memberBudgetViewModel.js`
- Modify: `web/src/MemberBudgetSettingsPage.jsx`
- Test: `tests/test_finops_acs_email.py`
- Create: `tests/test_finops_email_delivery_monitor.py`
- Modify: `tests/test_finops_member_budget_api.py`
- Modify: `web/src/memberBudgetViewModel.test.mjs`
- Modify: `web/tests/finops-portal-acceptance.spec.mjs`

**Interfaces:**
- Consumes: ACS send-operation result `id`, Azure Monitor `ACSEmailStatusUpdateOperational`, configured Log Analytics workspace, and the existing notification setting revision.
- Produces: public `delivery_state` in `{not_tested, accepted, delivered, failed, bounced, quarantined, filtered_spam, suppressed, unavailable}` without exposing the provider message ID.

- [ ] **Step 1: Write failing sender and monitor tests**

```python
assert sender.send(message, operation_id).state == "accepted"
assert sender.send(message, operation_id).provider_message_id == "message-guid"
assert monitor.query("message-guid").state == "delivered"
```

Tests must reject malformed message identifiers and must never return recipient addresses or raw provider errors.

- [ ] **Step 2: Run focused backend tests and verify failure**

Run: `python -m pytest -q tests/test_finops_acs_email.py tests/test_finops_email_delivery_monitor.py tests/test_finops_member_budget_api.py`

Expected: FAIL because only `sent` is currently supported and delivery evidence is not stored.

- [ ] **Step 3: Implement accepted-versus-delivered state**

```python
@dataclass(frozen=True)
class EmailDeliveryResult:
    state: str
    accepted_at: datetime | None
    safe_error_category: str | None
    provider_message_id: str | None = field(default=None, repr=False)
```

The sender returns `accepted` after ACS operation success. The monitor runs a bounded KQL query for one stored internal message ID and maps only allowlisted recipient-level terminal states.

- [ ] **Step 4: Add internal delivery fields with an additive SQL migration**

Add nullable `last_test_message_id`, `last_test_accepted_at`, `last_test_delivery_state`, and `last_test_delivery_checked_at` columns. Keep the message ID internal: repository public serialization must remove it.

- [ ] **Step 5: Refresh delivery state when notification settings are read**

`MemberBudgetService.refresh_notification_delivery()` queries only when a stored accepted test exists and uses a short TTL. It sets `test_email_succeeded_at` only for `delivered`; failed terminal states do not unlock automatic notification enablement.

- [ ] **Step 6: Update UI language and status behavior**

`safeTestEmailResult({state:"accepted"})` returns `已提交邮件服务，等待投递确认`. The settings page shows a compact status sequence “已受理 → 已投递” and retains exact safe terminal failure labels.

- [ ] **Step 7: Run focused backend and frontend tests**

Run: `python -m pytest -q tests/test_finops_acs_email.py tests/test_finops_email_delivery_monitor.py tests/test_finops_member_budget_api.py tests/test_finops_member_budget_sql.py`

Run: `node --test src/memberBudgetViewModel.test.mjs`

Expected: PASS.

---

### Task 3: Verified member identity display and configuration discoverability

**Files:**
- Modify: `web/src/components.jsx`
- Modify: `web/src/governanceViewModel.js`
- Reuse: `web/src/EnterpriseIdentityPolicyModal.jsx`
- Modify: `web/src/styles.css`
- Test: `web/src/governanceViewModel.test.mjs`
- Test: `web/src/enterpriseIdentityPolicyModal.test.mjs`
- Modify: `web/tests/finops-portal-acceptance.spec.mjs`

**Interfaces:**
- Consumes: existing `display.name`, `display.email`, `identity_visibility`, `loadEnterpriseIdentityPolicy`, and `updateEnterpriseIdentityPolicy`.
- Produces: primary member label = verified name, secondary label = verified email, technical pseudonym only in an expandable details area; owner-only identity display configuration is discoverable from Settings.

- [ ] **Step 1: Write failing member rendering tests**

```js
assert.equal(member.label, "Fu Zihao");
assert.equal(member.detail, "user@example.com");
assert.equal(member.subjectLabel, "member_4350e170…ac18");
```

Also prove that an untrusted-domain member remains pseudonymous.

- [ ] **Step 2: Run the tests and verify failure in Settings markup**

Run: `node --test src/governanceViewModel.test.mjs src/enterpriseIdentityPolicyModal.test.mjs`

- [ ] **Step 3: Render verified identity and expose policy control**

In the member row render `m.label`, render `m.detail` only for `verified_enterprise`, and put `m.subjectLabel` under “技术标识”. Add the existing identity-policy modal to the Settings member header for workspace owners.

- [ ] **Step 4: Run focused Node and Playwright tests**

Expected: verified email visible, pseudonym preserved for unverified members, and the policy save request remains workspace scoped.

---

### Task 4: Trend chart, ROI case narrative, and refresh layout

**Files:**
- Modify: `backend/finops/decision_service.py`
- Modify: `web/src/finopsDecisionViewModel.js`
- Modify: `web/src/finops/RoiDecisionPage.jsx`
- Modify: `web/src/FinOpsPortal.jsx`
- Modify: `web/src/styles.css`
- Test: `tests/test_finops_decision_service.py`
- Modify: `web/src/finopsDecisionViewModel.test.mjs`
- Modify: `web/src/finopsLayout.test.mjs`
- Modify: `web/tests/finops-operations-management.spec.mjs`

**Interfaces:**
- Consumes: existing server scenario title, `hours_saved`, `hourly_value`, `avoided_loss_or_revenue`, `implementation_cost`, `monthly_fixed_cost`, `model_cost`, and result metrics.
- Produces: safe ROI `case_story` with assumptions and formula; hover-only trend value labels; teal cache stack; compact refresh control.

- [ ] **Step 1: Write failing ROI and layout tests**

```python
assert decision["case_story"]["title"] == "运营自动化测算"
assert decision["case_story"]["formula"] == "节省工时价值 + 可避免损失或收入 - AI 运营投入 = 月度净收益"
```

Node tests must assert that `.finops-trend-value` is not a permanently visible plot child, cache uses a distinct semantic class, and `.finops-live > span` has no fixed 112px minimum.

- [ ] **Step 2: Run focused tests and verify failure**

Run: `python -m pytest -q tests/test_finops_decision_service.py`

Run: `node --test src/finopsDecisionViewModel.test.mjs src/finopsLayout.test.mjs`

- [ ] **Step 3: Add safe scenario assumptions to the decision payload**

Only allowlist numeric scenario inputs and a bounded title. Do not return internal scenario storage data or convert estimated assumptions into observed evidence.

- [ ] **Step 4: Add the case narrative card**

Render four compact cells: business baseline, AI-assisted saving, AI operating investment, and validation boundary. Include the formula and an “问 AI” action bound to the current scenario.

- [ ] **Step 5: Refine trend interaction and refresh control**

Remove permanently visible per-column values from the plot; keep dates below the plot and values in the viewport tooltip. Use emerald/teal for cached tokens, add a small lift/outline on hover and focus, and disable motion under `prefers-reduced-motion`. Remove the fixed timestamp width from `.finops-live`.

- [ ] **Step 6: Run focused tests**

Expected: backend, Node, and Playwright focused tests pass on desktop and 390px mobile.

---

### Task 5: Full verification and candidate release

**Files:**
- Create: `docs/validation/2026-08-10-finops-operations-closure.md`
- Modify only if required by verified deployment: `docs/validation/2026-08-10-finops-operations-closure-runbook.md`

- [ ] **Step 1: Run repository gates**

Run: `python -m pytest -q`

Run: `node --test` from `web/`

Run: `npm run build` from `web/`

Run: `npx playwright test` with a unique `DF_PLAYWRIGHT_PORT`.

Run: `git diff --check`.

- [ ] **Step 2: Deploy zero-traffic candidate revisions**

Build immutable backend and web images from the verified commit. Apply only the additive SQL migration. Configure the ACS resource link, sender, diagnostic categories, Log Analytics destination, and least-privilege managed-identity roles without exposing credentials.

- [ ] **Step 3: Perform real candidate acceptance**

Verify one DeepSeek call from an Agent and its model-specific cost, one trusted Entra member email display, one budget test email from accepted to delivered, trend hover/focus on desktop and mobile, ROI case narrative, and compact refresh layout.

- [ ] **Step 4: Production release gate**

Only after the candidate evidence is recorded: merge, deploy production revisions, switch traffic, then re-run health, authenticated page, DeepSeek, email, and responsive UI checks. Keep `DF_FINOPS_ACTIONS_ENABLED=0`.

## Self-review

- Spec coverage: all six user-visible issues and the two live environment causes are mapped to Tasks 1–5.
- Placeholder scan: no deferred implementation placeholders remain.
- Type consistency: public email states and ROI case-story names are shared consistently across backend, view-model, and UI tasks.
- Security: secrets, raw provider errors, provider message IDs, untrusted emails, and production governance execution remain outside public responses.
