# FinOps Demo Integrity Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the real FinOps API, frontend decision views, demo seed, assistant evidence scope, and model routing agree so the complete IT/finance demo is mathematically correct, evidence-bound, and reproducible.

**Architecture:** Keep the existing ledger, query service, authorization, decision endpoints, request-detail drawer, and governance state machine. Add explicit presentation contracts at the decision-service boundary, derive authorized request evidence in the router, use closed frontend mappings, and make the Playwright mock mirror the real demo seed instead of inventing a parallel contract.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, React, Vite, Node test runner, Playwright, Azure Container Apps candidate workflow.

## Global Constraints

- Preserve Easy Auth, Entra tenant/workspace authorization, and all current cross-tenant restrictions.
- Keep `DF_FINOPS_ACTIONS_ENABLED=0`; scans and AI remain advisory and read-only.
- Cost is request-level estimated cost, never an Azure invoice claim.
- ROI scenario values, observed operation evidence, and verified business outcomes remain separate.
- Do not expose raw provider IDs, secrets, prompts, response bodies, arbitrary scripts, or APIM XML.
- Do not add periodic refreshes; a completed manual risk scan may trigger one risk-decision refresh.
- Do not deploy or switch production traffic in this implementation plan.
- Preserve unrelated `.superpowers/sdd` files and other user-owned untracked files.

---

### Task 1: Canonical ROI Value Bridge and Demo Contract

**Files:**
- Modify: `backend/finops/decision_service.py`
- Modify: `tests/test_finops_decision_service.py`
- Modify: `web/src/finopsDecisionViewModel.js`
- Modify: `web/src/finopsDecisionViewModel.test.mjs`
- Modify: `web/tests/finopsMockApi.mjs`
- Modify: `web/tests/finops-operations-management.spec.mjs`

**Interfaces:**
- Consumes: `build_roi_decision(...).metrics` and the scenario result returned by `roi_scenario_store`.
- Produces: `value_bridge.items: [{id, label, value, unit, status, explanation}]` where `monthly_total_cost.value` is negative for formula direction while the metric card remains positive.

- [ ] **Step 1: Write failing backend and frontend tests**

Add backend assertions:

```python
assert result["value_bridge"]["items"] == [
    {"id": "monthly_benefit", "label": "月度收益", "value": 3000, "unit": "USD", "status": "estimated", "explanation": "情景测算中的月度收益。"},
    {"id": "monthly_total_cost", "label": "AI 运营总投入", "value": -800, "unit": "USD", "status": "estimated", "explanation": "价值桥中的成本扣减项。"},
    {"id": "monthly_net_benefit", "label": "月度净收益", "value": 2200, "unit": "USD", "status": "estimated", "explanation": "月度收益减去 AI 运营总投入。"},
]
```

Add Node assertions for both the explicit-item contract and the old-service fallback:

```js
assert.equal(view.valueBridge.items.find((item) => item.id === "monthly_total_cost").direction, "negative");
assert.equal(view.valueBridge.items.find((item) => item.id === "monthly_total_cost").formulaValueLabel, "$800.00");
```

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```powershell
python -m pytest -q tests/test_finops_decision_service.py
Set-Location web; node --test src/finopsDecisionViewModel.test.mjs
```

Expected: backend lacks `value_bridge.items`; frontend fallback reports cost direction as positive.

- [ ] **Step 3: Implement the canonical bridge and fallback**

In `build_roi_decision()`, construct explicit bridge items from the scenario result. In `safeBridge()`, when no explicit items exist, clone `monthly_total_cost` with `value: -Math.abs(value)` before `proportionalItems()`.

Update `demoCompletenessRoiDecision()` and related mock scenario records to the seed values `3000`, `800`, `2200`, `2.75`, and `2.2` months.

- [ ] **Step 4: Verify GREEN and the displayed formula**

