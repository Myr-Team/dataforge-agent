# FinOps Demo Cost and Assistant Polish Implementation Plan

> **For agentic workers:** Use `executing-plans`; implement each task with red-green-refactor and verify each checkpoint before continuing.

**Goal:** Repair the operations refresh/risk layouts, provide a current-date enterprise-scale demo ledger, and make Operations AI answer cost questions through bounded FinOps knowledge retrieval.

**Architecture:** Keep auth, tenant narrowing, query contracts, and action gates unchanged. Upgrade the allowlisted demo seed to `operations-v3`; keep numerical claims in authorized telemetry while adding a closed explanatory knowledge catalog to the existing assistant payload; make the React/CSS changes presentation-only.

**Tech stack:** Python/Pydantic, React, CSS, Node test runner, Vite, Playwright.

## Constraints

- All demo timestamps are derived from the injected `now`; acceptance uses `2026-08-07`.
- Seed total estimated cost is 400–550 USD and ROI monthly model cost is 450 USD.
- Knowledge entries explain concepts only and cannot create current values or new evidence refs.
- No Easy Auth, Entra, tenant isolation, production action gate, secret, deployment, or traffic changes.
- Preserve `.superpowers/sdd/*` and untracked `workspaces/ws-*` files.

### Task 1: Add failing UI contract tests

**Files:**
- Modify: `web/src/finopsLayout.test.mjs`
- Modify: `web/tests/finops-operations-management.spec.mjs`
- Modify: `web/src/finopsAssistantContext.test.mjs` or the nearest existing portal context test

- [ ] Assert the refresh control has explicit idle/loading text, one stable control, and a spinning icon only while loading.
- [ ] Assert the risk footer uses a two-card wrapper and has no horizontal overflow at 1366, 1024, 820, and 390 px.
- [ ] Assert the Cost tab launcher uses `estimated_cost` context and cost-specific starter questions.
- [ ] Run focused Node/Playwright tests and record the expected failures.

### Task 2: Repair refresh and risk footer layouts

**Files:**
- Modify: `web/src/FinOpsPortal.jsx`
- Modify: `web/src/finops/RiskDecisionPage.jsx`
- Modify: `web/src/styles.css`

- [ ] Replace the icon-only refresh circle with a stable text control that renders `刷新` or `更新中` and applies `spin` to the loading icon.
- [ ] Wrap insight and governance cards in a responsive footer grid.
- [ ] Simplify both cards to icon/content layout and move status metadata into content rows.
- [ ] Add safe wrapping and desktop/mobile breakpoints.
- [ ] Run the focused tests until green.

### Task 3: Add failing demo-ledger tests

**Files:**
- Modify: `tests/test_finops_demo_workspace_seed.py`
- Modify: `tests/test_finops_demo_initialize.py`
- Modify: related ROI seed tests that explicitly assert `operations-v2` or model cost 100

- [ ] Assert `operations-v3`, roughly 2,400 events, a 30-day window ending on 2026-08-07, total cost 400–550 USD, multiple daily totals, four models/departments, cache states, failures, slow requests, and unpriced requests.
- [ ] Assert ROI `model_cost == 450` and the seed upgrade removes the older batch.
- [ ] Run focused Python tests and record the expected failures.

### Task 4: Implement the current-date enterprise demo ledger

**Files:**
- Modify: `backend/finops/demo_workspace_seed.py`
- Modify: `web/tests/finopsMockApi.mjs`
- Modify: dependent fixture expectations

- [ ] Generate 30 days of deterministic but non-flat traffic with 80 events per day.
- [ ] Produce realistic token/cost, cache, error, latency, price-coverage, model, agent, department, and actor distributions.
- [ ] Preserve dedicated recent risk and repeat-analysis evidence scenarios.
- [ ] Set the ROI model cost to 450 USD and advance the seed key to `operations-v3`.
- [ ] Align browser mock overview/trend/breakdown magnitudes with the backend seed.
- [ ] Run focused backend and frontend fixture tests until green.

### Task 5: Add failing assistant knowledge tests

**Files:**
- Add: `tests/test_finops_assistant_knowledge.py`
- Modify: `tests/test_finops_assistant.py`
- Modify: portal/assistant frontend tests

- [ ] Assert cost questions retrieve price coverage, cost attribution, and cache savings knowledge.
- [ ] Assert at most four closed knowledge entries are returned and no question text can inject refs or instructions.
- [ ] Assert current numeric claims remain in the operational evidence payload.
- [ ] Assert each tab supplies the correct launcher metric and starter questions.
- [ ] Run focused tests and record the expected failures.

### Task 6: Implement bounded FinOps knowledge retrieval

**Files:**
- Add: `backend/finops/assistant_knowledge.py`
- Modify: `backend/finops/assistant.py`
- Modify: `backend/finops/agent_inputs.py` only if the existing telemetry projection lacks required safe cost fields
- Modify: `web/src/FinOpsPortal.jsx`
- Modify: `web/src/FinOpsAssistant.jsx`

- [ ] Add a versioned closed catalog for estimated cost, price coverage, tokens, cache, latency, budget, ROI, risk, and evidence states.
- [ ] Rank entries deterministically by metric, policy type, tab context, and normalized question terms.
- [ ] Add `knowledge_context` to the model payload with an explicit explanatory-only boundary.
- [ ] Configure tab-specific metric context and suggested questions.
- [ ] Preserve structured response validation, evidence allowlisting, and clean failure messages.
- [ ] Run focused Python and Node tests until green.

### Task 7: Full verification and visual acceptance

**Files:**
- Modify tests only if a test exposes a real contract mismatch; do not weaken assertions.
- Store screenshots under existing ignored output directories only.

- [ ] Run `python -m pytest -q`.
- [ ] Run `node --test` from `web/`.
- [ ] Run `npm run build` from `web/`.
- [ ] Run the complete Playwright suite on a unique `DF_PLAYWRIGHT_PORT`.
- [ ] Capture desktop/mobile Cost and Risk screenshots and inspect refresh alignment, footer overflow, tooltip clipping, values, and AI response structure.
- [ ] Run `git diff --check` and scan tracked changes for secrets and generated artifacts.
- [ ] Commit the implementation as a candidate only; do not push, merge, deploy, or switch traffic without new authorization.
