# FinOps Portal UI and GPT-5.6 Pricing Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct four visible Operations Management defects and add the three verified GPT-5.6 Global Standard prices.

**Architecture:** Keep the existing React/CSS component boundaries and official-price catalog. Move event evidence into the existing trend tooltip, scope metric help visibility to a dedicated button, offset the evidence drawer below the global header, and extend the additive immutable price catalog.

**Tech Stack:** React, CSS, Node test runner, Playwright, Python, Pydantic.

## Global Constraints

- Do not weaken Easy Auth, workspace authorization, audit persistence, or tenant scoping.
- Pricing remains an estimate from a versioned official catalog, not an Azure bill.
- Preserve all pre-existing uncommitted FinOps changes and unrelated test artifacts.
- Write failing tests before production changes.

---

### Task 1: Metric interaction and evidence drawer layout

**Files:**
- Modify: `web/src/FinOpsPortal.jsx`
- Modify: `web/src/styles.css`
- Test: `web/tests/finops-operations-management.spec.mjs`
- Test: `web/tests/finops-evidence-drawer.spec.mjs`

**Interfaces:**
- Consumes: `TrendBars({ payload, metric, comparisonPayload, events })`, `MetricCards({ payload, scope, onEvidence, onAsk, onConfigurePricing })`.
- Produces: accessible buttons named `<metric label>说明`, hover-only `.finops-metric-tooltip`, and a drawer whose top edge is below `.topbar`.

- [ ] **Step 1: Write failing Playwright assertions**

Assert that `.finops-trend-event` has count zero, the last trend tooltip contains `运营事件 1 条`, focusing the metric card does not expose the tooltip, focusing `Token说明` does expose it, and the evidence drawer top is at least the topbar bottom.

- [ ] **Step 2: Run tests to verify RED**

Run: `npx playwright test tests/finops-operations-management.spec.mjs tests/finops-evidence-drawer.spec.mjs`

Expected: failure on the persistent dot, whole-card tooltip trigger, missing help button, and drawer overlap.

- [ ] **Step 3: Implement the minimal UI changes**

Replace the decorative event element with a tooltip row, group the metric label and help button, remove the metric-card focus trigger, and offset `.finops-drawer-backdrop` below the topbar.

- [ ] **Step 4: Run tests to verify GREEN**

Run: `npx playwright test tests/finops-operations-management.spec.mjs tests/finops-evidence-drawer.spec.mjs`

Expected: all selected tests pass.

### Task 2: GPT-5.6 official price catalog

**Files:**
- Modify: `backend/finops/data/official_model_prices.json`
- Modify: `tests/test_finops_official_pricing.py`
- Modify: `web/tests/finopsMockApi.mjs`
- Test: `web/tests/finops-operations-management.spec.mjs`

**Interfaces:**
- Consumes: `OfficialPriceCatalog.get(price_key)` and the existing `/api/finops/pricing/catalog` response.
- Produces: keys `azure-openai:gpt-5.6-sol:global-standard:global`, `azure-openai:gpt-5.6-terra:global-standard:global`, and `azure-openai:gpt-5.6-luna:global-standard:global`.

- [ ] **Step 1: Write failing Python and Playwright assertions**

Assert the exact input, cached-input and output rates for all three price keys, and assert each model appears in its route price selector.

- [ ] **Step 2: Run tests to verify RED**

Run: `python -m pytest -q tests/test_finops_official_pricing.py`

Run: `npx playwright test tests/finops-operations-management.spec.mjs`

Expected: GPT-5.6 entries are missing.

- [ ] **Step 3: Add the verified catalog records**

Add versioned USD Global Standard entries sourced from `https://prices.azure.com/api/retail/prices`, effective `2026-07-01T00:00:00Z`, with exact rates from the design.

- [ ] **Step 4: Run focused and full verification**

Run: `python -m pytest -q`

Run: `node --test`

Run: `npm run build`

Run: `npx playwright test`

Expected: zero failures; any unrelated pre-existing failure remains explicitly reported and blocks completion.