Run the focused Python and Node tests, then the ROI Playwright case. Assert the accessible formula reads `月度收益减去 AI 运营总投入等于月度净收益` and shows `$3,000.00`, `$800.00`, `$2,200.00`, and `275%`.

- [ ] **Step 5: Commit the task**

```powershell
git add backend/finops/decision_service.py tests/test_finops_decision_service.py web/src/finopsDecisionViewModel.js web/src/finopsDecisionViewModel.test.mjs web/tests/finopsMockApi.mjs web/tests/finops-operations-management.spec.mjs
git commit -m "fix(finops): make ROI value bridge contract honest"
```

### Task 2: Risk Semantics and String Evidence Projection

**Files:**
- Modify: `backend/finops/decision_service.py`
- Modify: `tests/test_finops_decision_service.py`
- Modify: `web/src/finopsDecisionViewModel.js`
- Modify: `web/src/finopsDecisionViewModel.test.mjs`
- Modify: `web/src/finops/RiskDecisionPage.jsx`
- Modify: `web/tests/finops-operations-management.spec.mjs`

**Interfaces:**
- Consumes: existing `sample_count`, `impact`, and `selected_evidence_summaries[].signal` values.
- Produces: customer-facing “评估样本量” and “运营严重度” semantics plus numeric-or-string `signal.valueLabel`.

- [ ] **Step 1: Write failing semantic and signal tests**

Add a real-service-shaped evidence fixture containing:

```js
[
  { metric: "request_status", value: "failed", unit: "status", expected: "调用失败" },
  { metric: "cache_state", value: "miss", unit: "state", expected: "缓存未命中" },
  { metric: "pricing_status", value: "unpriced", unit: "status", expected: "未计价" },
  { metric: "gateway_coverage", value: "unmanaged", unit: "state", expected: "未纳入统一入口" },
]
```

Assert `portfolio_metadata.size === "sample_count"`, UI copy contains “评估样本量” and “运营严重度”, and no main risk card contains “真实影响范围” or “业务影响”.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```powershell
python -m pytest -q tests/test_finops_decision_service.py
Set-Location web; node --test src/finopsDecisionViewModel.test.mjs
```

Expected: string values collapse to unavailable and old risk labels remain.

- [ ] **Step 3: Implement closed mappings**

Add `EVIDENCE_SIGNAL_LABELS`, `EVIDENCE_VALUE_LABELS`, and `EVIDENCE_UNIT_LABELS`. Preserve numeric formatting for numeric values; for strings, accept only mapped values and expose the mapped label. Change the backend portfolio size metadata to `sample_count`; accept both `sample_count` and legacy `affected_scope` in the frontend, always displaying “评估样本量”.

Replace customer copy in `RiskDecisionPage.jsx` without changing field names or risk scoring logic.

- [ ] **Step 4: Verify GREEN and render assertions**

Run the focused tests and the desktop/mobile risk Playwright cases. Verify raw `gateway_coverage`, `cache_state`, `failed`, `miss`, and `unmanaged` do not appear in primary cards.

- [ ] **Step 5: Commit the task**

```powershell
git add backend/finops/decision_service.py tests/test_finops_decision_service.py web/src/finopsDecisionViewModel.js web/src/finopsDecisionViewModel.test.mjs web/src/finops/RiskDecisionPage.jsx web/tests/finops-operations-management.spec.mjs
git commit -m "fix(finops): present risk evidence with defensible semantics"
```

### Task 3: Refresh Risk Decision After a Manual Scan

**Files:**
- Modify: `web/src/FinOpsPortal.jsx`
- Modify: `web/tests/finopsMockApi.mjs`
- Modify: `web/tests/finops-operations-management.spec.mjs`

**Interfaces:**
- Consumes: `runRiskScan()`, `requestTabRefresh("risk", {force: true})`, and the risk tab resource lifecycle.
- Produces: one post-scan forced decision reload while retaining the completed scan result.

- [ ] **Step 1: Write a failing Playwright flow**

Configure the mock so the initial risk decision has four priorities and the post-scan forced request returns six. Assert:

```js
await page.getByRole("button", { name: "重新扫描" }).click();
await expect(page.getByText("6 项需关注")).toBeVisible();
await expect(page.locator(".finops-decision-priority-item")).toHaveCount(6);
```

Also cover the refresh-failure path: the scan summary remains visible and a retryable priority-update warning appears.

- [ ] **Step 2: Run the Playwright test and verify RED**

Expected: scan state updates but the priority list remains the pre-scan snapshot.

- [ ] **Step 3: Trigger one forced risk refresh**

After `setRiskScanState(...)`, call `requestTabRefresh("risk", { force: true })`. Reconcile `selectedRiskId` when the refreshed priority set changes. Do not clear the completed scan or start any interval.

- [ ] **Step 4: Verify GREEN**

Run the focused Playwright spec on a unique `DF_PLAYWRIGHT_PORT`; confirm one scan POST and one forced decision GET occur.

- [ ] **Step 5: Commit the task**

```powershell
git add web/src/FinOpsPortal.jsx web/tests/finopsMockApi.mjs web/tests/finops-operations-management.spec.mjs
git commit -m "fix(finops): refresh priorities after risk scans"
```

### Task 4: Bind Operations AI to the Selected Evidence

**Files:**
- Modify: `backend/finops/assistant.py`
- Modify: `backend/finops/agent_inputs.py`
- Modify: `backend/finops/router.py`
- Modify: `tests/test_finops_agent_inputs.py`
- Modify: `tests/test_finops_assistant.py`
- Modify: `tests/test_finops_api.py`
- Modify: `web/src/finopsInteraction.js`
- Modify: `web/src/finopsInteraction.test.mjs`
- Modify: `web/src/finops/RiskDecisionPage.jsx`
- Modify: `web/src/FinOpsAssistant.jsx`
- Modify: `web/tests/finopsMockApi.mjs`
- Modify: `web/tests/finops-operations-management.spec.mjs`

**Interfaces:**
- Consumes: `AssistantMetricContext`, `select_policy_evidence()`, `select_metric_evidence()`, and authorized query events.
- Produces: optional `policy_type` and up to three `evidence_refs`; `build_finops_agent_input(..., evidence_refs=...)` returns only the selected authorized catalog.

- [ ] **Step 1: Write failing backend contract and authorization tests**

Test three cases:

```python
assert response.json()["evidence_refs"] == ["req_latency_authorized"]
assert forged_response.json()["status"] == "insufficient_data"
assert "req_other_workspace" not in json.dumps(forged_response.json())
```

The model runner must receive only the selected rule evidence. A client ref that is not in the service-selected policy set must be rejected by intersection, not silently widened to the full window.

- [ ] **Step 2: Run focused Python tests and verify RED**

Expected: request validation rejects the new fields or the assistant still receives the first 50 query requests.

- [ ] **Step 3: Implement bounded evidence selection**

Add allowlisted `policy_type` and validated `evidence_refs` to `AssistantMetricContext`. In `assistant_query()`:

```python
selected = (
    select_policy_evidence(events, context.policy_type, 3)
    if context.policy_type
    else select_metric_evidence(events, context.metric_id, 3)
)
allowed = {item.request_ref for item in selected.items}
requested = set(context.evidence_refs)
effective = allowed & requested if requested else allowed
```

Pass `effective` to `build_finops_agent_input()`. When empty, return an insufficient-data payload without calling the model.

- [ ] **Step 4: Add frontend context propagation and RED/GREEN tests**

Extend `metricContext()` and `FinOpsAssistant` submission to retain the bounded fields. Risk scan and priority buttons pass their policy and request refs. Assert the generated question, assistant response refs, and evidence drawer refs all match the selected item.

- [ ] **Step 5: Verify focused backend, Node, and Playwright tests**

Run assistant and interaction suites, then select latency, cache, unpriced, and error items in Playwright. Each answer must cite the corresponding evidence type and must not reuse the cache example for unrelated policies.

- [ ] **Step 6: Commit the task**

```powershell
git add backend/finops/assistant.py backend/finops/agent_inputs.py backend/finops/router.py tests/test_finops_agent_inputs.py tests/test_finops_assistant.py tests/test_finops_api.py web/src/finopsInteraction.js web/src/finopsInteraction.test.mjs web/src/finops/RiskDecisionPage.jsx web/src/FinOpsAssistant.jsx web/tests/finopsMockApi.mjs web/tests/finops-operations-management.spec.mjs
git commit -m "fix(finops): bind operations AI to selected evidence"
```

### Task 5: Project ROI Run and Outcome Evidence to Request Details

**Files:**
- Modify: `backend/finops/router.py`
- Modify: `backend/finops/decision_service.py`
- Modify: `tests/test_finops_decision_service.py`
- Modify: `tests/test_finops_decision_api.py`
- Modify: `web/src/finops/RoiDecisionPage.jsx`
- Modify: `web/tests/finopsMockApi.mjs`
- Modify: `web/tests/finops-operations-management.spec.mjs`

**Interfaces:**
- Consumes: query-scoped `FinOpsRequestEvent.run_id`, ROI observed run IDs, artifact run IDs, and outcome source run IDs.
- Produces: ROI maturity stages whose `evidence_refs` are authorized `req_*` values that the existing request drawer can open.

- [ ] **Step 1: Write failing mapping tests**

Create two requests with different run IDs and one outcome sourced from the second run. Assert the ROI payload exposes only the corresponding request refs for usage/output/outcome stages and never returns another workspace’s ref.

- [ ] **Step 2: Run focused decision API tests and verify RED**

Expected: stages expose `run-*` or `outcome-*`, and `RoiDecisionPage` filters all of them out.

- [ ] **Step 3: Implement router-side authorized mapping**

Build a bounded map from current query rows:

```python
request_refs_by_run: dict[str, list[str]]
```

Pass the map and outcome source run IDs into `build_roi_decision()`. Replace stage refs with mapped request refs while keeping evidence counts and gaps truthful when a mapping is unavailable.

- [ ] **Step 4: Simplify the frontend action filter**

Keep the existing safe `req_*` validation, but update copy and tests to prove the real service now provides openable refs. Do not add a client-side conversion from run IDs.

- [ ] **Step 5: Verify GREEN and evidence drawer flow**

Run decision-service/API tests and Playwright. Click each available ROI stage and assert the request drawer opens the stage-specific request.

- [ ] **Step 6: Commit the task**

```powershell
git add backend/finops/router.py backend/finops/decision_service.py tests/test_finops_decision_service.py tests/test_finops_decision_api.py web/src/finops/RoiDecisionPage.jsx web/tests/finopsMockApi.mjs web/tests/finops-operations-management.spec.mjs
git commit -m "fix(finops): expose request-level ROI evidence"
```

### Task 6: Route FinOps and ROI Analysis Agents Through Workspace Terra Policy

**Files:**
- Modify: `backend/workspace_model_config.py`
- Modify: `backend/finops/router.py`
- Modify: `backend/finops/analysis_agents.py`
- Modify: `backend/finops/demo_workspace_seed.py`
- Modify: `tests/test_workspace_model_config.py`
- Modify: `tests/test_finops_api.py`
- Modify: `tests/test_finops_analysis_agents.py`
- Modify: `web/tests/finopsMockApi.mjs`

**Interfaces:**
- Consumes: `load_workspace_model_configuration()`, `workspace_model_policy_scope()`, `select_text_route_record()`, and `model_route_scope()`.
- Produces: configurable `df-finops-analyst` and `df-roi-analyst` assignments; demo default Terra primary with analysis fallback.

- [ ] **Step 1: Write failing policy tests**

Assert both Agent IDs are accepted by `validate_workspace_routing_policy()`. Wrap assistant and analysis calls with a policy assigning Terra and assert the runner observes deployment `gpt-5.6-terra` and selection `agent_policy`.

- [ ] **Step 2: Run focused model and FinOps tests and verify RED**

Expected: workspace policy rejects the new Agent IDs and FinOps calls use the general chat route.

- [ ] **Step 3: Add Agent IDs and a shared FinOps route scope helper**

Create one router helper that loads the selected workspace configuration and enters:

```python
with workspace_model_policy_scope(policy=config.get("policy"), price_card=config.get("price_card")):
    selected = select_text_route_record("full_analysis", agent_id=agent_id)
    with model_route_scope(route=selected, price_card=config.get("price_card")):
        yield selected
```

Use it for assistant, FinOps analysis, and ROI analysis model calls. Do not change read-only decision endpoints that do not invoke a model.

- [ ] **Step 4: Seed and mock the demo assignment**

Add Terra primary and analysis fallback for the two Agent IDs in the demo workspace configuration and settings mock. Keep base revision and audit persistence requirements unchanged.

- [ ] **Step 5: Verify GREEN and telemetry assertions**

Run model-policy, route-telemetry, FinOps assistant, and analysis-agent tests. Assert fallback remains safe when Terra is unavailable.

- [ ] **Step 6: Commit the task**

```powershell
git add backend/workspace_model_config.py backend/finops/router.py backend/finops/analysis_agents.py backend/finops/demo_workspace_seed.py tests/test_workspace_model_config.py tests/test_finops_api.py tests/test_finops_analysis_agents.py web/tests/finopsMockApi.mjs
git commit -m "feat(finops): route operations analysts through workspace policy"
```

### Task 7: Full Demo Regression and Release Handoff

**Files:**
- Modify if needed: `backend/finops/candidate_acceptance.py`
- Modify if needed: `tests/test_finops_candidate_acceptance.py`
- Modify: `docs/validation/2026-08-06-finops-demo-integrity-acceptance.md`

**Interfaces:**
- Consumes: all completed task contracts.
- Produces: reproducible local acceptance evidence and a candidate-only deployment checklist.

- [ ] **Step 1: Extend candidate acceptance assertions**

Require ROI bridge subtraction, six distinct risk evidence sets, openable ROI request refs, non-empty localized string signals, selected-item AI evidence, and the two operations Agent assignments.

- [ ] **Step 2: Run all repository gates**

Run:

```powershell
python -m pytest -q
Set-Location web
node --test
npm run build
$env:DF_PLAYWRIGHT_PORT='5257'; npx playwright test
git diff --check
```

Expected: zero failures; the existing Vite chunk-size advisory may remain but no new warning is accepted.

- [ ] **Step 3: Review real desktop and mobile screenshots**

Inspect overview, cost, ROI, risk, each evidence drawer, scan completion, AI response, and 390px mobile views. Reject overlaps, clipped tooltips, English internal enums, repeated evidence, incorrect formula operators, or stale post-scan priorities.

- [ ] **Step 4: Run a bounded secret and artifact scan**

Scan only committed diff and tracked files for private keys, bearer tokens, connection strings, API keys, PATs, and generated test artifacts. Keep `.superpowers/sdd`, `web/test-results`, screenshots, and workspace runtime data out of commits.

- [ ] **Step 5: Write the acceptance report**

Record exact commit range, command outputs, screenshot paths, known non-blocking advisories, and the zero-traffic candidate steps. State explicitly that Azure deployment and production traffic switching were not performed.

- [ ] **Step 6: Commit the acceptance contract**

```powershell
git add backend/finops/candidate_acceptance.py tests/test_finops_candidate_acceptance.py docs/validation/2026-08-06-finops-demo-integrity-acceptance.md
git commit -m "test(finops): gate the complete demo integrity flow"
```
